"""check-all runs dependencies in parallel: order is kept, failures isolated."""

import json
import random
import time

from click.testing import CliRunner

import depshift.cli as cli


def fake_check(package, *a, **k):
    time.sleep(random.uniform(0, 0.05))  # finish out of order
    if package == "boom":
        raise RuntimeError("kaput")
    return {"package": package, "current_version": "1.0", "target_version": "2.0",
            "risks": [], "safe_count": 1, "breaking_count": 0, "deprecated_count": 0}


def test_parallel_check_all_keeps_order_and_isolates_crash(tmp_path, monkeypatch):
    names = ["alpha", "boom", "gamma", "delta", "epsilon", "zeta"]
    (tmp_path / "requirements.txt").write_text("".join(f"{n}==1.0\n" for n in names))
    monkeypatch.setattr(cli, "_run_single_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json", "-j", "4",
                                        "--fail-on", "never"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)["results"]
    assert [r["package"] for r in out] == names
    boom = out[1]
    assert boom["unchecked"] and "kaput" in boom["warnings"][0]


def test_jobs_one_is_sequential(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==1.0\n")
    monkeypatch.setattr(cli, "_run_single_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json", "-j", "1"])
    assert [r["package"] for r in json.loads(res.output)["results"]] == ["a", "b"]


def test_json_stdout_is_pure_json(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    monkeypatch.setattr(cli, "_run_single_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json"])
    json.loads(res.output)  # no "Found N dependencies" banner mixed in
