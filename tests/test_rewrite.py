"""Planned rewrites: only safe, verified edits; everything else is manual."""

import ast
import textwrap

from depshift.apisurface import build_surface, diff_surfaces
from depshift.changelog import parse_changelog_text
from depshift.rewrite import Edit, apply_edits, plan_migration
from depshift.scanner import scan_file


def surfaces(old: dict, new: dict):
    o = build_surface("1.0", {k: [v] for k, v in old.items()})
    n = build_surface("2.0", {k: [v] for k, v in new.items()})
    return diff_surfaces(o, n), n


def migrate(tmp_path, code, old, new, changelog="", pkg="pkg"):
    path = tmp_path / "app.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    changes, new_surface = surfaces(old, new)
    usages = scan_file(str(path), pkg)
    entries = parse_changelog_text(changelog, "2.0") if changelog else []
    plan = plan_migration(usages, changes, new_surface, pkg, entries)
    src = path.read_text(encoding="utf-8")
    after = apply_edits(src, [e for f in plan.fixes for e in f.edits])
    ast.parse(after)  # every rewrite must still be valid Python
    return plan, after


# ── edit engine ───────────────────────────────────────────────────────────────

def test_apply_edits_handles_utf8_columns_and_overlaps():
    src = "x = 'é'; f(a)\n"
    col = len("x = 'é'; f(".encode("utf-8"))  # ast columns are UTF-8 byte offsets
    out = apply_edits(src, [Edit((1, col), (1, col), "b=")])
    assert out == "x = 'é'; f(b=a)\n"
    # two edits replacing the same character: the second is skipped, not merged
    out = apply_edits(src, [Edit((1, col), (1, col + 1), "y"), Edit((1, col), (1, col + 1), "zz")])
    assert out == "x = 'é'; f(y)\n"


# ── keyword-only ──────────────────────────────────────────────────────────────

def test_keyword_only_call_rewritten(tmp_path):
    plan, after = migrate(tmp_path, """
        import pkg
        pkg.f(1, 2, 3)
        pkg.f(1, b=2)
        """, {"pkg": "def f(a, b=0, c=0): pass"}, {"pkg": "def f(a, *, b=0, c=0): pass"})
    assert "pkg.f(1, b=2, c=3)" in after
    assert "pkg.f(1, b=2)\n" in after  # already fine, untouched
    assert not plan.manual


def test_keyword_only_with_starargs_is_manual(tmp_path):
    plan, after = migrate(tmp_path, """
        import pkg
        args = (1, 2)
        pkg.f(*args)
        pkg.f(1, *args)
        """, {"pkg": "def f(a, b=0): pass"}, {"pkg": "def f(a, *, b=0): pass"})
    assert "pkg.f(1, *args)" in after
    assert len(plan.manual) == 1


# ── renamed keyword ───────────────────────────────────────────────────────────

def test_renamed_kwarg_from_changelog(tmp_path):
    old = {"pkg": "class Retry:\n    def __init__(self, total=0, method_whitelist=None): pass"}
    new = {"pkg": "class Retry:\n    def __init__(self, total=0, allowed_methods=None): pass"}
    plan, after = migrate(tmp_path, """
        from pkg import Retry
        r = Retry(total=3, method_whitelist=["GET"])
        """, old, new,
        changelog="- Deprecated ``Retry`` option ``method_whitelist`` in favor of ``allowed_methods``.")
    assert 'Retry(total=3, allowed_methods=["GET"])' in after
    assert not plan.manual


def test_renamed_kwarg_not_applied_when_replacement_missing_in_new_api(tmp_path):
    old = {"pkg": "class Retry:\n    def __init__(self, method_whitelist=None): pass"}
    new = {"pkg": "class Retry:\n    def __init__(self): pass"}
    plan, after = migrate(tmp_path, """
        from pkg import Retry
        r = Retry(method_whitelist=["GET"])
        """, old, new,
        changelog="- Deprecated ``Retry`` option ``method_whitelist`` in favor of ``allowed_methods``.")
    assert "method_whitelist" in after  # replacement doesn't exist: no blind rename
    assert len(plan.manual) == 1


# ── moved / renamed names ─────────────────────────────────────────────────────

def test_moved_function_import_rewritten(tmp_path):
    old = {"pkg": "", "pkg.urls": "def url_quote(s): pass", "pkg.newurls": ""}
    new = {"pkg": "", "pkg.urls": "", "pkg.newurls": "def url_quote(s): pass"}
    plan, after = migrate(tmp_path, """
        from pkg.urls import url_quote
        x = url_quote("a b")
        """, old, new)
    assert "from pkg.newurls import url_quote\n" in after
    assert 'url_quote("a b")' in after
    assert not plan.manual


def test_renamed_attribute_from_changelog(tmp_path):
    old = {"numpy": "float_ = float\nfloat64 = float"}
    new = {"numpy": "float64 = float"}
    plan, after = migrate(tmp_path, """
        import numpy as np
        dtype = np.float_
        """, old, new, changelog="- ``np.float_`` has been removed, use ``np.float64`` instead.", pkg="numpy")
    assert "dtype = np.float64" in after
    assert not plan.manual


def test_renamed_import_keeps_local_name(tmp_path):
    old = {"numpy": "float_ = float\nfloat64 = float"}
    new = {"numpy": "float64 = float"}
    plan, after = migrate(tmp_path, """
        from numpy import float_
        x = float_(1)
        """, old, new, changelog="- ``np.float_`` has been removed, use ``np.float64`` instead.", pkg="numpy")
    assert "from numpy import float64 as float_" in after
    assert "x = float_(1)" in after  # call site still valid through the alias
    assert not plan.manual


def test_unverified_replacement_is_manual(tmp_path):
    plan, after = migrate(tmp_path, """
        import numpy as np
        dtype = np.float_
        """, {"numpy": "float_ = float"}, {"numpy": ""},
        changelog="- ``np.float_`` has been removed, use ``np.float64`` instead.", pkg="numpy")
    assert "np.float_" in after  # float64 isn't in the new API we read: don't guess
    assert len(plan.manual) == 1


def test_multi_name_import_is_manual(tmp_path):
    old = {"pkg": "", "pkg.a": "def x(): pass\ndef y(): pass", "pkg.b": ""}
    new = {"pkg": "", "pkg.a": "def y(): pass", "pkg.b": "def x(): pass"}
    plan, after = migrate(tmp_path, "from pkg.a import x, y\n", old, new)
    assert after == "from pkg.a import x, y\n"
    assert len(plan.manual) == 1


# ── things that can't be automated ────────────────────────────────────────────

def test_new_required_argument_is_manual(tmp_path):
    plan, after = migrate(tmp_path, """
        import pkg
        pkg.load(stream)
        """, {"pkg": "def load(stream, Loader=None): pass"}, {"pkg": "def load(stream, Loader): pass"})
    assert after.strip().endswith("pkg.load(stream)")
    assert "Loader" in plan.manual[0].reason


def test_removed_without_replacement_is_manual(tmp_path):
    plan, after = migrate(tmp_path, """
        import pkg
        pkg.gone()
        """, {"pkg": "def gone(): pass\ndef stays(): pass"}, {"pkg": "def stays(): pass"})
    assert "pkg.gone()" in after
    assert plan.manual


def test_same_name_in_other_package_is_not_a_new_home(tmp_path):
    old = {"numpy": "from numpy.core.fromnumeric import round_", "numpy.core.fromnumeric": "def round_(a): pass",
           "numpy.ma.core": "def round_(a): pass", "numpy.core": ""}
    new = {"numpy": "def round(a): pass", "numpy.core.fromnumeric": "", "numpy.ma.core": "def round_(a): pass",
           "numpy.core": ""}
    changes, new_surface = surfaces(old, new)
    assert all(c.replacement != "numpy.ma.core.round_" for c in changes)
    plan, after = migrate(tmp_path, "import numpy as np\nr = np.round_(1)\n", old, new,
                          changelog="- ``np.round_`` has been removed. Use ``np.round`` instead.", pkg="numpy")
    assert "np.round(1)" in after and not plan.manual
