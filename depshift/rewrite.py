"""Plan and apply safe, mechanical code rewrites for an upgrade.

Only changes that can be made without guessing are applied, and every
replacement is checked against the target version's real API surface:

* a parameter that became keyword-only:   f(a, 1)        -> f(a, b=1)
* a renamed keyword argument:             Retry(method_whitelist=x) -> Retry(allowed_methods=x)
* a name that moved or was renamed:       from pkg.old import X -> from pkg.new import X
                                          np.float_      -> np.float64

Everything else that breaks is reported as needing a manual change, with
the reason. Edits are made from exact AST positions, so formatting and
comments are untouched.
"""

import ast
import difflib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from depshift import notebook
from depshift.analyzer import COMMON_ALIASES
from depshift.apisurface import APIChange, APISurface, _reachable
from depshift.precise import match_precise
from depshift.scanner import Usage


@dataclass
class Edit:
    """Replace source between two (line, byte-col) positions (1-based lines)."""
    start: tuple[int, int | None]  # col None: end of that line
    end: tuple[int, int | None]
    new: str


@dataclass
class Fix:
    file: str
    line: int
    description: str
    edits: list[Edit] = field(default_factory=list)
    needs_import: str | None = None  # module the rewritten code refers to
    cell: int | None = None  # notebook cell; line is then within it (edits are not)


@dataclass
class Manual:
    file: str
    line: int
    code: str
    reason: str
    cell: int | None = None


@dataclass
class MigrationPlan:
    fixes: list[Fix] = field(default_factory=list)
    manual: list[Manual] = field(default_factory=list)

    def by_file(self) -> dict[str, list[Fix]]:
        out: dict[str, list[Fix]] = {}
        for f in self.fixes:
            out.setdefault(f.file, []).append(f)
        return out


# ── source positions ──────────────────────────────────────────────────────────

def _offset(lines: list[str], pos: tuple[int, int | None]) -> int:
    """Character offset of (1-based line, UTF-8 byte column) in the joined source."""
    line, bcol = pos
    if bcol is None:  # end of that line, including its newline
        return sum(len(lines[i]) for i in range(line))
    before = sum(len(lines[i]) for i in range(line - 1))
    ccol = len(lines[line - 1].encode("utf-8")[:bcol].decode("utf-8", errors="ignore"))
    return before + ccol


def _start(node: ast.AST) -> tuple[int, int]:
    """Where a parsed node starts (1-based line, UTF-8 byte column)."""
    return node.lineno, node.col_offset  # type: ignore[attr-defined]


def _end(node: ast.AST) -> tuple[int, int]:
    """Where a parsed node ends. ``ast.parse`` always sets end positions; the
    stubs only call them optional for hand-built nodes."""
    end_line, end_col = node.end_lineno, node.end_col_offset  # type: ignore[attr-defined]
    assert end_line is not None and end_col is not None
    return end_line, end_col


def apply_edits(source: str, edits: list[Edit]) -> str:
    """Apply non-overlapping edits; overlapping ones after the first are skipped."""
    lines = source.splitlines(keepends=True)
    spans = sorted(((_offset(lines, e.start), _offset(lines, e.end), e.new) for e in edits),
                   key=lambda s: s[0])
    out, cursor = [], 0
    for start, end, new in spans:
        if start < cursor:
            continue  # overlaps an earlier edit: leave it for a later run
        out.append(source[cursor:start])
        out.append(new)
        cursor = end
    out.append(source[cursor:])
    return "".join(out)


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


# ── finding the AST node a usage points at ────────────────────────────────────

class _Index:
    """AST nodes of one file by (line, col)."""

    def __init__(self, source: str):
        self.tree = ast.parse(source)
        self.lines = source.splitlines()
        self.n_lines = len(source.splitlines())
        self.ends_with_newline = source.endswith("\n")
        self.calls: dict[tuple[int, int], ast.Call] = {}
        self.attrs: dict[tuple[int, int], list[ast.Attribute]] = {}
        self.imports: dict[int, ast.ImportFrom] = {}
        # modules bound by a top-level `import x` / `import x.y`, and the line
        # after the last top-level import (or after the docstring) for new ones
        self.imported_modules: set[str] = set()
        self.insert_line = 1
        body = self.tree.body
        first = body[0] if body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            self.insert_line = _end(first)[0] + 1  # after the module docstring
        for stmt in body:
            if isinstance(stmt, ast.Import):
                self.imported_modules.update(a.name for a in stmt.names if not a.asname)
                self.insert_line = _end(stmt)[0] + 1
            elif isinstance(stmt, ast.ImportFrom):
                self.insert_line = _end(stmt)[0] + 1
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self.calls.setdefault((node.lineno, node.col_offset), node)
            elif isinstance(node, ast.Attribute):
                self.attrs.setdefault((node.lineno, node.col_offset), []).append(node)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                self.imports.setdefault(node.lineno, node)

    def call(self, u: Usage) -> ast.Call | None:
        return self.calls.get((u.line, u.col)) if u.col is not None else None

    def attribute(self, u: Usage, attr: str) -> ast.Attribute | None:
        """The outermost Attribute at the usage position ending in ``attr``."""
        if u.col is None:
            return None
        cands = [a for a in self.attrs.get((u.line, u.col), []) if a.attr == attr]
        return max(cands, key=_end, default=None)


# ── replacements ──────────────────────────────────────────────────────────────

def _normalize(api: str, roots: list[str]) -> str:
    head, _, rest = api.partition(".")
    full = COMMON_ALIASES.get(head)
    if full and any(full == r or full.startswith(r + ".") for r in roots):
        return f"{full}.{rest}" if rest else full
    return api


def replacement_map(entries: Iterable, roots: list[str]) -> dict[str, str]:
    """{old api (full and short): replacement} from changelog entries."""
    out: dict[str, str] = {}
    for e in entries:
        repl = getattr(e, "replacement", None)
        if not repl or not e.api or e.api == "unknown":
            continue
        old = _normalize(e.api, roots)
        new = _normalize(repl, roots)
        out.setdefault(old, new)
        out.setdefault(old.rsplit(".", 1)[-1], new)
    return out


def _resolve_replacement(old_api: str, repl: str, roots: list[str]) -> str:
    """Make a replacement fully qualified: a bare name lives where the old one did."""
    if any(repl == r or repl.startswith(r + ".") for r in roots):
        return repl
    parent = old_api.rsplit(".", 1)[0]
    return f"{parent}.{repl}" if "." not in repl else repl


def _exists(surface: APISurface, dotted: str) -> bool:
    return _reachable(surface, dotted) or (
        "." in dotted and dotted.rsplit(".", 1)[1] in surface.exports.get(dotted.rsplit(".", 1)[0], ()))


def _param_exists(surface: APISurface, func_api: str, param: str) -> bool:
    sig = surface.functions.get(func_api) or surface.functions.get(f"{func_api}.__init__")
    if sig is None:
        # usage path (pkg.Retry) differs from the defining path (pkg.util.retry.Retry)
        short = func_api.rsplit(".", 1)[-1]
        for name, s in surface.functions.items():
            parts = name.split(".")
            named = parts[-1] == short or (parts[-1] == "__init__" and len(parts) > 1 and parts[-2] == short)
            if named and param in s.params:
                return True
        return False
    return param in sig.params


def exists_elsewhere(dotted: str) -> bool:
    """Does ``dotted`` (e.g. ``markupsafe.Markup``) exist outside the package
    being upgraded? Standard-library modules are checked in the running
    Python; third-party ones by reading the API of their latest release."""
    import os
    import sys
    import sysconfig
    module, _, name = dotted.rpartition(".")
    if not module or not name:
        return False
    top = module.split(".")[0]
    if top in getattr(sys, "stdlib_module_names", ()):
        # read the module's source rather than importing it: a name taken
        # from changelog text must never decide what code runs
        from depshift.apisurface import build_surface
        base = os.path.join(sysconfig.get_paths()["stdlib"], *module.split("."))
        for path in (base + ".py", os.path.join(base, "__init__.py")):
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        surface = build_surface("stdlib", {module: [f.read()]})
                except (OSError, ValueError):
                    return False
                return _exists(surface, dotted)
        return False  # C module without Python source: can't verify
    from depshift.apisurface import extract_surface
    from depshift.changelog import get_current_version
    try:
        published = extract_surface(top, get_current_version(top))
    except Exception:
        return False
    return published is not None and _exists(published, dotted)


# ── planning ──────────────────────────────────────────────────────────────────

def plan_migration(usages: list[Usage], changes: list[APIChange], new_surface: APISurface,
                   package: str | Iterable[str], changelog_entries: Iterable = (),
                   returns: dict[str, str] | None = None,
                   sources: dict[str, str] | None = None,
                   verify_external: Callable[[str], bool] | None = None) -> MigrationPlan:
    """Turn the breaking changes that hit ``usages`` into fixes and manual items.

    ``sources`` maps file path -> source text (read from disk when missing).
    A notebook's edits apply to its joined source (:func:`notebook.load`);
    its fixes and manual items carry the cell and the line within it.
    """
    roots = [package] if isinstance(package, str) else list(package)
    plan = MigrationPlan()
    repl_map = replacement_map(changelog_entries, roots)

    # notebook usages in the coordinates of the notebook's joined source
    notebooks: dict[str, notebook.Notebook | None] = {}
    joined = []
    for u in usages:
        if u.cell is not None:
            if u.file not in notebooks:
                notebooks[u.file] = notebook.load(u.file)
            nb = notebooks[u.file]
            if nb is None or u.cell not in nb.starts or u.cell_line is None:
                continue
            u = replace(u, line=nb.global_line(u.cell, u.cell_line))
        joined.append(u)
    usages = joined
    risks = match_precise(usages, changes, roots, returns)

    sources = dict(sources or {})
    indexes: dict[str, _Index | None] = {}

    def index_for(path: str) -> _Index | None:
        if path not in indexes:
            nb = notebooks.get(path)
            idx: _Index | None
            try:
                if nb is not None:
                    sources[path] = nb.source
                elif path not in sources:
                    with open(path, encoding="utf-8") as f:
                        sources[path] = f.read()
                idx = _Index(sources[path])
            except (OSError, SyntaxError, ValueError, AttributeError):
                idx = None
            if nb is not None and idx is not None:
                # new imports go in a cell, never above the first cell's marker
                idx.insert_line = max(idx.insert_line, 2)
            indexes[path] = idx
        return indexes[path]

    def manual(u: Usage, reason: str):
        plan.manual.append(Manual(u.file, u.line, u.code, reason))

    # group keyword-only changes per call: every positional from the first
    # keyword-only parameter on must be converted together
    kwonly_by_call: dict[tuple[str, int, int | None], list[APIChange]] = {}
    handled = set()

    fixed_imports = set()  # (file, api) whose import line is rewritten with an alias
    planned_spans = set()  # (file, edits) already planned, to list each fix once
    # imports first, so uses of a re-aliased name know they're covered
    risks = sorted(risks, key=lambda r: r.usage.usage_type != "import")

    for risk in risks:
        u, ch = risk.usage, risk.change
        key = (u.file, u.line, u.col, u.attr_chain, ch.kind, ch.param, ch.api)
        if key in handled:
            continue
        handled.add(key)
        idx = index_for(u.file)

        if ch.kind == "became_keyword_only":
            kwonly_by_call.setdefault((u.file, u.line, u.col), []).append(ch)
            continue

        if idx is None:
            manual(u, f"{risk.reason} (file can't be rewritten automatically)")
            continue

        if ch.kind == "removed_param":
            fix = _fix_renamed_kwarg(u, ch, idx, repl_map, new_surface)
            if fix:
                plan.fixes.append(fix)
            else:
                manual(u, f"{risk.reason}: no verified replacement parameter")
            continue

        if ch.kind in ("removed_function", "removed_class", "removed_attribute", "removed_export"):
            if u.usage_type != "import" and (u.file, u.attr_chain) in fixed_imports \
                    and idx.attribute(u, u.attr_chain.rsplit(".", 1)[-1]) is None:
                continue  # bare name from a rewritten import: still valid via its alias
            fix = _fix_moved_name(u, ch, idx, repl_map, new_surface, roots, verify_external)
            if fix:
                spans = (fix.file, tuple((e.start, e.end, e.new) for e in fix.edits))
                if spans in planned_spans:
                    continue  # same rewrite reached through another usage on this spot
                planned_spans.add(spans)
                plan.fixes.append(fix)
                if u.usage_type == "import":
                    fixed_imports.add((u.file, u.attr_chain))
            else:
                manual(u, f"{risk.reason}: no verified replacement")
            continue

        if ch.kind == "added_required_param":
            manual(u, f"{risk.reason}: pass a value for '{ch.param}' (can't be chosen automatically)")
            continue

        manual(u, risk.reason)

    for (path, line, col), chs in kwonly_by_call.items():
        u = next(r.usage for r in risks if (r.usage.file, r.usage.line, r.usage.col) == (path, line, col))
        idx = index_for(path)
        fix = _fix_keyword_only(u, chs, idx) if idx else None
        if fix:
            plan.fixes.append(fix)
        else:
            manual(u, "; ".join(c.detail for c in chs) + " (could not convert the call automatically)")

    _add_needed_imports(plan, index_for)
    for item in plan.fixes + plan.manual:
        nb = notebooks.get(item.file)
        if nb is not None:
            item.cell, item.line = nb.locate(item.line)
    return plan


def idx_text_quote(idx: "_Index", node) -> str:
    """The quote character a string literal was written with."""
    line = idx.lines[node.lineno - 1].encode("utf-8")
    return line[node.col_offset:node.col_offset + 1].decode("utf-8", "replace")


def _plain_dotted(node) -> bool:
    """``a.b.c`` made only of names, nothing called or subscripted."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _dotted_text(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    parts.append(getattr(node, "id", "?"))
    return ".".join(reversed(parts))


def _add_needed_imports(plan: "MigrationPlan", index_for) -> None:
    """One `import x` per file and module, after the file's existing imports,
    unless the file already has it."""
    done = set()
    for fix in plan.fixes:
        mod = fix.needs_import
        if not mod or (fix.file, mod) in done:
            continue
        done.add((fix.file, mod))
        idx = index_for(fix.file)
        if idx is None or mod in idx.imported_modules:
            continue
        if idx.insert_line > idx.n_lines:
            # the imports are the last lines of the file: append at the end
            last = (idx.n_lines, None)
            prefix = "" if idx.ends_with_newline else "\n"
            fix.edits.append(Edit(last, last, f"{prefix}import {mod}\n"))
        else:
            fix.edits.append(Edit((idx.insert_line, 0), (idx.insert_line, 0), f"import {mod}\n"))


def _fix_keyword_only(u: Usage, chs: list[APIChange], idx: _Index) -> Fix | None:
    call = idx.call(u)
    if call is None:
        return None
    names = {c.position: c.param for c in chs if c.position is not None}
    first = min(names, default=None)
    if first is None:
        return None
    edits = []
    for i, arg in enumerate(call.args):
        if i < first:
            continue
        if isinstance(arg, ast.Starred) or i not in names:
            return None  # *args or a parameter we can't name: don't guess
        edits.append(Edit((arg.lineno, arg.col_offset), (arg.lineno, arg.col_offset), f"{names[i]}="))
    if not edits:
        return None
    params = ", ".join(f"'{names[i]}'" for i in sorted(names) if i < len(call.args))
    return Fix(u.file, u.line, f"pass {params} by keyword ({chs[0].api.rsplit('.', 1)[-1]})", edits)


def _fix_renamed_kwarg(u: Usage, ch: APIChange, idx: _Index, repl_map: dict[str, str],
                       new_surface: APISurface) -> Fix | None:
    call = idx.call(u)
    if call is None or not ch.param:
        return None
    owner = ch.api[:-len(".__init__")] if ch.api.endswith(".__init__") else ch.api
    candidates = [f"{owner}.{ch.param}", f"{owner.rsplit('.', 1)[-1]}.{ch.param}", ch.param]
    new_param = next((repl_map[c] for c in candidates if c in repl_map), None)
    if not new_param:
        return None
    new_param = new_param.rsplit(".", 1)[-1]
    if not new_param.isidentifier() or not _param_exists(new_surface, ch.api, new_param):
        return None
    kw = next((k for k in call.keywords if k.arg == ch.param), None)
    if kw is None or any(k.arg == new_param for k in call.keywords):
        return None
    start = (kw.lineno, kw.col_offset)
    end = (kw.lineno, kw.col_offset + len(ch.param.encode("utf-8")))
    return Fix(u.file, u.line, f"rename keyword '{ch.param}=' to '{new_param}='",
               [Edit(start, end, new_param)])


def _fix_moved_name(u: Usage, ch: APIChange, idx: _Index, repl_map: dict[str, str],
                    new_surface: APISurface, roots: list[str],
                    verify_external: Callable[[str], bool] | None = None) -> Fix | None:
    old_api = u.attr_chain
    removed_short = ch.api.rsplit(".", 1)[-1]
    parts = old_api.split(".")
    if parts[-1] != removed_short and removed_short in parts[:-1]:
        # the usage reaches through the removed name (flask.Markup(...).format):
        # rewrite the removed part, not the method called on it
        if u.usage_type == "import":
            return None
        old_api = ".".join(parts[:parts.index(removed_short) + 1])
    old_short = old_api.rsplit(".", 1)[-1]
    # the package's own words beat an inferred location
    target = repl_map.get(ch.api) or repl_map.get(old_api) or repl_map.get(old_short) \
        or ch.replacement
    if not target:
        return None
    target = _resolve_replacement(old_api, _normalize(target, roots), roots)
    if target == old_api:
        return None
    external = not any(target == r or target.startswith(r + ".") for r in roots)
    if external:
        # moved to another package (flask.Markup -> markupsafe.Markup): only
        # if that package really has it
        if not (verify_external or exists_elsewhere)(target):
            return None
    elif not _exists(new_surface, target):
        return None
    new_mod, _, new_short = target.rpartition(".")

    if u.usage_type == "import":
        node = idx.imports.get(u.line)
        if node is None or len(node.names) != 1:
            return None  # several names on one import line: leave it to a person
        alias = node.names[0]
        local = alias.asname or alias.name
        new_text = f"from {new_mod} import {new_short}" + (f" as {local}" if local != new_short else "")
        return Fix(u.file, u.line, f"import {new_short} from {new_mod}",
                   [Edit(_start(node), _end(node), new_text)])

    old_mod = old_api.rpartition(".")[0]
    if u.usage_type == "dynamic_attribute":
        # getattr(np, "float_") -> getattr(np, "float64"): only a new name in
        # the same place can be swapped inside the string
        call = idx.call(u)
        if call is None or new_mod != old_mod or len(call.args) != 2:
            return None
        lit = call.args[1]
        if not (isinstance(lit, ast.Constant) and lit.value == old_short) or lit.lineno != lit.end_lineno:
            return None
        quote = '"' if idx_text_quote(idx, lit) == '"' else "'"
        return Fix(u.file, u.line, f"use {new_short} instead of {old_short}",
                   [Edit(_start(lit), _end(lit), f"{quote}{new_short}{quote}")])
    if external:
        # flask.Markup(...) -> markupsafe.Markup(...), plus `import markupsafe`
        attr = idx.attribute(u, old_short)
        if attr is None or not _plain_dotted(attr):
            return None  # e.g. get_module().Markup: not a plain name to rewrite
        return Fix(u.file, u.line, f"use {target} instead of {_dotted_text(attr)}",
                   [Edit(_start(attr), _end(attr), target)],
                   needs_import=new_mod)
    if new_mod == old_mod and new_short != old_short:
        # same place, new name: np.float_ -> np.float64
        attr = idx.attribute(u, old_short)
        if attr is None:
            return None
        end_line, end_col = _end(attr)
        end = (end_line, end_col)
        start = (end_line, end_col - len(old_short.encode("utf-8")))
        return Fix(u.file, u.line, f"use {new_short} instead of {old_short}", [Edit(start, end, new_short)])
    return None
