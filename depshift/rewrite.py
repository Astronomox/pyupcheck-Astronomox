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
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple, Union

from depshift.analyzer import COMMON_ALIASES
from depshift.apisurface import APIChange, APISurface, _reachable
from depshift.precise import match_precise
from depshift.scanner import Usage


@dataclass
class Edit:
    """Replace source between two (line, byte-col) positions (1-based lines)."""
    start: Tuple[int, int]
    end: Tuple[int, int]
    new: str


@dataclass
class Fix:
    file: str
    line: int
    description: str
    edits: List[Edit] = field(default_factory=list)


@dataclass
class Manual:
    file: str
    line: int
    code: str
    reason: str


@dataclass
class MigrationPlan:
    fixes: List[Fix] = field(default_factory=list)
    manual: List[Manual] = field(default_factory=list)

    def by_file(self) -> Dict[str, List[Fix]]:
        out: Dict[str, List[Fix]] = {}
        for f in self.fixes:
            out.setdefault(f.file, []).append(f)
        return out


# ── source positions ──────────────────────────────────────────────────────────

def _offset(lines: List[str], pos: Tuple[int, int]) -> int:
    """Character offset of (1-based line, UTF-8 byte column) in the joined source."""
    line, bcol = pos
    before = sum(len(lines[i]) for i in range(line - 1))
    ccol = len(lines[line - 1].encode("utf-8")[:bcol].decode("utf-8", errors="ignore"))
    return before + ccol


def apply_edits(source: str, edits: List[Edit]) -> str:
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
        self.calls: Dict[Tuple[int, int], ast.Call] = {}
        self.attrs: Dict[Tuple[int, int], List[ast.Attribute]] = {}
        self.imports: Dict[int, ast.ImportFrom] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self.calls.setdefault((node.lineno, node.col_offset), node)
            elif isinstance(node, ast.Attribute):
                self.attrs.setdefault((node.lineno, node.col_offset), []).append(node)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                self.imports.setdefault(node.lineno, node)

    def call(self, u: Usage) -> Optional[ast.Call]:
        return self.calls.get((u.line, u.col)) if u.col is not None else None

    def attribute(self, u: Usage, attr: str) -> Optional[ast.Attribute]:
        """The outermost Attribute at the usage position ending in ``attr``."""
        cands = [a for a in self.attrs.get((u.line, u.col), []) if a.attr == attr]
        return max(cands, key=lambda a: (a.end_lineno, a.end_col_offset), default=None)


# ── replacements ──────────────────────────────────────────────────────────────

def _normalize(api: str, roots: List[str]) -> str:
    head, _, rest = api.partition(".")
    full = COMMON_ALIASES.get(head)
    if full and any(full == r or full.startswith(r + ".") for r in roots):
        return f"{full}.{rest}" if rest else full
    return api


def replacement_map(entries: Iterable, roots: List[str]) -> Dict[str, str]:
    """{old api (full and short): replacement} from changelog entries."""
    out: Dict[str, str] = {}
    for e in entries:
        repl = getattr(e, "replacement", None)
        if not repl or not e.api or e.api == "unknown":
            continue
        old = _normalize(e.api, roots)
        new = _normalize(repl, roots)
        out.setdefault(old, new)
        out.setdefault(old.rsplit(".", 1)[-1], new)
    return out


def _resolve_replacement(old_api: str, repl: str, roots: List[str]) -> str:
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
            if parts[-1] == short or (parts[-1] == "__init__" and len(parts) > 1 and parts[-2] == short):
                if param in s.params:
                    return True
        return False
    return param in sig.params


# ── planning ──────────────────────────────────────────────────────────────────

def plan_migration(usages: List[Usage], changes: List[APIChange], new_surface: APISurface,
                   package: Union[str, Iterable[str]], changelog_entries: Iterable = (),
                   returns: Optional[Dict[str, str]] = None,
                   sources: Optional[Dict[str, str]] = None) -> MigrationPlan:
    """Turn the breaking changes that hit ``usages`` into fixes and manual items.

    ``sources`` maps file path -> source text (read from disk when missing).
    """
    roots = [package] if isinstance(package, str) else list(package)
    plan = MigrationPlan()
    repl_map = replacement_map(changelog_entries, roots)
    risks = match_precise(usages, changes, roots, returns)

    sources = dict(sources or {})
    indexes: Dict[str, Optional[_Index]] = {}

    def index_for(path: str) -> Optional[_Index]:
        if path not in indexes:
            if path.endswith(".ipynb"):
                indexes[path] = None
            else:
                try:
                    if path not in sources:
                        with open(path, encoding="utf-8") as f:
                            sources[path] = f.read()
                    indexes[path] = _Index(sources[path])
                except (OSError, SyntaxError, ValueError):
                    indexes[path] = None
        return indexes[path]

    def manual(u: Usage, reason: str):
        plan.manual.append(Manual(u.file, u.line, u.code, reason))

    # group keyword-only changes per call: every positional from the first
    # keyword-only parameter on must be converted together
    kwonly_by_call: Dict[Tuple[str, int, Optional[int]], List[APIChange]] = {}
    handled = set()

    fixed_imports = set()  # (file, api) whose import line is rewritten with an alias
    # imports first, so uses of a re-aliased name know they're covered
    risks = sorted(risks, key=lambda r: r.usage.usage_type != "import")

    for risk in risks:
        u, ch = risk.usage, risk.change
        key = (u.file, u.line, u.col, ch.kind, ch.param, ch.api)
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
            fix = _fix_moved_name(u, ch, idx, repl_map, new_surface, roots)
            if fix:
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

    return plan


def _fix_keyword_only(u: Usage, chs: List[APIChange], idx: _Index) -> Optional[Fix]:
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


def _fix_renamed_kwarg(u: Usage, ch: APIChange, idx: _Index, repl_map: Dict[str, str],
                       new_surface: APISurface) -> Optional[Fix]:
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


def _fix_moved_name(u: Usage, ch: APIChange, idx: _Index, repl_map: Dict[str, str],
                    new_surface: APISurface, roots: List[str]) -> Optional[Fix]:
    old_api = u.attr_chain
    old_short = old_api.rsplit(".", 1)[-1]
    target = ch.replacement or repl_map.get(ch.api) or repl_map.get(old_api) \
        or repl_map.get(old_short)
    if not target:
        return None
    target = _resolve_replacement(old_api, _normalize(target, roots), roots)
    if target == old_api or not _exists(new_surface, target):
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
                   [Edit((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset), new_text)])

    old_mod = old_api.rpartition(".")[0]
    if new_mod == old_mod and new_short != old_short:
        # same place, new name: np.float_ -> np.float64
        attr = idx.attribute(u, old_short)
        if attr is None:
            return None
        end = (attr.end_lineno, attr.end_col_offset)
        start = (attr.end_lineno, attr.end_col_offset - len(old_short.encode("utf-8")))
        return Fix(u.file, u.line, f"use {new_short} instead of {old_short}", [Edit(start, end, new_short)])
    return None
