"""Jupyter notebooks as one Python source.

A notebook's code cells run in one namespace, so an ``import numpy as np`` in
the first cell is what ``np.float`` in the fifth refers to. Scanning each
cell on its own misses that, so the code cells are joined into one source:

    # pyupcheck:cell 1
    import numpy as np
    # pyupcheck:cell 3
    y = np.float(3)

Lines that are not Python (``%magic``, ``!shell``, ``obj?``, ``x = !ls``,
cells under ``%%bash`` and similar, cells that do not parse) are swapped for
placeholders that keep every line number and are put back on writing, so a
rewrite of the joined source can be split back into the notebook unchanged
apart from the edits.
"""

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_CELL = "# pyupcheck:cell {}"
_CELL_RE = re.compile(r"^# pyupcheck:cell (\d+)$")
_KEEP = "# pyupcheck:keep {}"
_KEEP_RE = re.compile(r"^[ \t]*(?:pass  )?# pyupcheck:keep (\d+)$")

# cell magics whose body is still Python
_PYTHON_CELL_MAGICS = {"time", "timeit", "capture", "prun", "debug", "pypy"}
# %magic, !shell, ?help, x = !ls, x = %time f(), obj? / obj??
_LINE_NOT_PYTHON = re.compile(r"^\s*(?:[%!?]|[\w.,\s\[\]]+=\s*[!%]|[\w.]+\?\??\s*$)")


@dataclass
class Notebook:
    """The joined code cells of one notebook."""
    path: str
    data: dict
    source: str = ""
    kept: List[str] = field(default_factory=list)  # original lines behind placeholders
    starts: Dict[int, int] = field(default_factory=dict)  # cell number -> marker line
    file_lines: Dict[int, List[int]] = field(default_factory=dict)  # cell -> .ipynb line per source line

    def locate(self, line: int) -> Tuple[int, int]:
        """(cell number, line within the cell) of a line of the joined source."""
        cell, start = 0, 0
        for c, s in self.starts.items():  # in line order
            if s >= line:
                break
            cell, start = c, s
        return cell, line - start

    def global_line(self, cell: int, line: int) -> int:
        return self.starts[cell] + line

    def file_line(self, cell: int, line: int) -> int:
        """The line of the .ipynb file holding this line of the cell (the
        cell's last source line for a source stored as one string)."""
        lines = self.file_lines.get(cell)
        if not lines:
            return 1
        return lines[min(line, len(lines)) - 1]


def _source_lines(text: str) -> Dict[int, List[int]]:
    """{cell number: [file line of each source string]} for a notebook's
    JSON text: where each ``cells[i].source`` element starts."""
    out: Dict[int, List[int]] = {}
    stack: List[list] = []  # [kind, key or index, ...]; kind "{" or "["
    key = None  # last object key read, before its value
    line, i, n = 1, 0, len(text)

    def path():  # where a value read now sits
        return [frame[1] for frame in stack[:-1]] + ([key] if stack[-1][0] == "{" else [stack[-1][1]])

    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        elif ch == '"':
            j, start_line = i + 1, line
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            value = text[i + 1:j]
            i = j
            if stack and stack[-1][0] == "{" and key is None:
                nxt = text[j + 1:j + 40].lstrip()
                if nxt.startswith(":"):
                    key = value
                    i += 1
                    continue
            p = path() if stack else []
            if len(p) >= 3 and p[0] == "cells" and p[2] == "source" and len(p) in (3, 4):
                out.setdefault(p[1] + 1, []).append(start_line)
            if stack and stack[-1][0] == "{":
                key = None
        elif ch in "{[":
            if stack and stack[-1][0] == "{":
                stack[-1][1] = key
                key = None
            stack.append([ch, 0 if ch == "[" else None])
        elif ch in "}]":
            stack.pop()
            if stack and stack[-1][0] == "{":
                key = None
        elif ch == ",":
            if stack and stack[-1][0] == "[":
                stack[-1][1] += 1
            else:
                key = None
        elif stack and stack[-1][0] == "{" and key is not None and not ch.isspace() and ch != ":":
            # a number, true, false or null value
            while i < n and text[i] not in ",}]\n":
                i += 1
            key = None
            continue
        i += 1
    return out


def _cell_text(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def _placeholder(nb: Notebook, line: str, with_pass: bool) -> str:
    nb.kept.append(line)
    indent = line[:len(line) - len(line.lstrip())]
    # `pass` keeps an indented block that only held a magic valid Python
    return f"{indent}{'pass  ' if with_pass else ''}{_KEEP.format(len(nb.kept) - 1)}"


def _python_lines(nb: Notebook, text: str) -> List[str]:
    lines = text.split("\n")
    first = lines[0].strip() if lines else ""
    if first.startswith("%%"):
        magic = first[2:].split()[0] if first[2:].split() else ""
        if magic not in _PYTHON_CELL_MAGICS:  # %%bash, %%writefile, %%html, ...
            return [_placeholder(nb, l, False) for l in lines]
    out = []
    for l in lines:
        if _LINE_NOT_PYTHON.match(l):
            out.append(_placeholder(nb, l, True))
        else:
            out.append(l)
    try:
        ast.parse("\n".join(out))
    except (SyntaxError, ValueError):
        del nb.kept[len(nb.kept) - sum(1 for l in out if _KEEP_RE.match(l)):]
        return [_placeholder(nb, l, False) for l in lines]
    return out


def load(path: str) -> Optional[Notebook]:
    """The notebook at ``path`` with its code cells joined, or None if it is
    not a notebook pyupcheck can read."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        return None
    nb = Notebook(path, data)
    try:
        nb.file_lines = _source_lines(text)
    except (IndexError, TypeError):
        nb.file_lines = {}
    out: List[str] = []
    for i, cell in enumerate(data["cells"]):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        nb.starts[i + 1] = len(out) + 1
        out.append(_CELL.format(i + 1))
        out.extend(_python_lines(nb, _cell_text(cell)))
    nb.source = "\n".join(out) + ("\n" if out else "")
    return nb


def restore(nb: Notebook, source: str) -> dict:
    """The notebook data with its code cells taken from ``source``, a
    (possibly rewritten) joined source from :func:`load`."""
    cells: Dict[int, List[str]] = {}
    current = None
    for line in source.split("\n"):
        m = _CELL_RE.match(line)
        if m:
            current = int(m.group(1))
            cells[current] = []
            continue
        if current is None:
            continue
        k = _KEEP_RE.match(line)
        cells[current].append(nb.kept[int(k.group(1))] if k else line)
    data = json.loads(json.dumps(nb.data))  # a copy
    for n, lines in cells.items():
        cell = data["cells"][n - 1]
        if n == max(cells):
            lines = lines[:-1] if lines and lines[-1] == "" else lines  # the trailing newline
        text = "\n".join(lines)
        if _cell_text(cell) == text:
            continue
        keep = text.splitlines(keepends=True)
        cell["source"] = keep if isinstance(cell.get("source"), list) else text
    return data


def dumps(data: dict) -> str:
    """Notebook JSON the way Jupyter writes it."""
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def cells_text(nb: Notebook, source: str) -> str:
    """The code cells of a joined source as readable text, for diffs."""
    out = []
    for line in source.split("\n"):
        m = _CELL_RE.match(line)
        if m:
            out.append(f"# [cell {m.group(1)}]")
            continue
        k = _KEEP_RE.match(line)
        out.append(nb.kept[int(k.group(1))] if k else line)
    return "\n".join(out)
