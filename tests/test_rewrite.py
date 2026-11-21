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


# ── moved to another package ──────────────────────────────────────────────────

FLASK_OLD = {"flask": "from markupsafe import Markup, escape\nfrom flask.app import Flask", "flask.app": "class Flask: pass"}
FLASK_NEW = {"flask": "from flask.app import Flask", "flask.app": "class Flask: pass"}
FLASK_NOTE = ("- Importing ``escape`` and ``Markup`` from ``flask`` is deprecated. "
              "Import them directly from ``markupsafe`` instead.")


def migrate_ext(tmp_path, code, verify):
    path = tmp_path / "app.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    changes, new_surface = surfaces(FLASK_OLD, FLASK_NEW)
    plan = plan_migration(scan_file(str(path), "flask"), changes, new_surface, "flask",
                          parse_changelog_text(FLASK_NOTE, "2.3.0"), verify_external=verify)
    after = apply_edits(path.read_text(), [e for f in plan.fixes for e in f.edits])
    ast.parse(after)
    return plan, after


def test_import_moved_to_other_package(tmp_path):
    plan, after = migrate_ext(tmp_path, """
        from flask import Markup
        from flask import escape as esc
        x = Markup("<b>") + esc("y")
        """, verify=lambda dotted: dotted in {"markupsafe.Markup", "markupsafe.escape"})
    assert "from markupsafe import Markup\n" in after
    assert "from markupsafe import escape as esc\n" in after
    assert 'Markup("<b>") + esc("y")' in after  # call sites untouched, still valid
    assert not plan.manual


def test_other_package_missing_name_is_manual(tmp_path):
    plan, after = migrate_ext(tmp_path, "from flask import Markup\n", verify=lambda dotted: False)
    assert after == "from flask import Markup\n"
    assert plan.manual


def test_attribute_use_of_moved_name_gets_new_import(tmp_path):
    plan, after = migrate_ext(tmp_path, """
        \"\"\"Doc.\"\"\"
        import flask
        x = flask.Markup('a')
        y = flask.escape(x)
        """, verify=lambda d: True)
    assert after == '\n"""Doc."""\nimport flask\nimport markupsafe\nx = markupsafe.Markup(\'a\')\ny = markupsafe.escape(x)\n'
    assert not plan.manual


def test_import_added_once_and_not_if_present(tmp_path):
    plan, after = migrate_ext(tmp_path, "import markupsafe\nimport flask\nx = flask.Markup(1)\n",
                              verify=lambda d: True)
    assert after.count("import markupsafe") == 1
    assert "x = markupsafe.Markup(1)" in after


def test_import_appended_when_file_ends_with_imports(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("import flask", encoding="utf-8")  # no trailing newline, nothing after
    changes, new_surface = surfaces(FLASK_OLD, FLASK_NEW)
    # a usage on the import line itself doesn't need a new import; this checks the
    # end-of-file insertion path directly
    from depshift.rewrite import Fix, _Index, _add_needed_imports, MigrationPlan
    plan = MigrationPlan(fixes=[Fix(str(path), 1, "x", [], needs_import="markupsafe")])
    _add_needed_imports(plan, lambda f: _Index(path.read_text()))
    assert apply_edits("import flask", plan.fixes[0].edits) == "import flask\nimport markupsafe\n"


def test_non_plain_expression_stays_manual(tmp_path):
    plan, after = migrate_ext(tmp_path, "import flask\nx = get().Markup\ny = flask.Markup\n",
                              verify=lambda d: True)
    assert "y = markupsafe.Markup" in after


def test_stdlib_verified_by_reading_source_not_importing():
    from depshift.rewrite import exists_elsewhere
    assert exists_elsewhere("urllib.parse.quote")
    assert not exists_elsewhere("urllib.parse.no_such_name")
    assert exists_elsewhere("antigravity.geohash")  # parsed, never imported


def test_method_called_on_moved_name(tmp_path):
    plan, after = migrate_ext(tmp_path, "import flask\nx = flask.Markup('<b>{}</b>').format(1)\n",
                              verify=lambda d: True)
    assert "x = markupsafe.Markup('<b>{}</b>').format(1)" in after
    assert len(plan.fixes) == 1 and not plan.manual
