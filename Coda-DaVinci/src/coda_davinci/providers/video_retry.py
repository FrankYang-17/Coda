import errno
from http.client import IncompleteRead, RemoteDisconnected
import logging
import re
import socket
import time
from urllib.error import HTTPError, URLError

DELAYS = (2, 4, 8, 16, 30)
PRIVACY = "InputImageSensitiveContentDetected.PrivacyInformation"
LOG = logging.getLogger(__name__)


class VideoAPIError(RuntimeError):
    pass


def api_keys(value):
    return tuple(dict.fromkeys((k for k in re.split("[\\s,;]+", value.strip()) if k)))


def retryable(error):
    if isinstance(
        error,
        (
            VideoAPIError,
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
            IncompleteRead,
            RemoteDisconnected,
            socket.gaierror,
        ),
    ):
        return True
    if isinstance(error, OSError) and error.errno in {
        errno.ETIMEDOUT,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.EPIPE,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }:
        return True
    return error.__cause__ is not None and retryable(error.__cause__)


def annotate(error, headers, body=""):
    headers = headers or {}
    error.request_id = headers.get("x-request-id") or headers.get("request-id")
    if not error.request_id:
        match = re.search("Request id:\\s*([\\w-]+)", body, re.I)
        error.request_id = match.group(1) if match else None
    error.privacy_rejection = PRIVACY in body
    return error


def backoff(delay, check_cancelled=None):
    if check_cancelled is None:
        time.sleep(delay)
        return
    end = time.monotonic() + delay
    while time.monotonic() < end:
        check_cancelled()
        time.sleep(min(0.25, max(0, end - time.monotonic())))


def with_retry(call, operation, *, client=None):
    if client is not None and getattr(client, "_managed_retry", False):
        return call()
    indices = range(client.key_index, len(client.api_keys)) if client else range(1)
    for index in indices:
        if client:
            client.key_index = index
            client.api_key = client.api_keys[index]
        for attempt in range(len(DELAYS) + 1):
            try:
                return call()
            except Exception as exc:
                if not retryable(exc):
                    raise
                LOG.warning(
                    "%s failed (%s) | key=%s attempt=%s/6 request_id=%s",
                    operation,
                    type(exc).__name__,
                    index + 1,
                    attempt + 1,
                    getattr(exc, "request_id", None),
                )
                if attempt < len(DELAYS):
                    backoff(DELAYS[attempt])
                elif client is None or index == len(client.api_keys) - 1:
                    raise
