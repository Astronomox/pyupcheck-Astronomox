"""One shared HTTP client: keeps connections alive across the many small
PyPI/GitHub requests a check makes, and is safe to use from worker threads.

It is polite: requests to one host are capped at a few at a time (lookups
probe many candidate files, and a burst of them got GitHub's raw file host to
answer 429 Too Many Requests), and a 429 is retried once after the wait the
server asks for.

It is also careful, because some URLs it fetches come from package metadata
that anyone can publish (a project's "Changelog" link on PyPI):

* only ``https`` on the default port, to a public host: never a private,
  loopback, link-local or otherwise internal address (no reaching a cloud
  metadata service or an intranet from a CI runner), checked again on every
  redirect;
* responses are read up to a size limit, never into unbounded memory.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlsplit

import httpx

_client: Optional[httpx.Client] = None
_lock = threading.Lock()

PER_HOST = 8          # concurrent requests to one host
MAX_RETRY_WAIT = 20   # seconds; a longer Retry-After is reported, not waited for
MAX_BYTES = 32 * 1024 * 1024  # a page of release notes or metadata; downloads pass their own limit
_slots: Dict[str, threading.BoundedSemaphore] = {}


class BlockedURL(ValueError):
    """A URL pyupcheck refuses to fetch."""


class TooLarge(ValueError):
    """A response bigger than the caller allows."""


def _internal_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True  # unparseable: don't connect
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


@functools.lru_cache(maxsize=512)
def _resolves_internal(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        # can't resolve here (some proxied environments): the connection
        # itself goes through the proxy or fails; literal addresses and
        # internal names are still refused by check_url
        return False
    return any(_internal_ip(info[4][0]) for info in infos)


def check_url(url: str) -> None:
    """Raise BlockedURL unless ``url`` is https to a public host on port 443."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise BlockedURL(f"only https is fetched: {url[:200]}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or parts.username or parts.password:
        raise BlockedURL(f"no host, or credentials in the URL: {url[:200]}")
    if parts.port not in (None, 443):
        raise BlockedURL(f"only the default https port is used: {url[:200]}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise BlockedURL(f"internal host: {host}")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal and _internal_ip(host):
        raise BlockedURL(f"internal address: {host}")
    if not literal and "." not in host:
        raise BlockedURL(f"internal host: {host}")
    if not literal and _resolves_internal(host):
        raise BlockedURL(f"{host} resolves to an internal address")


def _on_request(request: httpx.Request) -> None:
    # runs for the first request and for every redirect
    check_url(str(request.url))


def client() -> httpx.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(
                    follow_redirects=True,
                    max_redirects=5,
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                    headers={"User-Agent": "pyupcheck"},
                    event_hooks={"request": [_on_request]},
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


def _get(url: str, max_bytes: int, **kwargs) -> httpx.Response:
    """GET, reading the body only up to ``max_bytes``."""
    with _slot(url):
        with client().stream("GET", url, **kwargs) as resp:
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise TooLarge(f"{url[:200]} is {int(declared)} bytes (limit {max_bytes})")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise TooLarge(f"{url[:200]} is over {max_bytes} bytes")
            # a complete, already-read response the callers use as before. The
            # body is already decompressed (and counted that way, so a gzip
            # bomb hits the limit too): the headers describing the encoded
            # body would make httpx decode it a second time.
            headers = [(k, v) for k, v in resp.headers.multi_items()
                       if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
            return httpx.Response(resp.status_code, headers=headers, content=bytes(body),
                                  request=resp.request, extensions=resp.extensions)


def http_get(url: str, max_bytes: int = MAX_BYTES, **kwargs) -> httpx.Response:
    kwargs.pop("follow_redirects", None)  # always on for the shared client, with checks
    check_url(url)
    resp = _get(url, max_bytes, **kwargs)
    if resp.status_code == 429:
        wait = _retry_after(resp)
        if wait <= MAX_RETRY_WAIT:
            time.sleep(wait)
            resp = _get(url, max_bytes, **kwargs)
    return resp


# the implementation under another name, for tests (they block http_get itself)
_http_get = http_get
