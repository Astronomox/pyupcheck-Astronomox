"""The pyupcheck banner: a logo that fits the terminal, plus live version info.

Three logo sizes, picked from the terminal's width when the banner is drawn:
the full block logo on one row, the same letters stacked over two rows, and
a one-line wordmark for phones and split panes. Terminals that can't print
block characters get plain ASCII. PyPI is asked in the background while the
logo is drawn, so the animation costs no extra time.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console, Group, RenderableType
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
# a solid grey, not "dim": dimmed text vanishes over terminal background images
MUTED = "#A1A1AA"


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
            return [*top, "", *bottom]
        return ["▌ pyupcheck"]
    return ["pyupcheck"]


def _mix(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b, strict=True))


def _blend(t: float, phase: float = 0.0, shine: float = 0.0) -> str:
    """Colour at position t (0..1). With a phase the colours flow around a loop
    (cyan, blue, lavender, blue, cyan) so the moving gradient never jumps."""
    t = min(max(t, 0.0), 1.0)
    if phase:
        loop: tuple[tuple[int, int, int], ...] = _GRADIENT + _GRADIENT[-2::-1]
        u = ((t * 0.5 + phase) % 1.0) * (len(loop) - 1)
        stops = loop
    else:
        u = t * (len(_GRADIENT) - 1)
        stops = _GRADIENT
    i = min(int(u), len(stops) - 2)
    rgb = _mix(stops[i], stops[i + 1], u - i)
    if shine:
        rgb = _mix(rgb, (0xFF, 0xFF, 0xFF), shine)
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def gradient(line: str, span: int, phase: float = 0.0, shine_at: float | None = None) -> Text:
    """Colour a logo row left to right, the same span for every row. `phase`
    moves the colours; `shine_at` (a column) adds a bright band that sweeps."""
    text = Text(" " * INDENT)
    for i, ch in enumerate(line):
        if ch == " ":
            text.append(ch)
            continue
        glow = 0.0
        if shine_at is not None:
            glow = max(0.0, 1.0 - abs(i - shine_at) / 4.0) * 0.75
        text.append(ch, style=f"bold {_blend(i / max(span - 1, 1), phase, glow)}")
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
        from packaging.version import InvalidVersion, Version

        from depshift.http import http_get

        resp = http_get("https://pypi.org/pypi/pyupcheck/json", timeout=5)
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


def info_block(console: Console, data: dict, width: int | None = None,
               releases_max: int = 6, commands: bool = True) -> Group:
    """Version, links and releases, laid out for the terminal's width."""
    width = width or console.width
    unicode_ok = _can_encode(console, "✓→●")
    ok, dot = ("✓", "●") if unicode_ok else ("ok", "*")
    installed, latest = __version__, data["latest"]

    narrow = width < 60  # short links and commands
    tiny = width < 44  # no label column
    if latest is None:
        latest_cell = Text("couldn't reach PyPI", style="yellow")
    elif _newer(latest, installed):
        latest_cell = (Text.assemble((latest, "bold #7EE787"), (" available", MUTED)) if tiny else
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
        grid.add_row(Text.assemble((installed, "bold"), (" installed", MUTED)))
        grid.add_row(latest_cell)
        for _, value in rows[2:4]:
            grid.add_row(value)
    else:
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(overflow="fold")
        for label, value in rows:
            grid.add_row(label, value)

    parts: list[RenderableType] = []
    if width >= 64:
        parts.append(Panel(grid, title="[bold]pyupcheck[/]", title_align="left",
                           border_style="#5EEAD4", padding=(0, 2), expand=False))
    else:
        parts.append(Padding(grid, (0, 0, 0, INDENT), expand=False))

    releases = data["releases"][: min(releases_max, 6 if width >= 64 else 3)]
    if releases:
        rel = Table.grid(padding=(0, 2))
        rel.add_column(no_wrap=True)
        rel.add_column(no_wrap=True, style="bold")
        if width >= 40:
            rel.add_column(no_wrap=True, style=MUTED)
        rel.add_column(overflow="fold")
        for r in releases:
            v = r["version"]
            tags = [t for t, on in (("latest", v == latest), ("installed", v == installed)) if on]
            row: list[RenderableType] = [Text(dot, style="#5EEAD4" if v == latest else MUTED), f"v{v}"]
            if width >= 40:
                row.append(r["date"])
            row.append(Text(" · ".join(tags), style="#7EE787" if "latest" in tags else "#79C0FF"))
            rel.add_row(*row)
        parts += [Text(), Padding(Text("Recent releases", style="bold"), (0, 0, 0, INDENT), expand=False),
                  Padding(rel, (0, 0, 0, INDENT + 1), expand=False)]

    if not commands:
        return Group(*parts)
    start = Table.grid(padding=(0, 2))
    start.add_column(style="bold #5EEAD4", overflow="fold")
    if width >= 64:
        start.add_column(style=MUTED, overflow="fold")
    suggestions: list[tuple[str, str]] = []
    if latest and _newer(latest, installed):
        suggestions.append(("pip install -U pyupcheck" if tiny else "python -m pip install -U pyupcheck",
                            "get the latest version"))
    suggestions += [("pyupcheck check-all", "check every dependency"),
                    ("pyupcheck check flask", "check one package"),
                    ("pyupcheck migrate numpy 2", "rewrite code that would break"),
                    ("pyupcheck --help", "all commands")]
    for cmd, what in suggestions:
        if width >= 64:
            start.add_row(cmd, what)
        else:
            start.add_row(cmd)
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
            with console.status(f"[{MUTED}]Checking PyPI…[/]", spinner="dots"):
                data = future.result()
    console.print(info_block(console, data))


TIPS = (
    "pyupcheck check-all --deep  compares the real API of both versions",
    "pyupcheck migrate numpy 2  shows the diff; add --write to apply it",
    "pyupcheck fix  moves every pin to the highest version that's safe",
    "pyupcheck watch  re-checks whenever a dependency changes",
    "pyupcheck review --base main  for pull requests, with a comment",
    "pyupcheck outdated --safe  shows how far each package can go",
    "Notebooks count: .ipynb cells are scanned and rewritten too",
)


class _Keys:
    """Any key pressed, without waiting for Enter (Windows and Unix)."""

    def __enter__(self):
        self._old = None
        if sys.platform != "win32":
            import termios
            import tty
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc):
        if self._old is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    def pressed(self) -> bool:
        if sys.platform == "win32":
            import msvcrt
            hit = False
            while msvcrt.kbhit():
                msvcrt.getwch()
                hit = True
            return hit
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            os.read(sys.stdin.fileno(), 64)
        return bool(ready)


def live_frame(console: Console, data: dict | None, t: float) -> RenderableType:
    """One frame of the full-screen banner, laid out for the terminal's current
    width *and* height: sections are dropped, smallest first, until it fits."""
    from rich.align import Align

    width, height = console.size
    unicode_ok = _can_encode(console, "█╗")
    # logo: shrink further when the window is short
    rows = logo_rows(width, unicode_ok)
    if len(rows) > 6 and height < 30:
        rows = ["▌ pyupcheck"] if unicode_ok else ["pyupcheck"]
    if len(rows) == 6 and height < 18:
        rows = ["▌ pyupcheck"] if unicode_ok else ["pyupcheck"]
    span = max(map(len, rows))
    phase = (t * 0.12) % 1.0
    sweep = (t % 3.2) / 2.2 * (span + 16) - 8  # a bright band crosses every 3.2 s
    logo = Group(*(gradient(r, span, phase or 1e-9, sweep) for r in rows))

    tip = TIPS[int(t // 5) % len(TIPS)]
    typed = tip[: int((t % 5) * 40)]
    cursor = "▌" if unicode_ok and int(t * 2) % 2 == 0 else " "
    footer = (Text.assemble(("press any key to exit", MUTED), ("  ·  ", MUTED),
                            ("resize the window: it follows", f"italic {MUTED}"))
              if width >= 60 else Text("any key: exit", style=MUTED))

    if data is None:
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(t * 12) % 10] if unicode_ok else "-\\|/"[int(t * 8) % 4]
        info = Text.assemble((spinner + " ", "#5EEAD4"), ("Checking PyPI…", MUTED))
        body_options: list[RenderableType] = [info]
    else:
        body_options = [info_block(console, data, width, 6, True), info_block(console, data, width, 3, True),
                        info_block(console, data, width, 3, False), info_block(console, data, width, 0, False)]

    def measure(renderable) -> int:
        return len(console.render_lines(renderable, console.options.update(width=width), pad=False))

    tagline = Text(TAGLINE, style="italic", justify="center")
    tip_line = Text.assemble((typed, "#79C0FF"), (cursor, "#5EEAD4"), justify="center")
    blank = Text(" ")  # an empty Text has no height once centred
    # most to least: everything; then fewer releases/commands; then no tip;
    # then no tagline; then logo and the essentials only
    Layout = tuple[list[RenderableType], RenderableType, list[RenderableType]]
    layouts: list[Layout] = [([logo, blank, tagline, blank], body, [blank, tip_line, blank, footer])
                             for body in body_options]
    layouts += [([logo, blank, tagline, blank], body_options[-1], [blank, footer]),
                ([logo, blank], body_options[-1], [blank, footer]),
                ([logo, blank], body_options[-1], [])]
    for head, body, tail in layouts:
        if sum(measure(Align.center(r)) for r in [*head, body, *tail]) <= height:
            break
    frame = Group(*(Align.center(r) for r in [*head, body, *tail]))
    return Align.center(frame, vertical="middle", height=height)


def run_live(console: Console) -> dict:
    """Full screen, animated, re-laid out on every resize, until a key is pressed.
    Returns the PyPI data so the caller can leave a static copy behind."""
    from rich.live import Live

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_fetch_pypi_data)

        def frame():
            data = future.result() if future.done() else None
            return live_frame(console, data, time.monotonic() - start)

        try:
            with _Keys() as keys, Live(get_renderable=frame, console=console, screen=True,
                                       refresh_per_second=20, redirect_stdout=False):
                while not keys.pressed():
                    time.sleep(1 / 60)
        except KeyboardInterrupt:
            pass
        return future.result()


def _can_go_live(console: Console) -> bool:
    import sys
    try:
        # the old Windows console host has no alternate screen: print once there
        return _animated(console) and not console.legacy_windows and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def show_if_first_run(console: Console) -> None:
    if MARKER.exists():
        return
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.touch()
    except Exception:
        pass
    _print_banner(console)


def show_banner(console: Console, live: bool = True) -> None:
    """`pyupcheck banner`: full screen and animated in an interactive terminal,
    then a static copy at the final window size is left in the scrollback."""
    if live and _can_go_live(console):
        data = run_live(console)
        _print_static(console, data)
    else:
        _print_banner(console)


def _print_static(console: Console, data: dict) -> None:
    rows = logo_rows(console.width, _can_encode(console, "█╗"))
    span = max(map(len, rows))
    console.print()
    for row in rows:
        console.print(gradient(row, span), soft_wrap=False, overflow="crop", crop=True)
    console.print()
    console.print(Padding(Text(TAGLINE, style="italic"), (0, 0, 0, INDENT), expand=False))
    console.print()
    console.print(info_block(console, data))
