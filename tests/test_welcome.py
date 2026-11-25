"""The banner fits every terminal width and survives PyPI being down."""

import io

import pytest
from rich.console import Console

from depshift import welcome as w

DATA = {"latest": "9.9.0", "releases": [{"version": f"9.{i}.0", "date": "2026-11-25"} for i in range(9, 3, -1)]}


def render(monkeypatch, width, data=DATA, encoding="utf-8"):
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: data)
    buf = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="replace")
    con = Console(file=buf, width=width, color_system=None, force_terminal=False)
    w._print_banner(con)
    buf.flush()
    return buf.buffer.getvalue().decode(encoding, errors="replace")


def test_logo_letters_line_up():
    rows = w.word_rows("PYUPCHECK")
    assert len(rows) == 6 and rows[0].startswith("██████╗ ██╗")  # no stray leading space
    assert {len(r) for r in [ "".join(w._GLYPHS[c][i] for c in "PYUPCHECK") for i in range(6)]} == {74}


@pytest.mark.parametrize("width,rows", [(120, 6), (76, 6), (75, 13), (42, 13), (41, 1), (20, 1)])
def test_logo_size_follows_width(width, rows):
    logo = w.logo_rows(width)
    assert len(logo) == rows and all(len(r) + w.INDENT <= width for r in logo)


@pytest.mark.parametrize("width", [120, 80, 64, 60, 50, 44, 40, 36, 30])
def test_nothing_wider_than_the_terminal(monkeypatch, width):
    out = render(monkeypatch, width)
    assert max(len(line) for line in out.splitlines()) <= width
    assert "9.9.0" in out and "pyupcheck check-all" in out


def test_wide_terminal_shows_everything(monkeypatch):
    out = render(monkeypatch, 100)
    assert "upgrade available" in out and "github.com/Astronomox/pyupcheck-Astronomox" in out
    assert "python -m pip install -U pyupcheck" in out and out.count("2026-11-25") == 6


def test_up_to_date_and_pypi_down(monkeypatch):
    monkeypatch.setattr(w, "__version__", "9.9.0")
    assert "up to date" in render(monkeypatch, 100)
    out = render(monkeypatch, 100, data={"latest": None, "releases": []})
    assert "couldn't reach PyPI" in out and "pip install" not in out and "Recent releases" not in out


def test_ascii_terminal(monkeypatch):
    out = render(monkeypatch, 100, encoding="cp437")
    assert "pyupcheck" in out and "?" not in out.split("Know")[0]
