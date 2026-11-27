"""pyupcheck ui: a full-screen app for a project's dependency upgrades.

Every dependency is checked in the background and fills in its row as it
finishes. Select a package to read what breaks, with the release note and
the replacement; press m to preview what ``migrate`` would rewrite, and w in
the preview to apply it. Needs the optional ``textual`` dependency:
``pip install "pyupcheck[ui]"``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from rich.console import Group
from rich.padding import Padding
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

PENDING = "pending"

# status: (order, label, style); what needs attention sorts first
_STATUS = {
    "breaking": (0, "✗ {n} breaking", "bold #FF6B6B"),
    "deprecated": (1, "! {n} deprecated", "#F2CC60"),
    "unchecked": (2, "? not checked", "#C4B5FD"),
    "safe": (3, "✓ safe", "#7EE787"),
    "unused": (4, "✓ safe", "#7EE787"),
    "current": (5, "✓ up to date", "#5EEAD4"),
    PENDING: (6, "… checking", "#8a8f98"),
}
FILTERS = ("all", "attention", "used")
FILTER_LABEL = {"all": "all packages", "attention": "needs attention", "used": "used by your code"}


def status_of(r: Optional[dict]) -> str:
    if r is None:
        return PENDING
    if r.get("up_to_date"):
        return "current"
    if r.get("breaking_count"):
        return "breaking"
    if r.get("deprecated_count"):
        return "deprecated"
    if r.get("unchecked") and not r.get("risks"):
        return "unchecked"
    if r.get("no_usages"):
        return "unused"
    return "safe"


def _uses(r: dict) -> int:
    return r.get("safe_count", 0) + len({(k["file"], k["line"]) for k in r.get("risks", [])})


def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _indent(text: Text, n: int = 2) -> Padding:
    """Indented as a block, so wrapped lines stay under the first one."""
    return Padding(text, (0, 0, 0, n))


def _diff_text(diff: str) -> Text:
    out = Text()
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = "bold #8a8f98"
        elif line.startswith("@@"):
            style = "#79C0FF"
        elif line.startswith("+"):
            style = "#7EE787"
        elif line.startswith("-"):
            style = "#FF6B6B"
        else:
            style = "#E7E5E4"
        out.append(line + "\n", style=style)
    return out


def details(r: Optional[dict], name: str, root: str, show_low: bool = False) -> Group:
    """What the right-hand pane shows for one package."""
    from depshift.cli import _distinct, _is_major, tidy_note

    if r is None:
        return Group(Text(name, style="bold"), Text("Checking…", style="#8a8f98"))
    st = status_of(r)
    order, label, style = _STATUS[st]
    cur, new = r.get("current_version") or "?", r.get("target_version") or "?"
    head = Text.assemble((r["package"], "bold"), "  ", (cur, "#8a8f98"), " → ", (new, "bold"))
    if _is_major(cur, new):
        head.append("  major", style="#F2CC60")
    parts = [head, Text(label.format(n=r.get("breaking_count") or r.get("deprecated_count") or 0),
                        style=style), Text()]

    if st == "current":
        parts.append(Text("You're on the latest version your constraints allow.", style="#8a8f98"))
    elif st == "unused":
        parts.append(Text("Your code doesn't import it, so upgrading can't break your code directly.",
                          style="#8a8f98"))
    elif st == "unchecked":
        parts.append(Text("No release notes or API could be read for this upgrade, so it is not "
                          "counted as safe.", style="#8a8f98"))
    else:
        parts.append(Text(f"{_uses(r)} place{'s' if _uses(r) != 1 else ''} in your code use it.",
                          style="#8a8f98"))

    risks = _distinct(r.get("risks", []))
    hard = [k for k in risks if k["severity"] != "warning"]
    if hard:
        parts += [Text(), Text("What breaks", style="bold underline")]
    for k in hard:
        mark, colour = ("✗", "#FF6B6B") if k["severity"] == "breaking" else ("!", "#F2CC60")
        line = Text.assemble((f"{mark} ", colour), (f"{_rel(k['file'], root)}:{k['line']}", "bold"))
        if k.get("precise"):
            line.append("  verified", style="#5EEAD4")
        parts += [Text(), line, _indent(Text(k["code"].strip(), style="#E7E5E4", overflow="ellipsis",
                                             no_wrap=True)),
                  _indent(Text(tidy_note(k["change_description"]), style="#8a8f98"))]
        if k.get("replacement"):
            parts.append(_indent(Text(f"→ use {k['replacement']}", style="#7EE787")))

    mentions = [k for k in risks if k["severity"] == "warning" and (show_low or k.get("confidence") != "low")]
    groups: Dict[str, List[dict]] = {}
    for k in mentions:
        groups.setdefault(tidy_note(k["change_description"]), []).append(k)
    if groups:
        parts += [Text(), Text("Worth a look", style="bold underline")]
    for note, ks in groups.items():
        where = sorted({(_rel(k["file"], root), k["line"]) for k in ks})
        places = ", ".join(f"{f}:{n}" for f, n in where[:6]) + (f" +{len(where) - 6} more" if len(where) > 6 else "")
        parts += [Text(), Text("? " + places, style="#79C0FF"), _indent(Text(note, style="#8a8f98"))]
    hidden = sum(1 for k in risks if k["severity"] == "warning" and k.get("confidence") == "low")
    if hidden and not show_low:
        parts += [Text(), Text(f"{hidden} low-confidence mention{'s' if hidden != 1 else ''} hidden "
                               "(press v to show)", style="#8a8f98")]

    notes = [w for w in r.get("warnings", [])]
    if notes:
        parts += [Text(), Text("Notes", style="bold underline")]
        parts += [_indent(Text("• " + w, style="#8a8f98"), 0) for w in notes]
    if hard:
        parts += [Text(), Text("Press m to preview what pyupcheck can fix automatically.", style="#5EEAD4")]
    return Group(*parts)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.question)
            yield Static(Text("y  yes    n  no", style="#8a8f98"))

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class MigrateScreen(ModalScreen[bool]):
    """The diff ``migrate`` would apply, and what needs a manual change."""
    BINDINGS = [Binding("w", "write", "Apply fixes"), Binding("escape,q", "close", "Back")]

    def __init__(self, package: str, planner: Callable, root: str):
        super().__init__()
        self.package, self.planner, self.root = package, planner, root
        self.plan = None

    def compose(self) -> ComposeResult:
        with Vertical(id="migrate"):
            yield Static(Text(f"Fixes for {self.package}", style="bold"), id="migrate-title")
            with VerticalScroll():
                yield Static(Text("Planning the fixes…", style="#8a8f98"), id="migrate-body")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            plan = self.planner(self.package)
        except Exception as e:  # network, a package without sources...
            self.app.call_from_thread(self.show_error, f"Couldn't plan the fixes: {e}")
            return
        self.app.call_from_thread(self.show, plan)

    def show_error(self, message: str) -> None:
        self.query_one("#migrate-body", Static).update(Text(message, style="#FF6B6B"))

    def show(self, plan) -> None:
        from depshift import notebook
        from depshift.rewrite import unified_diff

        self.plan = plan
        body: list = []
        if plan.error:
            body.append(Text(plan.error, style="#FF6B6B"))
        elif plan.up_to_date:
            body.append(Text(f"{self.package} {plan.current} is already the latest allowed version.",
                             style="#7EE787"))
        else:
            body.append(Text.assemble((f"{plan.current} → {plan.target}", "bold"),
                                      (f"   ({plan.source})", "#8a8f98")))
            for fc in plan.files:
                body.append(Text())
                if fc.error:
                    body.append(Text(f"skipped {fc.rel}: {fc.error}", style="#FF6B6B"))
                    continue
                for fx in fc.fixes:
                    body.append(Text.assemble(("fix ", "#7EE787"), (plan.where(fx), "bold"), "  ",
                                              fx.description))
                if fc.notebook is not None:
                    diff = unified_diff(fc.rel, notebook.cells_text(fc.notebook, fc.before),
                                        notebook.cells_text(fc.notebook, fc.after))
                else:
                    diff = unified_diff(fc.rel, fc.before, fc.after)
                body.append(_diff_text(diff))
            if plan.manual:
                n = len(plan.manual)
                body += [Text(), Text(f"{n} change{'s need' if n != 1 else ' needs'} you", style="bold #F2CC60")]
                for m in plan.manual:
                    body += [Text.assemble(("! ", "#F2CC60"), (plan.where(m), "bold"), "  ", m.code.strip()),
                             _indent(Text(m.reason, style="#8a8f98"))]
            if not self.writable():
                if not plan.manual:
                    body.append(Text("Nothing to change: no breaking API change hits your code.",
                                     style="#7EE787"))
            else:
                n = len(self.writable())
                body += [Text(), Text(f"Press w to rewrite {n} file{'s' if n != 1 else ''}.", style="#5EEAD4")]
        self.query_one("#migrate-body", Static).update(Group(*body))

    def writable(self) -> list:
        return [fc for fc in (self.plan.files if self.plan else []) if not fc.error]

    def action_write(self) -> None:
        files = self.writable()
        if not files:
            self.notify("Nothing to apply.", severity="warning")
            return

        def done(yes: Optional[bool]) -> None:
            if not yes:
                return
            for fc in files:
                fc.write()
            self.notify(f"Rewrote {len(files)} file{'s' if len(files) != 1 else ''}.")
            self.dismiss(True)

        names = ", ".join(fc.rel for fc in files[:3]) + (" …" if len(files) > 3 else "")
        self.app.push_screen(ConfirmScreen(f"Rewrite {len(files)} file(s)? {names}"), done)

    def action_close(self) -> None:
        self.dismiss(False)


class PyupcheckApp(App):
    """Browse a project's dependency upgrades and fix what breaks."""

    TITLE = "pyupcheck"
    CSS = """
    Screen { layout: vertical; }
    #main { height: 1fr; }
    #packages { width: 58%; min-width: 40; }
    #detail-scroll { width: 1fr; border-left: tall $primary 30%; padding: 0 1; }
    #main.narrow { layout: vertical; }
    #main.narrow #packages { width: 100%; height: 55%; }
    #main.narrow #detail-scroll { width: 100%; border-left: none; border-top: tall $primary 30%; }
    #status { height: 1; padding: 0 1; background: $panel; }
    MigrateScreen, ConfirmScreen { align: center middle; }
    #migrate { width: 92%; height: 90%; border: round $primary; background: $surface; padding: 0 1; }
    #migrate-title { height: 1; }
    #confirm { width: auto; max-width: 80%; height: auto; border: round $warning; background: $surface;
               padding: 1 2; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("m", "migrate", "Preview fixes"),
        Binding("f", "filter", "Filter"),
        Binding("d", "deep", "Deep mode"),
        Binding("r", "recheck", "Re-check"),
        Binding("v", "toggle_low", "Low-confidence", show=False),
    ]

    def __init__(self, directory: str, discover: Callable[[], List], check: Callable,
                 planner: Callable, jobs: int = 8, deep: bool = False):
        super().__init__()
        self.directory = directory
        self.discover, self.check, self.planner = discover, check, planner
        self.jobs, self.deep = jobs, deep
        self.deps: List = []
        self.results: Dict[str, Optional[dict]] = {}
        self.filter = "all"
        self.show_low = False
        self.generation = 0  # a re-check makes earlier results stale
        self.user_moved = False  # once you've picked a row, re-sorting keeps it

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield DataTable(id="packages", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-scroll"):
                yield Static(Text("Finding dependencies…", style="#8a8f98"), id="detail")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = os.path.basename(os.path.abspath(self.directory)) or self.directory
        table = self.query_one(DataTable)
        for col in ("Package", "Current", "Latest", "Status", "Uses"):
            table.add_column(col, key=col.lower())
        self.layout_for(self.size.width)
        self.run_checks()

    def on_resize(self, event) -> None:
        self.layout_for(event.size.width)

    def layout_for(self, width: int) -> None:
        self.query_one("#main").set_class(width < 100, "narrow")

    # ── checking ──────────────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="check")
    def run_checks(self) -> None:
        self.generation += 1
        gen = self.generation
        deps = self.discover()
        self.call_from_thread(self.start, deps)
        if not deps:
            return
        with ThreadPoolExecutor(max_workers=max(1, self.jobs)) as pool:
            futures = {pool.submit(self._check_one, d): d for d in deps}
            for fut, dep in futures.items():
                try:
                    r = fut.result()
                except Exception as e:  # one broken package must not sink the run
                    r = {"package": dep.name, "current_version": None, "target_version": "?", "risks": [],
                         "safe_count": 0, "breaking_count": 0, "deprecated_count": 0, "unchecked": True,
                         "warnings": [f"check crashed ({type(e).__name__}: {e})"]}
                if gen == self.generation:
                    self.call_from_thread(self.finish_one, dep.name, r)
        if gen == self.generation:
            self.call_from_thread(self.all_done)

    def _check_one(self, dep):
        return self.check(dep, self.deep)

    def start(self, deps: List) -> None:
        self.deps = deps
        self.results = {d.name: None for d in deps}
        if not deps:
            self.query_one("#detail", Static).update(Text(
                "No dependency files found here (requirements*.txt, pyproject.toml, Pipfile, setup.cfg, "
                "setup.py, environment.yml).", style="#F2CC60"))
        self.refresh_table()
        self.update_status()

    def finish_one(self, name: str, r: Optional[dict]) -> None:
        self.results[name] = r or {"package": name, "current_version": None, "target_version": "?",
                                   "risks": [], "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
                                   "unchecked": True, "warnings": ["no result"]}
        table = self.query_one(DataTable)
        if name in table.rows:
            for key, value in zip(("package", "current", "latest", "status", "uses"), self.cells(name)):
                table.update_cell(name, key, value)
        self.update_status()
        self.show_selected()

    def mark_pending(self, name: str) -> None:
        self.results[name] = None
        table = self.query_one(DataTable)
        if name in table.rows:
            for key, value in zip(("package", "current", "latest", "status", "uses"), self.cells(name)):
                table.update_cell(name, key, value)
        self.update_status()
        self.show_selected()

    def on_key(self, event) -> None:
        if event.key in ("up", "down", "pageup", "pagedown", "home", "end"):
            self.user_moved = True

    def all_done(self) -> None:
        self.refresh_table()  # final order: what needs attention first
        if not self.user_moved and self.query_one(DataTable).row_count:
            self.query_one(DataTable).move_cursor(row=0)
        self.update_status()

    # ── table ─────────────────────────────────────────────────────────────

    def cells(self, name: str) -> list:
        from depshift.cli import _is_major
        r = self.results.get(name)
        st = status_of(r)
        _, label, style = _STATUS[st]
        n = (r or {}).get("breaking_count") or (r or {}).get("deprecated_count") or 0
        if r is None:
            return [Text(name, style="bold"), Text(""), Text(""), Text(label, style=style), Text("")]
        cur, new = r.get("current_version") or "?", r.get("target_version") or "?"
        latest = Text(new, style="#F2CC60" if _is_major(cur, new) else "")
        uses = "" if st in ("current", "unchecked") else ("—" if st == "unused" else str(_uses(r)))
        return [Text(name, style="bold"), Text(cur, style="#8a8f98"), latest,
                Text(label.format(n=n), style=style), Text(uses, justify="right")]

    def visible(self) -> List[str]:
        names = [d.name for d in self.deps]
        if self.filter == "attention":
            names = [n for n in names if status_of(self.results.get(n)) in ("breaking", "deprecated", "unchecked")]
        elif self.filter == "used":
            names = [n for n in names if status_of(self.results.get(n)) in ("breaking", "deprecated", "safe")]
        return sorted(names, key=lambda n: (_STATUS[status_of(self.results.get(n))][0], n.lower()))

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        selected = self.selected_name()
        table.clear()
        for name in self.visible():
            table.add_row(*self.cells(name), key=name)
        if selected and selected in table.rows:
            table.move_cursor(row=table.get_row_index(selected))
        self.show_selected()

    def selected_name(self) -> Optional[str]:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None

    @on(DataTable.RowHighlighted)
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_selected()

    def show_selected(self) -> None:
        name = self.selected_name()
        if not name:
            if self.deps and self.filter != "all":
                self.query_one("#detail", Static).update(Text(
                    f"Nothing in “{FILTER_LABEL[self.filter]}”. Press f to change the filter.", style="#8a8f98"))
            return
        self.query_one("#detail", Static).update(
            details(self.results.get(name), name, self.directory, self.show_low))

    def update_status(self) -> None:
        done = [r for r in self.results.values() if r is not None]
        total = len(self.results)
        counts = {k: sum(1 for r in done if status_of(r) == k) for k in _STATUS}
        text = Text()
        if len(done) < total:
            text.append(f"Checking {len(done)}/{total}…  ", style="#5EEAD4")
        text.append(f"{counts['breaking']} breaking", style="bold #FF6B6B" if counts["breaking"] else "#8a8f98")
        text.append(" · ")
        text.append(f"{counts['deprecated']} deprecated", style="#F2CC60" if counts["deprecated"] else "#8a8f98")
        text.append(" · ")
        safe = counts["safe"] + counts["unused"] + counts["current"]
        text.append(f"{safe} safe", style="#7EE787")
        if counts["unchecked"]:
            text.append(f" · {counts['unchecked']} not checked", style="#C4B5FD")
        text.append(f"   showing {FILTER_LABEL[self.filter]}", style="#8a8f98")
        text.append("   deep mode" if self.deep else "", style="#5EEAD4")
        self.query_one("#status", Static).update(text)

    # ── actions ───────────────────────────────────────────────────────────

    def action_filter(self) -> None:
        self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
        self.refresh_table()
        self.update_status()

    def action_toggle_low(self) -> None:
        self.show_low = not self.show_low
        self.show_selected()

    def action_recheck(self) -> None:
        self.run_checks()

    def action_deep(self) -> None:
        self.deep = not self.deep
        self.notify("Deep mode on: comparing the real API of both versions (slower)." if self.deep
                    else "Deep mode off: release notes only.")
        self.run_checks()

    def action_migrate(self) -> None:
        name = self.selected_name()
        r = self.results.get(name) if name else None
        if not name:
            return
        if r is None:
            self.notify(f"{name} is still being checked.")
            return
        if status_of(r) in ("current", "unused"):
            self.notify(f"Nothing to fix for {name}.")
            return

        def after(changed: Optional[bool]) -> None:
            if changed:
                self.recheck_one(name)

        self.push_screen(MigrateScreen(name, self.planner, self.directory), after)

    @work(thread=True)
    def recheck_one(self, name: str) -> None:
        dep = next((d for d in self.deps if d.name == name), None)
        if dep is None:
            return
        self.call_from_thread(self.mark_pending, name)
        try:
            r = self._check_one(dep)
        except Exception as e:
            r = {"package": name, "current_version": None, "target_version": "?", "risks": [],
                 "safe_count": 0, "breaking_count": 0, "deprecated_count": 0, "unchecked": True,
                 "warnings": [f"check crashed ({type(e).__name__}: {e})"]}
        self.call_from_thread(self.finish_one, name, r)
        self.call_from_thread(self.refresh_table)


def run(directory: str, github_token: Optional[str] = None, deep: bool = False, jobs: int = 8,
        exclude=()) -> None:
    from depshift.cli import check_dependency, plan_project_migration, project_dependencies
    from depshift.config import load_config

    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    cfg.exclude_dirs.update(exclude)
    locks_box: dict = {}

    def discover():
        deps, locks = project_dependencies(directory, cfg)
        locks_box["locks"] = locks
        return deps

    def check(dep, deep_mode):
        return check_dependency(dep, locks_box.get("locks", {}), directory, cfg, github_token, deep_mode)

    def planner(package):
        return plan_project_migration(package, None, directory, cfg, github_token)

    PyupcheckApp(directory, discover, check, planner, jobs=jobs, deep=deep).run()
