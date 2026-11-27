"""Generate reports in markdown and HTML formats."""

import html
import os
from datetime import datetime


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def _h(text) -> str:
    """Untrusted text for the HTML report: escaped, without control characters."""
    from depshift.safe import plain
    return html.escape(plain(text))


def render_markdown(results: list[dict]) -> str:
    """results: list of dicts with keys package, current, target, risks, safe_count.

    Release notes, versions and code are untrusted text: they're made inert
    (no HTML, links, images or mentions, and code can't break out of its span)."""
    from depshift.safe import md_code, md_text
    lines = [
        "# pyupcheck report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    total_breaking = sum(r["breaking_count"] for r in results)
    total_deprecated = sum(r["deprecated_count"] for r in results)
    lines.append(f"**{len(results)}** package(s) checked | "
                 f"**{total_breaking}** breaking | **{total_deprecated}** deprecated")
    lines.append("")

    for r in results:
        status = "BREAKING" if r["breaking_count"] else ("DEPRECATED" if r["deprecated_count"] else
                                                       ("NOT CHECKED" if r.get("unchecked") else "OK"))
        lines.append(f"## {md_text(r['package'])} {md_text(r.get('current_version') or '?')} -> "
                     f"{md_text(r['target_version'])} [{status}]")
        lines.append("")
        for w in r.get("warnings", []):
            lines.append(f"> Note: {md_text(w)}")
            lines.append("")
        if r.get("unchecked") and not r["risks"]:
            continue
        if not r["risks"]:
            lines.append(f"All {r['safe_count']} usages safe.")
            lines.append("")
            continue
        lines.append("| Severity | Location | Code | Change |")
        lines.append("|---|---|---|---|")
        for risk in r["risks"]:
            loc = md_code(f"{_rel(risk['file'])}:{int(risk['line'])}", in_table=True)
            code = md_code(risk["code"][:60], in_table=True)
            desc = md_text(risk["change_description"][:80])
            if risk.get("replacement"):
                desc += f" (use {md_code(risk['replacement'][:120], in_table=True)})"
            sev = risk["severity"] if risk["severity"] in ("breaking", "deprecated") else "warning"
            lines.append(f"| {sev} | {loc} | {code} | {desc} |")
        lines.append("")
        lines.append(f"{r['safe_count']} other usages safe.")
        lines.append("")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pyupcheck report</title>
<style>
  :root {{
    --bg: #10141a; --card: #1a212b; --text: #dbe4ee; --dim: #7d8ca0;
    --breaking: #ff5964; --deprecated: #ffb347; --warning: #58a6ff; --ok: #3fb97c;
    --border: #2a3442;
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--text);
    font: 15px/1.6 ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace;
    padding: 40px 20px; max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .meta {{ color: var(--dim); margin-bottom: 28px; font-size: 13px; }}
  .summary {{ display: flex; gap: 12px; margin-bottom: 28px; flex-wrap: wrap; }}
  .stat {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 18px; }}
  .stat b {{ font-size: 20px; display: block; }}
  .pkg {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; margin-bottom: 18px; }}
  .pkg h2 {{ font-size: 16px; margin-bottom: 12px; }}
  .badge {{ font-size: 11px; padding: 2px 10px; border-radius: 20px; margin-left: 10px;
    vertical-align: middle; }}
  .b-breaking {{ background: var(--breaking); color: #fff; }}
  .b-deprecated {{ background: var(--deprecated); color: #000; }}
  .b-unchecked {{ background: #b46ee6; color: #fff; }}
  .note {{ color: #b46ee6; font-size: 13px; margin-top: 8px; }}
  .b-ok {{ background: var(--ok); color: #fff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
  th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border);
    vertical-align: top; }}
  th {{ color: var(--dim); font-weight: 600; }}
  .sev-breaking {{ color: var(--breaking); font-weight: 700; }}
  .sev-deprecated {{ color: var(--deprecated); font-weight: 700; }}
  .sev-warning {{ color: var(--warning); }}
  code {{ background: #0c0f14; padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  .safe {{ color: var(--dim); font-size: 13px; margin-top: 10px; }}
</style>
</head>
<body>
<h1>pyupcheck report</h1>
<div class="meta">Generated {timestamp}</div>
<div class="summary">
  <div class="stat"><b>{n_packages}</b>packages checked</div>
  <div class="stat"><b style="color:var(--breaking)">{n_breaking}</b>breaking</div>
  <div class="stat"><b style="color:var(--deprecated)">{n_deprecated}</b>deprecated</div>
</div>
{packages}
</body>
</html>
"""


def render_html(results: list[dict]) -> str:
    pkg_blocks = []
    for r in results:
        if r["breaking_count"]:
            badge = '<span class="badge b-breaking">BREAKING</span>'
        elif r["deprecated_count"]:
            badge = '<span class="badge b-deprecated">DEPRECATED</span>'
        elif r.get("unchecked"):
            badge = '<span class="badge b-unchecked">NOT CHECKED</span>'
        else:
            badge = '<span class="badge b-ok">OK</span>'

        rows = ""
        for risk in r["risks"]:
            loc = _h(f"{_rel(risk['file'])}:{risk['line']}")
            code = _h(risk["code"][:70])
            desc = _h(risk["change_description"][:110])
            if risk.get("replacement"):
                desc += f' &rarr; use <code>{_h(risk["replacement"])}</code>'
            sev = risk["severity"]
            rows += (f'<tr><td class="sev-{sev}">{sev}</td>'
                     f'<td><code>{loc}</code></td>'
                     f'<td><code>{code}</code></td>'
                     f'<td>{desc}</td></tr>')

        table = (f'<table><tr><th>Severity</th><th>Location</th><th>Code</th><th>Change</th></tr>'
                 f'{rows}</table>') if rows else ""

        pkg_blocks.append(
            f'<div class="pkg"><h2>{_h(r["package"])} '
            f'{_h(r.get("current_version") or "?")} &rarr; {_h(r["target_version"])}{badge}</h2>'
            f'{table}'
            + "".join(f'<div class="note">Note: {_h(w)}</div>' for w in r.get("warnings", []))
            + f'<div class="safe">{r["safe_count"]} usages safe</div></div>'
        )

    return _HTML_TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_packages=len(results),
        n_breaking=sum(r["breaking_count"] for r in results),
        n_deprecated=sum(r["deprecated_count"] for r in results),
        packages="\n".join(pkg_blocks),
    )


# ── machine formats for CI: SARIF and GitHub annotations ──────────────────────

_SARIF_LEVEL = {"breaking": "error", "deprecated": "warning", "warning": "note"}
_GH_LEVEL = {"breaking": "error", "deprecated": "warning", "warning": "notice"}


def _repo_rel(path: str, root: str | None = None) -> str:
    """Path relative to the repository root, with forward slashes.
    In GitHub Actions the root is GITHUB_WORKSPACE, so a project in a
    subdirectory still gets annotations on the right file."""
    base = os.environ.get("GITHUB_WORKSPACE") or root or os.getcwd()
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def _findings(results: list[dict]):
    """Breaking and deprecated findings, once per (file, line, message).
    Weak "mentioned in the changes" hints are left to the full reports."""
    seen = set()
    for r in results:
        for risk in r.get("risks", []):
            if risk["severity"] not in ("breaking", "deprecated"):
                continue
            key = (risk["file"], risk["line"], _message(r, risk))
            if key in seen:
                continue
            seen.add(key)
            yield r, risk


def _message(r: dict, risk: dict) -> str:
    from depshift.review import _clean
    msg = f"{r['package']} {r.get('current_version') or '?'} -> {r['target_version']}: " \
          f"{_clean(risk['change_description'])}"
    repl = risk.get("replacement")
    if repl and f"`{repl}`" not in msg:  # unless the release note already says so
        msg += f" Use {repl} instead."
    return " ".join(msg.split())


def render_sarif(results: list[dict], root: str | None = None) -> str:
    """SARIF 2.1.0, for GitHub code scanning and other SARIF viewers."""
    import hashlib
    import json

    from depshift import __version__

    rules = [
        {"id": "pyupcheck/breaking", "name": "BreakingUpgrade",
         "shortDescription": {"text": "Code that breaks with the dependency upgrade"},
         "defaultConfiguration": {"level": "error"}},
        {"id": "pyupcheck/deprecated", "name": "DeprecatedInUpgrade",
         "shortDescription": {"text": "Code that uses an API deprecated in the upgrade"},
         "defaultConfiguration": {"level": "warning"}},
        {"id": "pyupcheck/warning", "name": "ChangedInUpgrade",
         "shortDescription": {"text": "Code that touches an API mentioned in the upgrade's changes"},
         "defaultConfiguration": {"level": "note"}},
    ]
    out = []
    for r, risk in _findings(results):
        sev = risk["severity"]
        rel = _repo_rel(risk["file"], root)
        # stable across commits: no line number, so moved code keeps its identity
        key = "|".join([r["package"], risk.get("api", ""), rel, risk.get("code", "").strip()])
        out.append({
            "ruleId": f"pyupcheck/{sev}",
            "level": _SARIF_LEVEL.get(sev, "note"),
            "message": {"text": _message(r, risk)},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": rel},
                "region": {"startLine": int(risk["line"])},
            }}],
            "partialFingerprints": {"pyupcheck/v1": hashlib.sha256(key.encode()).hexdigest()[:32]},
            "properties": {
                "package": r["package"], "from": r.get("current_version"), "to": r["target_version"],
                "api": risk.get("api"), "verified": bool(risk.get("precise")),
                **({"replacement": risk["replacement"]} if risk.get("replacement") else {}),
            },
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "pyupcheck", "version": __version__,
                                "informationUri": "https://github.com/Astronomox/pyupcheck-Astronomox",
                                "rules": rules}},
            "results": out,
        }],
    }
    return json.dumps(doc, indent=2)


def _gh_escape_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _gh_escape_prop(s: str) -> str:
    return _gh_escape_data(s).replace(":", "%3A").replace(",", "%2C")


def render_github_annotations(results: list[dict], root: str | None = None) -> str:
    """GitHub Actions workflow commands: one annotation per finding, shown on
    the line in the PR diff and in the run summary."""
    lines = []
    for r, risk in _findings(results):
        level = _GH_LEVEL.get(risk["severity"], "notice")
        title = f"pyupcheck: {r['package']} {r.get('current_version') or '?'} -> {r['target_version']}"
        props = (f"file={_gh_escape_prop(_repo_rel(risk['file'], root))},"
                 f"line={int(risk['line'])},title={_gh_escape_prop(title)}")
        lines.append(f"::{level} {props}::{_gh_escape_data(_message(r, risk))}")
    return "\n".join(lines)
