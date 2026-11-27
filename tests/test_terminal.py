"""The terminal output: badges, the summary, and next steps."""

import io

from rich.console import Console

from depshift import output


def result(pkg, breaking=0, deprecated=0, safe=0, warnings=(), unchecked=False, risks=()):
    return {"package": pkg, "current_version": "1.0", "target_version": "2.0", "risks": list(risks),
            "breaking_count": breaking, "deprecated_count": deprecated, "safe_count": safe,
            "warnings": list(warnings), "unchecked": unchecked}


def capture(monkeypatch, fn, *args, encoding="utf-8", **kw):
    buf = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="replace")
    con = Console(file=buf, width=100, color_system=None, force_terminal=False)
    monkeypatch.setattr(output, "console", con)
    fn(*args, **kw)
    buf.flush()
    return buf.buffer.getvalue().decode(encoding, errors="replace")


RISK = {"file": "app.py", "line": 7, "code": "flask.Markup('x')", "severity": "breaking",
        "change_description": "Removed. :pr:`1`", "replacement": "markupsafe.Markup", "precise": True}


def test_package_line_has_a_badge_and_indented_note(monkeypatch):
    out = capture(monkeypatch, output.print_result, result("flask", breaking=1, risks=[RISK]), False)
    assert "flask 1.0 → 2.0" in out and "✗ 1 BREAKING" in out
    assert "  ✗ app.py:7 verified  flask.Markup('x')" in out
    assert "    Removed." in out and ":pr:" not in out
    assert "    → use markupsafe.Markup" in out


def test_source_notes_said_once_in_the_summary(monkeypatch):
    w = ["could not read: GitHub releases (HTTP 403)"]
    results = [result("a", safe=2, warnings=w), result("b", breaking=1, warnings=w, risks=[RISK])]
    per_pkg = capture(monkeypatch, output.print_result, results[0], False, hide_source_notes=True)
    assert "could not read" not in per_pkg
    out = capture(monkeypatch, output.print_summary, results)
    assert "1 breaking · 0 deprecated · 1 safe across 2 packages" in out
    assert out.count("GitHub releases (HTTP 403)") == 1 and "for 2 packages" in out and "GITHUB_TOKEN" in out
    assert "pyupcheck migrate b 2.0" in out and "--write" not in out  # a safe first step: the diff


def test_all_safe_and_unchecked(monkeypatch):
    out = capture(monkeypatch, output.print_summary, [result("a", safe=1), result("b", safe=3)])
    assert "Every checked package is safe to upgrade" in out
    out = capture(monkeypatch, output.print_summary, [result("a", safe=1), result("b", unchecked=True)])
    assert "1 not checked" in out and "not counted as safe" in out and "Every checked" not in out


def test_plain_symbols_where_unicode_cant_print(monkeypatch):
    out = capture(monkeypatch, output.print_result, result("flask", breaking=1, risks=[RISK]), False,
                  encoding="cp1252")
    assert "x 1 BREAKING" in out and "flask 1.0 -> 2.0" in out and "?" not in out.split("BREAKING")[0]


# ── the check-all table and tidy notes ───────────────────────────────────────

def test_tidy_note():
    raw = ("* 🔥 Remove deprecated `openapi_prefix` from `FastAPI`. PR "
           "[#16518](https://github.com/fastapi/fastapi/pull/16518) by [@tiangolo](https://github.com/tiangolo).")
    assert output.tidy_note(raw) == "Remove deprecated `openapi_prefix` from `FastAPI`."
    assert output.tidy_note("**[sql] [usecase]** The ``x`` thing.") == "The `x` thing."
    long = "First sentence here. " + "word " * 80
    assert output.tidy_note(long, limit=40) == "First sentence here."
    assert output.tidy_note("word " * 80, limit=40).endswith("…")


def test_table_orders_by_attention_and_folds_up_to_date(monkeypatch):
    results = [result("aaa", safe=2), result("zzz", breaking=1, risks=[RISK]),
               dict(result("mid"), unchecked=True), dict(result("cur"), up_to_date=True),
               dict(result("unused"), no_usages=True)]
    out = capture(monkeypatch, output.print_table, results)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    order = [n for ln in lines for n in ("zzz", "mid", "aaa", "unused") if ln.startswith(n)]
    assert order == ["zzz", "mid", "aaa", "unused"]
    assert "1 breaking" in out and "not checked" in out and "not imported" in out
    assert "Already up to date (1): cur" in out and not any(ln.startswith("cur") for ln in lines)


def test_mentions_grouped_and_low_confidence_hidden(monkeypatch):
    note = "The `x` parameter in `F()` is deprecated."
    mention = lambda line, conf: {"file": "app.py", "line": line, "code": "F()", "severity": "warning",
                                  "change_description": note, "confidence": conf, "replacement": None}
    risks = [mention(1, "medium"), mention(2, "medium"), mention(2, "medium"), mention(9, "low")]
    out = capture(monkeypatch, output.print_findings, result("pkg", risks=risks))
    assert out.count(note) == 1 and "app.py:1, app.py:2" in out and "app.py:9" not in out
    assert "1 more line named in release notes" in out
    out = capture(monkeypatch, output.print_findings, result("pkg", risks=risks), output.View(verbose=True))
    assert "app.py:9" in out and "more line" not in out
