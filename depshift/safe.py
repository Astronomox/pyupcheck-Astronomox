"""Making untrusted text safe to show.

pyupcheck prints text it doesn't control: release notes from GitHub and docs
sites, package metadata from PyPI, and lines of code from the project being
checked (which may be someone else's repository). Any of it can contain
terminal control sequences (retitle the window, clear the screen, forge a
clickable link), bidirectional overrides that make text read differently from
what it is, or, inside GitHub Actions, workflow commands. ``plain`` removes
all of that; ``md_text`` and ``md_code`` additionally make it inert inside a
Markdown pull-request comment.
"""

from __future__ import annotations

import os
import re

# C0 controls except tab and newline, DEL, C1 controls; Unicode bidirectional
# overrides/isolates (the "Trojan Source" characters) and other invisible
# format characters that change how text reads
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


# whole escape sequences (CSI "ESC [ ... m", OSC "ESC ] ... BEL/ST", and
# two-byte ones), so no fragment like "]0;title" is left behind
_ESCAPE_SEQ = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]?"                    # CSI: colours, cursor moves, clearing
    r"|\][^\x07\x1b]{0,4096}(?:\x07|\x1b\\)?"  # OSC: window title, hyperlinks
    r"|[ -/]*[0-~]?)"                          # two-byte sequences
    r"|\x9b[0-?]*[ -/]*[@-~]?"                 # 8-bit CSI
)


def plain(text: object, keep_newlines: bool = True) -> str:
    """``text`` without control or invisible formatting characters. In GitHub
    Actions, a ``::`` can no longer start a workflow command."""
    s = _ESCAPE_SEQ.sub("", str(text if text is not None else ""))
    s = _CONTROL.sub("", s)
    if not keep_newlines:
        s = s.replace("\n", " ")
    if os.environ.get("GITHUB_ACTIONS") == "true" and "::" in s:
        s = s.replace("::", ":\u200b:")
    return s


# a link or image with a real target ([text](https://...)), not code that
# merely looks like one (``np.cast[dtype](arg)``)
_MD_LINK = re.compile(r"!?\[([^\[\]\n]{0,300})\]\((?:https?://|mailto:|//|/|#|www\.)[^()\s]{0,2000}\)")
# an inline code span: a run of backticks, then text, then the same run
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).){1,2000}?\1")


def _inert(prose: str) -> str:
    prose = _MD_LINK.sub(r"\1", prose)                       # [text](url) -> text; images too
    prose = prose.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    prose = re.sub(r"@(?=[\w-])", "@\u2060", prose)          # word joiner: no mention, no ping
    return re.sub(r"(?<![\w&])#(?=\d)", "#\u2060", prose)    # no issue/PR cross-links


def md_text(text: object, in_table: bool = False) -> str:
    """Untrusted prose for a Markdown comment: no HTML, images, links,
    @-mentions (which notify people) or #-references. Code spans are kept
    exactly: GitHub shows their contents literally, so nothing in them is
    live, and changing them would corrupt the code. In a table cell, ``|``
    is escaped (it would end the cell)."""
    s = plain(text, keep_newlines=False)
    parts, pos = [], 0
    for span in _CODE_SPAN.finditer(s):
        parts.append(_inert(s[pos:span.start()]))
        parts.append(span.group(0))
        pos = span.end()
    parts.append(_inert(s[pos:]))
    s = "".join(parts)
    return s.replace("|", "\\|") if in_table else s


def md_code(text: object, in_table: bool = False) -> str:
    """Untrusted text as an inline code span that it can't break out of."""
    s = plain(text, keep_newlines=False)
    if in_table:
        s = s.replace("|", "\\|")
    longest = max((len(m) for m in re.findall(r"`+", s)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s.strip() else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def plain_all(obj):
    """``plain`` applied to every string in nested dicts and lists."""
    if isinstance(obj, str):
        return plain(obj)
    if isinstance(obj, dict):
        return {k: plain_all(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(plain_all(v) for v in obj)
    return obj
