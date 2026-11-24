"""Shared video-only configuration and proxy policy, independent of the harness."""

import base64
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, build_opener, getproxies, proxy_bypass_environment, urlopen


def connection_settings():
    environment = dict(os.environ)
    source = environment.get("VIDEO_MODEL_ENV_FILE")
    if source:
        from dotenv import dotenv_values

        path = Path(source).expanduser()
        if not path.is_file():
            raise ValueError("VIDEO_MODEL_ENV_FILE does not name an existing configuration file")
        allowed = {
            "VIDEO_MODEL_BASE_URL",
            "VIDEO_MODEL_API_KEY",
            "VIDEO_MODEL",
            "VIDEO_MODEL_HTTP_PROXY",
            "VIDEO_MODEL_HTTPS_PROXY",
            "VIDEO_MODEL_NO_PROXY",
        }
        environment.update(
            {
                key: value
                for (key, value) in dotenv_values(path).items()
                if key in allowed and value is not None
            }
        )
    return {
        "base_url": environment.get("VIDEO_MODEL_BASE_URL", ""),
        "api_key": environment.get("VIDEO_MODEL_API_KEY", ""),
        "model": environment.get("VIDEO_MODEL", "doubao-seedance-2.5"),
        "http_proxy": environment.get("VIDEO_MODEL_HTTP_PROXY"),
        "https_proxy": environment.get("VIDEO_MODEL_HTTPS_PROXY"),
        "no_proxy": environment.get("VIDEO_MODEL_NO_PROXY"),
    }


class VideoProxyHandler(ProxyHandler):
    """A per-client proxy/NO_PROXY policy; never mutate the coding agent's environment."""

    def proxy_open(self, req, proxy, protocol):
        if req.host and proxy_bypass_environment(req.host, self.proxies):
            return None
        parsed = urlsplit(proxy if "://" in proxy else "http://" + proxy)
        if not parsed.hostname:
            raise ValueError("Invalid video proxy configuration")
        if parsed.username is not None:
            credentials = f"{unquote(parsed.username)}:{unquote(parsed.password or '')}"
            req.add_header(
                "Proxy-authorization", "Basic " + base64.b64encode(credentials.encode()).decode()
            )
        hostport = parsed.netloc.rsplit("@", 1)[-1]
        original_type = req.type
        req.set_proxy(hostport, parsed.scheme)
        if original_type == parsed.scheme or original_type == "https":
            return None
        return self.parent.open(req, timeout=req.timeout)


def video_opener(settings):
    if all(
        (value is None for value in (settings.http_proxy, settings.https_proxy, settings.no_proxy))
    ):
        return urlopen
    proxies = getproxies()
    for name, value in (
        ("http", settings.http_proxy),
        ("https", settings.https_proxy),
        ("no", settings.no_proxy),
    ):
        if value is not None:
            proxies.pop(name, None)
            if value:
                proxies[name] = value
    return build_opener(VideoProxyHandler(proxies)).open
