"""pyupcheck ui, driven headless with fake checks (no network)."""

import asyncio
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from depshift import tui  # noqa: E402
from depshift.cli import FileChange, MigrationPlan  # noqa: E402


def result(pkg, cur="1.0", new="2.0", **kw):
    r = {"package": pkg, "current_version": cur, "target_version": new, "risks": [], "breaking_count": 0,
         "deprecated_count": 0, "safe_count": 3, "warnings": []}
    r.update(kw)
    return r


BREAK = {"file": "/proj/app.py", "line": 7, "code": "flask.Markup('x')", "severity": "breaking",
         "change_description": "Removed. Import `Markup` from `markupsafe` instead.",
         "replacement": "markupsafe.Markup", "precise": True}
RESULTS = {
    "requests": result("requests"),
    "flask": result("flask", "2.2.5", "3.1.3", breaking_count=1, risks=[BREAK]),
    "rich": result("rich", "15.0.0", "15.0.0", up_to_date=True),
    "uvicorn": result("uvicorn", no_usages=True),
}


def usage(file, line, code):
    return SimpleNamespace(file=file, line=line, code=code, cell=None)


USAGES = {
    "flask": [usage("/proj/app.py", 1, "import flask"), usage("/proj/app.py", 7, "flask.Markup('x')"),
              usage("/proj/app.py", 7, "flask.Markup"), usage("/proj/web/views.py", 3, "flask.Flask(__name__)")],
    "requests": [usage("/proj/api.py", 2, "requests.get(url)")],
}


def make_app(tmp_path, results=RESULTS, planner=None):
    deps = [SimpleNamespace(name=n) for n in results]
    return tui.PyupcheckApp(str(tmp_path), lambda: deps, lambda dep, deep: results[dep.name],
                            planner or (lambda pkg: None), jobs=2,
                            usages=lambda pkg: USAGES.get(pkg, []))


async def settle(pilot, app):
    for _ in range(50):
        await pilot.pause(0.02)
        if app.results and all(v is not None for v in app.results.values()):
            break
    await pilot.pause(0.05)


def text_of(app, selector):
    """The plain text a Static shows."""
    import io
    from rich.console import Console
    w = app.query_one(selector)
    content = getattr(w, "content", None)
    if content is None:
        content = getattr(w, "renderable", "")
    con = Console(file=io.StringIO(), width=200, record=True)
    con.print(content)
    return con.export_text()


def test_table_orders_attention_first_and_shows_details(tmp_path):
    async def go():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot, app)
            table = app.query_one("DataTable")
            order = [table.coordinate_to_cell_key((i, 0)).row_key.value for i in range(table.row_count)]
            assert order == ["flask", "requests", "uvicorn", "rich"]
            detail = text_of(app, "#detail")
            assert "What breaks" in detail and "app.py:7" in detail and "markupsafe.Markup" in detail
            status = text_of(app, "#status")
            assert "1 breaking" in status and "3 safe" in status
            await pilot.press("down")
            assert "lines in your code use it" in text_of(app, "#detail") or "line in your code uses it" in text_of(app, "#detail")
            await pilot.press("f")  # needs attention only
            assert table.row_count == 1
            await pilot.press("f")  # used by your code
            assert table.row_count == 2
    asyncio.run(go())


def test_narrow_window_stacks(tmp_path):
    async def go():
        app = make_app(tmp_path)
        async with app.run_test(size=(70, 30)) as pilot:
            await settle(pilot, app)
            assert app.query_one("#main").has_class("narrow")
    asyncio.run(go())


def test_migrate_preview_and_apply(tmp_path):
    src = tmp_path / "app.py"
    src.write_text("import flask\nx = flask.Markup('x')\n")
    after = "import flask\nimport markupsafe\nx = markupsafe.Markup('x')\n"

    def planner(pkg):
        plan = MigrationPlan(pkg, str(tmp_path))
        plan.current, plan.target, plan.source, plan.has_fixes = "2.2.5", "3.1.3", "pinned", True
        fix = SimpleNamespace(file=str(src), line=2, cell=None, description="flask.Markup -> markupsafe.Markup")
        plan.files = [FileChange(str(src), "app.py", src.read_text(), after, [fix])]
        return plan

    async def go():
        app = make_app(tmp_path, planner=planner)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("m")
            await pilot.pause(0.2)
            body = text_of(app.screen, "#migrate-body")
            assert "+x = markupsafe.Markup('x')" in body and "Press w to rewrite 1 file" in body
            await pilot.press("w")
            await pilot.pause(0.05)
            await pilot.press("n")  # declined: nothing written
            await pilot.pause(0.05)
            assert "flask.Markup" in src.read_text()
            await pilot.press("w")
            await pilot.pause(0.05)
            await pilot.press("y")
            await pilot.pause(0.2)
            assert src.read_text() == after
    asyncio.run(go())


def test_no_dependencies(tmp_path):
    async def go():
        app = tui.PyupcheckApp(str(tmp_path), lambda: [], lambda d, deep: None, lambda p: None)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause(0.2)
            assert "No dependency files found" in text_of(app, "#detail")
    asyncio.run(go())


def test_ui_command_without_textual(monkeypatch):
    import builtins
    from click.testing import CliRunner
    from depshift import cli
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "depshift" and a and a[2] and "tui" in a[2]:
            raise ImportError("no textual")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    r = CliRunner().invoke(cli.main, ["ui", "-d", os.getcwd()])
    assert r.exit_code == 2 and "pyupcheck[ui]" in r.output


def test_where_used_summary_and_full_list(tmp_path):
    async def go():
        app = make_app(tmp_path)
        app.directory = "/proj"
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            detail = text_of(app, "#detail")
            assert "Where your code uses it" in detail and "3 lines in 2 files" in detail
            assert "app.py  2" in detail and "✗    7  flask.Markup('x')" in detail
            await pilot.press("u")
            await pilot.pause(0.1)
            table = app.screen.query_one("#usage-table")
            assert table.row_count == 3  # line 7 once, though it uses flask twice
            await pilot.press("escape")
            await pilot.pause(0.05)
            await pilot.press("down", "down")  # uvicorn: not imported
            await pilot.pause(0.2)
            assert "doesn't import it" in text_of(app, "#detail")
    asyncio.run(go())


def test_status_column_fits_every_label(tmp_path):
    async def go():
        app = make_app(tmp_path)
        async with app.run_test(size=(140, 30)) as pilot:
            await settle(pilot, app)
            table = app.query_one("DataTable")
            widths = {c.key.value: c.width for c in table.columns.values()}
            assert widths["status"] >= len("! 99 deprecated") - 1 and widths["latest"] >= 10
    asyncio.run(go())
