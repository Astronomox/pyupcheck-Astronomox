"""pyupcheck ui beyond the main flow: every status's details, mentions and
notes, the usage summary's limits, the keys (v, d, r, u, m) in each state,
failures while planning or writing, and how ``run`` wires the app up."""

import asyncio
import io
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from rich.console import Console
from test_tui import RESULTS, make_app, result, settle, text_of

from depshift import tui
from depshift.project import FileChange, MigrationPlan


def render(renderable, width=100) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, color_system=None)
    con.print(renderable)
    return con.export_text()


# ── the details pane ──────────────────────────────────────────────────────────

def test_details_for_every_status():
    assert "Checking…" in render(tui.details(None, "flask", "/"))
    assert "latest version your constraints allow" in render(tui.details(RESULTS["rich"], "rich", "/"))
    assert "doesn't import it" in render(tui.details(RESULTS["uvicorn"], "uvicorn", "/"))
    unchecked = result("x", unchecked=True, warnings=["could not read: GitHub releases (HTTP 403)"])
    out = render(tui.details(unchecked, "x", "/"))
    assert "not counted as safe" in out and "Notes" in out and "HTTP 403" in out
    major = render(tui.details(result("x", "1.0", "2.0"), "x", "/", rows=[]))
    assert "major" in major and "0 lines in your code use it" in major


def test_mentions_grouped_and_low_confidence_hidden():
    def mention(line, note, confidence="medium"):
        return {"file": "/p/a.py", "line": line, "code": "x.y()", "severity": "warning",
                "change_description": note, "confidence": confidence}
    risks = [mention(n, "``y`` changed behaviour.") for n in range(1, 9)] + [mention(20, "vague", "low")]
    r = result("x", risks=risks)
    out = render(tui.details(r, "x", "/p"))
    assert "Worth a look" in out and "+2 more" in out and "1 low-confidence mention hidden" in out
    assert "vague" not in out
    assert "vague" in render(tui.details(r, "x", "/p", show_low=True))


def test_deprecated_finding_shows_its_mark():
    risk = {"file": "/p/a.py", "line": 3, "code": "x.old()", "severity": "deprecated",
            "change_description": "``old`` is deprecated.", "replacement": "x.new"}
    out = render(tui.details(result("x", deprecated_count=1, risks=[risk]), "x", "/p"))
    assert "! a.py:3" in out and "→ use x.new" in out and "Press m" in out


def test_usage_summary_limits():
    assert "Finding the places" in render(tui.usage_summary(None)[-1])
    rows = [{"file": f"f{i % 10}.py", "line": i, "code": f"use({i})", "severity": ""} for i in range(50)]
    out = "\n".join(render(p) for p in tui.usage_summary(rows))
    assert "50 lines in 10 files" in out and "+2 more files" in out and "+2 more" in out


# ── keys and states ───────────────────────────────────────────────────────────

def _run(app, steps, size=(120, 30)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await settle(pilot, app)
            await steps(pilot, app)
    asyncio.run(go())


def test_low_confidence_deep_mode_and_recheck(tmp_path):
    app = make_app(tmp_path)
    calls = []
    original = app.check
    app.check = lambda dep, deep: calls.append(deep) or original(dep, deep)

    async def steps(pilot, app):
        await pilot.press("v")
        assert app.show_low
        await pilot.press("d")
        await settle(pilot, app)
        assert app.deep and "deep mode" in text_of(app, "#status")
        await pilot.press("r")
        await settle(pilot, app)
    _run(app, steps)
    assert True in calls and calls.count(True) >= 2 * len(RESULTS) - 1


def test_migrate_refuses_what_has_nothing_to_fix(tmp_path):
    notes = []

    async def steps(pilot, app):
        app.notify = lambda msg, **k: notes.append(msg)
        table = app.query_one("DataTable")
        for name in ("rich", "uvicorn"):
            table.move_cursor(row=table.get_row_index(name))
            await pilot.press("m")
        app.results["flask"] = None
        table.move_cursor(row=table.get_row_index("flask"))
        await pilot.press("m")
    _run(make_app(tmp_path), steps)
    assert "Nothing to fix for rich." in notes and "Nothing to fix for uvicorn." in notes
    assert "flask is still being checked." in notes


def test_where_used_states(tmp_path):
    notes = []

    async def steps(pilot, app):
        app.notify = lambda msg, **k: notes.append(msg)
        table = app.query_one("DataTable")
        table.move_cursor(row=table.get_row_index("uvicorn"))
        await pilot.pause(0.1)
        await pilot.press("u")
        app.usage_cache.pop("rich", None)
        app.loading_usages.add("rich")  # still being found
        table.move_cursor(row=table.get_row_index("rich"))
        await pilot.press("u")
    _run(make_app(tmp_path), steps)
    assert "Your code doesn't import uvicorn." in notes and "Still finding where rich is used…" in notes


def test_empty_filter_says_so(tmp_path):
    results = {"rich": RESULTS["rich"]}

    async def steps(pilot, app):
        await pilot.press("f")  # needs attention: nothing
        assert "Nothing in “needs attention”" in text_of(app, "#detail")
    _run(make_app(tmp_path, results=results), steps)


def test_a_crashing_check_is_shown_not_fatal(tmp_path):
    deps = [SimpleNamespace(name="boom")]

    def check(dep, deep):
        raise RuntimeError("kaput")
    app = tui.PyupcheckApp(str(tmp_path), lambda: deps, check, lambda p: None, jobs=1)

    async def steps(pilot, app):
        assert "check crashed (RuntimeError: kaput)" in text_of(app, "#detail")
        assert "1 not checked" in text_of(app, "#status")
    _run(app, steps)


def _plan(tmp_path, **kw):
    def planner(pkg):
        plan = MigrationPlan(pkg, str(tmp_path))
        plan.current, plan.target, plan.source = "2.2.5", "3.1.3", "pinned"
        for k, v in kw.items():
            setattr(plan, k, v)
        return plan
    return planner


def _migrate_body(tmp_path, planner, then=None):
    out = {}

    async def steps(pilot, app):
        table = app.query_one("DataTable")
        table.move_cursor(row=table.get_row_index("flask"))
        await pilot.press("m")
        await pilot.pause(0.2)
        out["body"] = text_of(app.screen, "#migrate-body")
        if then:
            await then(pilot, app)
    _run(make_app(tmp_path, planner=planner), steps)
    return out["body"]


def test_migrate_preview_states(tmp_path):
    assert "not found on PyPI" in _migrate_body(tmp_path, _plan(tmp_path, error="not found on PyPI"))
    assert "already the latest" in _migrate_body(tmp_path, _plan(tmp_path, up_to_date=True))
    manual = [SimpleNamespace(file=str(tmp_path / "a.py"), line=3, cell=None, code="flask.escape(x)",
                              reason="escape moved to markupsafe")]
    body = _migrate_body(tmp_path, _plan(tmp_path, manual=manual))
    assert "1 change needs you" in body and "escape moved to markupsafe" in body
    assert "Nothing to change" in _migrate_body(tmp_path, _plan(tmp_path))

    def broken(pkg):
        raise ConnectionError("offline")
    assert "Couldn't plan the fixes: offline" in _migrate_body(tmp_path, broken)


def test_migrate_skips_a_bad_rewrite_and_reports_a_failed_write(tmp_path):
    src = tmp_path / "app.py"
    src.write_text("x = 1\n")
    fix = SimpleNamespace(file=str(src), line=1, cell=None, description="x -> y")
    good = FileChange(str(src), "app.py", "x = 1\n", "y = 1\n", [fix])
    bad = FileChange(str(src), "bad.py", "a", "b(", [], error="the rewrite would not parse")
    notes = []

    def readonly(self, root=None):
        raise PermissionError("read-only")

    async def then(pilot, app):
        app.notify = lambda msg, **k: notes.append(msg)
        FileChange.write = readonly
        await pilot.press("w")
        await pilot.pause(0.05)
        await pilot.press("y")
        await pilot.pause(0.1)

    original = FileChange.write
    try:
        body = _migrate_body(tmp_path, _plan(tmp_path, files=[good, bad], has_fixes=True), then)
    finally:
        FileChange.write = original
    assert "skipped bad.py: the rewrite would not parse" in body and "Press w to rewrite 1 file." in body
    assert any(n.startswith("Not written: read-only") for n in notes)
    assert src.read_text() == "x = 1\n"


# ── run() ─────────────────────────────────────────────────────────────────────

def test_run_wires_the_project_in(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("flask==2.2.5\n")
    (tmp_path / "app.py").write_text("import flask\n")
    seen = {}

    def fake_run(self):
        seen["deps"] = [d.name for d in self.discover()]
        seen["usages"] = [u.line for u in self.usages_of("flask")]
        seen["app"] = self
    monkeypatch.setattr(tui.PyupcheckApp, "run", fake_run)
    import depshift.project as project
    monkeypatch.setattr(project, "check_dependency", lambda dep, locks, d, cfg, tok, deep: {"package": dep.name})
    monkeypatch.setattr(project, "plan_project_migration", lambda pkg, t, d, cfg, tok: "plan")
    tui.run(str(tmp_path), deep=True, exclude=("build",))
    app = seen["app"]
    assert seen["deps"] == ["flask"] and seen["usages"] == [1] and app.deep
    assert app.check(SimpleNamespace(name="flask"), False) == {"package": "flask"}
    assert app.planner("flask") == "plan"
