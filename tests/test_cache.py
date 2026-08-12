"""Tests for the local response cache."""

import json
import os
import time
import tempfile

import pytest


def test_cache_set_and_get(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.cache_set("mykey", {"data": 42})
    result = cache.cache_get("mykey")
    assert result == {"data": 42}


def test_cache_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    result = cache.cache_get("nonexistent_key_xyz_999")
    assert result is None


def test_cache_ttl_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.cache_set("expkey", "value")
    # immediately get with ttl=0 should expire
    result = cache.cache_get("expkey", ttl=0)
    assert result is None


def test_cache_stores_various_types(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.cache_set("str", "hello")
    cache.cache_set("list", [1, 2, 3])
    cache.cache_set("dict", {"a": "b"})
    cache.cache_set("none_val", None)

    assert cache.cache_get("str") == "hello"
    assert cache.cache_get("list") == [1, 2, 3]
    assert cache.cache_get("dict") == {"a": "b"}
    assert cache.cache_get("none_val") is None


def test_cache_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.cache_set("k1", 1)
    cache.cache_set("k2", 2)
    cache.cache_set("k3", 3)
    count = cache.cache_clear()
    assert count == 3
    assert cache.cache_get("k1") is None


def test_cache_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.disable_cache()
    cache.cache_set("key", "value")
    assert cache.cache_get("key") is None
    # re-enable for subsequent tests by reloading
    reload(cache)


def test_cache_key_collision_resistance(tmp_path, monkeypatch):
    monkeypatch.setenv("PYUPCHECK_CACHE_DIR", str(tmp_path))
    from importlib import reload
    import depshift.cache as cache
    reload(cache)

    cache.cache_set("key:a", "value_a")
    cache.cache_set("key:b", "value_b")
    assert cache.cache_get("key:a") == "value_a"
    assert cache.cache_get("key:b") == "value_b"
