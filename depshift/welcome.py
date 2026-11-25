"""The pyupcheck banner: a logo that fits the terminal, plus live version info.

Three logo sizes, picked from the terminal's width when the banner is drawn:
the full block logo on one row, the same letters stacked over two rows, and
a one-line wordmark for phones and split panes. Terminals that can't print
block characters get plain ASCII. PyPI is asked in the background while the
logo is drawn, so the animation costs no extra time.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from depshift import __version__

MARKER = Path.home() / ".cache" / "pyupcheck" / ".welcomed"
PYPI_URL = "https://pypi.org/project/pyupcheck"
GITHUB_URL = "https://github.com/Astronomox/pyupcheck-Astronomox"
TAGLINE = "Know if a dependency upgrade will break your code before you run it."

# ANSI Shadow letters, each six rows of equal width
_GLYPHS = {
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
}

# the website's terminal palette: cyan -> blue -> lavender
_GRADIENT = ((0x5E, 0xEA, 0xD4), (0x79, 0xC0, 0xFF), (0xC4, 0xB5, 0xFD))
INDENT = 2


def word_rows(word: str) -> list[str]:
    return ["".join(_GLYPHS[ch][row] for ch in word).rstrip() for row in range(6)]


def logo_rows(width: int, unicode_ok: bool = True) -> list[str]:
    """The biggest logo that fits in `width` columns (indent included)."""
    room = width - INDENT
    if unicode_ok:
        full = word_rows("PYUPCHECK")
        if max(map(len, full)) <= room:
            return full
        top, bottom = word_rows("PYUP"), word_rows("CHECK")
        if max(map(len, top + bottom)) <= room:
            return top + [""] + bottom
        return ["▌ pyupcheck"]
    return ["pyupcheck"]


def _blend(t: float) -> str:
    t = min(max(t, 0.0), 1.0) * (len(_GRADIENT) - 1)
    i = min(int(t), len(_GRADIENT) - 2)
    f = t - i
    a, b = _GRADIENT[i], _GRADIENT[i + 1]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def gradient(line: str, span: int) -> Text:
    """Colour a logo row left to right, the same span for every row."""
    text = Text(" " * INDENT)
    for i, ch in enumerate(line):
        text.append(ch, style=f"bold {_blend(i / max(span - 1, 1))}" if ch != " " else "")
    return text


def _can_encode(console: Console, s: str) -> bool:
    try:
        s.encode(console.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _fetch_pypi_data() -> dict:
    """Latest version and the six newest releases with their dates."""
    try:
        import httpx
        from packaging.version import InvalidVersion, Version

        resp = httpx.get("https://pypi.org/pypi/pyupcheck/json", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        parsed = []
        for v, files in data.get("releases", {}).items():
            try:
                ver = Version(v)
            except InvalidVersion:
                continue
            if files and not ver.is_prerelease and not all(f.get("yanked") for f in files):
                date = next((f["upload_time"][:10] for f in files if f.get("upload_time")), "")
                parsed.append((ver, date))
        parsed.sort(reverse=True)
        return {"latest": data.get("info", {}).get("version", __version__),
                "releases": [{"version": str(v), "date": d} for v, d in parsed[:6]]}
    except Exception:
        return {"latest": None, "releases": []}


def _newer(a: str, b: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return False


def _animated(console: Console) -> bool:
    return (console.is_terminal and not console.is_dumb_terminal
            and not os.environ.get("CI") and not os.environ.get("PYUPCHECK_NO_ANIMATION"))


def _short(url: str) -> str:
    return url.split("://", 1)[1]


def info_block(console: Console, data: dict) -> Group:
    """Version, links and releases, laid out for the terminal's width."""
    width = console.width
    unicode_ok = _can_encode(console, "✓→●")
    ok, arrow, dot = ("✓", "→", "●") if unicode_ok else ("ok", "->", "*")
    installed, latest = __version__, data["latest"]

    narrow = width < 60  # short links and commands
    tiny = width < 44  # no label column
    if latest is None:
        latest_cell = Text("couldn't reach PyPI", style="yellow")
    elif _newer(latest, installed):
        latest_cell = (Text.assemble((latest, "bold #7EE787"), (" available", "dim")) if tiny else
                       Text.assemble((latest, "bold #7EE787"), "  ", ("upgrade available", "black on #FDE047")))
    else:
        latest_cell = Text.assemble((latest, "bold"), "  ", (f"{ok} up to date", "#7EE787"))
    pypi = Text("pypi.org/p/pyupcheck" if narrow else _short(PYPI_URL), style=f"link {PYPI_URL} #79C0FF")
    github = Text("GitHub repo" if width < 36 else "Astronomox/pyupcheck-Astronomox" if narrow
                  else _short(GITHUB_URL),
                  style=f"link {GITHUB_URL} #79C0FF")
    rows = [("Installed", Text(installed, style="bold")), ("Latest", latest_cell),
            ("PyPI", pypi), ("GitHub", github), ("Author", Text("AgbaDev (Astronomox)"))]

    grid = Table.grid(padding=(0, 2))
    if tiny:  # one column: values only, each says what it is
        grid.add_column(overflow="fold")
        grid.add_row(Text.assemble((installed, "bold"), (" installed", "dim")))
        grid.add_row(latest_cell)
        for _, value in rows[2:4]:
            grid.add_row(value)
    else:
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(overflow="fold")
        for label, value in rows:
            grid.add_row(label, value)

    parts = []
    if width >= 64:
        parts.append(Panel(grid, title="[bold]pyupcheck[/]", title_align="left",
                           border_style="#5EEAD4", padding=(0, 2), expand=False))
    else:
        parts.append(Padding(grid, (0, 0, 0, INDENT), expand=False))

    releases = data["releases"][: 6 if width >= 64 else 3]
    if releases:
        rel = Table.grid(padding=(0, 2))
        rel.add_column(no_wrap=True)
        rel.add_column(no_wrap=True, style="bold")
        if width >= 40:
            rel.add_column(no_wrap=True, style="dim")
        rel.add_column(overflow="fold")
        for r in releases:
            v = r["version"]
            tags = [t for t, on in (("latest", v == latest), ("installed", v == installed)) if on]
            row = [Text(dot, style="#5EEAD4" if v == latest else "dim"), f"v{v}"]
            if width >= 40:
                row.append(r["date"])
            row.append(Text(" · ".join(tags), style="#7EE787" if "latest" in tags else "#79C0FF"))
            rel.add_row(*row)
        parts += [Text(), Padding(Text("Recent releases", style="bold"), (0, 0, 0, INDENT), expand=False),
                  Padding(rel, (0, 0, 0, INDENT + 1), expand=False)]

    start = Table.grid(padding=(0, 2))
    start.add_column(style="bold #5EEAD4", overflow="fold")
    if width >= 64:
        start.add_column(style="dim", overflow="fold")
    commands = []
    if latest and _newer(latest, installed):
        commands.append(("pip install -U pyupcheck" if tiny else "python -m pip install -U pyupcheck",
                         "get the latest version"))
    commands += [("pyupcheck check-all", "check every dependency"),
                 ("pyupcheck check flask", "check one package"),
                 ("pyupcheck migrate numpy 2", "rewrite code that would break"),
                 ("pyupcheck --help", "all commands")]
    for cmd, what in commands:
        start.add_row(cmd, what) if width >= 64 else start.add_row(cmd)
    parts += [Text(), Padding(Text("Get started", style="bold"), (0, 0, 0, INDENT), expand=False),
              Padding(start, (0, 0, 0, INDENT + 1), expand=False), Text()]
    return Group(*parts)


def _print_banner(console: Console) -> None:
    animate = _animated(console)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_fetch_pypi_data)  # runs while the logo is drawn

        rows = logo_rows(console.width, _can_encode(console, "█╗"))
        span = max(map(len, rows))
        console.print()
        for row in rows:
            console.print(gradient(row, span), soft_wrap=False, overflow="crop", crop=True)
            if animate:
                time.sleep(0.035)
        console.print()
        console.print(Padding(Text(TAGLINE, style="italic"), (0, 0, 0, INDENT), expand=False))
        console.print()

        if future.done() or not animate:
            data = future.result()
        else:
            with console.status("[dim]Checking PyPI…[/]", spinner="dots"):
                data = future.result()
    console.print(info_block(console, data))


def show_if_first_run(console: Console) -> None:
    if MARKER.exists():
        return
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.touch()
    except Exception:
        pass
    _print_banner(console)


def show_banner(console: Console) -> None:
    """Always show the live banner: used by `pyupcheck banner`."""
    _print_banner(console)
