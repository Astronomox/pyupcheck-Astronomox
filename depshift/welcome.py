"""Show a welcome banner on first run."""

from pathlib import Path
from depshift import __version__

MARKER = Path.home() / ".cache" / "pyupcheck" / ".welcomed"

BANNER = f"""
[bold cyan] ██████╗ ██╗   ██╗██╗   ██╗██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗[/]
[bold cyan]██╔══██╗╚██╗ ██╔╝██║   ██║██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝[/]
[bold cyan]██████╔╝ ╚████╔╝ ██║   ██║██████╔╝██║     ███████║█████╗  ██║     █████╔╝ [/]
[bold cyan]██╔═══╝   ╚██╔╝  ██║   ██║██╔═══╝ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ [/]
[bold cyan]██║        ██║   ╚██████╔╝██║     ╚██████╗██║  ██║███████╗╚██████╗██║  ██╗[/]
[bold cyan]╚═╝        ╚═╝    ╚═════╝ ╚═╝      ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝[/]

[bold white]  Know if a dependency upgrade will break your code before you run it.[/]

  [dim]Version[/]   [green]{__version__}[/]
  [dim]PyPI[/]      [blue]https://pypi.org/project/pyupcheck[/]
  [dim]GitHub[/]    [blue]https://github.com/Astronomox/pyupcheck-Astronomox[/]
  [dim]Author[/]    [cyan]AgbaDev (Astronomox)[/]
  [dim]Issues[/]    [blue]https://github.com/Astronomox/pyupcheck-Astronomox/issues[/]

[dim]  Run [bold]pyupcheck --help[/][dim] to get started.[/]
"""


def show_if_first_run(console):
    if MARKER.exists():
        return
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.touch()
    except Exception:
        pass
    console.print(BANNER)
