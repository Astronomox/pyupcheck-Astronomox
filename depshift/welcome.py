"""Show a welcome banner on first run."""

import os
from pathlib import Path

MARKER = Path.home() / ".cache" / "pyupcheck" / ".welcomed"

BANNER = """
[bold cyan] ██████╗ ██╗   ██╗██╗   ██╗██████╗  ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗[/]
[bold cyan]██╔══██╗╚██╗ ██╔╝██║   ██║██╔══██╗██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝[/]
[bold cyan]██████╔╝ ╚████╔╝ ██║   ██║██████╔╝██║     ███████║█████╗  ██║     █████╔╝ [/]
[bold cyan]██╔═══╝   ╚██╔╝  ██║   ██║██╔═══╝ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ [/]
[bold cyan]██║        ██║   ╚██████╔╝██║     ╚██████╗██║  ██║███████╗╚██████╗██║  ██╗[/]
[bold cyan]╚═╝        ╚═╝    ╚═════╝ ╚═╝      ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝[/]

[bold white]  Know if a dependency upgrade will break your code before you run it.[/]

  [dim]Version[/]   [green]0.3.2[/]
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
