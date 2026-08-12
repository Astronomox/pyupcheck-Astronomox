"""Generate reports in markdown and HTML formats."""

import html
import os
from datetime import datetime
from typing import List


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def render_markdown(results: List[dict]) -> str:
    """results: list of dicts with keys package, current, target, risks, safe_count."""
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
        status = "BREAKING" if r["breaking_count"] else ("DEPRECATED" if r["deprecated_count"] else "OK")
        lines.append(f"## {r['package']} {r['current_version']} -> {r['target_version']} [{status}]")
        lines.append("")
        if not r["risks"]:
            lines.append(f"All {r['safe_count']} usages safe.")
            lines.append("")
            continue
        lines.append("| Severity | Location | Code | Change |")
        lines.append("|---|---|---|---|")
        for risk in r["risks"]:
            loc = f"{_rel(risk['file'])}:{risk['line']}"
            code = risk["code"].replace("|", "\\|")[:60]
            desc = risk["change_description"].replace("|", "\\|")[:80]
            lines.append(f"| {risk['severity']} | `{loc}` | `{code}` | {desc} |")
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


def render_html(results: List[dict]) -> str:
    pkg_blocks = []
    for r in results:
        if r["breaking_count"]:
            badge = '<span class="badge b-breaking">BREAKING</span>'
        elif r["deprecated_count"]:
            badge = '<span class="badge b-deprecated">DEPRECATED</span>'
        else:
            badge = '<span class="badge b-ok">OK</span>'

        rows = ""
        for risk in r["risks"]:
            loc = html.escape(f"{_rel(risk['file'])}:{risk['line']}")
            code = html.escape(risk["code"][:70])
            desc = html.escape(risk["change_description"][:110])
            sev = risk["severity"]
            rows += (f'<tr><td class="sev-{sev}">{sev}</td>'
                     f'<td><code>{loc}</code></td>'
                     f'<td><code>{code}</code></td>'
                     f'<td>{desc}</td></tr>')

        table = (f'<table><tr><th>Severity</th><th>Location</th><th>Code</th><th>Change</th></tr>'
                 f'{rows}</table>') if rows else ""

        pkg_blocks.append(
            f'<div class="pkg"><h2>{html.escape(r["package"])} '
            f'{html.escape(r["current_version"])} &rarr; {html.escape(r["target_version"])}{badge}</h2>'
            f'{table}<div class="safe">{r["safe_count"]} usages safe</div></div>'
        )

    return _HTML_TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_packages=len(results),
        n_breaking=sum(r["breaking_count"] for r in results),
        n_deprecated=sum(r["deprecated_count"] for r in results),
        packages="\n".join(pkg_blocks),
    )
