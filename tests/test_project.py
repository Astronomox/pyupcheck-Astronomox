"""The logic behind the commands (depshift.project) with PyPI, release notes
and the API diff faked: run_check's paths, the safe-version search, migration
plans, outdated rows and the small version helpers."""

from types import SimpleNamespace

import pytest

from depshift import project
from depshift.apisurface import APIChange
from depshift.changelog import ChangeEntry, ChangeReport
from depshift.config import Config
from depshift.deps import Dependency

PKG = "fakepkg"  # not installed anywhere, so the project's files decide its version


def releases(*versions, yanked=(), dates=None):
    dates = dates or {}
    return {v: [{"upload_time": f"{dates.get(v, '2026-01-01')}T00:00:00", "yanked": v in yanked}]
            for v in versions}


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "requirements.txt").write_text(f"{PKG}==1.0.0\n")
    (tmp_path / "app.py").write_text(f"import {PKG}\n{PKG}.old_api(1)\n{PKG}.kept()\n")
    return tmp_path


@pytest.fixture
def fake_pypi(monkeypatch):
    info = {"info": {"version": "2.0.0"}, "releases": releases("1.0.0", "1.1.0", "1.2.0", "2.0.0")}
    monkeypatch.setattr(project, "get_pypi_info", lambda package, fresh=False: info)
    return info


def notes(*entries, failed=(), checked=True):
    report = ChangeReport(list(entries), ["changelog"] if checked else [], list(failed),
                          versions_read=["2.0.0"] if checked else [])
    report.history = list(entries)
    return lambda *a, **k: report


REMOVED = ChangeEntry("removed", f"{PKG}.old_api", "Removed ``old_api``; use ``new_api``.", "2.0.0",
                      confidence="high")


# ── run_check ─────────────────────────────────────────────────────────────────

def test_run_check_finds_the_breaking_line(proj, fake_pypi, monkeypatch):
    monkeypatch.setattr(project, "collect_changes", notes(REMOVED))
    r = project.run_check(PKG, None, str(proj), None, Config())
    assert r["current_version"] == "1.0.0" and r["current_source"] == "pinned"
    assert r["target_version"] == "2.0.0"
    assert r["breaking_count"] == 1 and r["risks"][0]["line"] == 2 and not r["unchecked"]
    assert r["safe_count"] >= 1 and r["warnings"] == []


def test_run_check_up_to_date_and_unknown_baseline(proj, fake_pypi, tmp_path):
    r = project.run_check(PKG, "1.0.0", str(proj), None, Config())
    assert r["up_to_date"] and r["risks"] == []
    empty = tmp_path / "empty"
    empty.mkdir()
    r = project.run_check(PKG, "2.0.0", str(empty), None, Config())
    assert r["unchecked"] and r["current_version"] is None and "current version unknown" in r["warnings"][0]


def test_run_check_not_imported(proj, fake_pypi):
    (proj / "app.py").write_text("print('no imports')\n")
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config())
    assert r["no_usages"] and r["breaking_count"] == 0


def test_run_check_not_on_pypi(proj, monkeypatch):
    def missing(*a, **k):
        raise LookupError("404")
    monkeypatch.setattr(project, "get_pypi_info", missing)
    monkeypatch.setattr(project, "get_current_version", missing)
    assert project.run_check(PKG, None, str(proj), None, Config()) is None


def test_run_check_reports_sources_it_could_not_read(proj, fake_pypi, monkeypatch):
    monkeypatch.setattr(project, "collect_changes", notes(failed=["GitHub releases (HTTP 403)"], checked=False))
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config())
    assert r["unchecked"] and r["warnings"] == ["could not read: GitHub releases (HTTP 403)"]

    def broken(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(project, "collect_changes", broken)
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config())
    assert r["unchecked"] and r["warnings"] == ["changelog lookup failed (TimeoutError)"]


def test_run_check_min_severity_filters(proj, fake_pypi, monkeypatch):
    deprecated = ChangeEntry("deprecated", f"{PKG}.kept", "``kept`` is deprecated.", "1.2.0", confidence="high")
    monkeypatch.setattr(project, "collect_changes", notes(REMOVED, deprecated))
    assert project.run_check(PKG, "2.0.0", str(proj), None, Config())["deprecated_count"] == 1
    cfg = Config()
    cfg.min_severity = "breaking"
    r = project.run_check(PKG, "2.0.0", str(proj), None, cfg)
    assert r["deprecated_count"] == 0 and r["breaking_count"] == 1


def test_deep_check_adds_verified_findings_once(proj, fake_pypi, monkeypatch):
    monkeypatch.setattr(project, "collect_changes", notes(REMOVED))
    changes = [APIChange("removed_function", f"{PKG}.old_api", "removed", replacement=f"{PKG}.new_api"),
               APIChange("removed_function", f"{PKG}.kept", "removed")]
    monkeypatch.setattr(project, "get_api_changes", lambda *a: changes)
    monkeypatch.setattr(project, "get_return_types", lambda *a: {})
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config(), deep=True)
    by_line = {x["line"]: x for x in r["risks"]}
    # old_api: the changelog already flagged it, so it isn't repeated; kept(): only the diff knows
    assert sorted(by_line) == [2, 3] and len(r["risks"]) == 2
    assert by_line[3]["precise"] and by_line[3]["confidence"] == "high" and r["breaking_count"] == 2


def test_deep_check_failures_are_warnings(proj, fake_pypi, monkeypatch):
    monkeypatch.setattr(project, "collect_changes", notes(checked=False))
    monkeypatch.setattr(project, "get_api_changes", lambda *a: None)
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config(), deep=True)
    assert r["unchecked"] and "deep API diff unavailable" in r["warnings"][0]

    def broken(*a):
        raise MemoryError
    monkeypatch.setattr(project, "get_api_changes", broken)
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config(), deep=True)
    assert r["warnings"] == ["deep API diff failed (MemoryError)"]


def test_since_keeps_only_newer_releases(proj, monkeypatch):
    info = {"releases": releases("1.0.0", "1.5.0", "2.0.0", dates={"1.5.0": "2025-01-01", "2.0.0": "2026-06-01"})}
    monkeypatch.setattr(project, "get_pypi_info", lambda package, fresh=False: info)
    old = ChangeEntry("removed", f"{PKG}.kept", "Removed ``kept``.", "1.5.0", confidence="high")
    monkeypatch.setattr(project, "collect_changes", notes(REMOVED, old))
    r = project.run_check(PKG, "2.0.0", str(proj), None, Config(), since="2026-01-01")
    assert [x["api"] for x in r["risks"]] == [f"{PKG}.old_api"]


def test_filter_by_since_edge_cases(monkeypatch):
    changes = [REMOVED, ChangeEntry("removed", "x", "", "9.9")]
    assert project.filter_by_since(changes, "not-a-date", PKG) == changes

    def down(*a, **k):
        raise OSError
    monkeypatch.setattr(project, "get_pypi_info", down)
    assert project.filter_by_since(changes, "2026-01-01", PKG) == changes
    monkeypatch.setattr(project, "get_pypi_info", lambda p: {"releases": {"2.0.0": [{"upload_time": "garbage"}]}})
    assert project.filter_by_since(changes, "2020-01-01", PKG) == []  # unknown dates are excluded


# ── checking dependencies and changes ─────────────────────────────────────────

def test_check_dependency_marks_undeclared_imports(proj, monkeypatch):
    seen = {}

    def fake_run(package, target, directory, token, cfg, **kw):
        seen.update(kw)
        return {"package": package, "warnings": []}
    monkeypatch.setattr(project, "run_check", fake_run)
    dep = Dependency(name="urllib3", pinned_version=None, raw="urllib3", source=f"uv.lock {project.UNDECLARED}")
    locks = {"urllib3": SimpleNamespace(version="2.0.0", source="uv.lock")}
    r = project.check_dependency(dep, locks, str(proj), Config())
    assert r["undeclared"] and "only present via uv.lock" in r["warnings"][0] and seen["locked"] == "2.0.0"


def test_check_change_downgrade_and_fixable(proj, monkeypatch):
    change = SimpleNamespace(name=PKG, old="2.0.0", new="1.0.0", raw=f"{PKG}==1.0.0")
    monkeypatch.setattr(project, "run_check", lambda *a, **k: {"up_to_date": True, "breaking_count": 0})
    r = project.check_change(change, str(proj), Config(), None, deep=True)
    assert r["current_version"] == "2.0.0" and "is not an upgrade" in r["warnings"][0]
    assert r["current_source"] == "previous"

    monkeypatch.setattr(project, "run_check", lambda *a, **k: {"breaking_count": 2})
    monkeypatch.setattr(project, "fixable_count", lambda *a: 1)
    assert project.check_change(change, str(proj), Config(), None, deep=True)["fixable"] == 1

    def broken(*a):
        raise RuntimeError
    monkeypatch.setattr(project, "fixable_count", broken)
    assert project.check_change(change, str(proj), Config(), None, deep=True)["fixable"] is None
    monkeypatch.setattr(project, "run_check", lambda *a, **k: None)
    assert project.check_change(change, str(proj), Config(), None, deep=True) is None


def test_should_fail_levels():
    clean = {"breaking_count": 0, "deprecated_count": 0, "risks": []}
    dep = {"breaking_count": 0, "deprecated_count": 1, "risks": [{}]}
    warn = {"breaking_count": 0, "deprecated_count": 0, "risks": [{}]}
    brk = {"breaking_count": 1, "deprecated_count": 0, "risks": [{}]}
    assert not project.should_fail([brk], "never")
    assert project.should_fail([brk], "breaking") and not project.should_fail([dep], "breaking")
    assert project.should_fail([dep], "deprecated") and not project.should_fail([warn], "deprecated")
    assert project.should_fail([warn], "any") and not project.should_fail([clean], "any")
    assert project.should_fail([brk], "unknown-level")


# ── the highest safe version ──────────────────────────────────────────────────

def test_candidate_versions_newest_of_each_minor(monkeypatch):
    info = {"releases": {**releases("1.0.0", "1.1.0", "1.1.5", "1.2.0rc1", "2.0.0", "2.1.0", yanked={"2.1.0"}),
                         "garbage": [], "2.0.1.dev1": []}}
    monkeypatch.setattr(project, "get_pypi_info", lambda p: info)
    assert project.candidate_versions(PKG, "1.0.0", None) == ["1.1.5", "2.0.0"]
    assert project.candidate_versions(PKG, "1.0.0", "<2") == ["1.1.5"]
    assert project.candidate_versions(PKG, "1.0.0", "==1.0.0") == ["1.1.5", "2.0.0"]  # a pin isn't a ceiling
    assert project.candidate_versions(PKG, "1.0.0", "not a spec") == ["1.1.5", "2.0.0"]
    assert project.candidate_versions(PKG, "not-a-version", None) == []


def test_highest_safe_version_binary_search(monkeypatch):
    versions = ["1.1.0", "1.2.0", "1.3.0", "1.4.0", "2.0.0"]
    monkeypatch.setattr(project, "candidate_versions", lambda *a: versions)
    breaking = {"1.1.0": 0, "1.2.0": 0, "1.3.0": 0, "1.4.0": 1, "2.0.0": 3}
    calls = []

    def fake_run(package, version, *a, **k):
        calls.append(version)
        return {"breaking_count": breaking[version]}
    monkeypatch.setattr(project, "run_check", fake_run)
    plan = project.highest_safe_version(PKG, "1.0.0", ".", Config())
    assert plan["safe"] == "1.3.0" and plan["breaking_at_latest"] == 3 and plan["latest"] == "2.0.0"
    assert calls[0] == "2.0.0" and len(calls) <= 4 and not plan["unchecked"]


def test_highest_safe_version_never_trusts_an_unchecked_version(monkeypatch):
    monkeypatch.setattr(project, "candidate_versions", lambda *a: ["1.1.0", "2.0.0"])
    monkeypatch.setattr(project, "run_check", lambda *a, **k: {"unchecked": True, "breaking_count": 0})
    plan = project.highest_safe_version(PKG, "1.0.0", ".", Config())
    assert plan["safe"] == "1.0.0" and plan["unchecked"]
    monkeypatch.setattr(project, "run_check", lambda *a, **k: {"breaking_count": 0})
    assert project.highest_safe_version(PKG, "1.0.0", ".", Config())["safe"] == "2.0.0"
    monkeypatch.setattr(project, "candidate_versions", lambda *a: [])
    assert project.highest_safe_version(PKG, "1.0.0", ".", Config())["safe"] == "1.0.0"


# ── migration plans ───────────────────────────────────────────────────────────

_SURFACE = object()  # stands in for the new version's API surface


def _fake_migration(monkeypatch, fixes, manual=(), changes=(), surface=_SURFACE):
    from depshift import apisurface, rewrite
    monkeypatch.setattr(project, "get_api_changes", lambda *a: list(changes) if changes is not None else None)
    monkeypatch.setattr(apisurface, "extract_surface", lambda *a: surface)
    monkeypatch.setattr(project, "collect_changes", notes(REMOVED))
    monkeypatch.setattr(project, "get_return_types", lambda *a: {})
    monkeypatch.setattr(rewrite, "plan_migration",
                        lambda *a: rewrite.MigrationPlan(list(fixes), list(manual)))


def test_plan_project_migration_rewrites_and_flags(proj, fake_pypi, monkeypatch):
    from depshift.rewrite import Edit, Fix, Manual
    app = str(proj / "app.py")
    fix = Fix(app, 2, f"{PKG}.old_api -> {PKG}.new_api", [Edit((2, 8), (2, 15), "new_api")])
    _fake_migration(monkeypatch, [fix], [Manual(app, 3, f"{PKG}.kept()", "kept() was removed")])
    mp = project.plan_project_migration(PKG, None, str(proj), Config())
    assert (mp.current, mp.target, mp.source) == ("1.0.0", "2.0.0", "pinned") and mp.has_fixes
    assert mp.files[0].rel == "app.py" and f"{PKG}.new_api(1)" in mp.files[0].after and mp.files[0].error is None
    assert mp.where(mp.manual[0]) == "app.py:3"
    mp.files[0].write(str(proj))
    assert f"{PKG}.new_api(1)" in (proj / "app.py").read_text()


def test_plan_project_migration_refuses_a_rewrite_that_would_not_parse(proj, fake_pypi, monkeypatch):
    from depshift.rewrite import Edit, Fix
    fix = Fix(str(proj / "app.py"), 2, "broken", [Edit((2, 0), (2, 3), "def (")])
    _fake_migration(monkeypatch, [fix])
    mp = project.plan_project_migration(PKG, "2.0.0", str(proj), Config())
    assert mp.files[0].error == "the rewrite would not parse"


def test_plan_project_migration_stops_early(proj, fake_pypi, monkeypatch, tmp_path):
    assert project.plan_project_migration(PKG, "1.0.0", str(proj), Config()).up_to_date
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert "current version unknown" in project.plan_project_migration(PKG, None, str(empty), Config()).error
    _fake_migration(monkeypatch, [], changes=None)
    assert "could not read the API" in project.plan_project_migration(PKG, "2.0.0", str(proj), Config()).error

    def down(*a, **k):
        raise LookupError("404")
    monkeypatch.setattr(project, "latest_allowed_version", down)
    assert "not found on PyPI" in project.plan_project_migration(PKG, None, str(proj), Config()).error


def test_fixable_count(proj, monkeypatch):
    from depshift.rewrite import Fix
    _fake_migration(monkeypatch, [Fix("a.py", 1, "x"), Fix("a.py", 2, "y")])
    assert project.fixable_count(PKG, "1.0.0", "2.0.0", str(proj), Config(), None) == 2
    _fake_migration(monkeypatch, [], surface=None)
    assert project.fixable_count(PKG, "1.0.0", "2.0.0", str(proj), Config(), None) is None


# ── versions, outdated, baselines ─────────────────────────────────────────────

def test_latest_allowed_version_respects_the_constraint(fake_pypi):
    fake_pypi["releases"].update(releases("3.0.0a1", "2.5.0", yanked={"2.5.0"}))
    fake_pypi["releases"]["junk"] = []
    assert project.latest_allowed_version(PKG) == "2.0.0"
    assert project.latest_allowed_version(PKG, "<2") == "1.2.0"
    assert project.latest_allowed_version(PKG, "not a spec") == "2.0.0"
    assert project.latest_allowed_version(PKG, ">9") is None


def test_outdated_status_rows(proj, fake_pypi, monkeypatch):
    dep = Dependency(name=PKG, pinned_version="1.0.0", raw=f"{PKG}==1.0.0", source="requirements.txt")
    row = project.outdated_status(dep, {}, str(proj), Config())
    assert row == {"name": PKG, "current": "1.0.0", "latest": "2.0.0", "outdated": True, "major": True}
    monkeypatch.setattr(project, "highest_safe_version", lambda *a: {"safe": "1.2.0"})
    assert project.outdated_status(dep, {}, str(proj), Config(), safe=True)["plan"] == {"safe": "1.2.0"}
    odd = Dependency(name=PKG, pinned_version="banana", raw=PKG, source="requirements.txt")
    assert project.outdated_status(odd, {}, str(proj), Config())["major"] is False
    unpinned = Dependency(name="other", pinned_version=None, raw="other", source="requirements.txt")
    assert project.outdated_status(unpinned, {}, str(proj), Config()) == {
        "name": "other", "current": None, "latest": "2.0.0"}

    def down(*a, **k):
        raise OSError
    monkeypatch.setattr(project, "latest_allowed_version", down)
    assert project.outdated_status(dep, {}, str(proj), Config()) is None


def test_baseline_version_order(proj, monkeypatch):
    assert project.baseline_version(PKG, str(proj), locked="3.0") == ("3.0", "lockfile")
    assert project.baseline_version(PKG, str(proj)) == ("1.0.0", "pinned")
    monkeypatch.setattr(project, "get_installed_version", lambda p: "1.5.0")
    assert project.baseline_version("other", str(proj), spec=">=1") == ("1.5.0", "installed")
    assert project.baseline_version("other", str(proj), spec=">=2,<3") == ("2", "constraint lower bound")
    monkeypatch.setattr(project, "get_installed_version", lambda p: None)
    assert project.baseline_version("other", str(proj)) == (None, None)
    assert project.project_version(PKG, str(proj)) == ("1.0.0", "pinned")
    assert project.project_version("absent", str(proj)) == (None, None)


def test_small_version_helpers():
    assert project.specifier_of("x>=1,<2") in (">=1,<2", "<2,>=1") and project.specifier_of("x") is None
    assert project.specifier_of("not valid ===") is None
    assert project.satisfies("1.5", ">=1") and not project.satisfies("0.5", ">=1")
    assert project.satisfies("banana", ">=1")  # can't tell: don't exclude
    assert project.is_exact_pin("==1.0") and not project.is_exact_pin("==1.*") and not project.is_exact_pin(None)
    assert not project.is_exact_pin(">=1,==1.0")
    assert project.not_newer("1.0", "1.0") and not project.not_newer("2.0", "1.0")
    assert project.not_newer("abc", "abc")
    assert project.lower_bound("~=1.4") == "1.4" and project.lower_bound("<2") is None
    assert project.lower_bound("garbage!") is None and project.lower_bound(None) is None
    assert project.get_installed_version("surely-not-installed-anywhere") is None


def test_release_history_newest_first():
    info = {"releases": {**releases("1.0.0", "2.0.0rc1", "1.5.0", yanked={"1.5.0"}), "nope": [], "0.9": []}}
    history = project.release_history(info)
    assert [r.version for r in history] == ["2.0.0rc1", "1.5.0", "1.0.0", "0.9"]
    assert history[0].prerelease and history[1].yanked and history[3].date == "" and not history[3].yanked


def test_replace_file_keeps_mode_and_cleans_up(tmp_path, monkeypatch):
    import os
    target = tmp_path / "f.txt"
    target.write_text("old")
    target.chmod(0o640)
    project.replace_file(str(target), "new\r\n")
    assert target.read_bytes() == b"new\r\n" and (target.stat().st_mode & 0o777) == 0o640

    def fail(*a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        project.replace_file(str(target), "newer")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"] and target.read_bytes() == b"new\r\n"
