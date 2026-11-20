"""One shared HTTP client: keeps connections alive across the many small
PyPI/GitHub requests a check makes, and is safe to use from worker threads."""

import threading
from typing import Optional

import httpx

_client: Optional[httpx.Client] = None
_lock = threading.Lock()


def client() -> httpx.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                    headers={"User-Agent": "pyupcheck"},
                )
    return _client


def http_get(url: str, **kwargs) -> httpx.Response:
    kwargs.pop("follow_redirects", None)  # always on for the shared client
    return client().get(url, **kwargs)
