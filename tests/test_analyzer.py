"""Tests for the changelog-based analyzer."""

import pytest

from depshift.scanner import Usage
from depshift.changelog import ChangeEntry
from depshift.analyzer import analyze, normalize_api, match_usage_to_change


def make_usage(attr_chain: str, usage_type: str = "function_call") -> Usage:
    return Usage(
        file="test.py", line=1, code=f"{attr_chain}()",
        attr_chain=attr_chain, usage_type=usage_type,
    )


def make_change(kind: str, api: str, version: str = "2.0") -> ChangeEntry:
    return ChangeEntry(
        kind=kind, api=api,
        description=f"{kind} {api}",
        version=version,
    )


# ── normalize_api ─────────────────────────────────────────────────────────────

def test_normalize_api_strips_parens():
    assert normalize_api("requests.get()", "requests") == "requests.get"


def test_normalize_api_lowercases():
    assert normalize_api("requests.get", "requests") == "requests.get"


def test_normalize_api_prepends_package():
    result = normalize_api("get", "requests")
    assert result == "requests.get"


# ── match_usage_to_change ─────────────────────────────────────────────────────

def test_direct_match_removed():
    usage = make_usage("requests.get")
    change = make_change("removed", "requests.get")
    risk = match_usage_to_change(usage, change, "requests")
    assert risk is not None
    assert risk.severity == "breaking"


def test_direct_match_deprecated():
    usage = make_usage("requests.packages")
    change = make_change("deprecated", "requests.packages")
    risk = match_usage_to_change(usage, change, "requests")
    assert risk is not None
    assert risk.severity == "deprecated"


def test_no_match_different_api():
    usage = make_usage("requests.get")
    change = make_change("removed", "requests.post")
    risk = match_usage_to_change(usage, change, "requests")
    assert risk is None


def test_child_match():
    usage = make_usage("requests.packages.urllib3")
    change = make_change("removed", "requests.packages")
    risk = match_usage_to_change(usage, change, "requests")
    assert risk is not None


# ── analyze ───────────────────────────────────────────────────────────────────

def test_analyze_returns_risks_and_safe():
    usages = [
        make_usage("requests.get"),
        make_usage("requests.post"),
        make_usage("requests.Session"),
    ]
    changes = [
        make_change("removed", "requests.get"),
    ]
    risks, safe = analyze(usages, changes, "requests")
    assert len(risks) == 1
    assert risks[0].usage.attr_chain == "requests.get"
    assert len(safe) == 2


def test_analyze_no_changes_all_safe():
    usages = [make_usage("requests.get"), make_usage("requests.post")]
    risks, safe = analyze(usages, [], "requests")
    assert len(risks) == 0
    assert len(safe) == 2


def test_analyze_sort_order():
    usages = [make_usage("flask.deprecated_thing"), make_usage("flask.removed_thing")]
    changes = [
        make_change("deprecated", "flask.deprecated_thing"),
        make_change("removed", "flask.removed_thing"),
    ]
    risks, _ = analyze(usages, changes, "flask")
    # breaking should come first
    assert risks[0].severity == "breaking"
