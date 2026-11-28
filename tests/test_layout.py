"""Every command's terminal output fits the terminal, from a phone-width split
pane (40 columns) to a wide monitor, and keeps what matters readable."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from rich.console import Console

from depshift import cli, output
from depshift import review as review_mod
from depshift.changelog import ChangeEntry
from depshift.project import FileChange, MigrationPlan

WIDTHS = [40, 50, 60, 64, 80, 120, 160]
LONG_NOTE = ("The `root_path` parameter in `FastAPI()` is deprecated and will be removed; configure the server "
             "with `fastapi run --root-path /api/v1` instead of passing it to the application object.")


def risk(line, severity="breaking", replacement="markupsafe.Markup"):
    return {"file": "/proj/src/application/routes/very_long_module_name.py", "line": line,
            "code": "return flask.Markup(f'<b>{values.mean():.2f}</b>') + something_else(argument=1)",
            "severity": severity, "change_description": LONG_NOTE, "replacement": replacement,
            "precise": True, "confidence": "high"}


def result(pkg, cur="2.2.5", new="3.1.3", **kw):
    r = {"package": pkg, "current_version": cur, "target_version": new, "risks": [], "breaking_count": 0,
         "deprecated_count": 0, "safe_count": 4, "warnings": []}
    r.update(kw)
    return r


RESULTS = {
    "flask": result("flask", breaking_count=1, risks=[risk(13)], fixable=1),
    "pydantic-settings-extended": result("pydantic-settings-extended", "2.15.0", "12.16.3rc1",
                                         deprecated_count=1, risks=[risk(7, "deprecated", None)]),
    "requests": result("requests", "2.31.0", "2.34.2"),
    "rich": result("rich", "15.0.0", "15.0.0", up_to_date=True),
    "uvicorn": result("uvicorn", "0.52.4", "0.54.0", no_usages=True),
    "mystery": result("mystery", None, "1.0", unchecked=True, warnings=["current version unknown"]),
}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "requirements.txt").write_text("".join(f"{n}==1.0\n" for n in RESULTS))
    (tmp_path / "app.py").write_text("import flask\nx = flask.Markup('" + "y" * 150 + "')\n")
    return tmp_path


def run(width, monkeypatch, *args):
    con = Console(width=width, color_system=None, highlight=False)
    err = Console(width=width, color_system=None, highlight=False, stderr=True)
    for module in (cli, output):
        monkeypatch.setattr(module, "console", con)
        monkeypatch.setattr(module, "err_console", err)
    res = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
    return res.output


def fits(text, width):
    wide = [ln for ln in text.splitlines() if len(ln) > width]
    assert not wide, f"{len(wide)} line(s) wider than {width}: {wide[0]!r}"


@pytest.mark.parametrize("width", WIDTHS)
def test_check_all(width, project, monkeypatch):
    monkeypatch.setattr(cli, "check_dependency", lambda dep, *a, **k: RESULTS[dep.name])
    out = run(width, monkeypatch, "check-all", "-d", str(project))
    fits(out, width)
    assert "pyupcheck migrate flask" in out
    # statuses are never cut short, and the offending code is always shown
    for status in ("✗ 1 breaking", "! 1 deprecated", "? not checked", "✓ safe"):
        assert status in out, status
    assert "return flask.Markup(" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_check_one(width, project, monkeypatch):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: RESULTS["flask"])
    out = run(width, monkeypatch, "check", "flask", "-d", str(project))
    fits(out, width)
    assert "BREAKING" in out and "use markupsafe.Markup" in out and "return flask.Markup(" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_outdated(width, project, monkeypatch):
    rows = {"flask": {"name": "flask", "current": "2.2.5", "latest": "3.1.3", "outdated": True, "major": True},
            "requests": {"name": "requests", "current": "2.31.0", "latest": "2.34.2", "outdated": True,
                         "major": False},
            "mystery": {"name": "mystery", "current": None, "latest": "1.0"}}
    monkeypatch.setattr(cli, "outdated_status", lambda dep, *a, **k: rows.get(dep.name))
    out = run(width, monkeypatch, "outdated", "-d", str(project))
    fits(out, width)
    assert "2 outdated" in out and "major upgrade" in out and "update available" in out
    assert "current version unknown" in out and "2.2.5" in out and "3.1.3" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_fix(width, project, monkeypatch):
    plans = {"flask": {"safe": "2.3.3", "latest": "3.1.3", "breaking_at_latest": 1, "unchecked": False}}
    monkeypatch.setattr(cli, "highest_safe_version", lambda name, *a, **k: plans.get(
        name, {"safe": "1.0", "latest": "1.0", "breaking_at_latest": 0, "unchecked": False}))
    out = run(width, monkeypatch, "fix", "--dry-run", "-d", str(project))
    fits(out, width)
    assert "1 pin would move" in out and "2.3.3" in out
    assert "pyupcheck migrate flask 3.1.3" in " ".join(out.split())  # may wrap, never cut


@pytest.mark.parametrize("width", WIDTHS)
def test_versions(width, project, monkeypatch):
    releases = {f"1.{i}.0": [{"upload_time": "2026-01-01T00:00:00", "yanked": i == 3}] for i in range(20)}
    monkeypatch.setattr(cli, "get_pypi_info", lambda p: {"info": {"version": "1.19.0"}, "releases": releases})
    out = run(width, monkeypatch, "versions", "flask", "-d", str(project))
    fits(out, width)
    assert "1.19.0" in out and "latest" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_diff(width, monkeypatch):
    changes = [ChangeEntry("removed", "flask.Markup", LONG_NOTE, "3.0.0"),
               ChangeEntry("deprecated", "flask.x", "Deprecate ``x``.", "2.3.0")]
    monkeypatch.setattr(cli, "get_available_versions", lambda p: ["2.2.5", "3.0.0"])
    monkeypatch.setattr(cli, "get_changes_between", lambda *a, **k: changes)
    out = run(width, monkeypatch, "diff", "flask", "2.2.5", "3.0.0")
    fits(out, width)
    assert "3.0.0" in out and "removed" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_scan(width, project, monkeypatch):
    out = run(width, monkeypatch, "scan", "flask", "-d", str(project))
    fits(out, width)
    assert "app.py" in out and "use flask" in out and "flask.Markup call" in out and "import flask" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_migrate(width, project, monkeypatch):
    app = project / "app.py"

    def planner(package, target, directory, cfg, token=None):
        plan = MigrationPlan(package, directory)
        plan.current, plan.target, plan.source, plan.has_fixes = "2.2.5", "3.1.3", "pinned", True
        fix = SimpleNamespace(file=str(app), line=2, cell=None, description="flask.Markup -> markupsafe.Markup")
        plan.files = [FileChange(str(app), "app.py", app.read_text(),
                                 app.read_text().replace("flask.Markup", "markupsafe.Markup"), [fix])]
        plan.manual = [SimpleNamespace(file=str(app), line=9, cell=None, code="flask.escape(" + "z" * 120 + ")",
                                       reason=LONG_NOTE)]
        return plan

    monkeypatch.setattr(cli, "plan_project_migration", planner)
    out = run(width, monkeypatch, "migrate", "flask", "-d", str(project))
    fits(out, width)
    assert "What pyupcheck fixes" in out and "What needs you" in out and "Would rewrite 1 file" in out
    assert "flask.Markup -> markupsafe.Markup" in out and "flask.escape(" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_review_terminal(width, project, monkeypatch):
    changes = [SimpleNamespace(name="flask", old="2.2.5", new="3.1.3", raw="flask==3.1.3")]
    monkeypatch.setattr(review_mod, "changed_dependencies", lambda directory, base: changes)
    monkeypatch.setattr(cli, "check_change", lambda *a, **k: RESULTS["flask"])
    out = run(width, monkeypatch, "review", "--base", "HEAD", "-f", "terminal", "-d", str(project))
    fits(out, width)
    assert "Before" in out or width < 64  # narrow tables combine or drop the version columns
    assert "✗ 1 breaking" in out and "pyupcheck migrate flask" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_explain_dry_run(width, project, monkeypatch):
    r = result("flask", breaking_count=1, risks=[dict(risk(2), file=str(project / "app.py"), api="flask.Markup")])
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: r)
    out = run(width, monkeypatch, "explain", "flask", "--dry-run", "-d", str(project))
    fits(out, width)
    assert "Nothing was sent" in out
