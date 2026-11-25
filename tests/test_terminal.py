"""The terminal output: badges, the summary, and next steps."""

import io

from rich.console import Console

from depshift import cli


def result(pkg, breaking=0, deprecated=0, safe=0, warnings=(), unchecked=False, risks=()):
    return {"package": pkg, "current_version": "1.0", "target_version": "2.0", "risks": list(risks),
            "breaking_count": breaking, "deprecated_count": deprecated, "safe_count": safe,
            "warnings": list(warnings), "unchecked": unchecked}


def capture(monkeypatch, fn, *args, encoding="utf-8", **kw):
    buf = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="replace")
    con = Console(file=buf, width=100, color_system=None, force_terminal=False)
    monkeypatch.setattr(cli, "console", con)
    fn(*args, **kw)
    buf.flush()
    return buf.buffer.getvalue().decode(encoding, errors="replace")


RISK = {"file": "app.py", "line": 7, "code": "flask.Markup('x')", "severity": "breaking",
        "change_description": "Removed. :pr:`1`", "replacement": "markupsafe.Markup", "precise": True}


def test_package_line_has_a_badge_and_indented_note(monkeypatch):
    out = capture(monkeypatch, cli._print_terminal_result, result("flask", breaking=1, risks=[RISK]), False)
    assert "flask 1.0 → 2.0" in out and "✗ 1 BREAKING" in out
    assert "  ✗ app.py:7 verified  flask.Markup('x')" in out
    assert "    Removed." in out and ":pr:" not in out
    assert "    → use markupsafe.Markup" in out


def test_source_notes_said_once_in_the_summary(monkeypatch):
    w = ["could not read: GitHub releases (HTTP 403)"]
    results = [result("a", safe=2, warnings=w), result("b", breaking=1, warnings=w, risks=[RISK])]
    per_pkg = capture(monkeypatch, cli._print_terminal_result, results[0], False, hide_source_notes=True)
    assert "could not read" not in per_pkg
    out = capture(monkeypatch, cli._print_summary, results)
    assert "1 breaking · 0 deprecated · 1 safe across 2 packages" in out
    assert out.count("GitHub releases (HTTP 403)") == 1 and "for 2 packages" in out and "GITHUB_TOKEN" in out
    assert "pyupcheck migrate b 2.0" in out and "--write" not in out  # a safe first step: the diff


def test_all_safe_and_unchecked(monkeypatch):
    out = capture(monkeypatch, cli._print_summary, [result("a", safe=1), result("b", safe=3)])
    assert "Every checked package is safe to upgrade" in out
    out = capture(monkeypatch, cli._print_summary, [result("a", safe=1), result("b", unchecked=True)])
    assert "1 not checked" in out and "not counted as safe" in out and "Every checked" not in out


def test_plain_symbols_where_unicode_cant_print(monkeypatch):
    out = capture(monkeypatch, cli._print_terminal_result, result("flask", breaking=1, risks=[RISK]), False,
                  encoding="cp1252")
    assert "x 1 BREAKING" in out and "flask 1.0 -> 2.0" in out and "?" not in out.split("BREAKING")[0]
