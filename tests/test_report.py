"""Tests for HTML and Markdown report generation."""

import pytest

from depshift.report import render_markdown, render_html


def make_result(breaking=0, deprecated=0, safe=5, package="flask", risks=None):
    if risks is None:
        risks = []
        for i in range(breaking):
            risks.append({
                "file": f"src/file{i}.py", "line": i + 1,
                "code": f"flask.removed_func{i}()",
                "api": f"flask.removed_func{i}",
                "severity": "breaking",
                "change_kind": "removed",
                "change_description": f"Removed function removed_func{i}",
                "change_version": "2.0",
            })
        for i in range(deprecated):
            risks.append({
                "file": f"src/dep{i}.py", "line": i + 10,
                "code": f"flask.old_api{i}()",
                "api": f"flask.old_api{i}",
                "severity": "deprecated",
                "change_kind": "deprecated",
                "change_description": f"Deprecated old_api{i}",
                "change_version": "2.0",
            })
    return {
        "package": package,
        "current_version": "1.0",
        "target_version": "2.0",
        "risks": risks,
        "safe_count": safe,
        "breaking_count": breaking,
        "deprecated_count": deprecated,
    }


# ── markdown ──────────────────────────────────────────────────────────────────

def test_markdown_contains_package_name():
    md = render_markdown([make_result()])
    assert "flask" in md


def test_markdown_empty_results():
    md = render_markdown([])
    assert "pyupcheck report" in md
    assert "0" in md


def test_markdown_breaking_status():
    md = render_markdown([make_result(breaking=2)])
    assert "BREAKING" in md


def test_markdown_ok_status():
    md = render_markdown([make_result(breaking=0, deprecated=0)])
    assert "OK" in md


def test_markdown_deprecated_status():
    md = render_markdown([make_result(deprecated=1)])
    assert "DEPRECATED" in md


def test_markdown_pipe_chars_escaped():
    result = make_result(risks=[{
        "file": "a|b.py", "line": 1, "code": "x | y",
        "api": "pkg.f", "severity": "breaking",
        "change_kind": "removed",
        "change_description": "removed | this",
        "change_version": "2.0",
    }], breaking=1, safe=0)
    md = render_markdown([result])
    assert "\\|" in md


def test_markdown_multiple_packages():
    results = [make_result(package="flask"), make_result(package="requests")]
    md = render_markdown(results)
    assert "flask" in md
    assert "requests" in md
    assert "2" in md  # 2 packages checked


def test_markdown_has_table():
    md = render_markdown([make_result(breaking=1)])
    assert "|" in md
    assert "---" in md


def test_markdown_safe_count_shown():
    md = render_markdown([make_result(safe=13)])
    assert "13" in md


# ── html ──────────────────────────────────────────────────────────────────────

def test_html_valid_structure():
    html = render_html([make_result()])
    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "</html>" in html


def test_html_package_name_escaped():
    result = make_result(package="<script>alert(1)</script>")
    html = render_html([result])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_breaking_badge():
    html = render_html([make_result(breaking=1)])
    assert "BREAKING" in html


def test_html_ok_badge():
    html = render_html([make_result(breaking=0)])
    assert "OK" in html


def test_html_empty_results():
    html = render_html([])
    assert "<!DOCTYPE html>" in html
    assert "0" in html


def test_html_contains_table():
    html = render_html([make_result(breaking=1)])
    assert "<table" in html
    assert "</table>" in html


def test_html_risk_description_escaped():
    result = make_result(risks=[{
        "file": "a.py", "line": 1, "code": "f()",
        "api": "pkg.f", "severity": "breaking",
        "change_kind": "removed",
        "change_description": "<img src=x onerror=alert(1)>",
        "change_version": "2.0",
    }], breaking=1, safe=0)
    html = render_html([result])
    assert "<img" not in html
    assert "&lt;img" in html
