"""Finding the highest version that breaks none of your code."""

import pytest

import depshift.cli as cli
from depshift.config import load_config

RELEASES = ["1.0.0", "1.0.1", "1.1.0", "1.1.4", "2.0.0", "2.0.3", "2.1.0", "3.0.0rc1",
            "3.0.0", "3.1.0", "3.1.2", "3.2.0"]


@pytest.fixture
def pkg(monkeypatch):
    calls = []
    yanked = {"2.1.0"}

    def info(name):
        return {"releases": {v: [{"yanked": v in yanked}] for v in RELEASES}}

    def check(package, target, *a, **k):
        calls.append(target)
        broken = target.startswith(("3.1", "3.2"))
        return {"breaking_count": 4 if broken else 0, "risks": [], "unchecked": target in unchecked}

    unchecked = set()
    monkeypatch.setattr(cli, "get_pypi_info", info)
    monkeypatch.setattr(cli, "_run_single_check", check)
    return calls, unchecked


def safe(tmp_path, current="1.0.1", spec=None):
    return cli.highest_safe_version("pkg", current, str(tmp_path), load_config(str(tmp_path)), spec=spec)


def test_candidates_are_newest_of_each_minor(pkg):
    assert cli._candidate_versions("pkg", "1.0.1", None) == ["1.1.4", "2.0.3", "3.0.0", "3.1.2", "3.2.0"]
    # yanked 2.1.0 and the pre-release are skipped; a constraint is respected
    assert cli._candidate_versions("pkg", "1.0.1", ">=1,<3") == ["1.1.4", "2.0.3"]


def test_binary_search_finds_last_safe(pkg, tmp_path):
    calls, _ = pkg
    plan = safe(tmp_path)
    assert plan["latest"] == "3.2.0" and plan["safe"] == "3.0.0"
    assert plan["breaking_at_latest"] == 4
    assert len(calls) <= 4  # latest first, then ~log2(n), not every version
    assert calls[0] == "3.2.0"


def test_latest_safe_is_one_check(pkg, tmp_path):
    calls, _ = pkg
    plan = safe(tmp_path, current="1.0.1", spec="<3.1")
    assert plan["safe"] == plan["latest"] == "3.0.0" and calls == ["3.0.0"]


def test_unchecked_version_is_never_safe(pkg, tmp_path):
    calls, unchecked = pkg
    unchecked.update({"3.0.0", "2.0.3"})
    plan = safe(tmp_path)
    assert plan["safe"] == "1.1.4"  # the newest version that was actually verified
    assert plan["unchecked"]


def test_already_latest(pkg, tmp_path):
    plan = safe(tmp_path, current="3.2.0")
    assert plan["safe"] == plan["latest"] == "3.2.0" and plan["checked"] == []
