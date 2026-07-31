"""Scan Python files for all usages of a given package."""

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set


@dataclass
class Usage:
    """A single usage of a package in a file."""
    file: str
    line: int
    code: str
    attr_chain: str  # e.g. "requests.get" or "requests.packages.urllib3"
    usage_type: str  # "import", "function_call", "attribute_access", "submodule"


class PackageVisitor(ast.NodeVisitor):
    """Walk an AST and collect every reference to the target package."""

    def __init__(self, package_name: str, source_lines: List[str], filepath: str):
        self.package = package_name
        self.lines = source_lines
        self.filepath = filepath
        self.usages: List[Usage] = []
        self.aliases: dict[str, str] = {}  # alias -> real dotted name
        self.from_imports: dict[str, str] = {}  # local name -> full dotted name

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if alias.name == self.package or alias.name.startswith(f"{self.package}."):
                local = alias.asname or alias.name
                self.aliases[local] = alias.name
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
        if module == self.package or module.startswith(f"{self.package}."):
            for alias in node.names:
                local = alias.asname or alias.name
                full = f"{module}.{alias.name}"
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
            self.usages.append(Usage(
                file=self.filepath,
                line=node.lineno,
                code=self.lines[node.lineno - 1].strip(),
                attr_chain=chain,
                usage_type="function_call",
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


def scan_file(filepath: str, package_name: str) -> List[Usage]:
    """Scan a single Python file for usages of package_name."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        lines = source.splitlines()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return []

    visitor = PackageVisitor(package_name, lines, filepath)
    visitor.visit(tree)

    # dedupe by (file, line, attr_chain)
    seen: Set[tuple] = set()
    deduped: List[Usage] = []
    for u in visitor.usages:
        key = (u.file, u.line, u.attr_chain)
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped


def scan_directory(directory: str, package_name: str, exclude_dirs: Optional[Set[str]] = None) -> List[Usage]:
    """Recursively scan a directory for usages of package_name."""
    if exclude_dirs is None:
        exclude_dirs = {".venv", "venv", "env", ".env", "node_modules", "__pycache__",
                        ".git", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
                        ".eggs", "*.egg-info"}

    usages: List[Usage] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.endswith(".egg-info")]
        for fname in files:
            if fname.endswith(".py"):
                fpath = os.path.join(root, fname)
                usages.extend(scan_file(fpath, package_name))
    return usages
