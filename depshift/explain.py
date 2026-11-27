"""``pyupcheck explain``: migration advice from Claude for what breaks.

``migrate`` rewrites only what can be changed without guessing. For the rest
(an argument whose meaning changed, an API with no drop-in replacement, a
pattern that has to be restructured) this sends each breaking finding to
Claude: your code around the line, the release note that explains it, and
the replacement pyupcheck verified, and prints the advice it streams back.

Needs the optional ``anthropic`` package (``pip install "pyupcheck[ai]"``)
and Claude API credentials (``ANTHROPIC_API_KEY``, or ``ant auth login``).
Nothing is sent without them, and ``--dry-run`` shows exactly what would be.
"""

import os
from collections.abc import Callable

MODEL = "claude-opus-5-5"
INSTALL_HINT = 'pip install "pyupcheck[ai]"'
CREDENTIALS_HINT = ("Set ANTHROPIC_API_KEY (from https://platform.claude.com/settings/keys), "
                    "or sign in with `ant auth login`.")

# stable, so it can be cached across runs
SYSTEM = """\
You help Python developers upgrade a dependency. For each finding you get the
code that uses the package, the release note that describes what changed, and
sometimes a replacement that was verified to exist in the new version.

For each finding:
1. Say in one sentence what breaks and why, from the release note.
2. Show the changed code as a minimal before/after diff of the lines shown.
   Keep the developer's names and style; change only what the upgrade needs.
3. Say what to check afterwards when the new code can behave differently
   (another default, return type, dtype, ordering, exceptions).

Rules:
- Base every claim on the release note and the verified replacement. If they
  don't say enough to be sure, say what is uncertain and how to confirm it
  (the docs page to read, a quick check to run) instead of guessing.
- Never invent APIs or parameters. Prefer the verified replacement.
- Group findings that need the same change; don't repeat yourself.
- Be brief: a heading per API, then the diff and the notes. No preamble.
"""


class ExplainError(Exception):
    """A problem the user can fix (missing package, credentials)."""


def _snippet(path: str, line: int, context: int = 3, cell: int | None = None,
             cell_line: int | None = None) -> str:
    """The lines around a finding, numbered, with the finding marked."""
    if cell is not None and path.endswith(".ipynb"):
        from depshift import notebook
        nb = notebook.load(path)
        if nb is None:
            return ""
        src = nb.data["cells"][cell - 1].get("source", "")
        lines = ("".join(src) if isinstance(src, list) else src).splitlines()
        line = cell_line or 1
    else:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return ""
    lo, hi = max(1, line - context), min(len(lines), line + context)
    width = len(str(hi))
    return "\n".join(f"{'>' if n == line else ' '} {n:>{width}} | {lines[n - 1]}" for n in range(lo, hi + 1))


def findings_from(result: dict, root: str, fixable: dict[tuple, str] | None = None,
                  limit: int = 20) -> list[dict]:
    """Breaking findings of one check result, one per (file, line, API),
    with the code around each. ``fixable`` marks what ``migrate`` rewrites."""
    from depshift.review import _clean
    out, seen = [], set()
    for risk in result.get("risks", []):
        if risk.get("severity") != "breaking":
            continue
        key = (risk["file"], risk.get("cell"), risk.get("cell_line") or risk["line"], risk.get("api"))
        if key in seen:
            continue
        seen.add(key)
        rel = os.path.relpath(risk["file"], root)
        where = f"{rel} cell {risk['cell']}, line {risk['cell_line']}" if risk.get("cell") \
            else f"{rel}:{risk['line']}"
        out.append({
            "where": where,
            "api": risk.get("api", ""),
            "note": _clean(risk.get("change_description", "")),
            "replacement": risk.get("replacement"),
            "verified": bool(risk.get("precise")),
            "code": _snippet(risk["file"], risk["line"], cell=risk.get("cell"), cell_line=risk.get("cell_line")),
            "auto": (fixable or {}).get((risk["file"], risk.get("cell"), risk.get("cell_line") or risk["line"])),
        })
        if len(out) >= limit:
            break
    return out


def build_prompt(package: str, current: str, target: str, findings: list[dict]) -> str:
    parts = [f"Upgrading `{package}` from {current} to {target}. "
             + ("1 place in the code breaks.\n" if len(findings) == 1
                else f"{len(findings)} places in the code break.\n")]
    for i, f in enumerate(findings, 1):
        parts.append(f"## Finding {i}: `{f['api']}` at {f['where']}")
        parts.append(f"Release note: {f['note'] or '(none)'}")
        if f["replacement"]:
            parts.append(f"Verified replacement in {target}: `{f['replacement']}`")
        if f["verified"]:
            parts.append("Confirmed by comparing the two versions' real API.")
        if f["auto"]:
            parts.append(f"`pyupcheck migrate` already rewrites this automatically ({f['auto']}); "
                         "mention that and explain only what to check.")
        parts.append(f"```python\n{f['code']}\n```\n" if f["code"] else "(code not available)\n")
    return "\n".join(parts)


def make_client():
    """An Anthropic client, or ExplainError saying what's missing."""
    try:
        import anthropic
    except ImportError as e:
        raise ExplainError(f"`pyupcheck explain` needs the anthropic package: {INSTALL_HINT}") from e
    try:
        return anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        raise ExplainError(f"No Claude API credentials ({e}). {CREDENTIALS_HINT}") from e


def stream_advice(client, prompt: str, write: Callable[[str], None], model: str = MODEL,
                  effort: str = "high", fallbacks: bool = True) -> dict:
    """Stream Claude's advice through ``write``; returns a summary of the call."""
    import anthropic
    kwargs = {
        "model": model,
        "max_tokens": 32000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": prompt}],
    }
    if fallbacks:
        # a request a safety classifier declines is retried on another model
        # in the same call, instead of ending with nothing
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                write(text)
            final = stream.get_final_message()
    except TypeError as e:
        if "authentication method" not in str(e):
            raise
        # the SDK found no API key, token or `ant auth login` profile
        raise ExplainError(f"No Claude API credentials found. {CREDENTIALS_HINT}") from e
    except anthropic.AuthenticationError as e:
        raise ExplainError(f"The Claude API rejected the credentials. {CREDENTIALS_HINT}") from e
    except anthropic.PermissionDeniedError as e:
        raise ExplainError(f"The Claude API refused the request: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise ExplainError("Rate limited by the Claude API; try again in a minute.") from e
    except anthropic.APIConnectionError as e:
        raise ExplainError("Could not reach the Claude API (network error).") from e
    except anthropic.APIStatusError as e:
        raise ExplainError(f"Claude API error {e.status_code}: {e.message}") from e
    usage = getattr(final, "usage", None)
    return {
        "stop_reason": final.stop_reason,
        "model": getattr(final, "model", model),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
