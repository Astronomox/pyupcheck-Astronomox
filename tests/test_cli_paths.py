"""The commands' less travelled paths: errors with exit codes, reports written
to files, review's git failures, watch, explain with a fake Claude client,
migrate --write, outdated --safe, ci-setup, ui without Textual."""

import builtins
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from depshift import cli, output
from depshift import explain as ex
from depshift import review as review_mod
from depshift.project import FileChange, MigrationPlan


@pytest.fixture(autouse=True)
def _console(monkeypatch):
    from rich.console import Console
    con = Console(width=120, color_system=None, highlight=False)
    err = Console(width=120, color_system=None, highlight=False, stderr=True)
    for module in (cli, output):
        monkeypatch.setattr(module, "console", con)
        monkeypatch.setattr(module, "err_console", err)
    cli._REPORTED_CONFIG.clear()


def run(*args):
    return CliRunner().invoke(cli.main, list(args), catch_exceptions=False)


def result(**kw):
    r = {"package": "flask", "current_version": "2.2.5", "target_version": "3.1.3", "risks": [],
         "breaking_count": 0, "deprecated_count": 0, "safe_count": 3, "warnings": []}
    r.update(kw)
    return r


BREAKING = {"file": "app.py", "line": 2, "code": "flask.Markup('x')", "severity": "breaking", "api": "flask.Markup",
            "change_description": "Markup was removed.", "replacement": "markupsafe.Markup", "precise": True,
            "confidence": "high"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==2.2.5\n")
    (tmp_path / "app.py").write_text("import flask\nflask.Markup('x')\n")
    return tmp_path


# ── check ─────────────────────────────────────────────────────────────────────

def test_check_not_on_pypi_exits_2(project, monkeypatch):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: None)
    r = run("check", "nope-[red]x", "-d", str(project))
    assert r.exit_code == 2 and "nope-[red]x: not found on PyPI" in r.output  # shown literally, not as markup


def test_check_fail_on_and_report_file(project, monkeypatch, tmp_path):
    seen = {}

    def fake(package, target, directory, token, cfg, **kw):
        seen["cfg"] = cfg
        return result(breaking_count=1, risks=[dict(BREAKING, file=str(project / "app.py"))])
    monkeypatch.setattr(cli, "run_check", fake)
    out = tmp_path / "report.json"
    r = run("check", "flask", "-d", str(project), "-f", "json", "-o", str(out), "--min-severity", "breaking",
            "--no-cache")
    assert r.exit_code == 1 and json.loads(out.read_text())["results"][0]["breaking_count"] == 1
    assert seen["cfg"].min_severity == "breaking"
    assert run("check", "flask", "-d", str(project), "--fail-on", "never", "-q").exit_code == 0


def test_check_all_without_dependency_files(tmp_path):
    r = run("check-all", "-d", str(tmp_path))
    assert r.exit_code == 2 and "No dependency files found" in r.output


# ── review ────────────────────────────────────────────────────────────────────

def _review(project, *extra):
    return run("review", "--base", "main", "-d", str(project), "--no-deep", *extra)


def test_review_git_missing(project, monkeypatch):
    def no_git(*a):
        raise FileNotFoundError("git")
    monkeypatch.setattr(review_mod, "changed_dependencies", no_git)
    r = _review(project)
    assert r.exit_code == 2 and "git isn't installed" in r.output


def test_review_not_a_repository_and_bad_ref(project, monkeypatch):
    def fails(stderr):
        def inner(*a):
            raise subprocess.CalledProcessError(128, ["git"], stderr=stderr)
        return inner
    monkeypatch.setattr(review_mod, "changed_dependencies", fails("fatal: not a git repository"))
    r = _review(project)
    assert r.exit_code == 2 and "isn't a git repository" in r.output
    monkeypatch.setattr(review_mod, "changed_dependencies", fails("fatal: bad revision 'main'"))
    r = _review(project)
    assert r.exit_code == 2 and "Could not read the dependency files at main" in r.output and "bad revision" in r.output


def test_review_writes_markdown_sarif_and_annotations(project, monkeypatch, tmp_path):
    changes = [SimpleNamespace(name="flask", old="2.2.5", new="3.1.3", raw="flask==3.1.3"),
               SimpleNamespace(name="ignored-pkg", old="1", new="2", raw="ignored-pkg==2")]
    (project / "pyproject.toml").write_text('[tool.pyupcheck]\nignore = ["ignored-pkg"]\n')
    monkeypatch.setattr(review_mod, "changed_dependencies", lambda d, b: changes)
    checked = []

    def fake(change, *a, **k):
        checked.append(change.name)
        return result(breaking_count=1, risks=[dict(BREAKING, file=str(project / "app.py"))])
    monkeypatch.setattr(cli, "check_change", fake)
    md, sarif = tmp_path / "review.md", tmp_path / "out.sarif"
    r = _review(project, "-o", str(md), "--sarif", str(sarif), "--annotations", "--fail-on", "breaking")
    assert r.exit_code == 1 and checked == ["flask"]
    assert md.read_text().startswith(review_mod.COMMENT_MARKER) and "**1 breaking**" in md.read_text()
    assert json.loads(sarif.read_text())["runs"][0]["results"]
    assert "::error" in r.output


def test_review_terminal_with_nothing_changed(project, monkeypatch):
    monkeypatch.setattr(review_mod, "changed_dependencies", lambda d, b: [])
    r = _review(project, "-f", "terminal")
    assert r.exit_code == 0 and "No dependency versions changed since main" in r.output


def test_review_md_to_stdout(project, monkeypatch):
    monkeypatch.setattr(review_mod, "changed_dependencies", lambda d, b: [])
    r = _review(project, "-f", "md")
    assert r.output.startswith(review_mod.COMMENT_MARKER)


# ── watch ─────────────────────────────────────────────────────────────────────

class FakeWatcher:
    instances: list = []

    def __init__(self, directory, check, latest, direct, releases_every, jobs):
        self.check, self.latest, self.direct = check, latest, direct
        self.snapshot, self.known_latest = {"flask": "2.2.5"}, {"flask": "3.1.3"}
        FakeWatcher.instances.append(self)

    def start(self):
        return []

    def poll(self):
        return [SimpleNamespace(kind="breaks")]


def test_watch_polls_rings_and_stops(project, monkeypatch):
    import depshift.watch as watch_mod
    monkeypatch.setattr(watch_mod, "Watcher", FakeWatcher)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "print_watch_event", lambda ev, d, q: True)
    rings = []
    monkeypatch.setattr(cli.console, "bell", lambda: rings.append(1))
    monkeypatch.setattr(cli, "check_change", lambda change, *a: "checked")
    monkeypatch.setattr(cli, "latest_allowed_version", lambda name, spec, fresh: f"latest({spec})")
    (project / "pyproject.toml").write_text('[tool.pyupcheck]\nignore = ["skipme"]\n')
    r = run("watch", "-d", str(project), "--cycles", "2", "--bell", "--interval", "0.2")
    assert r.exit_code == 0 and "requirements.txt (1 package), and PyPI every 60 min for 1 direct dependency." in r.output
    assert len(rings) == 2
    w = FakeWatcher.instances[-1]
    assert w.check(SimpleNamespace(name="skipme")) is None and w.check(SimpleNamespace(name="flask")) == "checked"
    assert w.latest("flask", "flask>=2,<4") in ("latest(>=2,<4)", "latest(<4,>=2)")
    assert w.latest("flask", "flask==2.2.5") == "latest(None)"  # a pin isn't a ceiling
    assert w.direct() == ["flask"]


def test_watch_without_files_and_ctrl_c(tmp_path, monkeypatch):
    import depshift.watch as watch_mod
    monkeypatch.setattr(watch_mod, "Watcher", FakeWatcher)

    def interrupt(s):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", interrupt)
    r = run("watch", "-d", str(tmp_path), "--releases", "0")
    assert "No dependency files" in r.output and "Stopped." in r.output


# ── explain ───────────────────────────────────────────────────────────────────

def _breaking(project):
    return result(breaking_count=2, risks=[dict(BREAKING, file=str(project / "app.py"))])


def test_explain_streams_and_reports_usage(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: _breaking(project))
    monkeypatch.setattr(ex, "make_client", lambda: "client")
    sent = {}

    def advise(client, prompt, write, **kw):
        sent.update(kw, prompt=prompt)
        write("\x1b]0;evil\x07## Use markupsafe\n")
        return {"stop_reason": "max_tokens", "model": "m", "input_tokens": 10, "output_tokens": 5}
    monkeypatch.setattr(ex, "stream_advice", advise)
    r = run("explain", "flask", "-d", str(project), "--max", "1", "--effort", "low", "--no-fallbacks")
    assert r.exit_code == 0 and "(1 more not included: --max)" in r.output
    assert "## Use markupsafe" in r.output and "\x1b" not in r.output  # the answer can't drive the terminal
    assert "The answer was cut off" in r.output and "10 tokens in, 5 out" in r.output
    assert sent["effort"] == "low" and sent["fallbacks"] is False


def test_explain_refusal_and_errors(project, monkeypatch):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: _breaking(project))
    monkeypatch.setattr(ex, "make_client", lambda: "client")
    monkeypatch.setattr(ex, "stream_advice", lambda *a, **k: {"stop_reason": "refusal", "model": "m",
                                                              "input_tokens": 1, "output_tokens": 0})
    assert "Claude declined this request" in run("explain", "flask", "-d", str(project)).output

    def fails(*a, **k):
        raise ex.ExplainError("Rate limited")
    monkeypatch.setattr(ex, "stream_advice", fails)
    r = run("explain", "flask", "-d", str(project))
    assert r.exit_code == 2 and "Rate limited" in r.output
    monkeypatch.setattr(ex, "make_client", fails)
    assert run("explain", "flask", "-d", str(project)).exit_code == 2


def test_explain_nothing_to_explain_and_not_found(project, monkeypatch):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: result())
    r = run("explain", "flask", "-d", str(project))
    assert r.exit_code == 0 and "nothing to explain" in r.output
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: None)
    assert run("explain", "flask", "-d", str(project)).exit_code == 2


# ── migrate ───────────────────────────────────────────────────────────────────

def _plan(project, error=None, up_to_date=False, files=True, manual=True):
    app = project / "app.py"

    def planner(package, target, directory, cfg, token=None):
        mp = MigrationPlan(package, directory)
        mp.current, mp.target, mp.source = "2.2.5", "3.1.3", "pinned"
        mp.error, mp.up_to_date = error, up_to_date
        if files:
            fix = SimpleNamespace(file=str(app), line=2, cell=None, description="flask.Markup -> markupsafe.Markup")
            after = app.read_text().replace("flask.Markup", "markupsafe.Markup")
            mp.files = [FileChange(str(app), "app.py", app.read_text(), after, [fix]),
                        FileChange(str(app), "bad.py", "x", "y", [], error="the rewrite would not parse")]
            mp.has_fixes = True
        if manual:
            mp.manual = [SimpleNamespace(file=str(app), line=1, cell=None, code="flask.escape(x)", reason="gone")]
        return mp
    return planner


def test_migrate_write_rewrites_and_exits_1_while_manual_work_remains(project, monkeypatch):
    monkeypatch.setattr(cli, "plan_project_migration", _plan(project))
    r = run("migrate", "flask", "-d", str(project), "--write")
    assert r.exit_code == 1 and "Rewrote 1 file." in r.output and "Check the changes that need you" in r.output
    assert "bad.py skipped: the rewrite would not parse" in r.output
    assert "markupsafe.Markup('x')" in (project / "app.py").read_text()


def test_migrate_write_failure_is_reported(project, monkeypatch):
    monkeypatch.setattr(cli, "plan_project_migration", _plan(project, manual=False))

    def readonly(self, root=None):
        raise PermissionError("read-only file system")
    monkeypatch.setattr(FileChange, "write", readonly)
    r = run("migrate", "flask", "-d", str(project), "--write")
    assert r.exit_code == 1 and "not written: read-only file system" in r.output


def test_migrate_error_up_to_date_and_nothing_to_change(project, monkeypatch):
    monkeypatch.setattr(cli, "plan_project_migration", _plan(project, error="not found on PyPI"))
    assert run("migrate", "flask", "-d", str(project)).exit_code == 2
    monkeypatch.setattr(cli, "plan_project_migration", _plan(project, up_to_date=True))
    assert "already the latest" in run("migrate", "flask", "-d", str(project)).output
    monkeypatch.setattr(cli, "plan_project_migration", _plan(project, files=False, manual=False))
    r = run("migrate", "flask", "-d", str(project))
    assert r.exit_code == 0 and "Nothing to change" in r.output


# ── outdated --safe, diff errors, ci-setup, ui ────────────────────────────────

def test_outdated_safe_statuses(project, monkeypatch):
    (project / "requirements.txt").write_text("a==1.0\nb==1.0\nc==1.0\nd==1.0\n")
    rows = {"a": {"safe": "2.0", "breaking_at_latest": 0}, "b": {"safe": "1.5", "breaking_at_latest": 3},
            "c": {"safe": "1.0", "breaking_at_latest": 1}, "d": {"safe": "1.0", "unchecked": True}}
    monkeypatch.setattr(cli, "outdated_status", lambda dep, *a, **k: {
        "name": dep.name, "current": "1.0", "latest": "2.0", "outdated": True, "major": True,
        "plan": rows[dep.name]})
    out = " ".join(run("outdated", "--safe", "-d", str(project)).output.split())
    assert "safe to upgrade" in out and "2.0 breaks 3 usages" in out and "2.0 breaks 1 usage" in out
    assert "couldn't be checked" in out and "not checked" in out and "4 outdated." in out


def test_outdated_all_current(project, monkeypatch):
    monkeypatch.setattr(cli, "outdated_status", lambda dep, *a, **k: {"name": dep.name, "current": "1",
                                                                     "latest": "1", "outdated": False})
    assert "All dependencies are up to date" in run("outdated", "-d", str(project)).output


def test_diff_rejects_bad_input(monkeypatch):
    r = run("diff", "flask", "[bold]x", "3.0")
    assert r.exit_code == 2 and "'[bold]x' is not a valid version string" in r.output

    def down(p):
        raise OSError("[red]offline")
    monkeypatch.setattr(cli, "get_available_versions", down)
    r = run("diff", "flask", "1.0", "2.0")
    assert r.exit_code == 2 and "[red]offline" in r.output
    monkeypatch.setattr(cli, "get_available_versions", lambda p: ["1.0"])
    r = run("diff", "flask", "1.0", "2.0")
    assert r.exit_code == 2 and "flask 2.0 not found on PyPI (to_version)" in r.output
    monkeypatch.setattr(cli, "get_available_versions", lambda p: ["1.0", "2.0"])
    monkeypatch.setattr(cli, "get_changes_between", lambda *a, **k: [])
    assert "No breaking/deprecated changes found between 1.0 and 2.0" in run("diff", "flask", "1.0", "2.0").output


def test_ci_setup_writes_once(tmp_path):
    r = run("ci-setup", "-d", str(tmp_path))
    assert (tmp_path / ".github" / "workflows" / "pyupcheck.yml").exists()
    assert (tmp_path / ".pre-commit-config.yaml").exists() and "pre-commit install" in r.output
    r = run("ci-setup", "-d", str(tmp_path))
    assert r.output.count("Already exists") == 2 and "Add this to your existing config" in r.output


def test_ui_without_textual(monkeypatch):
    real = builtins.__import__

    def no_tui(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "depshift" and fromlist and "tui" in fromlist:
            raise ImportError("no textual")
        return real(name, globals, locals, fromlist, level)
    monkeypatch.delitem(sys.modules, "depshift.tui", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_tui)
    r = run("ui")
    assert r.exit_code == 2 and 'pip install "pyupcheck[ui]"' in r.output


def test_cache_clear_and_completion(monkeypatch):
    monkeypatch.setattr(cli.cache_mod, "cache_clear", lambda: 3)
    assert "Removed 3 cached entries." in run("cache-clear").output
    for shell in ("bash", "zsh", "fish", "powershell"):
        assert "_PYUPCHECK_COMPLETE" in run("install-completion", shell).output
