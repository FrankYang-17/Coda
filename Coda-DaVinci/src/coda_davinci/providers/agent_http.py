from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import threading
import time
import httpcore
import httpx
from hyperframe.frame import PingFrame

LOG = logging.getLogger(__name__)
PING_INTERVAL = 30.0
READ_DEADLINE = ContextVar("agent_http_read_deadline", default=None)


@contextmanager
def read_deadline(timeout):
    token = READ_DEADLINE.set(None if timeout is None else time.monotonic() + timeout)
    try:
        yield
    finally:
        READ_DEADLINE.reset(token)


class KeepaliveStream(httpcore.NetworkStream):
    def __init__(self, stream, interval, host=None):
        (self.stream, self.interval, self.host) = (stream, interval, host)
        self.write_lock = threading.Lock()
        self.next_ping = time.monotonic() + interval
        tls = stream.get_extra_info("ssl_object")
        self.http2 = tls is not None and tls.selected_alpn_protocol() == "h2"

    def read(self, max_bytes, timeout=None):
        if not self.http2:
            return self.stream.read(max_bytes, timeout)
        deadline = READ_DEADLINE.get()
        if deadline is None and timeout is not None:
            deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                raise httpcore.ReadTimeout("Agent API response read timed out")
            wait = max(0, self.next_ping - now)
            if remaining is not None:
                wait = min(wait, remaining)
            if wait > 0:
                try:
                    data = self.stream.read(max_bytes, wait)
                except httpcore.ReadTimeout:
                    continue
                self.next_ping = time.monotonic() + self.interval
                return data
            frame = PingFrame(0, opaque_data=os.urandom(8))
            self.write(
                frame.serialize(),
                min(self.interval, remaining) if remaining is not None else self.interval,
            )
            LOG.info("Agent HTTP/2 PING sent | host=%s", self.host)

    def write(self, buffer, timeout=None):
        with self.write_lock:
            self.stream.write(buffer, timeout)
            self.next_ping = time.monotonic() + self.interval

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = self.stream.start_tls(ssl_context, server_hostname, timeout)
        result = KeepaliveStream(stream, self.interval, server_hostname)
        tls = stream.get_extra_info("ssl_object")
        LOG.info(
            "Agent TLS negotiated | host=%s | ALPN=%s",
            server_hostname,
            tls.selected_alpn_protocol(),
        )
        return result

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def close(self):
        self.stream.close()


class KeepaliveBackend(httpcore.NetworkBackend):
    def __init__(self, backend, interval):
        (self.backend, self.interval) = (backend, interval)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = self.backend.connect_tcp(host, port, timeout, local_address, socket_options)
        return KeepaliveStream(stream, self.interval, host)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return KeepaliveStream(
            self.backend.connect_unix_socket(path, timeout, socket_options), self.interval
        )

    def sleep(self, seconds):
        self.backend.sleep(seconds)


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream, timeout):
        (self.stream, self.timeout) = (stream, timeout)

    def __iter__(self):
        iterator = iter(self.stream)
        while True:
            try:
                with read_deadline(self.timeout):
                    chunk = next(iterator)
            except StopIteration:
                return
            yield chunk

    def close(self):
        self.stream.close()


class KeepaliveTransport(httpx.BaseTransport):
    def __init__(self, transport, interval):
        self.transport = transport
        pool = transport._pool
        pool._network_backend = KeepaliveBackend(pool._network_backend, interval)

    def handle_request(self, request):
        timeout = request.extensions.get("timeout", {}).get("read")
        with read_deadline(timeout):
            response = self.transport.handle_request(request)
        response.stream = ResponseStream(response.stream, timeout)
        return response

    def close(self):
        self.transport.close()


class AgentHTTPClient(httpx.Client):
    def __init__(self, *, timeout, ping_interval=PING_INTERVAL, **kwargs):
        super().__init__(http2=True, timeout=timeout, **kwargs)
        self._transport = KeepaliveTransport(self._transport, ping_interval)
        self._mounts = {
            pattern: KeepaliveTransport(transport, ping_interval) if transport is not None else None
            for (pattern, transport) in self._mounts.items()
        }
