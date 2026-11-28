"""Regenerate docs/check-all.svg, the README's screenshot, from a real run.

    python scripts/screenshot.py

Builds a small demo project in a temporary folder (Flask and NumPy uses that
break on upgrade, requests and click that don't), runs ``pyupcheck
check-all --deep`` on it against live PyPI, and saves the terminal output
as SVG. Needs network access; set GITHUB_TOKEN (or sign in with gh) so the
note about GitHub's rate limit doesn't appear in the picture.
"""

import os
import sys
import tempfile

from click.testing import CliRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from depshift import cli, output  # noqa: E402

REQUIREMENTS = "flask==2.2.5\nnumpy==1.26.4\nrequests==2.31.0\nclick==8.1.7\n"
APP = '''"""A small report service."""
import click
import flask
import numpy as np
import requests

app = flask.Flask(__name__)


@app.route("/report")
def report():
    data = requests.get("https://example.com/data.json", timeout=10).json()
    values = np.array(data["values"], dtype=np.float_)
    return flask.Markup(f"<b>{values.mean():.2f}</b>")


@click.command()
def cli():
    print(np.cast["float64"]([1, 2]))
    session = requests.Session()
    click.echo(session.get("https://example.com", timeout=10).status_code)
'''


def main() -> None:
    with tempfile.TemporaryDirectory() as project:
        with open(os.path.join(project, "requirements.txt"), "w") as f:
            f.write(REQUIREMENTS)
        with open(os.path.join(project, "app.py"), "w") as f:
            f.write(APP)
        import io
        # recorded, not shown: colours are kept, the live progress spinner isn't drawn
        console = output.TidyConsole(width=96, record=True, file=io.StringIO(), force_terminal=False,
                                     color_system="truecolor", highlight=False)
        for module in (cli, output):
            module.console = console
            module.err_console = console
        # the elapsed time differs on every run: leave it out to keep the picture stable
        summary = output.print_summary
        output.print_summary = lambda results, elapsed=None: summary(results, None)
        console.print("[bold #5EEAD4]$[/] pyupcheck check-all --deep")
        cwd = os.getcwd()
        os.chdir(project)
        try:
            CliRunner().invoke(cli.main, ["check-all", "--deep", "--no-cache"], catch_exceptions=False)
        finally:
            os.chdir(cwd)
        svg = console.export_svg(title="pyupcheck check-all --deep")
    path = os.path.join(ROOT, "docs", "check-all.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
