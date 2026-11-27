"""Commands end to end with fake PyPI data: config problems, check-all for
named packages, versions, diff, scan and the JSON report's shape."""

import json

import pytest
from click.testing import CliRunner

from depshift import cli
from depshift.changelog import ChangeEntry, clip
from depshift.config import load_config


def run(*args, **kw):
    return CliRunner().invoke(cli.main, list(args), catch_exceptions=False, **kw)


@pytest.fixture(autouse=True)
def _wide(monkeypatch):
    from rich.console import Console
    monkeypatch.setattr(cli, "console", Console(width=140, color_system=None, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=140, color_system=None, highlight=False, stderr=True))
    cli._REPORTED_CONFIG.clear()


# ── configuration ─────────────────────────────────────────────────────────────

def test_config_mistakes_are_reported_with_suggestions(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyupcheck]\nignores = ["x"]\nfail_on = "breakng"\nexclude = "migrations"\ncache = "yes"\n')
    cfg = load_config(str(tmp_path))
    joined = "\n".join(cfg.problems)
    assert "unknown key 'ignores'" in joined and "did you mean 'ignore'" in joined
    assert "fail_on = 'breakng'" in joined and "did you mean 'breaking'" in joined
    assert "exclude must be a list" in joined and "cache must be true or false" in joined
    assert cfg.fail_on == "breaking" and cfg.cache is True  # defaults kept


def test_valid_config_has_no_problems(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyupcheck]\nignore = ["Internal"]\nexclude = ["legacy/"]\nfail_on = "any"\n')
    cfg = load_config(str(tmp_path))
    assert cfg.problems == [] and cfg.ignore_packages == {"internal"} and cfg.exclude_dirs == {"legacy"}
    assert cfg.fail_on == "any"


def test_broken_toml_is_a_problem_not_a_crash(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pyupcheck\n")
    assert "not valid TOML" in load_config(str(tmp_path)).problems[0]


# ── check-all ─────────────────────────────────────────────────────────────────

def _project(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==2.2.5\nrequests==2.31.0\nnumpy==1.26.4\n")
    return str(tmp_path)


def _fake_check(dep, locks, directory, cfg, token=None, deep=False, since=None):
    return {"package": dep.name, "current_version": dep.pinned_version, "target_version": "9.0", "risks": [],
            "breaking_count": 0, "deprecated_count": 0, "safe_count": 2, "warnings": []}


def test_check_all_named_packages_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "check_dependency", _fake_check)
    r = run("check-all", "Requests", "numpy", "-d", _project(tmp_path))
    assert "Checking 2 dependencies" in r.output and "requests" in r.output and "numpy" in r.output
    assert "flask" not in r.output and "across 2 packages in" in r.output


def test_check_all_unknown_name_suggests(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "check_dependency", _fake_check)
    r = run("check-all", "reqests", "-d", _project(tmp_path))
    assert r.exit_code == 2 and "reqests is not a dependency" in r.output and "did you mean requests" in r.output


def test_json_report_has_a_schema_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "check_dependency", _fake_check)
    data = json.loads(run("check-all", "-d", _project(tmp_path), "-f", "json").output)
    assert data["schema_version"] == 1 and data["pyupcheck_version"] == cli.__version__
    assert {r["package"] for r in data["results"]} == {"flask", "requests", "numpy"}


def test_config_problems_never_mix_into_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "check_dependency", _fake_check)
    project = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.pyupcheck]\nignores = ["x"]\n')
    try:
        runner = CliRunner(mix_stderr=False)  # click < 8.2 mixes them unless told not to
    except TypeError:
        runner = CliRunner()  # click 8.2+: always separate
    r = runner.invoke(cli.main, ["check-all", "-d", project, "-f", "json"])
    assert "config:" in r.stderr
    json.loads(r.stdout)  # nothing but the report on stdout


# ── versions ──────────────────────────────────────────────────────────────────

def _release(date, yanked=False):
    return [{"upload_time": f"{date}T10:00:00", "yanked": yanked}]


def test_versions_marks_latest_yours_yanked_and_prereleases(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("numpy==1.26.4\n")
    releases = {"2.0.0": _release("2026-06-01"), "2.0.0rc1": _release("2026-05-01"),
                "1.27.0": _release("2025-01-01", yanked=True), "1.26.4": _release("2024-02-05"),
                "not-a-version": _release("2020-01-01")}
    monkeypatch.setattr(cli, "get_pypi_info", lambda p: {"info": {"version": "2.0.0"}, "releases": releases})
    out = run("versions", "numpy", "-d", str(tmp_path)).output
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip() and ln.split()[0][0].isdigit()}
    assert "latest" in lines["2.0.0"] and "2026-06-01" in lines["2.0.0"]
    assert "pre-release" in lines["2.0.0rc1"] and "yanked" in lines["1.27.0"]
    assert "you're on this" in lines["1.26.4"] and "(pinned)" in lines["1.26.4"]
    assert "not-a-version" not in out and "pyupcheck check numpy" in out


# ── diff ──────────────────────────────────────────────────────────────────────

def test_diff_groups_by_release_and_drops_repeats(monkeypatch):
    changes = [
        ChangeEntry("removed", "flask.Markup", "Removed ``Markup``. :pr:`1`", "3.0.0"),
        ChangeEntry("removed", "flask.escape", "Removed ``Markup``. :pr:`1`", "3.0.0"),  # same note
        ChangeEntry("deprecated", "flask.x", "Deprecate ``x``.", "2.3.0"),
    ]
    monkeypatch.setattr(cli, "get_available_versions", lambda p: ["2.2.5", "3.0.0"])
    monkeypatch.setattr(cli, "get_changes_between", lambda *a, **k: changes)
    out = run("diff", "flask", "2.2.5", "3.0.0").output
    assert "1 removed · 1 deprecated in 2 releases" in out
    assert out.index("3.0.0\n") < out.index("2.3.0\n")  # newest first
    assert out.count("Removed `Markup`.") == 1 and ":pr:" not in out


# ── scan ──────────────────────────────────────────────────────────────────────

def test_scan_groups_by_file_and_names_each_use(tmp_path):
    (tmp_path / "app.py").write_text("import numpy as np\nx = np.array([1], dtype=np.float_)\n")
    (tmp_path / "util.py").write_text("import numpy\n")
    out = run("scan", "numpy", "-d", str(tmp_path)).output
    assert "app.py  2 lines" in out and "util.py  1 line" in out
    assert "numpy.array call, numpy.float_ attribute" in out
    assert "3 lines in 2 files use numpy." in out


def test_clip_cuts_at_a_word():
    assert clip("short", 10) == "short"
    assert clip("one two three four", 12) == "one two…"


# ── fix ───────────────────────────────────────────────────────────────────────

def test_fix_table_and_rewrite_keeps_line_endings(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_bytes(b"flask==2.2.5\r\nnumpy==1.26.4\r\nclick==8.1.7\r\n")
    plans = {"flask": {"safe": "2.3.3", "latest": "3.1.3", "breaking_at_latest": 1, "unchecked": False},
             "numpy": {"safe": "1.26.4", "latest": "2.5.5", "breaking_at_latest": 2, "unchecked": False},
             "click": {"safe": "8.5.1", "latest": "8.5.1", "breaking_at_latest": 0, "unchecked": False}}
    monkeypatch.setattr(cli, "highest_safe_version", lambda name, *a, **k: plans[name])
    out = run("fix", "--dry-run", "-d", str(tmp_path)).output
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln.split() and ln.split()[0] in plans}
    assert "→ 2.3.3" in rows["flask"] and "3.1.3 breaks 1 usage" in rows["flask"]
    assert "stays" in rows["numpy"] and "pyupcheck migrate numpy 2.5.5" in rows["numpy"]
    assert "latest is safe" in rows["click"] and "2 pins would move" in out
    assert req.read_bytes() == b"flask==2.2.5\r\nnumpy==1.26.4\r\nclick==8.1.7\r\n"  # dry run
    run("fix", "-d", str(tmp_path))
    assert req.read_bytes() == b"flask==2.3.3\r\nnumpy==1.26.4\r\nclick==8.5.1\r\n"
    assert [p.name for p in tmp_path.iterdir()] == ["requirements.txt"]
