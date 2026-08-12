"""Tests for the API surface extractor and differ."""

import ast
import pytest

from depshift.apisurface import (
    APISurface, FuncSig, _SurfaceVisitor, _is_public_api,
    diff_surfaces,
)


# ── _is_public_api ────────────────────────────────────────────────────────────

def test_public_module_is_public():
    assert _is_public_api("requests.auth") is True


def test_private_module_is_not_public():
    assert _is_public_api("requests._compat") is False


def test_dunder_is_public():
    assert _is_public_api("requests.__version__") is True


def test_nested_private_not_public():
    assert _is_public_api("pkg.sub._internal.func") is False


# ── _SurfaceVisitor ───────────────────────────────────────────────────────────

def parse_visitor(source: str, module="pkg") -> _SurfaceVisitor:
    tree = ast.parse(source)
    v = _SurfaceVisitor(module)
    v.visit(tree)
    return v


def test_visitor_extracts_function():
    v = parse_visitor("def get(url, verify=True): pass")
    assert "get" in v.functions
    assert "url" in v.functions["get"].params
    assert "verify" in v.functions["get"].params


def test_visitor_extracts_class():
    v = parse_visitor("class Session: pass")
    assert "Session" in v.classes


def test_visitor_extracts_method():
    v = parse_visitor("class Session:\n    def get(self, url): pass")
    assert "Session" in v.classes
    assert "Session.get" in v.functions


def test_visitor_skips_private():
    v = parse_visitor("def _internal(): pass\ndef public(): pass")
    assert "_internal" not in v.functions
    assert "public" in v.functions


def test_visitor_detects_async():
    v = parse_visitor("async def fetch(url): pass")
    assert v.functions["fetch"].is_async is True


def test_visitor_detects_varargs():
    v = parse_visitor("def func(*args): pass")
    assert v.functions["func"].has_varargs is True


def test_visitor_detects_kwargs():
    v = parse_visitor("def func(**kw): pass")
    assert v.functions["func"].has_kwargs is True


# ── diff_surfaces ─────────────────────────────────────────────────────────────

def make_surface(version: str, funcs: dict, modules=None, classes=None) -> APISurface:
    s = APISurface(version=version)
    s.modules = set(modules or ["pkg"])
    s.classes = set(classes or [])
    for name, params in funcs.items():
        s.functions[name] = FuncSig(name=name, params=params)
    return s


def test_diff_removed_function():
    old = make_surface("1.0", {"pkg.get": ["url", "verify"]})
    new = make_surface("2.0", {})
    changes = diff_surfaces(old, new)
    kinds = [c.kind for c in changes]
    assert "removed_function" in kinds


def test_diff_removed_param():
    old = make_surface("1.0", {"pkg.get": ["url", "verify"]})
    new = make_surface("2.0", {"pkg.get": ["url"]})
    changes = diff_surfaces(old, new)
    param_changes = [c for c in changes if c.kind == "removed_param"]
    assert len(param_changes) == 1
    assert param_changes[0].param == "verify"


def test_diff_no_change():
    old = make_surface("1.0", {"pkg.get": ["url"]})
    new = make_surface("2.0", {"pkg.get": ["url"]})
    changes = diff_surfaces(old, new)
    assert len(changes) == 0


def test_diff_kwargs_accept_forgives_param_removal():
    old = make_surface("1.0", {"pkg.get": ["url", "verify"]})
    s = make_surface("2.0", {"pkg.get": ["url"]})
    s.functions["pkg.get"].has_kwargs = True
    changes = diff_surfaces(old, s)
    param_changes = [c for c in changes if c.kind == "removed_param"]
    assert len(param_changes) == 0


def test_diff_removed_class():
    old = make_surface("1.0", {}, classes=["pkg.OldClass"])
    new = make_surface("2.0", {})
    changes = diff_surfaces(old, new)
    class_changes = [c for c in changes if c.kind == "removed_class"]
    assert len(class_changes) == 1


def test_diff_skips_private():
    old = make_surface("1.0", {"pkg._internal": ["x"]})
    new = make_surface("2.0", {})
    changes = diff_surfaces(old, new)
    assert len(changes) == 0
