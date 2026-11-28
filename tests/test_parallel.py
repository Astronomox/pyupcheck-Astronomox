"""check-all runs dependencies in parallel: order is kept, failures isolated."""

import json
import random
import time

from click.testing import CliRunner

import depshift.cli as cli
import depshift.project as project


def fake_check(package, *a, **k):
    time.sleep(random.uniform(0, 0.05))  # finish out of order
    if package == "boom":
        raise RuntimeError("kaput")
    return {"package": package, "current_version": "1.0", "target_version": "2.0",
            "risks": [], "safe_count": 1, "breaking_count": 0, "deprecated_count": 0}


def test_parallel_check_all_keeps_order_and_isolates_crash(tmp_path, monkeypatch):
    names = ["alpha", "boom", "gamma", "delta", "epsilon", "zeta"]
    (tmp_path / "requirements.txt").write_text("".join(f"{n}==1.0\n" for n in names))
    monkeypatch.setattr(project, "run_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json", "-j", "4",
                                        "--fail-on", "never"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)["results"]
    assert [r["package"] for r in out] == names
    boom = out[1]
    assert boom["unchecked"] and "kaput" in boom["warnings"][0]


def test_jobs_one_is_sequential(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==1.0\n")
    monkeypatch.setattr(project, "run_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json", "-j", "1"])
    assert [r["package"] for r in json.loads(res.output)["results"]] == ["a", "b"]


def test_json_stdout_is_pure_json(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    monkeypatch.setattr(project, "run_check", fake_check)
    res = CliRunner().invoke(cli.main, ["check-all", "-d", str(tmp_path), "-f", "json"])
    json.loads(res.output)  # no "Found N dependencies" banner mixed in


def test_429_is_retried_after_the_wait_asked_for(monkeypatch):
    from depshift import http

    class R:
        def __init__(self, status, headers=None):
            self.status_code, self.headers = status, headers or {}

    answers = [R(429, {"retry-after": "1"}), R(200)]
    slept = []
    monkeypatch.setattr(http, "_get", lambda url, max_bytes, **kw: answers.pop(0))
    monkeypatch.setattr(http.time, "sleep", slept.append)
    assert http._http_get("https://raw.githubusercontent.com/o/r/HEAD/x").status_code == 200
    assert slept == [1.0]


def test_long_retry_after_is_not_waited_for(monkeypatch):
    from depshift import http

    class R:
        status_code, headers = 429, {"retry-after": "3600"}

    monkeypatch.setattr(http, "_get", lambda url, max_bytes, **kw: R())
    monkeypatch.setattr(http.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("waited")))
    assert http._http_get("https://example.com/y").status_code == 429
