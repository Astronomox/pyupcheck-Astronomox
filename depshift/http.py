"""One shared HTTP client: keeps connections alive across the many small
PyPI/GitHub requests a check makes, and is safe to use from worker threads.

It is also polite: requests to one host are capped at a few at a time
(lookups probe many candidate files, and a burst of them got GitHub's raw
file host to answer 429 Too Many Requests), and a 429 is retried once after
the wait the server asks for."""

import threading
import time
from typing import Dict, Optional
from urllib.parse import urlsplit

import httpx

_client: Optional[httpx.Client] = None
_lock = threading.Lock()

PER_HOST = 8          # concurrent requests to one host
MAX_RETRY_WAIT = 20   # seconds; a longer Retry-After is reported, not waited for
_slots: Dict[str, threading.BoundedSemaphore] = {}


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


def _slot(url: str) -> threading.BoundedSemaphore:
    host = urlsplit(url).netloc
    with _lock:
        if host not in _slots:
            _slots[host] = threading.BoundedSemaphore(PER_HOST)
        return _slots[host]


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(0.5, float(resp.headers.get("retry-after", "2")))
    except ValueError:
        return 2.0


def http_get(url: str, **kwargs) -> httpx.Response:
    kwargs.pop("follow_redirects", None)  # always on for the shared client
    with _slot(url):
        resp = client().get(url, **kwargs)
    if resp.status_code == 429:
        wait = _retry_after(resp)
        if wait <= MAX_RETRY_WAIT:
            time.sleep(wait)
            with _slot(url):
                resp = client().get(url, **kwargs)
    return resp


# the implementation under another name, for tests (they block http_get itself)
_http_get = http_get
