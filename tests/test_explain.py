"""pyupcheck explain, with a fake Claude client: nothing is sent anywhere."""

import builtins
import sys

import pytest
from click.testing import CliRunner

from depshift import cli
from depshift import explain as ex


def result_for(path, **risk):
    base = {"file": str(path), "line": 3, "code": "y = np.asfarray(x)", "api": "numpy.asfarray",
            "severity": "breaking", "precise": True,
            "change_description": "``np.asfarray`` has been removed. Use ``np.asarray`` with a proper dtype. :pr:`1`"}
    base.update(risk)
    return {"package": "numpy", "current_version": "1.26.4", "target_version": "2.0.0", "risks": [base],
            "breaking_count": 1, "deprecated_count": 0, "safe_count": 0}


@pytest.fixture
def app(tmp_path):
    p = tmp_path / "app.py"
    p.write_text("import numpy as np\nx = [1, 2]\ny = np.asfarray(x)\nprint(y)\n", encoding="utf-8")
    return p


def test_findings_carry_code_and_clean_note(app, tmp_path):
    (f,) = ex.findings_from(result_for(app), str(tmp_path))
    assert f["where"] == "app.py:3" and f["verified"]
    assert "> 3 | y = np.asfarray(x)" in f["code"] and "  1 | import numpy as np" in f["code"]
    assert ":pr:" not in f["note"] and "`np.asfarray`" in f["note"]


def test_warnings_and_duplicates_are_not_sent(app, tmp_path):
    r = result_for(app)
    r["risks"] += [dict(r["risks"][0]), dict(r["risks"][0], severity="warning", line=4)]
    assert len(ex.findings_from(r, str(tmp_path))) == 1


def test_prompt(app, tmp_path):
    findings = ex.findings_from(result_for(app, replacement="numpy.asarray"), str(tmp_path))
    prompt = ex.build_prompt("numpy", "1.26.4", "2.0.0", findings)
    assert prompt.startswith("Upgrading `numpy` from 1.26.4 to 2.0.0. 1 place in the code breaks")
    assert "Verified replacement in 2.0.0: `numpy.asarray`" in prompt
    assert "```python\n" in prompt and "y = np.asfarray(x)" in prompt


class FakeStream:
    def __init__(self, chunks, stop="end_turn", error=None):
        self.chunks, self.stop, self.error = chunks, stop, error

    def __enter__(self):
        if self.error:
            raise self.error
        self.text_stream = iter(self.chunks)
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        class U:
            input_tokens, output_tokens = 120, 40

        class M:
            stop_reason, model, usage = self.stop, ex.MODEL, U()
        return M()


class FakeClient:
    def __init__(self, stream):
        self.calls = []
        client = self

        class Messages:
            def stream(self, **kw):
                client.calls.append(kw)
                return stream

        class Beta:
            messages = Messages()
        self.beta = Beta()


def test_stream_request_shape_and_output():
    pytest.importorskip("anthropic")
    client = FakeClient(FakeStream(["## `np.asfarray`\n", "use asarray"]))
    out = []
    info = ex.stream_advice(client, "PROMPT", out.append)
    assert "".join(out) == "## `np.asfarray`\nuse asarray"
    (kw,) = client.calls
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"] == {"effort": "high"}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert info["stop_reason"] == "end_turn" and info["output_tokens"] == 40
    ex.stream_advice(client, "P", out.append, fallbacks=False)
    assert "fallbacks" not in client.calls[-1] and "betas" not in client.calls[-1]


def test_missing_credentials_is_a_clear_error():
    pytest.importorskip("anthropic")
    err = TypeError('"Could not resolve authentication method. Expected one of api_key..."')
    with pytest.raises(ex.ExplainError, match="ANTHROPIC_API_KEY"):
        ex.stream_advice(FakeClient(FakeStream([], error=err)), "P", print)


def test_missing_package_is_a_clear_error(monkeypatch):
    real = builtins.__import__

    def no_anthropic(name, *a, **k):
        if name == "anthropic":
            raise ImportError("no anthropic")
        return real(name, *a, **k)

    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    with pytest.raises(ex.ExplainError, match=r'pyupcheck\[ai\]'):
        ex.make_client()


def run_cli(monkeypatch, app, tmp_path, args, client=None):
    monkeypatch.setattr(cli, "run_check", lambda *a, **k: result_for(app))
    if client is not None:
        monkeypatch.setattr(ex, "make_client", lambda: client)
    return CliRunner().invoke(cli.main, ["explain", "numpy", "2.0.0", "-d", str(tmp_path), *args])


def test_dry_run_sends_nothing(monkeypatch, app, tmp_path):
    def boom():
        raise AssertionError("dry run must not create a client")
    monkeypatch.setattr(ex, "make_client", boom)
    res = run_cli(monkeypatch, app, tmp_path, ["--dry-run"])
    assert res.exit_code == 0, res.output
    assert "Upgrading `numpy` from 1.26.4 to 2.0.0" in res.output and "y = np.asfarray(x)" in res.output


def test_cli_streams_and_reports_refusal(monkeypatch, app, tmp_path):
    pytest.importorskip("anthropic")
    res = run_cli(monkeypatch, app, tmp_path, ["--effort", "medium"],
                  client=FakeClient(FakeStream(["advice text"], stop="refusal")))
    assert res.exit_code == 0, res.output
    assert "Sending 1 finding and 4 lines of your code" in res.output
    assert "advice text" in res.output and "Claude declined" in res.output


# ── API errors become plain, actionable messages ──────────────────────────────

def _api_errors():
    anthropic = pytest.importorskip("anthropic")
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code):
        return cls(f"error {code}", response=httpx.Response(code, request=request), body=None)
    return [
        (status(anthropic.AuthenticationError, 401), "rejected the credentials"),
        (status(anthropic.PermissionDeniedError, 403), "refused the request: error 403"),
        (status(anthropic.RateLimitError, 429), "Rate limited"),
        (anthropic.APIConnectionError(request=request), "network error"),
        (status(anthropic.InternalServerError, 500), "Claude API error 500: error 500"),
    ]


def test_api_errors_are_explained():
    for error, message in _api_errors():
        with pytest.raises(ex.ExplainError, match=message):
            ex.stream_advice(FakeClient(FakeStream([], error=error)), "P", print)


def test_unrelated_type_errors_are_not_hidden():
    pytest.importorskip("anthropic")
    with pytest.raises(TypeError, match="something else"):
        ex.stream_advice(FakeClient(FakeStream([], error=TypeError("something else"))), "P", print)


def test_make_client_without_credentials(monkeypatch):
    anthropic = pytest.importorskip("anthropic")

    def no_key(*a, **k):
        raise anthropic.AnthropicError("no key")
    monkeypatch.setattr(anthropic, "Anthropic", no_key)
    with pytest.raises(ex.ExplainError, match="No Claude API credentials"):
        ex.make_client()
    monkeypatch.setattr(anthropic, "Anthropic", lambda: "client")
    assert ex.make_client() == "client"


# ── findings ──────────────────────────────────────────────────────────────────

def test_findings_limit_auto_fixes_and_missing_files(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    risks = [{"file": str(tmp_path / "a.py"), "line": n, "severity": "breaking", "api": f"x.f{n}",
              "change_description": "gone"} for n in (2, 5, 9)]
    risks.append({"file": str(tmp_path / "missing.py"), "line": 1, "severity": "breaking", "api": "x.g",
                  "change_description": ""})
    found = ex.findings_from({"risks": risks}, str(tmp_path), fixable={(str(tmp_path / "a.py"), None, 5): "x.f5 -> y"})
    assert [f["where"] for f in found] == ["a.py:2", "a.py:5", "a.py:9", "missing.py:1"]
    assert found[1]["auto"] == "x.f5 -> y" and found[3]["code"] == ""
    assert "> 2 | line2" in found[0]["code"] and "  5 | line5" in found[0]["code"]
    assert len(ex.findings_from({"risks": risks}, str(tmp_path), limit=2)) == 2
    prompt = ex.build_prompt("x", "1", "2", found)
    assert "4 places in the code break" in prompt and "already rewrites this automatically (x.f5 -> y)" in prompt
    assert "(code not available)" in prompt and "Release note: (none)" in prompt


def test_notebook_findings_show_the_cell(tmp_path):
    import json
    nb = {"cells": [{"cell_type": "code", "source": ["import x\n", "x.old()\n"], "metadata": {}, "outputs": [],
                     "execution_count": None}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    path = tmp_path / "n.ipynb"
    path.write_text(json.dumps(nb))
    risk = {"file": str(path), "line": 7, "cell": 1, "cell_line": 2, "severity": "breaking", "api": "x.old",
            "change_description": "removed"}
    (found,) = ex.findings_from({"risks": [risk]}, str(tmp_path))
    assert found["where"] == "n.ipynb cell 1, line 2" and "> 2 | x.old()" in found["code"]
    path.write_text("not json")
    assert ex.findings_from({"risks": [risk]}, str(tmp_path))[0]["code"] == ""
