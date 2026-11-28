"""The banner fits every terminal width and survives PyPI being down."""

import io
import os

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


@pytest.mark.parametrize("size", [(160, 50), (110, 40), (110, 16), (80, 24), (60, 40), (46, 30), (34, 20), (30, 12)])
@pytest.mark.parametrize("data", [DATA, None])
def test_live_frame_fits_the_window(size, data):
    width, height = size
    con = Console(file=io.StringIO(), width=width, height=height, color_system="truecolor", force_terminal=True,
                  record=True)
    for t in (0.0, 1.3, 4.9):
        con.print(w.live_frame(con, data, t))
        lines = con.export_text().splitlines()
        assert len(lines) <= height and max(map(len, lines)) <= width


def test_live_frame_drops_sections_when_short():
    tall = Console(file=io.StringIO(), width=110, height=45, record=True)
    tall.print(w.live_frame(tall, DATA, 1.0))
    short = Console(file=io.StringIO(), width=110, height=16, record=True)
    short.print(w.live_frame(short, DATA, 1.0))
    big = tall.export_text()
    assert "Recent releases" in big and "██████╗" in big
    text = short.export_text()
    assert "Recent releases" not in text and "Installed" in text and "▌ pyupcheck" in text


def test_not_live_without_a_terminal(monkeypatch):
    monkeypatch.setattr(w, "run_live", lambda c: pytest.fail("went full screen in a pipe"))
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: DATA)
    w.show_banner(Console(file=io.StringIO(), width=80), live=True)


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a Unix pseudo-terminal")
def test_key_reader_in_a_real_terminal(monkeypatch):
    """The full-screen banner's key reader, on a real pseudo-terminal: it
    switches the terminal to cbreak, sees a key press, and restores it."""
    import sys
    import termios
    master, slave = os.openpty()
    stdin = os.fdopen(slave, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    before = termios.tcgetattr(slave)
    try:
        with w._Keys() as keys:
            assert keys.pressed() is False
            os.write(master, b"q")
            assert keys.pressed() is True
            assert keys.pressed() is False  # the key was consumed
        assert termios.tcgetattr(slave) == before
    finally:
        stdin.close()
        os.close(master)


# ── PyPI data, the live loop and the first-run greeting ───────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_fetch_pypi_data_keeps_six_real_releases(monkeypatch):
    import depshift.http
    files = [{"upload_time": "2026-11-01T00:00:00", "yanked": False}]
    releases = {f"1.{i}.0": files for i in range(9)}
    releases.update({"2.0.0rc1": files, "1.9.0": [{"upload_time": "x", "yanked": True}], "junk": files, "0.1": []})
    monkeypatch.setattr(depshift.http, "http_get",
                        lambda url, timeout: _Resp({"info": {"version": "1.8.0"}, "releases": releases}))
    data = w._fetch_pypi_data()
    assert data["latest"] == "1.8.0" and [r["version"] for r in data["releases"]][:2] == ["1.8.0", "1.7.0"]
    assert len(data["releases"]) == 6 and data["releases"][0]["date"] == "2026-11-01"


def test_fetch_pypi_data_offline():
    assert w._fetch_pypi_data() == {"latest": None, "releases": []}  # the network is blocked in tests


def test_newer_ignores_junk():
    assert w._newer("2.0", "1.9") and not w._newer("junk", "1.0")


def test_run_live_until_a_key_then_leaves_a_static_copy(monkeypatch):
    presses = iter([False, False, True])

    class Keys:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def pressed(self):
            return next(presses)
    monkeypatch.setattr(w, "_Keys", Keys)
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: DATA)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    monkeypatch.setattr(w, "_can_go_live", lambda c: True)
    buf = io.StringIO()
    con = Console(file=buf, width=100, height=40, force_terminal=True, color_system=None)
    w.show_banner(con, live=True)
    assert "9.9.0" in buf.getvalue() and "pip install" in buf.getvalue()


def test_ctrl_c_ends_the_live_banner(monkeypatch):
    class Keys:
        def __enter__(self):
            raise KeyboardInterrupt

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(w, "_Keys", Keys)
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: DATA)
    assert w.run_live(Console(file=io.StringIO(), width=80)) == DATA


def test_first_run_greets_once(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "MARKER", tmp_path / "cache" / ".welcomed")
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: DATA)
    first, second = io.StringIO(), io.StringIO()
    w.show_if_first_run(Console(file=first, width=100))
    w.show_if_first_run(Console(file=second, width=100))
    assert "9.9.0" in first.getvalue() and second.getvalue() == ""


def test_first_run_with_a_read_only_home(monkeypatch, tmp_path):
    marker = tmp_path / "ro" / ".welcomed"
    monkeypatch.setattr(w, "MARKER", marker)
    monkeypatch.setattr(w, "_fetch_pypi_data", lambda: DATA)

    def denied(*a, **k):
        raise PermissionError
    monkeypatch.setattr(type(marker), "mkdir", denied)
    out = io.StringIO()
    w.show_if_first_run(Console(file=out, width=100))  # still greets; never crashes
    assert "9.9.0" in out.getvalue()
