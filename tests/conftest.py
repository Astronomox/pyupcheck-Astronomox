"""Tests never touch the network: any real HTTP request fails loudly."""

import pytest


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(url, *a, **k):
        raise NetworkBlocked(f"test tried to fetch {url}")
    import depshift.http
    monkeypatch.setattr(depshift.http, "http_get", blocked)
    import depshift.changelog
    import depshift.apisurface
    monkeypatch.setattr(depshift.changelog, "http_get", blocked)
    monkeypatch.setattr(depshift.apisurface, "http_get", blocked)
    # the URL policy resolves host names: answer "public" without DNS
    monkeypatch.setattr(depshift.http, "_resolves_internal", lambda host: False)


@pytest.fixture(autouse=True)
def _private_cache(monkeypatch, tmp_path):
    """Never read or write the real ~/.cache/pyupcheck from tests."""
    import depshift.cache
    monkeypatch.setattr(depshift.cache, "CACHE_DIR", tmp_path / "pyupcheck-cache")
    monkeypatch.setattr(depshift.cache, "_disabled", False)
