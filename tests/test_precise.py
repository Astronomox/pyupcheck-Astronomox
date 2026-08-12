"""Tests for argument-level precise risk matching."""

import pytest

from depshift.scanner import Usage
from depshift.apisurface import APIChange
from depshift.precise import match_precise, _short_name, _usage_callable


def make_usage(attr_chain: str, kwargs=None, usage_type="function_call") -> Usage:
    return Usage(
        file="test.py", line=1,
        code=f"{attr_chain}()",
        attr_chain=attr_chain,
        usage_type=usage_type,
        kwargs=kwargs or [],
    )


def make_change(kind: str, api: str, param=None) -> APIChange:
    return APIChange(
        kind=kind, api=api,
        detail=f"{kind} {api}",
        param=param,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def test_short_name():
    assert _short_name("requests.auth.HTTPBasicAuth", "requests") == "HTTPBasicAuth"


def test_usage_callable():
    u = make_usage("requests.get")
    assert _usage_callable(u, "requests") == "get"


# ── removed function ──────────────────────────────────────────────────────────

def test_removed_function_flagged():
    usage = make_usage("requests.get")
    change = make_change("removed_function", "requests.get")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 1
    assert risks[0].severity == "breaking"


def test_removed_function_not_used_not_flagged():
    usage = make_usage("requests.post")
    change = make_change("removed_function", "requests.get")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 0


# ── removed parameter ─────────────────────────────────────────────────────────

def test_removed_param_flagged_when_kwarg_used():
    usage = make_usage("requests.get", kwargs=["url", "verify"])
    change = make_change("removed_param", "requests.get", param="verify")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 1
    assert risks[0].reason.startswith("You pass 'verify='")


def test_removed_param_not_flagged_when_kwarg_not_used():
    usage = make_usage("requests.get", kwargs=["url", "timeout"])
    change = make_change("removed_param", "requests.get", param="verify")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 0


def test_removed_param_not_flagged_when_no_kwargs():
    usage = make_usage("requests.get", kwargs=[])
    change = make_change("removed_param", "requests.get", param="verify")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 0


def test_multiple_removed_params_each_flagged():
    usage = make_usage("requests.get", kwargs=["verify", "cert", "timeout"])
    changes = [
        make_change("removed_param", "requests.get", param="verify"),
        make_change("removed_param", "requests.get", param="cert"),
    ]
    risks = match_precise([usage], changes, "requests")
    assert len(risks) == 2


# ── removed class ─────────────────────────────────────────────────────────────

def test_removed_class_flagged():
    usage = make_usage("requests.Session", usage_type="attribute_access")
    change = make_change("removed_class", "requests.Session")
    risks = match_precise([usage], [change], "requests")
    assert len(risks) == 1


# ── multiple usages ───────────────────────────────────────────────────────────

def test_multiple_usages_only_affected_flagged():
    usages = [
        make_usage("requests.get", kwargs=["verify"]),
        make_usage("requests.post", kwargs=["json"]),
        make_usage("requests.Session"),
    ]
    changes = [make_change("removed_param", "requests.get", param="verify")]
    risks = match_precise(usages, changes, "requests")
    assert len(risks) == 1
    assert risks[0].usage.attr_chain == "requests.get"


def test_no_changes_no_risks():
    usages = [make_usage("requests.get", kwargs=["verify"])]
    risks = match_precise(usages, [], "requests")
    assert len(risks) == 0


def test_no_usages_no_risks():
    changes = [make_change("removed_function", "requests.get")]
    risks = match_precise([], changes, "requests")
    assert len(risks) == 0
