"""pyupcheck ui: a full-screen app for a project's dependency upgrades.

Every dependency is checked in the background and fills in its row as it
finishes. Select a package to read what breaks, with the release note and
the replacement; press m to preview what ``migrate`` would rewrite, and w in
the preview to apply it. Needs the optional ``textual`` dependency:
``pip install "pyupcheck[ui]"``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from depshift.output import diff_text
from depshift.safe import plain, plain_all

if TYPE_CHECKING:  # annotations only: cli imports this module lazily
    from depshift.project import FileChange, MigrationPlan

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
COLUMNS = ("package", "current", "latest", "status", "uses")
FILTER_LABEL = {"all": "all packages", "attention": "needs attention", "used": "used by your code"}


def status_of(r: dict | None) -> str:
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




def places(usages: list, risks: list[dict], root: str) -> list[dict]:
    """Each line of your code that uses the package once: file, line, code,
    and the worst finding on it ("breaking", "deprecated", "warning" or "")."""
    rank = {"breaking": 0, "deprecated": 1, "warning": 2}
    worst: dict[tuple, str] = {}
    for k in risks:
        key = (k["file"], k["line"])
        if key not in worst or rank[k["severity"]] < rank[worst[key]]:
            worst[key] = k["severity"]
    seen: dict[tuple, dict] = {}
    for u in usages:
        key = (u.file, u.line)
        if key not in seen:
            cell = getattr(u, "cell", None)
            seen[key] = {"file": plain(_rel(u.file, root)), "line": u.line, "cell": cell,
                         "code": plain(u.code or "", keep_newlines=False).strip(),
                         "severity": worst.get(key, "")}
    return sorted(seen.values(), key=lambda x: (x["file"], x["line"]))


_MARK = {"breaking": ("✗", "#FF6B6B"), "deprecated": ("!", "#F2CC60"), "warning": ("?", "#79C0FF"),
         "": ("·", "#8a8f98")}


def usage_summary(rows: list[dict] | None, limit_files: int = 8, lines_per_file: int = 3) -> list:
    """The "Where your code uses it" section: files by number of uses, with
    the first few lines of each."""
    parts: list = [Text(), Text("Where your code uses it", style="bold underline")]
    if rows is None:
        return [*parts, Text("Finding the places…", style="#8a8f98")]
    if not rows:
        return [*parts, Text("Nowhere: your code doesn't import it.", style="#8a8f98")]
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        by_file.setdefault(row["file"], []).append(row)
    files = sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    parts.append(Text(f"{len(rows)} line{'s' if len(rows) != 1 else ''} in {len(by_file)} "
                      f"file{'s' if len(by_file) != 1 else ''}", style="#8a8f98"))
    for path, lines in files[:limit_files]:
        parts += [Text(), Text.assemble((path, "bold"), (f"  {len(lines)}", "#8a8f98"))]
        shown = sorted(lines, key=lambda x: (x["severity"] == "", x["line"]))[:lines_per_file]
        for row in sorted(shown, key=lambda x: x["line"]):
            mark, colour = _MARK[row["severity"]]
            parts.append(_indent(Text.assemble((f"{mark} ", colour), (f"{row['line']:>4}  ", "#8a8f98"),
                                               (row["code"], "#E7E5E4"), overflow="ellipsis", no_wrap=True)))
        if len(lines) > lines_per_file:
            parts.append(_indent(Text(f"+{len(lines) - lines_per_file} more", style="#8a8f98"), 4))
    if len(files) > limit_files:
        parts.append(Text(f"\n+{len(files) - limit_files} more files", style="#8a8f98"))
    parts += [Text(), Text("Press u to see every place.", style="#5EEAD4")]
    return parts


def details(r: dict | None, name: str, root: str, show_low: bool = False,
            rows: list[dict] | None = None) -> Group:
    """What the right-hand pane shows for one package."""
    from depshift.output import distinct, is_major, tidy_note

    if r is None:
        return Group(Text(name, style="bold"), Text("Checking…", style="#8a8f98"), *usage_summary(rows))
    st = status_of(r)
    _, label, style = _STATUS[st]
    cur, new = r.get("current_version") or "?", r.get("target_version") or "?"
    head = Text.assemble((r["package"], "bold"), "  ", (cur, "#8a8f98"), " → ", (new, "bold"))
    if is_major(cur, new):
        head.append("  major", style="#F2CC60")
    parts: list[RenderableType] = [
        head, Text(label.format(n=r.get("breaking_count") or r.get("deprecated_count") or 0), style=style), Text()]

    if st == "current":
        parts.append(Text("You're on the latest version your constraints allow.", style="#8a8f98"))
    elif st == "unused":
        parts.append(Text("Your code doesn't import it, so upgrading can't break your code directly.",
                          style="#8a8f98"))
    elif st == "unchecked":
        parts.append(Text("No release notes or API could be read for this upgrade, so it is not "
                          "counted as safe.", style="#8a8f98"))
    else:
        n = len(rows) if rows is not None else _uses(r)
        parts.append(Text(f"{n} line{'s' if n != 1 else ''} in your code use{'s' if n == 1 else ''} it.",
                          style="#8a8f98"))

    risks = distinct(r.get("risks", []))
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
    groups: dict[str, list[dict]] = {}
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

    if hard:
        parts += [Text(), Text("Press m to preview what pyupcheck can fix automatically.", style="#5EEAD4")]
    if st != "unused":
        parts += usage_summary(rows)
    notes = list(r.get("warnings", []))
    if notes:
        parts += [Text(), Text("Notes", style="bold underline")]
        parts += [_indent(Text("• " + w, style="#8a8f98"), 0) for w in notes]
    return Group(*parts)


class UsagesScreen(ModalScreen):
    """Every line of your code that uses one package."""
    BINDINGS = [Binding("escape,q,u", "close", "Back")]

    def __init__(self, package: str, rows: list[dict]):
        super().__init__()
        self.package, self.rows = package, rows

    def compose(self) -> ComposeResult:
        files = len({r["file"] for r in self.rows})
        with Vertical(id="usages"):
            yield Static(Text.assemble((f"Where your code uses {self.package}", "bold"),
                                       (f"   {len(self.rows)} line{'s' if len(self.rows) != 1 else ''} in "
                                        f"{files} file{'s' if files != 1 else ''}", "#8a8f98")))
            yield DataTable(id="usage-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#usage-table", DataTable)
        width = max([len(r["file"]) for r in self.rows] + [4])
        table.add_column("", key="mark", width=1)
        table.add_column("File", key="file", width=min(width, 48))
        table.add_column("Line", key="line", width=6)
        table.add_column("Code", key="code")
        for i, r in enumerate(self.rows):
            mark, colour = _MARK[r["severity"]]
            line = f"cell {r['cell']}" if r.get("cell") is not None else str(r["line"])
            table.add_row(Text(mark, style=colour), r["file"], Text(line, justify="right"),
                          Text(r["code"], style="#E7E5E4"), key=str(i))
        table.focus()

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

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
        self.plan: MigrationPlan | None = None

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
            body.append(Text(plain(plan.error), style="#FF6B6B"))
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
                                              plain(fx.description)))
                if fc.notebook is not None:
                    diff = unified_diff(fc.rel, notebook.cells_text(fc.notebook, fc.before),
                                        notebook.cells_text(fc.notebook, fc.after))
                else:
                    diff = unified_diff(fc.rel, fc.before, fc.after)
                body.append(diff_text(diff))
            if plan.manual:
                n = len(plan.manual)
                body += [Text(), Text(f"{n} change{'s need' if n != 1 else ' needs'} you", style="bold #F2CC60")]
                for m in plan.manual:
                    body += [Text.assemble(("! ", "#F2CC60"), (plain(plan.where(m)), "bold"), "  ",
                                           plain(m.code, keep_newlines=False).strip()),
                             _indent(Text(plain(m.reason), style="#8a8f98"))]
            if not self.writable():
                if not plan.manual:
                    body.append(Text("Nothing to change: no breaking API change hits your code.",
                                     style="#7EE787"))
            else:
                n = len(self.writable())
                body += [Text(), Text(f"Press w to rewrite {n} file{'s' if n != 1 else ''}.", style="#5EEAD4")]
        self.query_one("#migrate-body", Static).update(Group(*body))

    def writable(self) -> list[FileChange]:
        return [fc for fc in (self.plan.files if self.plan else []) if not fc.error]

    def action_write(self) -> None:
        files = self.writable()
        if not files:
            self.notify("Nothing to apply.", severity="warning")
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            written, failed = 0, []
            for fc in files:
                try:
                    fc.write(self.root)
                    written += 1
                except OSError as e:
                    failed.append(plain(str(e)))
            if failed:
                self.notify("Not written: " + "; ".join(failed[:3]), severity="error", timeout=10)
            if written:
                self.notify(f"Rewrote {written} file{'s' if written != 1 else ''}.")
            self.dismiss(written > 0)

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
    MigrateScreen, ConfirmScreen, UsagesScreen { align: center middle; }
    #usages { width: 94%; height: 90%; border: round $primary; background: $surface; padding: 0 1; }
    #usages > Static { height: 1; }
    #migrate { width: 92%; height: 90%; border: round $primary; background: $surface; padding: 0 1; }
    #migrate-title { height: 1; }
    #confirm { width: auto; max-width: 80%; height: auto; border: round $warning; background: $surface;
               padding: 1 2; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("m", "migrate", "Preview fixes"),
        Binding("u", "usages", "Where used"),
        Binding("f", "filter", "Filter"),
        Binding("d", "deep", "Deep mode"),
        Binding("r", "recheck", "Re-check"),
        Binding("v", "toggle_low", "Low-confidence", show=False),
    ]

    def __init__(self, directory: str, discover: Callable[[], list], check: Callable,
                 planner: Callable, jobs: int = 8, deep: bool = False,
                 usages: Callable[[str], list] | None = None):
        super().__init__()
        self.directory = directory
        self.discover, self.check, self.planner = discover, check, planner
        self.usages_of = usages or (lambda package: [])
        self.usage_cache: dict[str, list] = {}
        self.loading_usages: set = set()
        self.jobs, self.deep = jobs, deep
        self.deps: list = []
        self.results: dict[str, dict | None] = {}
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
        self.add_columns(12)
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

    def add_columns(self, name_width: int) -> None:
        # fixed widths: Textual sizes a column from its first contents, so
        # "… checking" would cut off "✓ up to date" and long versions later
        table = self.query_one(DataTable)
        table.clear(columns=True)
        for label, width in (("Package", name_width), ("Current", 10), ("Latest", 12),
                             ("Status", 14), ("Uses", 5)):
            table.add_column(label, key=label.lower(), width=width)

    def start(self, deps: list) -> None:
        self.add_columns(min(max([len(d.name) for d in deps] + [7]) + 2, 30))
        self.deps = deps
        self.usage_cache.clear()  # files may have changed since the last check
        self.results = {d.name: None for d in deps}
        if not deps:
            self.query_one("#detail", Static).update(Text(
                "No dependency files found here (requirements*.txt, pyproject.toml, Pipfile, setup.cfg, "
                "setup.py, environment.yml).", style="#F2CC60"))
        self.refresh_table()
        self.update_status()

    def finish_one(self, name: str, r: dict | None) -> None:
        # release notes, versions and code lines: shown as text, never as
        # terminal control sequences
        r = plain_all(r) if r else r
        self.results[name] = r or {"package": name, "current_version": None, "target_version": "?",
                                   "risks": [], "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
                                   "unchecked": True, "warnings": ["no result"]}
        self.update_row(name)
        self.update_status()
        self.show_selected()

    def update_row(self, name: str) -> None:
        table = self.query_one(DataTable)
        if name in table.rows:
            for key, value in zip(COLUMNS, self.cells(name), strict=True):
                table.update_cell(name, key, value)

    def mark_pending(self, name: str) -> None:
        self.results[name] = None
        self.update_row(name)
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
        from depshift.output import is_major
        r = self.results.get(name)
        st = status_of(r)
        _, label, style = _STATUS[st]
        n = (r or {}).get("breaking_count") or (r or {}).get("deprecated_count") or 0
        if r is None:
            return [Text(name, style="bold"), Text(""), Text(""), Text(label, style=style), Text("")]
        cur, new = r.get("current_version") or "?", r.get("target_version") or "?"
        latest = Text(new, style="#F2CC60" if is_major(cur, new) else "")
        uses = "" if st in ("current", "unchecked") else ("—" if st == "unused" else str(_uses(r)))
        return [Text(name, style="bold"), Text(cur, style="#8a8f98"), latest,
                Text(label.format(n=n), style=style), Text(uses, justify="right")]

    def visible_names(self) -> list[str]:
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
        for name in self.visible_names():
            table.add_row(*self.cells(name), key=name)
        if selected and selected in table.rows:
            table.move_cursor(row=table.get_row_index(selected))
        self.show_selected()

    def selected_name(self) -> str | None:
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
        rows = self.usage_rows(name)
        self.query_one("#detail", Static).update(
            details(self.results.get(name), name, self.directory, self.show_low, rows))

    def usage_rows(self, name: str) -> list[dict] | None:
        """This package's places in your code, or None while they're found."""
        if name not in self.usage_cache:
            if name not in self.loading_usages:
                self.loading_usages.add(name)
                self.load_usages(name)
            return None
        r = self.results.get(name) or {}
        return places(self.usage_cache[name], r.get("risks", []), self.directory)

    @work(thread=True)
    def load_usages(self, name: str) -> None:
        try:
            found = self.usages_of(name)
        except Exception:
            found = []
        self.call_from_thread(self._usages_loaded, name, found)

    def _usages_loaded(self, name: str, found: list) -> None:
        self.usage_cache[name] = found
        self.loading_usages.discard(name)
        if self.selected_name() == name:
            self.show_selected()

    def action_usages(self) -> None:
        name = self.selected_name()
        if not name:
            return
        rows = self.usage_rows(name)
        if rows is None:
            self.notify(f"Still finding where {name} is used…")
        elif not rows:
            self.notify(f"Your code doesn't import {name}.")
        else:
            self.push_screen(UsagesScreen(name, rows))

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

        def after(changed: bool | None) -> None:
            if changed:
                self.recheck_one(name)

        self.push_screen(MigrateScreen(name, self.planner, self.directory), after)

    @work(thread=True)
    def recheck_one(self, name: str) -> None:
        dep = next((d for d in self.deps if d.name == name), None)
        if dep is None:
            return
        self.usage_cache.pop(name, None)
        self.call_from_thread(self.mark_pending, name)
        try:
            r = self._check_one(dep)
        except Exception as e:
            r = {"package": name, "current_version": None, "target_version": "?", "risks": [],
                 "safe_count": 0, "breaking_count": 0, "deprecated_count": 0, "unchecked": True,
                 "warnings": [f"check crashed ({type(e).__name__}: {e})"]}
        self.call_from_thread(self.finish_one, name, r)
        self.call_from_thread(self.refresh_table)


def run(directory: str, github_token: str | None = None, deep: bool = False, jobs: int = 8,
        exclude=()) -> None:
    from depshift.config import load_config
    from depshift.project import check_dependency, plan_project_migration, project_dependencies

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

    def usages(package):
        from depshift.names import import_names_for
        from depshift.scanner import scan_directory
        return scan_directory(directory, import_names_for(package), exclude_dirs=cfg.exclude_dirs)

    PyupcheckApp(directory, discover, check, planner, jobs=jobs, deep=deep, usages=usages).run()
