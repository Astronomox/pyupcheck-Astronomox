"""SARIF and GitHub annotation output."""

import json

from depshift.report import render_github_annotations, render_sarif


def result(tmp_path, **risk_kw):
    risk = {"file": str(tmp_path / "src" / "app.py"), "line": 7, "code": "flask.Markup('x')",
            "api": "flask.Markup", "severity": "breaking",
            "change_description": "'Markup' can no longer be imported from 'flask', see: docs\nmore",
            "precise": True, "replacement": "markupsafe.Markup"}
    risk.update(risk_kw)
    return {"package": "flask", "current_version": "2.2.5", "target_version": "3.0.0",
            "risks": [risk], "breaking_count": 1, "deprecated_count": 0}


def test_sarif_structure(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    doc = json.loads(render_sarif([result(tmp_path)], str(tmp_path)))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "pyupcheck"
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} >= {"pyupcheck/breaking", "pyupcheck/deprecated"}
    (res,) = run["results"]
    assert res["ruleId"] == "pyupcheck/breaking" and res["level"] == "error"
    loc = res["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/app.py"
    assert loc["region"]["startLine"] == 7
    assert "Use markupsafe.Markup instead." in res["message"]["text"]
    assert res["properties"]["verified"] is True


def test_sarif_fingerprint_ignores_line_number(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    fp = lambda line: json.loads(render_sarif([result(tmp_path, line=line)], str(tmp_path)))[
        "runs"][0]["results"][0]["partialFingerprints"]
    assert fp(7) == fp(40)  # the same issue after code moves keeps its identity


def test_github_annotations_escape_and_use_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    (line,) = render_github_annotations([result(tmp_path)], str(tmp_path / "src")).splitlines()
    assert line.startswith("::error file=src/app.py,line=7,title=pyupcheck%3A flask 2.2.5 -> 3.0.0::")
    msg = line.split("::", 2)[2]
    assert "\n" not in msg and "%0A" not in msg  # whitespace collapsed to one line
    assert "Use markupsafe.Markup instead." in msg


def test_severity_levels(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    out = render_github_annotations([result(tmp_path, severity="deprecated")], str(tmp_path))
    assert out.startswith("::warning ")
    assert render_github_annotations([], str(tmp_path)) == ""


def test_one_annotation_per_line_and_no_weak_hints(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    r = result(tmp_path, change_description="Removed ``flask.Markup``. :pr:`5223`")
    r["risks"].append(dict(r["risks"][0]))                       # same line, same message
    r["risks"].append(dict(r["risks"][0], line=3, severity="warning"))  # weak hint
    out = render_github_annotations([r], str(tmp_path)).splitlines()
    assert len(out) == 1
    assert "``" not in out[0] and ":pr:" not in out[0]
    assert len(json.loads(render_sarif([r], str(tmp_path)))["runs"][0]["results"]) == 1


def test_replacement_not_repeated_when_the_note_names_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    r = result(tmp_path, change_description="Alias ``np.float_`` has been removed. Use ``np.float64`` instead.",
               replacement="np.float64")
    msg = render_github_annotations([r], str(tmp_path)).split("::", 2)[2]
    assert msg.count("np.float64") == 1
