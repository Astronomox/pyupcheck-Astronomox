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
