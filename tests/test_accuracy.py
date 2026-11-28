"""Accuracy tests: changelog section awareness, honest version handling,
deeper API diffs, and instance tracking in the scanner."""

import os
import tempfile
import textwrap

import pytest

from depshift import project
from depshift.apisurface import build_surface, diff_surfaces
from depshift.changelog import (
    ChangeReport,
    collect_changes,
    parse_changelog_text,
    parse_sectioned_changelog,
    version_from_tag,
)
from depshift.precise import match_precise
from depshift.scanner import Usage, scan_file

# ── changelog: false positives ────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "- Fixed crash when removing an item from the cache",
    "- Fix dropdown rendering",
    "- Handle outbreak of warnings in logs",
    "- Fixed a bug where `Session.close` was not called",
    "- Improve breakpoint handling",
])
def test_benign_lines_are_not_breaking(line):
    assert [e for e in parse_changelog_text(line, "2.0") if e.kind == "removed"] == []


def test_strong_wording_survives_fix_prefix():
    entries = parse_changelog_text("- Fixed: `foo()` no longer accepts `bar`", "2.0")
    assert entries and entries[0].kind == "removed"


def test_keyword_under_bugfix_heading_is_dropped():
    text = "### Bug fixes\n- Removed a stray print in `pkg.util`\n"
    assert parse_changelog_text(text, "2.0") == []


# ── changelog: sections ───────────────────────────────────────────────────────

def test_breaking_section_without_keywords():
    text = textwrap.dedent("""\
        ## Breaking changes
        - `pkg.connect` takes a `Config` object
        ## Features
        - Added `pkg.retry`
        """)
    entries = parse_changelog_text(text, "3.0")
    assert len(entries) == 1
    assert entries[0].kind == "removed"
    assert entries[0].api == "pkg.connect"
    # in a breaking section, but the item changes behaviour rather than
    # removing pkg.connect: reported, at warning strength
    assert entries[0].confidence == "low"


def test_subject_vs_mention():
    removed = parse_changelog_text("- ``pkg.old`` has been removed.", "2.0")
    mention = parse_changelog_text("- Removed a crash when passing None to ``pkg.DataFrame``.", "2.0")
    behaviour = parse_changelog_text(
        "## Backwards incompatible API changes\n- Passing ``dtype`` to ``pkg.DataFrame`` now raises.", "2.0")
    assert removed[0].confidence != "low"
    assert all(e.confidence == "low" for e in mention + behaviour)


def test_alias_in_changelog_matches():
    from depshift.analyzer import analyze
    entries = parse_changelog_text("- ``np.NaN`` has been removed, use ``np.nan``.", "2.0.0")
    u = Usage(file="a.py", line=1, code="", attr_chain="numpy.NaN", usage_type="attribute_access")
    ok = Usage(file="a.py", line=2, code="", attr_chain="numpy.nan", usage_type="attribute_access")
    risks, _ = analyze([u, ok], entries, "numpy")
    assert [(r.usage.line, r.severity) for r in risks] == [(1, "breaking")]


def test_bare_import_gets_no_warning_spam():
    from depshift.analyzer import analyze
    entries = parse_changelog_text("- ``numpy.fft.helper`` was renamed to ``numpy.fft._helper``.", "2.0.0")
    u = Usage(file="a.py", line=1, code="import numpy", attr_chain="numpy", usage_type="import")
    assert analyze([u], entries, "numpy")[0] == []


def test_rst_section_headings():
    text = textwrap.dedent("""\
        Deprecations
        ------------
        * ``pkg.old_api`` is superseded by ``pkg.new_api``.
        """)
    entries = parse_changelog_text(text, "3.0")
    assert len(entries) == 1
    assert entries[0].kind == "deprecated"
    assert entries[0].api == "pkg.old_api"


def test_rename_in_breaking_section_is_breaking():
    text = "## Backwards incompatible\n- Renamed `pkg.a` to `pkg.b`\n"
    entries = parse_changelog_text(text, "3.0")
    assert entries[0].kind == "changed"
    assert entries[0].replacement == "pkg.b"


def test_replacement_extracted():
    entries = parse_changelog_text("- Deprecated `pkg.old` - use `pkg.new` instead", "2.0")
    assert entries[0].api == "pkg.old"
    assert entries[0].replacement == "pkg.new"


def test_sphinx_role_api():
    entries = parse_changelog_text("- Removed :func:`pkg.helpers.thing`", "2.0")
    assert entries[0].api == "pkg.helpers.thing"


def test_no_api_means_low_confidence():
    entries = parse_changelog_text("- Removed support for old configs", "2.0")
    assert entries[0].confidence == "low"


def test_sectioned_changelog_still_versions():
    text = "## 2.0.0\n### Removed\n- `pkg.a`\n## 1.0.0\n- Removed `pkg.b`\n"
    entries = parse_sectioned_changelog(text, "pkg")
    assert {(e.version, e.api) for e in entries} == {("2.0.0", "pkg.a"), ("1.0.0", "pkg.b")}


# ── version tags ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag,expected", [
    ("v2.0.1", "2.0.1"),
    ("2.0", "2.0"),
    ("flask-3.0.1", "3.0.1"),
    ("release-2.0", "2.0"),
    ("Version 4.0", "4.0"),
    ("v1.2.0rc1", "1.2.0rc1"),
    ("nightly", None),
    ("", None),
])
def test_version_from_tag(tag, expected):
    assert version_from_tag(tag) == expected


def test_collect_changes_reports_failures(monkeypatch):
    import depshift.changelog as cl

    def boom(*a, **k):
        raise cl.httpx.ConnectError("offline")

    monkeypatch.setattr(cl, "get_pypi_info", boom)
    report = collect_changes("pkg", "1.0", "2.0")
    assert isinstance(report, ChangeReport)
    assert not report.checked
    assert report.sources_failed


def test_collect_changes_drops_unversioned_and_out_of_range(monkeypatch):
    import depshift.changelog as cl
    monkeypatch.setattr(cl, "get_pypi_info", lambda p: {"info": {"project_urls": {
        "Source": "https://github.com/o/r"}}})
    monkeypatch.setattr(cl, "fetch_github_releases", lambda o, r, token=None: [
        {"tag_name": "v1.5.0", "body": "- Removed `pkg.a`"},
        {"tag_name": "v3.0.0", "body": "- Removed `pkg.too_new`"},
        {"tag_name": "nightly", "body": "- Removed `pkg.noise`"},
    ])
    monkeypatch.setattr(cl, "get_pypi_version_info", lambda p, v: {"info": {"description": ""}})
    report = collect_changes("pkg", "1.0", "2.0")
    assert report.checked
    assert [e.api for e in report.entries] == ["pkg.a"]


def test_rate_limit_is_an_error_not_empty(monkeypatch):
    import depshift.changelog as cl
    from depshift import cache
    monkeypatch.setattr(cache, "_disabled", True)

    class R:
        status_code = 403
        request = cl.httpx.Request("GET", "https://api.github.com")

        def raise_for_status(self):
            raise cl.httpx.HTTPStatusError("rate limited", request=self.request,
                                           response=cl.httpx.Response(403, request=self.request))

    monkeypatch.setattr(cl, "http_get", lambda *a, **k: R())
    with pytest.raises(cl.httpx.HTTPStatusError):
        cl.fetch_github_releases("o", "r")


# ── CLI version handling ──────────────────────────────────────────────────────

def test_latest_allowed_respects_constraint(monkeypatch):
    monkeypatch.setattr(project, "get_pypi_info", lambda p: {"releases": {
        "1.0": [{}], "2.5": [{}], "3.0": [{}], "3.1rc1": [{}], "2.9": [{"yanked": True}],
    }})
    assert project.latest_allowed_version("pkg", ">=2,<3") == "2.5"
    assert project.latest_allowed_version("pkg") == "3.0"


def test_specifier_and_lower_bound():
    assert project.specifier_of("flask>=2.0,<3") in (">=2.0,<3", "<3,>=2.0")
    assert project.lower_bound(">=2.0,<3") == "2.0"
    assert project.lower_bound("<3") is None


def test_unknown_current_is_not_checked(monkeypatch, tmp_path):
    from depshift.config import load_config
    monkeypatch.setattr(project, "get_installed_version", lambda p: None)
    r = project.run_check("somepkg", "2.0", str(tmp_path), None, load_config(str(tmp_path)))
    assert r["unchecked"] is True
    assert r["current_version"] is None
    assert r["warnings"]


def test_reports_handle_unchecked():
    from depshift.report import render_html, render_markdown
    r = {"package": "x", "current_version": None, "target_version": "2.0", "risks": [],
         "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
         "unchecked": True, "warnings": ["current version unknown"]}
    assert "NOT CHECKED" in render_markdown([r])
    assert "NOT CHECKED" in render_html([r])


# ── deep diff ─────────────────────────────────────────────────────────────────

def diff(old: dict, new: dict):
    return diff_surfaces(build_surface("1.0", {k: [v] for k, v in old.items()}),
                         build_surface("2.0", {k: [v] for k, v in new.items()}))


def kinds(changes):
    return {(c.kind, c.api, c.param) for c in changes}


def test_added_required_param():
    ch = diff({"pkg": "def f(a): pass"}, {"pkg": "def f(a, b): pass"})
    assert ("added_required_param", "pkg.f", "b") in kinds(ch)


def test_added_optional_param_is_fine():
    assert diff({"pkg": "def f(a): pass"}, {"pkg": "def f(a, b=1, *, c=2): pass"}) == []


def test_added_required_kwonly_param():
    ch = diff({"pkg": "def f(a): pass"}, {"pkg": "def f(a, *, mode): pass"})
    assert ("added_required_param", "pkg.f", "mode") in kinds(ch)


def test_method_self_is_not_required_param():
    old = "class C:\n    def m(self): pass\n"
    new = "class C:\n    def m(self, x=1): pass\n"
    assert diff({"pkg": old}, {"pkg": new}) == []


def test_became_keyword_only():
    ch = diff({"pkg": "def f(a, b=1): pass"}, {"pkg": "def f(a, *, b=1): pass"})
    (c,) = [c for c in ch if c.kind == "became_keyword_only"]
    assert c.param == "b" and c.position == 1


def test_moved_but_reexported_is_not_removed():
    old = {"pkg": "from pkg.api import get", "pkg.api": "def get(url): pass",
           "pkg.sessions": ""}
    new = {"pkg": "from pkg.sessions import get", "pkg.api": "",
           "pkg.sessions": "def get(url): pass"}
    ch = diff(old, new)
    (c,) = [c for c in ch if c.kind == "removed_function"]
    assert c.api == "pkg.api.get"
    assert c.replacement == "pkg.sessions.get"
    assert "pkg.get" in c.still_at


def test_reexport_in_all_counts():
    old = {"pkg": "def thing(): pass"}
    new = {"pkg": "__all__ = ['thing']\nfrom pkg._impl import *", "pkg._impl": "def thing(): pass"}
    assert [c for c in diff(old, new) if c.api == "pkg.thing"] == []


def test_method_moved_to_base_class_is_not_removed():
    old = "class Base: pass\nclass C(Base):\n    def m(self): pass\n"
    new = "class Base:\n    def m(self): pass\nclass C(Base): pass\n"
    assert diff({"pkg": old}, {"pkg": new}) == []


def test_removed_constant():
    ch = diff({"pkg": "TIMEOUT = 5\nVERSION = '1'"}, {"pkg": "VERSION = '2'"})
    assert ("removed_attribute", "pkg.TIMEOUT", None) in kinds(ch)


def test_private_constant_ignored():
    assert diff({"pkg": "_x = 1"}, {"pkg": ""}) == []


# ── precise matching with new kinds ───────────────────────────────────────────

def U(chain, kwargs=None, pos=0, usage_type="function_call"):
    return Usage(file="a.py", line=1, code="", attr_chain=chain, usage_type=usage_type,
                 call_args=[f"pos:{pos}"], kwargs=kwargs or [])


def test_precise_reexported_usage_not_flagged():
    old = {"pkg": "from pkg.api import get", "pkg.api": "def get(url): pass", "pkg.s": ""}
    new = {"pkg": "from pkg.s import get", "pkg.api": "", "pkg.s": "def get(url): pass"}
    ch = diff(old, new)
    assert match_precise([U("pkg.get", pos=1)], ch, "pkg") == []
    assert len(match_precise([U("pkg.api.get", pos=1)], ch, "pkg")) == 1


def test_precise_new_required_param():
    ch = diff({"pkg": "def f(a): pass"}, {"pkg": "def f(a, b): pass"})
    assert len(match_precise([U("pkg.f", pos=1)], ch, "pkg")) == 1
    assert match_precise([U("pkg.f", kwargs=["b"], pos=1)], ch, "pkg") == []


def test_precise_keyword_only_positional_call():
    ch = diff({"pkg": "def f(a, b=1): pass"}, {"pkg": "def f(a, *, b=1): pass"})
    assert len(match_precise([U("pkg.f", pos=2)], ch, "pkg")) == 1
    assert match_precise([U("pkg.f", pos=1, kwargs=["b"])], ch, "pkg") == []


def test_precise_constructor_change_matches_class_call():
    old = "class Client:\n    def __init__(self, url, verify=True): pass\n"
    new = "class Client:\n    def __init__(self, url): pass\n"
    ch = diff({"pkg": old}, {"pkg": new})
    risks = match_precise([U("pkg.Client", kwargs=["verify"], pos=1)], ch, "pkg")
    assert len(risks) == 1


def test_precise_removed_constant():
    ch = diff({"pkg": "TIMEOUT = 5"}, {"pkg": ""})
    risks = match_precise([U("pkg.TIMEOUT", usage_type="attribute_access")], ch, "pkg")
    assert len(risks) == 1


# ── scanner: instance tracking ────────────────────────────────────────────────

def scan(code, pkg="requests"):
    f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    f.write(textwrap.dedent(code))
    f.close()
    try:
        return scan_file(f.name, pkg)
    finally:
        os.unlink(f.name)


def calls(usages):
    return {(u.attr_chain, tuple(u.kwargs)) for u in usages if u.usage_type == "function_call"}


def test_instance_method_call_resolved():
    us = scan("""
        import requests
        s = requests.Session()
        s.get("u", verify=False)
    """)
    assert ("requests.Session.get", ("verify",)) in calls(us)


def test_from_import_instance():
    us = scan("""
        from requests import Session
        s = Session()
        s.post("u")
    """)
    assert ("requests.Session.post", ()) in calls(us)


def test_with_statement_instance():
    us = scan("""
        import requests
        with requests.Session() as s:
            s.get("u")
    """)
    assert ("requests.Session.get", ()) in calls(us)


def test_annotated_parameter_instance():
    us = scan("""
        import requests
        from typing import Optional
        def fetch(s: Optional[requests.Session], url):
            return s.get(url, timeout=3)
    """)
    assert ("requests.Session.get", ("timeout",)) in calls(us)


def test_reassignment_clears_instance():
    us = scan("""
        import requests
        s = requests.Session()
        s = "not a session"
        s.upper()
    """)
    assert not any(c[0].endswith(".upper") for c in calls(us))


def test_function_params_do_not_leak():
    us = scan("""
        import requests
        def f(s: requests.Session):
            pass
        s.get("x")
    """)
    assert not any(c[0] == "requests.Session.get" for c in calls(us))


def test_method_change_does_not_match_module_function():
    old = {"pkg": "def command(): pass", "pkg.core": "class Context:\n    def command(self): pass\n"}
    new = {"pkg": "def command(): pass", "pkg.core": "class Context: pass\n"}
    ch = diff(old, new)
    assert match_precise([U("pkg.command")], ch, "pkg") == []
    assert len(match_precise([U("pkg.core.Context.command")], ch, "pkg")) == 1


def test_method_param_change_matches_instance_call_only():
    old = {"pkg": "class Session:\n    def get(self, url, verify=True): pass\ndef get(url, verify=True): pass"}
    new = {"pkg": "class Session:\n    def get(self, url): pass\ndef get(url, verify=True): pass"}
    ch = diff(old, new)
    assert len(match_precise([U("pkg.Session.get", kwargs=["verify"], pos=1)], ch, "pkg")) == 1
    assert match_precise([U("pkg.get", kwargs=["verify"], pos=1)], ch, "pkg") == []


def test_exact_pin_does_not_cap_target_and_no_downgrades():
    assert project.is_exact_pin("==2.25.0")
    assert not project.is_exact_pin(">=2,<3")
    assert project.not_newer("7.1.2", "8.5.1")
    assert not project.satisfies("8.5.1", ">=7.0,<8")


def test_removed_reexport_detected():
    old = {"pkg": "from markupsafe import Markup, escape\nfrom pkg.app import Flask", "pkg.app": "class Flask: pass"}
    new = {"pkg": "from pkg.app import Flask", "pkg.app": "class Flask: pass"}
    ch = diff(old, new)
    assert {c.api for c in ch if c.kind == "removed_export"} == {"pkg.Markup", "pkg.escape"}
    risks = match_precise([U("pkg.Markup", usage_type="import"), U("pkg.Flask", usage_type="import")], ch, "pkg")
    assert [r.usage.attr_chain for r in risks] == ["pkg.Markup"]


def test_removed_internal_reexport_only_matches_exact_path():
    old = {"pkg": "from pkg.app import Flask", "pkg.app": "class Flask: pass",
           "pkg.bp": "from pkg.app import Flask"}
    new = {"pkg": "from pkg.app import Flask", "pkg.app": "class Flask: pass", "pkg.bp": ""}
    ch = diff(old, new)
    assert match_precise([U("pkg.Flask")], ch, "pkg") == []
    assert len(match_precise([U("pkg.bp.Flask")], ch, "pkg")) == 1


# ── changelog: items, multiple APIs, deprecation sweeps ───────────────────────

def test_wrapped_bullet_is_one_item():
    text = ("- The ``JSONIFY_PRETTYPRINT_REGULAR`` config keys and the\n"
            "  corresponding ``json.JSONEncoder`` classes are removed.\n")
    entries = parse_changelog_text(text, "2.3.0")
    assert {e.api for e in entries} == {"json.JSONEncoder"}
    assert all(e.kind == "removed" for e in entries)


def test_multiple_apis_in_one_item():
    text = "- Importing ``escape`` and ``Markup`` from ``flask`` is deprecated.\n"
    apis = {e.api for e in parse_changelog_text(text, "2.3.0")}
    assert {"escape", "Markup"} <= apis


def test_package_name_change_does_not_flag_everything():
    from depshift.analyzer import analyze
    entries = parse_changelog_text("- Importing ``escape`` from ``flask`` is deprecated.", "2.3")
    usages = [Usage(file="a.py", line=1, code="", attr_chain="flask.Flask", usage_type="import")]
    risks, _ = analyze(usages, entries, "flask")
    assert risks == []


def test_deprecation_sweep_promotes_to_removed(monkeypatch):
    import depshift.changelog as cl
    changelog = textwrap.dedent("""\
        Version 3.0.0
        -------------
        - Remove previously deprecated code.

        Version 2.3.0
        -------------
        - Importing ``escape`` from ``flask`` is deprecated.

        Version 2.2.0
        -------------
        - ``old_thing`` is deprecated.
        """)
    monkeypatch.setattr(cl, "get_pypi_info", lambda p: {"info": {"project_urls": {
        "Source": "https://github.com/o/r"}}})
    monkeypatch.setattr(cl, "fetch_github_releases", lambda o, r, token=None: [])
    monkeypatch.setattr(cl, "fetch_raw_changelog", lambda o, r, token=None: changelog)
    monkeypatch.setattr(cl, "get_pypi_version_info", lambda p, v: {"info": {"description": ""}})
    report = collect_changes("flask", "2.2.5", "3.0.0")
    removed = {e.api for e in report.entries if e.kind == "removed"}
    # escape: deprecated inside the range; old_thing: deprecated before it, swept in 3.0
    assert {"escape", "old_thing"} <= removed


def test_param_owner_phrase():
    text = "- Removed deprecated ``Retry`` options ``method_whitelist``, ``DEFAULT_REDIRECT_HEADERS_BLACKLIST``."
    apis = {e.api for e in parse_changelog_text(text, "2.0.0")}
    assert "Retry" not in apis
    assert "Retry.method_whitelist" in apis


def test_changelog_kwarg_match():
    from depshift.analyzer import analyze
    entries = parse_changelog_text("- Removed ``Retry`` option ``method_whitelist``.", "2.0.0")
    bad = Usage(file="a.py", line=1, code="", attr_chain="urllib3.util.retry.Retry",
                usage_type="function_call", kwargs=["total", "method_whitelist"])
    good = Usage(file="a.py", line=2, code="", attr_chain="urllib3.util.retry.Retry",
                 usage_type="function_call", kwargs=["total", "allowed_methods"])
    risks, _ = analyze([bad, good], entries, "urllib3")
    assert [r.usage.line for r in risks] == [1]
    assert risks[0].severity == "breaking"


def test_subheading_inherits_breaking_section_and_rst_table():
    text = textwrap.dedent("""\
        Backwards incompatible changes
        ==============================

        Constants
        ^^^^^^^^^

        =====================  ============================
        Removed                Use instead
        =====================  ============================
        ``Image.ANTIALIAS``    ``Image.LANCZOS``
        ``Image.LINEAR``       ``Image.BILINEAR``
        =====================  ============================

        Other changes
        =============

        Added things
        ^^^^^^^^^^^^
        - ``Image.foo`` was improved.
        """)
    entries = parse_changelog_text(text, "10.0.0")
    removed = {e.api: e for e in entries if e.kind in ("removed", "changed")}
    assert set(removed) == {"Image.ANTIALIAS", "Image.LINEAR"}
    assert removed["Image.ANTIALIAS"].replacement == "Image.LANCZOS"
    assert removed["Image.ANTIALIAS"].confidence != "low"
    assert "Removed" not in removed  # header row skipped


def test_no_longer_infers_is_not_removal():
    text = "## Backwards incompatible\n- ``pkg.DataFrame`` aggregations no longer infer dtypes."
    assert all(e.confidence == "low" for e in parse_changelog_text(text, "2.0"))


def test_bare_name_usages_recorded_once():
    us = scan("""
        from pydantic import BaseSettings, BaseModel
        class S(BaseSettings):
            pass
        m = BaseModel()
        x = BaseModel.construct
    """, pkg="pydantic")
    lines = sorted((u.line, u.attr_chain, u.usage_type) for u in us if u.usage_type != "import")
    assert lines == [
        (3, "pydantic.BaseSettings", "attribute_access"),
        (5, "pydantic.BaseModel", "function_call"),
        (6, "pydantic.BaseModel.construct", "attribute_access"),
    ]


def test_star_import_reexports():
    old = {"pkg": "from ._q import *", "pkg._q": "__all__ = ['simps', 'simpson']\ndef simps(y): pass\ndef simpson(y): pass"}
    new = {"pkg": "from ._q import *", "pkg._q": "__all__ = ['simpson']\ndef simpson(y): pass"}
    ch = diff(old, new)
    assert match_precise([U("pkg.simps", pos=1)], ch, "pkg")
    assert match_precise([U("pkg.simpson", pos=1)], ch, "pkg") == []


def test_param_became_required():
    ch = diff({"pkg": "def load(stream, Loader=None): pass"}, {"pkg": "def load(stream, Loader): pass"})
    assert match_precise([U("pkg.load", pos=1)], ch, "pkg")
    assert match_precise([U("pkg.load", pos=1, kwargs=["Loader"])], ch, "pkg") == []
    assert match_precise([U("pkg.load", pos=2)], ch, "pkg") == []


def test_param_of_phrase_and_multiple_replacements():
    e = parse_changelog_text("- The ``even`` argument of `scipy.integrate.simpson` has been removed.", "1.14")
    assert [x.api for x in e] == ["scipy.integrate.simpson.even"]
    e = parse_changelog_text(
        "- ``a.trapz`` and ``a.simps`` are deprecated in favor of ``a.trapezoid`` and ``a.simpson``.", "1.12")
    assert {x.api for x in e} == {"a.trapz", "a.simps"}


def test_nested_bullets_belong_to_parent():
    text = textwrap.dedent("""\
        Deprecated features
        -------------------
        - Positional use of some arguments is deprecated in these functions,
          to be changed later. Affected functions are:

          - ``integrate.simpson``
          - ``special.comb``
        - ``scipy.misc`` is deprecated.
        """)
    entries = parse_changelog_text(text, "1.12.0")
    simpson = [e for e in entries if e.api == "integrate.simpson"]
    assert simpson and all(e.confidence == "low" for e in simpson)
    assert any(e.api == "scipy.misc" and e.confidence != "low" for e in entries)


def test_keyword_from_owner_phrase():
    e = parse_changelog_text(
        "- Remove keyword ``mangle_dupe_cols`` from :func:`read_csv` and :func:`read_table`", "2.0")
    assert {x.api for x in e} == {"read_csv.mangle_dupe_cols", "read_table.mangle_dupe_cols"}


# ── scanner inference ─────────────────────────────────────────────────────────

def test_helper_return_and_self_attribute_inference():
    us = scan("""
        from sqlalchemy import create_engine
        import pandas as pd

        def get_engine():
            return create_engine("sqlite://")

        def build():
            df = pd.DataFrame()
            return df

        def load() -> "pd.DataFrame":
            return something()

        class Repo:
            def __init__(self):
                self.engine = get_engine()

            def run(self):
                return self.engine.execute("x")

        e = get_engine()
        e.execute("y")
        get_engine().execute("z")
        build().append({})
        load().append({})
        pd.DataFrame().append({})
    """, pkg="sqlalchemy") + scan("""
        import pandas as pd
        def build():
            df = pd.DataFrame()
            return df
        def load() -> "pd.DataFrame":
            return something()
        build().append({})
        load().append({})
        pd.DataFrame().append({})
    """, pkg="pandas")
    got = calls(us)
    assert ("sqlalchemy.create_engine.execute", ()) in got
    assert sum(1 for c in got if c[0] == "sqlalchemy.create_engine.execute") == 1  # deduped set
    lines = sorted(u.line for u in us if u.attr_chain == "sqlalchemy.create_engine.execute")
    assert len(lines) == 3  # self.engine.execute, e.execute, get_engine().execute
    assert {u.attr_chain for u in us if u.attr_chain.endswith(".append")} == {"pandas.DataFrame.append"}
    assert len([u for u in us if u.attr_chain == "pandas.DataFrame.append"]) == 3


def test_unknown_self_attribute_not_resolved():
    us = scan("""
        import requests
        class C:
            def __init__(self):
                self.name = "x"
            def go(self):
                return self.name.upper()
    """)
    assert not any("upper" in u.attr_chain for u in us)


# ── replacements: pairing, aliases, call-style mentions, generic sub-headings ─

def test_respectively_pairs_by_order():
    e = parse_changelog_text(
        "- ``a.trapz``, ``a.cumtrapz``, and ``a.simps`` have been deprecated in favour of "
        "``a.trapezoid``, ``a.cumulative_trapezoid``, and ``a.simpson`` respectively", "1.12")
    assert {x.api: x.replacement for x in e} == {
        "a.trapz": "a.trapezoid", "a.cumtrapz": "a.cumulative_trapezoid", "a.simps": "a.simpson"}


def test_one_replacement_for_many_is_ambiguous():
    e = parse_changelog_text("- ``a.old`` and ``a.older`` are deprecated, use ``a.new`` instead.", "1.0")
    assert all(x.replacement is None for x in e)


def test_aliases_of_is_a_replacement_and_not_deprecated():
    e = parse_changelog_text(
        "- The ``smart_text()`` and ``force_text()`` aliases (since Django 2.0) of "
        "``smart_str()`` and ``force_str()`` are deprecated.", "3.0")
    assert {x.api: x.replacement for x in e} == {"smart_text": "smart_str", "force_text": "force_str"}


def test_call_style_kwarg_mention():
    e = parse_changelog_text(
        "- Deprecated ``Retry`` options ``Retry(method_whitelist=...)`` in favor of "
        "``Retry(allowed_methods=...)``", "1.26.0")
    assert [(x.api, x.replacement) for x in e] == [("Retry.method_whitelist", "Retry.allowed_methods")]


def test_in_favor_of_is_not_keyword_in():
    e = parse_changelog_text("- Deprecated ``Retry`` option ``method_whitelist`` in favor of ``allowed_methods``.", "1.0")
    assert [(x.api, x.replacement) for x in e] == [("Retry.method_whitelist", "allowed_methods")]


def test_generic_subheading_inherits_deprecation_section():
    text = textwrap.dedent("""\
        Features deprecated in 3.0
        ==========================

        Miscellaneous
        -------------

        * ``pkg.ugettext()`` is deprecated in favor of ``pkg.gettext()``.
        """)
    e = parse_changelog_text(text, "3.0")
    assert [(x.kind, x.api, x.replacement) for x in e] == [("deprecated", "pkg.ugettext", "pkg.gettext")]


def test_collect_changes_history_keeps_earlier_deprecations(monkeypatch):
    import depshift.changelog as cl
    changelog = "## 2.0.0\n- Removed ``pkg.old``.\n\n## 1.1.0\n- ``pkg.old`` is deprecated, use ``pkg.new``.\n"
    monkeypatch.setattr(cl, "get_pypi_info", lambda p: {"info": {"project_urls": {"Source": "https://github.com/o/r"}}})
    monkeypatch.setattr(cl, "fetch_github_releases", lambda o, r, token=None: [])
    monkeypatch.setattr(cl, "fetch_raw_changelog", lambda o, r, token=None: changelog)
    monkeypatch.setattr(cl, "fetch_docs_release_notes", lambda *a, **k: None)
    monkeypatch.setattr(cl, "get_pypi_version_info", lambda p, v: {"info": {"description": ""}})
    report = collect_changes("pkg", "1.5.0", "2.0.0")
    assert [e.version for e in report.entries] == ["2.0.0"]           # range only
    assert any(e.replacement == "pkg.new" for e in report.history)    # history keeps 1.1.0


def test_getattr_tombstone_is_not_an_export():
    old = {"pkg": "def load_boston(): pass\ndef load_iris(): pass"}
    new = {"pkg": textwrap.dedent("""\
        def load_iris(): pass
        def __getattr__(name):
            if name == "load_boston":
                raise ImportError("load_boston was removed, see ...")
            if name in ("old_alias",):
                return load_iris
            raise AttributeError(name)
        """)}
    ch = diff(old, new)
    assert match_precise([U("pkg.load_boston")], ch, "pkg")
    n = build_surface("2.0", {k: [v] for k, v in new.items()})
    assert "old_alias" in n.exports["pkg"] and "load_boston" not in n.exports["pkg"]


def test_bundled_tests_are_not_api():
    ch = diff({"pkg.tests.test_x": "def test_a(): pass", "pkg": ""}, {"pkg.tests.test_x": "", "pkg": ""})
    assert ch == []


@pytest.mark.parametrize("line,apis", [
    ("- Remove to_numpy_matrix & from_numpy_matrix (#5746)", {"to_numpy_matrix", "from_numpy_matrix"}),
    ("- Removed the read_only flag handling", {"unknown"}),
    ("- Drop support for python_2", {"unknown"}),
])
def test_bare_snake_case_names_after_verb(line, apis):
    assert {e.api for e in parse_changelog_text(line, "3.0")} == apis


def test_import_them_from_other_package():
    e = parse_changelog_text(
        "- Importing ``escape`` and ``Markup`` from ``flask`` is deprecated. "
        "Import them directly from ``markupsafe`` instead.", "2.3.0")
    assert {(x.api, x.replacement) for x in e} == {("escape", "markupsafe.escape"), ("Markup", "markupsafe.Markup")}


# ── migration guides ──────────────────────────────────────────────────────────

def test_rst_table_header_marks_removals_and_strict_replacements():
    text = textwrap.dedent("""\
        Main namespace
        --------------

        ======================  ============================================
        removed member          migration guideline
        ======================  ============================================
        alltrue                 Use ``np.all`` instead.
        byte_bounds             Now it's available under ``np.lib.array_utils.byte_bounds``
        asfarray                Use ``np.asarray`` with a float dtype instead.
        ======================  ============================================
        """)
    got = {e.api: (e.kind, e.replacement) for e in parse_changelog_text(text, "2.0.0")}
    assert got["alltrue"] == ("removed", "np.all")
    assert got["byte_bounds"] == ("removed", "np.lib.array_utils.byte_bounds")
    assert got["asfarray"] == ("removed", None)  # conditional advice: no automatic rewrite


def test_markdown_table_under_moved_heading():
    text = textwrap.dedent("""\
        ## Moved in Pydantic V2

        | Pydantic V1 | Pydantic V2 |
        | --- | --- |
        | `pydantic.BaseSettings` | [`pydantic_settings.BaseSettings`](#moved) |
        | `pydantic.color` | [`pydantic_extra_types.color`][x] |

        ## Other
        Some prose that mentions `pydantic.Field`.
        """)
    got = {e.api: (e.kind, e.replacement) for e in parse_changelog_text(text, "2.0")}
    assert got["pydantic.BaseSettings"] == ("removed", "pydantic_settings.BaseSettings")
    assert got["pydantic.color"] == ("removed", "pydantic_extra_types.color")
    assert "pydantic.Field" not in got


def test_default_section_for_removals_file():
    e = parse_changelog_text("- ``matplotlib.cm.get_cmap``; see the colormap registry.\n", "3.9.0",
                             default_section="removed")
    assert [(x.kind, x.api) for x in e] == [("removed", "matplotlib.cm.get_cmap")]


def test_fetch_migration_guides_picks_versioned_and_major_guides(monkeypatch):
    import depshift.changelog as cl
    files = {
        "doc/source/numpy_2_0_migration_guide.rst": "Removed\n-------\n- ``np.alltrue`` was removed. Use ``np.all``.\n",
        "docs/migration.md": "## Removed\n- `pkg.old_thing` was removed.\n",
        "doc/api/prev_api_changes/api_changes_2.0.0/removals.rst": "- ``pkg.cm.get_cmap``\n",
    }
    monkeypatch.setattr(cl, "_cached_raw", lambda o, r, path: files.get(path))
    entries = cl.fetch_migration_guides("o", "r", "1.26.4", "2.0.0", ["2.0.0"])
    assert {(e.api, e.version) for e in entries} >= {
        ("np.alltrue", "2.0.0"), ("pkg.old_thing", "2.0.0"), ("pkg.cm.get_cmap", "2.0.0")}
    # no major jump: the unversioned guide is not read
    minor = cl.fetch_migration_guides("o", "r", "2.0.0", "2.1.0", ["2.1.0"])
    assert all(e.api != "pkg.old_thing" for e in minor)


def test_removals_list_entry_vs_sentence():
    text = ("Removals\n--------\n\n"
            "- ``matplotlib.cm.get_cmap``; use ``matplotlib.colormaps[name]`` instead.\n"
            "- ``pkg.connect`` takes a ``Config`` object now.\n")
    got = {e.api: (e.kind, e.confidence) for e in parse_changelog_text(text, "3.9.0")}
    assert got["matplotlib.cm.get_cmap"] == ("removed", "high")
    assert got["pkg.connect"][1] == "low"   # a sentence about it: behaviour change


def test_sponsor_link_is_not_the_repo():
    from depshift.changelog import extract_github_repo
    info = {"info": {"project_urls": {"Funding": "https://github.com/sponsors/someone",
                                      "Source": "https://github.com/org/project"}}}
    assert extract_github_repo(info) == ("org", "project")


@pytest.mark.parametrize("item", [
    "- :func:`read_csv`: specifying an incorrect number of columns now raises ``ParserError``",
    "- ``integrate.simpson``",
])
def test_colon_or_bare_name_in_breaking_section_is_not_a_removal(item):
    text = "Backwards incompatible API changes\n----------------------------------\n\n" + item + "\n"
    assert all(e.confidence == "low" for e in parse_changelog_text(text, "2.0.0"))


def test_escaped_table_names_and_no_replacement_as_target():
    text = textwrap.dedent("""\
        ======================  ==========================
        removed member          migration guideline
        ======================  ==========================
        unicode\\_               Use ``np.str_`` instead.
        ======================  ==========================
        """)
    got = [(e.api, e.replacement) for e in parse_changelog_text(text, "2.0.0")]
    assert got == [("unicode_", "np.str_")]  # np.str_ itself is not removed


def test_except_clause_keeps_members():
    from depshift.analyzer import analyze
    entries = parse_changelog_text(
        "- Deprecate the ``werkzeug.urls`` module, except for the ``uri_to_iri`` and "
        "``iri_to_uri`` functions.", "2.3.0")
    assert entries[0].exceptions == ["uri_to_iri", "iri_to_uri"]
    us = [Usage(file="a.py", line=1, code="", attr_chain="werkzeug.urls", usage_type="import"),
          Usage(file="a.py", line=2, code="", attr_chain="werkzeug.urls.iri_to_uri", usage_type="function_call"),
          Usage(file="a.py", line=3, code="", attr_chain="werkzeug.urls.url_quote", usage_type="function_call")]
    risks, _ = analyze(us, entries, "werkzeug")
    assert [r.usage.line for r in risks] == [3]


def test_use_of_is_not_a_replacement_phrase():
    e = parse_changelog_text("- Positional use of ``pkg.f`` arguments is deprecated.", "1.0")
    assert [(x.api, x.replacement) for x in e] == [("pkg.f", None)]


def test_short_names_in_list_inherit_first_module():
    e = parse_changelog_text(
        "- ``django.utils.translation.ugettext()``, ``ugettext_lazy()``, ``ungettext()``, "
        "and ``ungettext_lazy()`` are removed.", "4.0")
    assert {x.api for x in e} == {"django.utils.translation." + n
                                  for n in ("ugettext", "ugettext_lazy", "ungettext", "ungettext_lazy")}
    # not a list: the sentence is about pkg.connect only
    e = parse_changelog_text("## Breaking\n- ``pkg.connect`` takes a ``Config`` object.", "2.0")
    assert {x.api for x in e} == {"pkg.connect"}


def test_qualified_advice_is_not_a_drop_in_replacement():
    from depshift.changelog import _classify_item
    got = {e.api: e.replacement for e in _classify_item(
        "``np.asfarray`` has been removed. Use ``np.asarray`` with a proper dtype instead.", "removed", "2.0")}
    assert got == {"np.asfarray": None}
    got = {e.api: e.replacement for e in _classify_item(
        "Removed ``foo``. Use ``bar`` and pass ``strict=False``.", "removed", "2.0")}
    assert got == {"foo": None}
    got = {e.api: e.replacement for e in _classify_item(
        "Removed ``foo``; use ``bar`` with the same arguments.", "removed", "2.0")}
    assert got == {"foo": "bar"}
