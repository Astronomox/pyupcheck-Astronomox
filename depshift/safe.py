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


_MD_LINK = re.compile(r"!?\[([^\[\]\n]{0,300})\]\([^()\s]{0,2000}\)")


def md_text(text: object) -> str:
    """Untrusted prose for a Markdown comment: no HTML, images, links,
    @-mentions (which notify people) or #-references, no table breakage."""
    s = plain(text, keep_newlines=False)
    s = _MD_LINK.sub(r"\1", s)                            # [text](url) -> text; images too
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"@(?=[\w-])", "@\u2060", s)                 # word joiner: no mention, no ping
    s = re.sub(r"(?<![\w&])#(?=\d)", "#\u2060", s)          # no issue/PR cross-links
    return s.replace("|", "\\|")


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
