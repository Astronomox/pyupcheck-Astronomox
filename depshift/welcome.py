"""Live animated banner — fetches real data from PyPI on every run."""

import time
from pathlib import Path
from depshift import __version__

MARKER = Path.home() / ".cache" / "pyupcheck" / ".welcomed"

ASCII_LOGO = """\
 ██████╗ ██╗   ██╗██╗   ██╗██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗
██╔══██╗╚██╗ ██╔╝██║   ██║██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝
██████╔╝ ╚████╔╝ ██║   ██║██████╔╝██║     ███████║█████╗  ██║     █████╔╝ 
██╔═══╝   ╚██╔╝  ██║   ██║██╔═══╝ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ 
██║        ██║   ╚██████╔╝██║     ╚██████╗██║  ██║███████╗╚██████╗██║  ██╗
╚═╝        ╚═╝    ╚═════╝ ╚═╝      ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝"""


def _fetch_pypi_data() -> dict:
    """Fetch live pyupcheck data from PyPI. Returns dict with version, releases, changelog."""
    try:
        import httpx
        resp = httpx.get("https://pypi.org/pypi/pyupcheck/json", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        info = data.get("info", {})
        releases = data.get("releases", {})

        from packaging.version import Version, InvalidVersion
        parsed = []
        for v in releases:
            try:
                parsed.append(Version(v))
            except InvalidVersion:
                pass
        parsed.sort(reverse=True)

        # build release list with upload dates
        release_list = []
        for v in parsed[:6]:
            vs = str(v)
            files = releases.get(vs, [])
            date = ""
            for f in files:
                upload = f.get("upload_time", "")
                if upload:
                    date = upload[:10]
                    break
            release_list.append({"version": vs, "date": date})

        return {
            "latest": info.get("version", __version__),
            "summary": info.get("summary", ""),
            "releases": release_list,
        }
    except Exception:
        return {"latest": __version__, "summary": "", "releases": []}


def _type_out(console, text: str, style: str = "", delay: float = 0.018):
    """Print text character by character with a typing effect."""
    import sys
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay + (0.04 if ch in ".,!:>" else 0))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _print_banner(console):
    # logo — print in bold cyan
    console.print(f"\n[bold cyan]{ASCII_LOGO}[/]\n")

    # tagline typed out
    _type_out(console, "  Know if a dependency upgrade will break your code before you run it.", delay=0.012)
    console.print()

    # live fetch with spinner
    with console.status("[dim]  Connecting to PyPI...[/]", spinner="dots2"):
        time.sleep(0.3)
        data = _fetch_pypi_data()

    latest  = data["latest"]
    releases = data["releases"]
    installed = __version__
    up_to_date = installed == latest

    # installed vs latest
    _type_out(console, f"  Installed  {installed}", delay=0.02)
    time.sleep(0.15)

    if up_to_date:
        _type_out(console, f"  Latest     {latest}  (you are up to date)", delay=0.02)
    else:
        _type_out(console, f"  Latest     {latest}  <- upgrade available", delay=0.02)

    time.sleep(0.1)
    _type_out(console, "  PyPI       https://pypi.org/project/pyupcheck", delay=0.015)
    _type_out(console, "  GitHub     https://github.com/Astronomox/pyupcheck-Astronomox", delay=0.015)
    _type_out(console, "  Author     AgbaDev (Astronomox)", delay=0.02)
    console.print()

    # release history streamed in
    if releases:
        _type_out(console, "  Fetching release history...", delay=0.02)
        time.sleep(0.4)

        for r in releases:
            v = r["version"]
            d = r["date"]
            is_latest   = v == latest
            is_installed = v == installed

            tag = ""
            if is_latest and is_installed:
                tag = "  [latest + installed]"
            elif is_latest:
                tag = "  [latest]"
            elif is_installed:
                tag = "  [installed]"

            bullet = ">" if is_latest else " "
            line = f"  {bullet} v{v:<10} {d}{tag}"
            _type_out(console, line, delay=0.008)
            time.sleep(0.06)

    console.print()

    if not up_to_date:
        _type_out(console, "  Run: pip install pyupcheck --upgrade", delay=0.018)
        console.print()

    _type_out(console, "  Run: pyupcheck --help  to get started.", delay=0.018)
    console.print()


def show_if_first_run(console):
    if MARKER.exists():
        return
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.touch()
    except Exception:
        pass
    _print_banner(console)


def show_banner(console):
    """Always show the live banner — used by pyupcheck banner."""
    _print_banner(console)
