"""Tests for changelog parsing."""

import pytest

from depshift.changelog import (
    parse_changelog_text,
    parse_sectioned_changelog,
    extract_github_repo,
)


# ── parse_changelog_text ──────────────────────────────────────────────────────

def test_detects_removed():
    entries = parse_changelog_text("- Removed `Session.get` method", "2.0")
    assert any(e.kind == "removed" for e in entries)


def test_detects_deprecated():
    entries = parse_changelog_text("- Deprecated `requests.packages` - use urllib3 instead", "2.0")
    assert any(e.kind == "deprecated" for e in entries)


def test_detects_renamed():
    entries = parse_changelog_text("- Renamed `old_func` to `new_func`", "2.0")
    assert any(e.kind == "renamed" for e in entries)


def test_extracts_version():
    entries = parse_changelog_text("- Removed `get` function", "3.1.0")
    assert all(e.version == "3.1.0" for e in entries)


def test_empty_text_returns_empty():
    entries = parse_changelog_text("", "2.0")
    assert entries == []


def test_no_breaking_text_returns_empty():
    entries = parse_changelog_text("Fixed a bug with SSL handshakes", "2.0")
    assert entries == []


def test_multiple_entries_in_one_block():
    text = """
- Removed `Session.get`
- Deprecated `packages.urllib3`
- Renamed `old_api` to `new_api`
"""
    entries = parse_changelog_text(text, "2.0")
    assert len(entries) >= 3


def test_extracts_api_name_from_backticks():
    entries = parse_changelog_text("- Removed `requests.get` in this version", "2.0")
    apis = [e.api for e in entries]
    assert any("requests" in a or "get" in a for a in apis)


def test_description_populated():
    entries = parse_changelog_text("- Removed `get` function from Session", "2.0")
    assert all(len(e.description) > 0 for e in entries)


# ── parse_sectioned_changelog ─────────────────────────────────────────────────

def test_sectioned_parses_version_headers():
    text = """
## 2.0.0

- Removed `old_func`

## 1.9.0

- Fixed a bug
"""
    entries = parse_sectioned_changelog(text, "mypkg")
    assert any(e.version == "2.0.0" for e in entries)


def test_sectioned_v_prefix_header():
    text = """
v3.0.0
------

- Deprecated `legacy_method`
"""
    entries = parse_sectioned_changelog(text, "mypkg")
    assert any(e.version == "3.0.0" for e in entries)


def test_sectioned_empty_returns_empty():
    entries = parse_sectioned_changelog("", "mypkg")
    assert entries == []


def test_sectioned_no_version_headers_returns_empty():
    text = "Just some text without version headers"
    entries = parse_sectioned_changelog(text, "mypkg")
    assert entries == []


# ── extract_github_repo ───────────────────────────────────────────────────────

def test_extracts_from_project_urls():
    info = {
        "info": {
            "project_urls": {"Homepage": "https://github.com/psf/requests"},
            "home_page": None,
        }
    }
    result = extract_github_repo(info)
    assert result == ("psf", "requests")


def test_extracts_from_home_page():
    info = {
        "info": {
            "project_urls": {},
            "home_page": "https://github.com/pallets/flask",
        }
    }
    result = extract_github_repo(info)
    assert result == ("pallets", "flask")


def test_strips_dot_git_suffix():
    info = {
        "info": {
            "project_urls": {"Source": "https://github.com/org/repo.git"},
            "home_page": None,
        }
    }
    result = extract_github_repo(info)
    assert result == ("org", "repo")


def test_no_github_url_returns_none():
    info = {
        "info": {
            "project_urls": {"Homepage": "https://example.com/myproject"},
            "home_page": None,
        }
    }
    result = extract_github_repo(info)
    assert result is None


def test_missing_project_urls_returns_none():
    info = {"info": {"project_urls": None, "home_page": None}}
    result = extract_github_repo(info)
    assert result is None
