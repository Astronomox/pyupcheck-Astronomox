"""Tests for the AST-based code scanner."""

import os
import tempfile
import textwrap

import pytest

from depshift.scanner import scan_file, scan_directory


def write_temp(code: str, suffix=".py") -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    f.write(textwrap.dedent(code))
    f.close()
    return f.name


def cleanup(*paths):
    for p in paths:
        try:
            os.unlink(p)
        except Exception:
            pass


# ── basic imports ────────────────────────────────────────────────────────────

def test_detects_simple_import():
    path = write_temp("import requests\n")
    try:
        usages = scan_file(path, "requests")
        apis = [u.attr_chain for u in usages]
        assert "requests" in apis
    finally:
        cleanup(path)


def test_detects_from_import():
    path = write_temp("from requests import get\n")
    try:
        usages = scan_file(path, "requests")
        apis = [u.attr_chain for u in usages]
        assert "requests.get" in apis
    finally:
        cleanup(path)


def test_detects_aliased_import():
    path = write_temp("import requests as req\nreq.get('http://example.com')\n")
    try:
        usages = scan_file(path, "requests")
        apis = [u.attr_chain for u in usages]
        assert any("requests" in a for a in apis)
    finally:
        cleanup(path)


# ── function calls ───────────────────────────────────────────────────────────

def test_detects_function_call():
    path = write_temp("import requests\nrequests.get('http://example.com')\n")
    try:
        usages = scan_file(path, "requests")
        calls = [u for u in usages if u.usage_type == "function_call"]
        assert any(u.attr_chain == "requests.get" for u in calls)
    finally:
        cleanup(path)


def test_captures_kwargs():
    path = write_temp("import requests\nrequests.get('http://x.com', verify=False, timeout=5)\n")
    try:
        usages = scan_file(path, "requests")
        call = next(u for u in usages if u.usage_type == "function_call" and "get" in u.attr_chain)
        assert call.kwargs is not None
        assert "verify" in call.kwargs
        assert "timeout" in call.kwargs
    finally:
        cleanup(path)


def test_detects_nested_attr_chain():
    path = write_temp("import requests\nfrom requests.packages import urllib3\n")
    try:
        usages = scan_file(path, "requests")
        apis = [u.attr_chain for u in usages]
        assert any("requests" in a for a in apis)
    finally:
        cleanup(path)


# ── dynamic imports ──────────────────────────────────────────────────────────

def test_detects_importlib_import_module():
    path = write_temp("import importlib\nmod = importlib.import_module('flask')\n")
    try:
        usages = scan_file(path, "flask")
        assert any(u.usage_type == "dynamic_import" for u in usages)
    finally:
        cleanup(path)


def test_detects_dunder_import():
    path = write_temp("mod = __import__('flask')\n")
    try:
        usages = scan_file(path, "flask")
        assert any(u.usage_type == "dynamic_import" for u in usages)
    finally:
        cleanup(path)


# ── exclusions ───────────────────────────────────────────────────────────────

def test_no_false_positives_other_package():
    path = write_temp("import flask\nflask.Flask(__name__)\n")
    try:
        usages = scan_file(path, "requests")
        assert len(usages) == 0
    finally:
        cleanup(path)


def test_syntax_error_returns_empty():
    path = write_temp("def broken(\n")
    try:
        usages = scan_file(path, "requests")
        assert usages == []
    finally:
        cleanup(path)


# ── directory scan ───────────────────────────────────────────────────────────

def test_scan_directory():
    with tempfile.TemporaryDirectory() as d:
        f1 = os.path.join(d, "a.py")
        f2 = os.path.join(d, "b.py")
        with open(f1, "w") as f:
            f.write("import requests\n")
        with open(f2, "w") as f:
            f.write("import flask\n")
        usages = scan_directory(d, "requests")
        assert any("requests" in u.attr_chain for u in usages)
        assert all("flask" not in u.attr_chain for u in usages)


def test_scan_directory_excludes_venv():
    with tempfile.TemporaryDirectory() as d:
        venv_dir = os.path.join(d, ".venv")
        os.makedirs(venv_dir)
        with open(os.path.join(venv_dir, "site.py"), "w") as f:
            f.write("import requests\n")
        usages = scan_directory(d, "requests")
        assert len(usages) == 0
