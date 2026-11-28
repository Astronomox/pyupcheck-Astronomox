"""How pyupcheck shows results: one console, one table style, badges, tidy
release notes, findings, the check-all table and summary, and reports.

Everything that reaches the terminal goes through ``esc`` or ``tidy_note``,
which strip control sequences from untrusted text (see safe.py)."""

import json as _json
import os
import re
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.text import Text

from depshift import __version__
from depshift.report import render_github_annotations, render_html, render_markdown, render_sarif


@dataclass(frozen=True)
class View:
    """How results are shown: ``verbose`` also lists low-confidence mentions
    (release notes that name an API you use without saying it changed);
    ``root`` is the project checked, which file paths are shown relative to."""
    verbose: bool = False
    root: str | None = None


DEFAULT_VIEW = View()


# highlight=False: pyupcheck picks its colours; rich's automatic highlighting
# split versions into coloured fragments (2.2 . 5) and recoloured code
console = Console(highlight=False)


# notes that must never mix into a report written to stdout (-f json)
err_console = Console(stderr=True, highlight=False)


def styled_table(*columns: str, **kwargs) -> Table:
    """A table in pyupcheck's one style: a rule under bold headers, no box."""
    from rich import box
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, header_style="bold", padding=(0, 1),
              **kwargs)
    for column in columns:
        t.add_column(column)
    return t


def print_stacked(items: list[tuple[str, str]]) -> None:
    """A table for a narrow terminal: each row as a heading line with its
    details indented under it, so nothing is cut off mid-word."""
    from rich.padding import Padding
    for head, detail in items:
        console.print(head, overflow="fold")
        if detail:
            console.print(Padding(detail, (0, 0, 0, 4)))


def esc(text: str) -> str:
    """Text shown as is: never read as rich markup ("[cell 3] x = a[0]"), and
    without terminal control sequences (it may come from release notes, PyPI
    or a repository you just cloned)."""
    from rich.markup import escape

    from depshift.safe import plain
    return escape(plain(text))


# bounded patterns: release notes are untrusted input, and an unbounded
# "[" or whitespace run made these quadratic
_MD_LINK = re.compile(r"\[([^\[\]\n]{1,300})\]\((?:https?://|#)[^()\s]{0,2000}\)")


_PR_CREDIT = re.compile(r"(?:(?<=\s)|^)\(?\b(?:PR|Pull request)\s+#?\d+(?:\s+by\s+@?[\w-]+)?\)?\.?\s*$",
                        re.IGNORECASE)


_LEAD_TAGS = re.compile(r"^\W*?(?:\*\*)?(?:\[[\w ]+\]\s*)+(?:\*\*)?\s*")


def tidy_note(text: str, limit: int = 240) -> str:
    """Release-note text as plain prose: no Markdown links, emoji, category
    tags, PR credits or reST roles, cut at a sentence (or word) boundary."""
    import unicodedata

    from depshift.review import _clean
    from depshift.safe import plain
    # a note is a sentence or two: cap the input before any pattern runs on it
    text = plain(text, keep_newlines=False)[: max(limit * 4, 2000)]
    text = _MD_LINK.sub(r"\1", text)
    text = _LEAD_TAGS.sub("", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Cn", "Co")
                   and ch not in "\ufe0f\u200d")
    text = text.replace("**", "")
    text = _clean(text).replace("\\|", "|").strip(" -*")
    text = _PR_CREDIT.sub("", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def clean_note(text: str) -> str:
    """Release-note text for the terminal: tidy, and nothing rich would read
    as a style tag."""
    from rich.markup import escape
    return escape(tidy_note(text))


def sym(fancy: str, plain: str) -> str:
    """A symbol the console can print: ✗ ✓ → where it can, x ok -> where not."""
    enc = (getattr(console.file, "encoding", None) or "utf-8").lower()
    try:
        fancy.encode(enc)
        return fancy
    except (UnicodeEncodeError, LookupError):
        return plain


def make_badge(text: str, style: str) -> str:
    return f"[{style}] {text} [/]"


def display_path(path: str, root: str | None = None) -> str:
    """A path as the user knows it: relative to the project checked (or to
    where pyupcheck runs, when that's shorter)."""
    options = []
    for base in (root, os.getcwd()):
        if base:
            try:
                options.append(os.path.relpath(path, base))
            except ValueError:
                pass
    return min(options, key=len) if options else path


def distinct(risks: list[dict]) -> list[dict]:
    """One finding per line and note: a line that uses an API three ways
    (``update(x).where(...)``) is one finding."""
    seen, out = set(), []
    for k in risks:
        key = (k["file"], k["line"], k["severity"], tidy_note(k["change_description"]))
        if key not in seen:
            seen.add(key)
            out.append(k)
    return out


def print_findings(r: dict, view: View = DEFAULT_VIEW) -> None:
    """Breaking and deprecated lines one by one; mentions ("worth a look")
    grouped by the release note, with where your code uses it."""
    from rich.padding import Padding

    arrow = sym("→", "->")
    risks = distinct(r["risks"])
    for risk in (k for k in risks if k["severity"] != "warning"):
        color = "red" if risk["severity"] == "breaking" else "yellow"
        mark = sym("✗", "x") if risk["severity"] == "breaking" else "!"
        if risk.get("precise"):
            tag = " [cyan]verified[/]"
        elif risk.get("confidence") == "low":
            tag = " [dim](low confidence)[/]"
        else:
            tag = ""
        location = f"{esc(display_path(risk['file'], view.root))}:{risk['line']}"
        code = esc(risk["code"].strip())
        head = Text.from_markup(f"  [{color}]{mark}[/] [bold]{location}[/]{tag}  {code}")
        if head.cell_len <= console.width:
            console.print(head, no_wrap=True)
        else:  # too long for one line: the code gets a line of its own instead of being cut off
            console.print(f"  [{color}]{mark}[/] [bold]{location}[/]{tag}", overflow="ellipsis", no_wrap=True)
            console.print(Padding(code, (0, 0, 0, 4)), overflow="ellipsis", no_wrap=True)
        console.print(Padding(f"[dim]{clean_note(risk['change_description'])}[/]", (0, 0, 0, 4)))
        if risk.get("replacement"):
            console.print(Padding(f"[green]{arrow} use {esc(risk['replacement'])}[/]", (0, 0, 0, 4)))

    mentions = [k for k in risks if k["severity"] == "warning"]
    shown = mentions if view.verbose else [k for k in mentions if k.get("confidence") != "low"]
    groups: dict[str, list[dict]] = {}
    for k in shown:
        groups.setdefault(tidy_note(k["change_description"]), []).append(k)
    for note, ks in groups.items():
        where = sorted({(display_path(k["file"], view.root), k["line"]) for k in ks})
        places = ", ".join(f"{f}:{n}" for f, n in where[:4])
        more = f" +{len(where) - 4} more" if len(where) > 4 else ""
        console.print(f"  [blue]?[/] [dim]worth a look,[/] {esc(places)}[dim]{more}[/]")
        console.print(Padding(f"[dim]{esc(note)}[/]", (0, 0, 0, 4)))
    hidden = len({(k["file"], k["line"]) for k in mentions}) - len({(k["file"], k["line"]) for k in shown})
    if hidden > 0 and not view.verbose:
        console.print(f"  [dim]{hidden} more line{'s' if hidden != 1 else ''} named in release notes "
                      f"without a stated change (-v to list)[/]")


def print_result(r: dict, quiet: bool, hide_source_notes: bool = False, view: View = DEFAULT_VIEW) -> None:
    """One package's result. ``hide_source_notes`` leaves out "could not
    read: ..." notes, for a caller that summarises them once."""
    from rich.padding import Padding

    arrow = sym("→", "->")
    cur = r.get("current_version") or "?"
    if r.get("up_to_date"):
        if not quiet:
            console.print(f"[bold]{r['package']}[/] {r['target_version']}  "
                          f"{make_badge(sym('✓', '') + ' UP TO DATE', 'black on green')}")
        return
    if r.get("no_usages"):
        if not quiet:
            console.print(f"[bold]{r['package']}[/] {cur} {arrow} {r['target_version']}  "
                          f"[dim]not used in your code, safe to upgrade[/]")
        return

    notes = [w for w in r.get("warnings", [])
             if not (hide_source_notes and w.startswith("could not read: "))]

    if r.get("unchecked") and not r["risks"]:
        console.print(f"[bold]{r['package']}[/] {cur} {arrow} {r['target_version']}  "
                      f"{make_badge('? NOT CHECKED', 'black on magenta')}")
        for w in notes:
            console.print(Padding(f"[dim]{esc(w)}[/]", (0, 0, 0, 4)))
        return

    via = " [dim](locked)[/]" if r.get("current_source") == "lockfile" else ""
    undeclared = " [magenta](undeclared)[/]" if r.get("undeclared") else ""
    header = f"[bold]{r['package']}[/]{undeclared} {cur}{via} [dim]{arrow}[/] {r['target_version']}"
    if r["breaking_count"]:
        badge = make_badge(f"{sym('✗', 'x')} {r['breaking_count']} BREAKING", "bold white on red")
        console.print(f"{header}  {badge}")
    elif r["deprecated_count"]:
        console.print(f"{header}  {make_badge('! ' + str(r['deprecated_count']) + ' DEPRECATED', 'black on yellow')}")
    else:
        if quiet:
            return
        safe = r["safe_count"]
        console.print(f"{header}  {make_badge(sym('✓', '') + ' OK', 'black on green')} "
                      f"[dim]{safe} usage{'s' if safe != 1 else ''} safe[/]")

    print_findings(r, view)
    if not quiet:
        for w in notes:
            console.print(Padding(f"[magenta]note:[/] [dim]{esc(w)}[/]", (0, 0, 0, 4)))


def status_row(r: dict):
    """(order, status text, style) for the results table."""
    if r.get("up_to_date"):
        return 5, sym("✓", "ok") + " up to date", "green"
    if r.get("unchecked") and not r["risks"]:
        return 2, "? not checked", "magenta"
    if r["breaking_count"]:
        return 0, f"{sym('✗', 'x')} {r['breaking_count']} breaking", "bold red"
    if r["deprecated_count"]:
        return 1, f"! {r['deprecated_count']} deprecated", "yellow"
    if r.get("no_usages"):
        return 4, sym("✓", "ok") + " safe", "green"
    return 3, sym("✓", "ok") + " safe", "green"


def is_major(cur: str | None, new: str | None) -> bool:
    if not cur or not new:
        return False
    try:
        from packaging.version import Version
        a, b = Version(cur), Version(new)
    except Exception:
        return False
    return b.major > a.major


def _code_cell(r: dict, view: View) -> str:
    """The "Your code" cell: how many places use the package."""
    if r.get("no_usages"):
        return "[dim]not imported[/]"
    if r.get("unchecked") and not r["risks"]:
        return "[dim]couldn't read its release notes[/]"
    uses = r.get("safe_count", 0) + len({(k["file"], k["line"]) for k in r["risks"]})
    cell = f"{uses} use{'s' if uses != 1 else ''}"
    looks = {tidy_note(k["change_description"]) for k in r["risks"]
             if k["severity"] == "warning" and (view.verbose or k.get("confidence") != "low")}
    return cell + (f" [blue]· {len(looks)} to look at[/]" if looks else "")


def _cell_width(markup: str) -> int:
    return Text.from_markup(markup).cell_len


def print_table(results: list[dict], view: View = DEFAULT_VIEW,
                labels: tuple[str, str] = ("Current", "Latest")) -> None:
    """Every package in one table, what needs attention first. Packages that
    are already up to date are listed on one line after it.

    The status is what the table is for, so it is never shortened. Every
    column is measured first, and the layout is the widest one that fits:
    with "Your code", then without it, then with the two version columns
    merged into one, then with only the package and its status (each
    package's details still name its versions). The package name takes what
    is left, cut with an ellipsis if it must be."""
    arrow = sym("→", "->")
    rows = []
    for r in sorted((r for r in results if not r.get("up_to_date")),
                    key=lambda r: (status_row(r)[0], r["package"].lower())):
        _, status, style = status_row(r)
        cur = esc(r.get("current_version") or "?")
        new = r.get("target_version") or "?"
        latest = f"[yellow]{esc(new)}[/] [dim]major[/]" if is_major(cur, new) else esc(new)
        name = f"[bold]{esc(r['package'])}[/]" + (" [magenta]*[/]" if r.get("undeclared") else "")
        rows.append({"name": name, "cur": cur, "latest": latest, "upgrade": f"{cur} {arrow} {latest}",
                     "status": f"[{style}]{status}[/]", "code": _code_cell(r, view)})
    if rows:
        def widest(key: str, header: str = "") -> int:
            return max(len(header), *(_cell_width(row[key]) for row in rows))

        layouts = [[("Package", "name"), (labels[0], "cur"), (labels[1], "latest"), ("Status", "status"),
                    ("Your code", "code")],
                   [("Package", "name"), (labels[0], "cur"), (labels[1], "latest"), ("Status", "status")],
                   [("Package", "name"), ("Upgrade", "upgrade"), ("Status", "status")],
                   [("Package", "name"), ("Status", "status")]]
        for columns in layouts:
            # columns are three cells apart (padding and the gap between them)
            others = sum(widest(key, header) for header, key in columns[1:]) + 3 * (len(columns) - 1)
            room = console.width - others
            if room >= 12 or columns is layouts[-1]:
                break
        table = styled_table(expand=False)
        for header, key in columns:
            if key == "name":
                table.add_column(header, no_wrap=True, overflow="ellipsis", max_width=max(6, min(30, room)))
            else:
                table.add_column(header, no_wrap=True, min_width=widest(key, header),
                                 style="dim" if key == "cur" else None)
        for row in rows:
            table.add_row(*(row[key] for _, key in columns))
        console.print(table)
    current = sorted(r["package"] for r in results if r.get("up_to_date"))
    if current:
        console.print(f"[green]{sym('✓', 'ok')}[/] [dim]Already up to date ({len(current)}):[/] "
                      f"[dim]{esc(', '.join(current))}[/]")
    if any(r.get("undeclared") for r in results):
        console.print("[magenta]*[/] [dim]imported by your code but not declared as a dependency[/]")


def needs_details(r: dict, view: View = DEFAULT_VIEW) -> bool:
    if r.get("up_to_date") or r.get("no_usages"):
        return False
    if r["breaking_count"] or r["deprecated_count"]:
        return True
    return any(k["severity"] == "warning" and (view.verbose or k.get("confidence") != "low")
               for k in r["risks"])


def print_summary(results: list[dict], elapsed: float | None = None) -> None:
    """The end of a check-all: totals, sources that couldn't be read (once,
    not under every package), and the commands that fix what can be fixed."""
    breaking = [r for r in results if r.get("breaking_count")]
    total_breaking = sum(r["breaking_count"] for r in results)
    total_deprecated = sum(r["deprecated_count"] for r in results)
    unchecked = [r for r in results if r.get("unchecked") and not r.get("risks")]
    safe = sum(1 for r in results if not r.get("breaking_count") and not r.get("deprecated_count")
               and not (r.get("unchecked") and not r.get("risks")))

    console.print()
    console.rule(style="dim")
    parts = [f"[bold red]{total_breaking} breaking[/]" if total_breaking else "[green]0 breaking[/]",
             f"[yellow]{total_deprecated} deprecated[/]" if total_deprecated else "[dim]0 deprecated[/]",
             f"[green]{safe} safe[/]"]
    if unchecked:
        parts.append(f"[magenta]{len(unchecked)} not checked[/]")
    dot = sym(" · ", " | ")
    took = f" in {elapsed:.1f}s" if elapsed is not None else ""
    console.print(dot.join(parts) + f" [dim]across {len(results)} package{'s' if len(results) != 1 else ''}{took}[/]")

    # "could not read: GitHub releases (HTTP 403)" under every package is
    # noise: say it once, with what to do about it
    failed: dict[str, int] = {}
    for r in results:
        for w in r.get("warnings", []):
            if w.startswith("could not read: "):
                for src in w[len("could not read: "):].split(", "):
                    failed[src] = failed.get(src, 0) + 1
    for src, n in sorted(failed.items(), key=lambda kv: -kv[1]):
        limited = "GitHub" in src and ("403" in src or "429" in src)
        hint = " Set GITHUB_TOKEN for GitHub's higher limits." if limited else ""
        packages = f"{n} package{'s' if n != 1 else ''}"
        console.print(f"[magenta]note:[/] [dim]couldn't read {esc(src)} for {packages}.{hint}[/]")
    if unchecked:
        console.print("[magenta]note:[/] [dim]packages that couldn't be checked are not counted as safe[/]")

    if breaking:
        console.print("\n[dim]Fix what can be fixed automatically (shows the diff first):[/]")
        for r in breaking:
            console.print(f"  [cyan]pyupcheck migrate {r['package']} {r['target_version']}[/]")
    elif not total_deprecated and not unchecked:
        console.print(f"[green]{sym('✓', 'OK:')} Every checked package is safe to upgrade.[/]")


def emit(results: list[dict], fmt: str, output: str | None, quiet: bool, view: View = DEFAULT_VIEW) -> None:
    """Results as a report (json, md, html, sarif, github) to a file or
    stdout, or for the terminal."""
    if fmt == "json":
        # the report's shape, for tools that read it: bumped on a breaking change
        text = _json.dumps({"schema_version": 1, "pyupcheck_version": __version__, "results": results}, indent=2)
    elif fmt == "md":
        text = render_markdown(results)
    elif fmt == "html":
        text = render_html(results)
    elif fmt == "sarif":
        text = render_sarif(results)
    elif fmt == "github":
        text = render_github_annotations(results)
    else:
        text = None

    if text is not None:
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(text)
            if not quiet:
                console.print(f"Report written to [bold]{output}[/]")
        else:
            print(text)
        return

    for r in results:
        print_result(r, quiet, view=view)


KIND_LABEL = {"removed": ("✗", "x", "removed", "red"), "deprecated": ("!", "!", "deprecated", "yellow"),
               "renamed": ("→", "->", "renamed", "blue"), "changed": ("~", "~", "changed", "magenta")}


def print_changes(package: str, from_version: str, to_version: str, changes: list) -> None:
    """Release-note changes grouped by release, newest first: each one once,
    as plain prose, labelled by kind."""
    from packaging.version import InvalidVersion, Version
    from rich.padding import Padding

    by_release: dict[str, list[tuple[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    for c in changes:
        note = tidy_note(c.description, 400)
        if note and (c.version, note) not in seen:
            seen.add((c.version, note))
            by_release.setdefault(c.version, []).append((c.kind, note))

    def order(v: str):
        try:
            return (1, Version(v))
        except InvalidVersion:
            return (0, Version("0"))

    counts: dict[str, int] = {}
    for items in by_release.values():
        for kind, _ in items:
            counts[kind] = counts.get(kind, 0) + 1
    summary = " · ".join(f"[{KIND_LABEL.get(k, ('', '', k, 'white'))[3]}]{n} {k}[/]"
                         for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    releases = f"{len(by_release)} release{'s' if len(by_release) != 1 else ''}"
    console.print(f"\n[bold]{esc(package)}[/] {esc(from_version)} {sym('→', '->')} {esc(to_version)}   "
                  f"{summary} [dim]in {releases}[/]")
    for release in sorted(by_release, key=order, reverse=True):
        console.print(f"\n[bold]{esc(release)}[/]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        for kind, note in by_release[release]:
            fancy, plain_mark, word, colour = KIND_LABEL.get(kind, ("·", "-", kind, "white"))
            grid.add_row(f"[{colour}]{sym(fancy, plain_mark)} {word:<10}[/]", clean_note(note))
        console.print(Padding(grid, (0, 0, 0, 2)))


def plural(n: int, word: str, many: str | None = None) -> str:
    """``3 files``, ``1 file`` (``many`` for irregular plurals)."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def print_watch_event(ev, directory: str, quiet: bool) -> bool:
    """Print one watcher event; True if it found something breaking."""
    import datetime as _dt
    stamp = f"[dim]{_dt.datetime.now().strftime('%H:%M:%S')}[/]"
    view = View(root=directory)
    if ev.kind == "error":
        where = esc(", ".join(ev.files) or "dependency files")
        console.print(f"{stamp} [yellow]{where}:[/] {esc(ev.message)} [dim](keeping the last good state)[/]")
        return False
    if ev.kind == "release":
        c = ev.changes[0]
        console.print(f"{stamp} [bold]New release:[/] {esc(c.name)} {esc(c.new)} "
                      f"[dim](you're on {esc(c.old)})[/]")
    else:
        moved = plural(len(ev.changes), "version") + " moved" if ev.changes else "no versions moved"
        console.print(f"{stamp} [bold]{esc(', '.join(ev.files))}[/] changed [dim]·[/] {moved}")
        if ev.added:
            console.print(f"  [green]+[/] added: {esc(', '.join(ev.added))} [dim](nothing to compare with)[/]")
        if ev.removed:
            console.print(f"  [red]-[/] removed: {esc(', '.join(ev.removed))}")
    breaking = False
    for r in ev.results:
        print_result(r, quiet, view=view)
        if r.get("breaking_count"):
            breaking = True
            if r.get("fixable"):
                console.print(f"    [cyan]pyupcheck migrate {esc(r['package'])} {esc(r['target_version'])}[/] "
                              f"[dim]fixes {r['fixable']} of these (shows the diff first)[/]")
    if ev.kind == "release" and ev.results and not breaking and not ev.results[0].get("unchecked"):
        console.print(f"  [green]{sym('✓', 'OK:')} Safe to upgrade:[/] nothing in your code breaks.")
    return breaking


def print_report(results: list[dict], quiet: bool = False, view: View = DEFAULT_VIEW,
                 elapsed: float | None = None, labels: tuple[str, str] = ("Current", "Latest")) -> None:
    """Several packages' results for the terminal: the table, details for
    what needs attention, and the summary (``check-all`` and ``review``).
    ``quiet`` prints only what breaks."""
    if quiet:
        for r in results:
            print_result(r, quiet, hide_source_notes=True, view=view)
        return
    print_table(results, view, labels)
    for r in sorted(results, key=lambda r: status_row(r)[0]):
        if needs_details(r, view):
            console.print()
            print_result(r, quiet, hide_source_notes=True, view=view)
    print_summary(results, elapsed=elapsed)

def diff_text(diff: str) -> Text:
    """A unified diff, coloured: removed lines red, added green, hunk headers
    blue; unchanged lines in the terminal's own colour (readable on light and
    dark themes)."""
    from depshift.safe import plain

    out = Text()
    for i, line in enumerate(diff.splitlines()):
        if i:
            out.append("\n")
        if line.startswith(("+++", "---")):
            style = "bold"
        elif line.startswith("@@"):
            style = "#79C0FF"
        elif line.startswith("+"):
            style = "#7EE787"
        elif line.startswith("-"):
            style = "#FF6B6B"
        else:
            style = ""
        out.append(plain(line), style=style)
    return out
