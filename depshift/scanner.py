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


class PackageVisitor(ast.NodeVisitor):
    """Walk an AST and collect every reference to the target package."""

    def __init__(self, package_name: str, source_lines: List[str], filepath: str):
        self.package = package_name
        self.lines = source_lines
        self.filepath = filepath
        self.usages: List[Usage] = []
        self.aliases: Dict[str, str] = {}  # alias -> real dotted name
        self.from_imports: Dict[str, str] = {}  # local name -> full dotted name

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
                    line=node.lineno,
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
                        file=self.filepath, line=node.lineno,
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
                    line=node.lineno,
                    code=self.lines[node.lineno - 1].strip(),
                    attr_chain=full,
                    usage_type="import",
                ))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        chain = self._resolve_attr_chain(node)
        if chain and self._is_package_ref(chain):
            self.usages.append(Usage(
                file=self.filepath,
                line=node.lineno,
                code=self.lines[node.lineno - 1].strip(),
                attr_chain=chain,
                usage_type="attribute_access",
            ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        chain = self._resolve_attr_chain(node.func)
        if chain and self._is_package_ref(chain):
            kwargs = [kw.arg for kw in node.keywords if kw.arg]
            self.usages.append(Usage(
                file=self.filepath,
                line=node.lineno,
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
                        line=node.lineno,
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
    """Scan a Jupyter notebook's code cells for usages of package_name."""
    import json as _json
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            nb = _json.load(f)
    except Exception:
        return []

    usages: List[Usage] = []
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", [])
        code = "".join(src) if isinstance(src, list) else str(src)
        # strip magics and shell commands
        cleaned = "\n".join(
            l for l in code.splitlines() if not l.lstrip().startswith(("%", "!", "?"))
        )
        try:
            lines = cleaned.splitlines()
            tree = ast.parse(cleaned)
        except SyntaxError:
            continue
        for name in _as_names(package_name):
            visitor = PackageVisitor(name, lines, filepath)
            visitor.visit(tree)
            for u in visitor.usages:
                # u.line stays relative to the cell; the cell number goes in code
                u.code = f"[cell {i + 1}] {u.code}"
                usages.append(u)

    seen: Set[tuple] = set()
    deduped: List[Usage] = []
    for u in usages:
        key = (u.file, u.code, u.line, u.attr_chain)
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped


def scan_directory(directory: str, package_name: PackageNames, exclude_dirs: Optional[Set[str]] = None,
                   include_notebooks: bool = True) -> List[Usage]:
    """Recursively scan a directory for usages of package_name."""
    default_excludes = {".venv", "venv", "env", ".env", "node_modules", "__pycache__",
                        ".git", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
                        ".eggs", ".ipynb_checkpoints", ".nox", ".hg", ".svn",
                        "site-packages", "__pypackages__", ".ruff_cache"}
    if exclude_dirs:
        default_excludes = default_excludes | set(exclude_dirs)
    exclude_dirs = default_excludes

    usages: List[Usage] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.endswith(".egg-info")]
        for fname in files:
            fpath = os.path.join(root, fname)
            if fname.endswith(".py") or fname.endswith(".pyi"):
                usages.extend(scan_file(fpath, package_name))
            elif include_notebooks and fname.endswith(".ipynb"):
                usages.extend(scan_notebook(fpath, package_name))
    return usages
