"""Scan Python files for all usages of a given package."""

import ast
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Union


@dataclass
class Usage:
    """A single usage of a package in a file."""
    file: str
    line: int
    code: str
    attr_chain: str  # e.g. "requests.get" or "requests.packages.urllib3"
    usage_type: str  # "import", "function_call", "attribute_access", "submodule"
    call_args: Optional[List[str]] = None  # positional arg count marker + keyword names
    kwargs: Optional[List[str]] = None  # keyword argument names used in a call
    col: Optional[int] = None  # column (UTF-8 byte offset) of the node, for rewriting
    # notebooks: the cell number and the line within it; line is the .ipynb
    # file's line holding that source line, so file:line points at it
    cell: Optional[int] = None
    cell_line: Optional[int] = None


class PackageVisitor(ast.NodeVisitor):
    """Walk an AST and collect every reference to the target package."""

    def __init__(self, package_name: str, source_lines: List[str], filepath: str):
        self.package = package_name
        self.lines = source_lines
        self.filepath = filepath
        self.usages: List[Usage] = []
        self.aliases: Dict[str, str] = {}  # alias -> real dotted name
        self.from_imports: Dict[str, str] = {}  # local name -> full dotted name
        # variable -> package class it holds an instance of, e.g. s -> requests.Session
        self.instances: Dict[str, str] = {}
        self._call_funcs: Set[int] = set()  # Name nodes already recorded as calls
        # light inference, filled by prepare() before the walk
        self.func_returns: Dict[str, str] = {}             # user function -> package class it returns
        self.attr_types: Dict[tuple, str] = {}             # (user class, attr) -> package class
        self._class_stack: List[str] = []

    # ── pre-pass: what user functions return, what classes keep on self ──────

    def prepare(self, tree):
        """Learn imports, user function return types and ``self.x`` attribute
        types before the main walk, so ``engine = get_engine()`` and
        ``self.engine.execute()`` resolve to the package API."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if self._is_package_ref(a.name) or self.package.startswith(f"{a.name}."):
                        if a.asname:
                            self.aliases[a.asname] = a.name
                        else:
                            first = a.name.split(".")[0]
                            self.aliases[first] = first
            elif isinstance(node, ast.ImportFrom) and not node.level:
                module = node.module or ""
                for a in node.names:
                    full = f"{module}.{a.name}"
                    if a.name != "*" and (self._is_package_ref(module) or self._is_package_ref(full)):
                        self.from_imports[a.asname or a.name] = full

        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        for _ in range(3):  # helpers that call helpers
            for fn in funcs:
                if fn.name in self.func_returns:
                    continue
                cls = self._annotation_class(fn.returns) or self._infer_returns(fn)
                if cls:
                    self.func_returns[fn.name] = cls
            for c in classes:
                for item in ast.walk(c):
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        cls = self._annotation_class(item.annotation)
                        if cls:
                            self.attr_types.setdefault((c.name, item.target.id), cls)
                    targets, value = None, None
                    if isinstance(item, ast.Assign):
                        targets, value = item.targets, item.value
                    elif isinstance(item, ast.AnnAssign):
                        targets, value = [item.target], item.value
                    if not targets:
                        continue
                    for t in targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                                and t.value.id == "self":
                            cls = (self._annotation_class(item.annotation) if isinstance(item, ast.AnnAssign)
                                   else None) or (self._class_of(value) if value is not None else None)
                            if cls:
                                self.attr_types.setdefault((c.name, t.attr), cls)

    def _infer_returns(self, fn) -> Optional[str]:
        """Package class a function returns, from its return statements."""
        local: Dict[str, str] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                cls = self._class_of(node.value)
                for t in node.targets:
                    if isinstance(t, ast.Name) and cls:
                        local[t.id] = cls
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and node.value is not None:
                if isinstance(node.value, ast.Name) and node.value.id in local:
                    return local[node.value.id]
                cls = self._class_of(node.value)
                if cls:
                    return cls
        return None

    def visit_ClassDef(self, node: ast.ClassDef):
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    # ── light type tracking ──────────────────────────────────────────────────

    def _class_of(self, value) -> Optional[str]:
        """Package class an expression evaluates to an instance of, if obvious."""
        if isinstance(value, ast.Call):
            if isinstance(value.func, ast.Name) and value.func.id in self.func_returns:
                return self.func_returns[value.func.id]  # engine = get_engine()
            if isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Name) \
                    and value.func.value.id == "self" and value.func.attr in self.func_returns:
                return self.func_returns[value.func.attr]  # self.make_engine()
            chain = self._resolve_attr_chain(value.func)
            if chain and self._is_package_ref(chain):
                return chain
        if isinstance(value, ast.Await):
            return self._class_of(value.value)
        return None

    def _annotation_class(self, ann) -> Optional[str]:
        if ann is None:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(ann, ast.Subscript):  # Optional[pkg.X]
            return self._annotation_class(ann.slice)
        if isinstance(ann, ast.BinOp):  # pkg.X | None
            return self._annotation_class(ann.left) or self._annotation_class(ann.right)
        chain = self._resolve_attr_chain(ann)
        if chain and self._is_package_ref(chain):
            return chain
        return None

    def _bind(self, target, cls: Optional[str]):
        if isinstance(target, ast.Name):
            if cls:
                self.instances[target.id] = cls
            else:
                self.instances.pop(target.id, None)

    def visit_Assign(self, node: ast.Assign):
        self.generic_visit(node)
        cls = self._class_of(node.value)
        for t in node.targets:
            self._bind(t, cls)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.generic_visit(node)
        self._bind(node.target, self._annotation_class(node.annotation) or
                   (self._class_of(node.value) if node.value else None))

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind(item.optional_vars, self._class_of(item.context_expr))
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncWith = visit_With

    def visit_FunctionDef(self, node):
        saved = dict(self.instances)
        args = node.args
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            self._bind(ast.Name(id=a.arg), self._annotation_class(a.annotation))
        self.generic_visit(node)
        self.instances = saved  # parameters are local to the function

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if self._is_package_ref(alias.name) or self.package.startswith(f"{alias.name}."):
                if alias.asname:
                    self.aliases[alias.asname] = alias.name
                else:
                    # `import a.b.c` binds the name `a`
                    first = alias.name.split(".")[0]
                    self.aliases[first] = first
                if not self._is_package_ref(alias.name):
                    continue  # parent of a namespace package, e.g. `import google`
                self.usages.append(Usage(
                    file=self.filepath,
                    line=node.lineno, col=node.col_offset,
                    code=self.lines[node.lineno - 1].strip(),
                    attr_chain=alias.name,
                    usage_type="import",
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        if node.level:
            return  # relative imports never refer to a third-party package
        for alias in node.names:
            if alias.name == "*":
                if self._is_package_ref(module):
                    self.usages.append(Usage(
                        file=self.filepath, line=node.lineno, col=node.col_offset,
                        code=self.lines[node.lineno - 1].strip(),
                        attr_chain=module, usage_type="import",
                    ))
                continue
            full = f"{module}.{alias.name}"
            # `from google import protobuf` for package "google.protobuf"
            if self._is_package_ref(module) or self._is_package_ref(full):
                local = alias.asname or alias.name
                self.from_imports[local] = full
                self.usages.append(Usage(
                    file=self.filepath,
                    line=node.lineno, col=node.col_offset,
                    code=self.lines[node.lineno - 1].strip(),
                    attr_chain=full,
                    usage_type="import",
                ))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name):
            self._call_funcs.add(id(root))  # part of a longer chain, recorded there
        chain = self._resolve_attr_chain(node)
        if chain and self._is_package_ref(chain):
            self.usages.append(Usage(
                file=self.filepath,
                line=node.lineno, col=node.col_offset,
                code=self.lines[node.lineno - 1].strip(),
                attr_chain=chain,
                usage_type="attribute_access",
            ))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        """A from-imported name used on its own: a base class, an argument,
        a decorator, a value (``class S(BaseSettings)``, ``f(Markup)``)."""
        if not isinstance(node.ctx, ast.Load) or id(node) in self._call_funcs:
            return
        full = self.from_imports.get(node.id)
        if full and self._is_package_ref(full):
            self.usages.append(Usage(
                file=self.filepath, line=node.lineno, col=node.col_offset,
                code=self.lines[node.lineno - 1].strip(),
                attr_chain=full, usage_type="attribute_access",
            ))

    def visit_Call(self, node: ast.Call):
        self._call_funcs.add(id(node.func))
        chain = self._resolve_attr_chain(node.func)
        if chain and self._is_package_ref(chain):
            kwargs = [kw.arg for kw in node.keywords if kw.arg]
            self.usages.append(Usage(
                file=self.filepath,
                line=node.lineno, col=node.col_offset,
                code=self.lines[node.lineno - 1].strip(),
                attr_chain=chain,
                usage_type="function_call",
                call_args=[f"pos:{len(node.args)}"],
                kwargs=kwargs,
            ))

        # detect dynamic imports: importlib.import_module("pkg"), __import__("pkg")
        func = node.func
        is_import_module = (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
        ) or (
            isinstance(func, ast.Name) and func.id == "__import__"
        )
        if is_import_module and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                mod = first_arg.value
                if mod == self.package or mod.startswith(f"{self.package}."):
                    self.usages.append(Usage(
                        file=self.filepath,
                        line=node.lineno, col=node.col_offset,
                        code=self.lines[node.lineno - 1].strip(),
                        attr_chain=mod,
                        usage_type="dynamic_import",
                    ))

        self.generic_visit(node)

    def _resolve_attr_chain(self, node) -> Optional[str]:
        """Turn a nested Attribute node into a dotted string."""
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Call) and parts:
            # method on a call result: get_engine().execute, pd.DataFrame(...).append
            base = self._class_of(current)
            if base:
                parts.reverse()
                return f"{base}.{'.'.join(parts)}"
            return None
        if isinstance(current, ast.Name) and current.id == "self" and len(parts) >= 2 \
                and self._class_stack:
            # self.engine.execute -> sqlalchemy.create_engine.execute
            parts.reverse()
            cls = self.attr_types.get((self._class_stack[-1], parts[0]))
            if cls:
                return f"{cls}.{'.'.join(parts[1:])}"
            return None
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            name = parts[0]
            rest = ".".join(parts[1:])
            # resolve aliases
            if name in self.aliases:
                return f"{self.aliases[name]}.{rest}" if rest else self.aliases[name]
            if name in self.from_imports:
                return f"{self.from_imports[name]}.{rest}" if rest else self.from_imports[name]
            if name in self.instances and rest:
                # method/attribute on an instance: s.get -> requests.Session.get
                return f"{self.instances[name]}.{rest}"
            if name == self.package:
                return ".".join(parts)
        return None

    def _is_package_ref(self, chain: str) -> bool:
        return chain == self.package or chain.startswith(f"{self.package}.")


PackageNames = Union[str, Iterable[str]]


def _as_names(package_name: PackageNames) -> List[str]:
    if isinstance(package_name, str):
        return [package_name]
    return [n for n in package_name if n]


def scan_file(filepath: str, package_name: PackageNames) -> List[Usage]:
    """Scan a single Python file for usages of package_name.

    package_name may be one import name or several (e.g. ``["attr", "attrs"]``).
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        lines = source.splitlines()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, ValueError, OSError):
        return []

    found: List[Usage] = []
    for name in _as_names(package_name):
        visitor = PackageVisitor(name, lines, filepath)
        visitor.prepare(tree)
        visitor.visit(tree)
        found.extend(visitor.usages)

    # dedupe by (file, line, attr_chain)
    seen: Set[tuple] = set()
    deduped: List[Usage] = []
    for u in found:
        key = (u.file, u.line, u.attr_chain)
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped


def scan_notebook(filepath: str, package_name: PackageNames) -> List[Usage]:
    """Scan a Jupyter notebook's code cells for usages of package_name.

    The cells are scanned as one module, as they run, so a name imported in
    one cell is followed into the others. Each usage has its ``cell`` and
    ``cell_line``; its ``line`` is the line of the .ipynb file."""
    from depshift import notebook
    nb = notebook.load(filepath)
    if nb is None:
        return []
    try:
        tree = ast.parse(nb.source)
    except (SyntaxError, ValueError):
        return []
    lines = nb.source.splitlines()
    usages: List[Usage] = []
    seen: Set[tuple] = set()
    for name in _as_names(package_name):
        visitor = PackageVisitor(name, lines, filepath)
        visitor.prepare(tree)
        visitor.visit(tree)
        for u in visitor.usages:
            key = (u.line, u.attr_chain)
            if key in seen:
                continue
            seen.add(key)
            u.cell, u.cell_line = nb.locate(u.line)
            u.line = nb.file_line(u.cell, u.cell_line)
            u.code = f"[cell {u.cell}] {u.code}"
            usages.append(u)
    return usages


def scan_directory(directory: str, package_name: PackageNames, exclude_dirs: Optional[Set[str]] = None,
                   include_notebooks: bool = True) -> List[Usage]:
    """Recursively scan a directory for usages of package_name."""
    default_excludes = set(DEFAULT_EXCLUDES)
    if exclude_dirs:
        default_excludes = default_excludes | set(exclude_dirs)
    exclude_dirs = default_excludes

    usages: List[Usage] = []
    for fpath in _source_files(directory, exclude_dirs, include_notebooks):
        if fpath.endswith(".ipynb"):
            usages.extend(scan_notebook(fpath, package_name))
        else:
            usages.extend(scan_file(fpath, package_name))
    return usages


DEFAULT_EXCLUDES = {".venv", "venv", "env", ".env", "node_modules", "__pycache__",
                    ".git", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
                    ".eggs", ".ipynb_checkpoints", ".nox", ".hg", ".svn",
                    "site-packages", "__pypackages__", ".ruff_cache"}


def _source_files(directory: str, exclude_dirs: Set[str], include_notebooks: bool = True):
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.endswith(".egg-info")]
        for fname in files:
            if fname.endswith((".py", ".pyi")) or (include_notebooks and fname.endswith(".ipynb")):
                yield os.path.join(root, fname)


def imported_top_modules(directory: str, exclude_dirs: Optional[Set[str]] = None) -> Set[str]:
    """Top-level names of every absolute import in the project (``import a.b``
    and ``from a.b import c`` both give ``a``), plus dotted prefixes so
    namespace packages (``google.protobuf``) can be matched too."""
    excludes = DEFAULT_EXCLUDES | set(exclude_dirs or ())
    found: Set[str] = set()
    for fpath in _source_files(directory, excludes, include_notebooks=False):
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, ValueError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                mods = [node.module]
            else:
                continue
            for m in mods:
                parts = m.split(".")
                found.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return found
