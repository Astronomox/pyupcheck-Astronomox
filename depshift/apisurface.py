"""Extract and diff the public API surface between two versions of a package.

This is the accuracy core of pyupcheck. Instead of guessing breaking changes
from changelog prose, we download both versions, extract their actual public
API (modules, classes, functions, and signatures), and compute a precise diff:
what was removed, what changed signature, what parameters disappeared.
"""

import ast
import io

import tarfile
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import httpx

from depshift.cache import cache_get, cache_set
from depshift.changelog import get_pypi_info


@dataclass
class FuncSig:
    """A function or method signature."""
    name: str  # dotted path within the package, e.g. "Session.get"
    params: List[str]  # ordered parameter names
    has_varargs: bool = False
    has_kwargs: bool = False
    is_async: bool = False


@dataclass
class APISurface:
    """The extracted public API of one version of a package."""
    version: str
    modules: Set[str] = field(default_factory=set)       # importable module paths
    classes: Set[str] = field(default_factory=set)       # dotted class names
    functions: Dict[str, FuncSig] = field(default_factory=dict)  # dotted name -> sig
    names: Set[str] = field(default_factory=set)          # all top-level public names


@dataclass
class APIChange:
    """A precise difference between two API surfaces."""
    kind: str  # "removed_function", "removed_class", "removed_module",
               # "removed_param", "signature_changed"
    api: str   # dotted path
    detail: str
    param: Optional[str] = None


PYPI_FILES_API = "https://pypi.org/pypi/{package}/{version}/json"


def _find_sdist_or_wheel_url(package: str, version: str) -> Optional[Tuple[str, str]]:
    """Return (url, kind) for a downloadable wheel or sdist for this version."""
    key = f"disturl:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        return tuple(cached) if cached else None

    try:
        info = get_pypi_info(package)
    except Exception:
        cache_set(key, [])
        return None

    releases = info.get("releases", {})
    files = releases.get(version, [])

    # prefer a pure-python wheel, then any wheel, then sdist
    wheel = None
    sdist = None
    for f in files:
        fn = f.get("filename", "")
        url = f.get("url", "")
        if fn.endswith("-py3-none-any.whl") or fn.endswith("-py2.py3-none-any.whl"):
            wheel = (url, "wheel")
            break
        if fn.endswith(".whl") and wheel is None:
            wheel = (url, "wheel")
        if (fn.endswith(".tar.gz") or fn.endswith(".zip")) and sdist is None:
            sdist = (url, "sdist")

    result = wheel or sdist
    # Cache a "not found" result as [] rather than None: cache_get() cannot
    # distinguish a cached None from a cache miss, so None here would defeat
    # negative caching and re-trigger a network fetch on every call.
    cache_set(key, list(result) if result else [])
    return result


def _download(url: str) -> Optional[bytes]:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _extract_py_files_from_wheel(data: bytes, package: str) -> Dict[str, str]:
    """Return {module_path: source} for .py files in a wheel."""
    out = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if not name.endswith(".py"):
                    continue
                if ".dist-info/" in name or ".data/" in name:
                    continue
                try:
                    src = zf.read(name).decode("utf-8", errors="ignore")
                    out[name] = src
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _extract_py_files_from_sdist(data: bytes, package: str) -> Dict[str, str]:
    """Return {module_path: source} for .py files in an sdist tarball or zip."""
    out = {}
    # try tar.gz
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.name.endswith(".py"):
                    continue
                if "/test" in member.name or "/docs" in member.name:
                    continue
                try:
                    f = tf.extractfile(member)
                    if f:
                        out[member.name] = f.read().decode("utf-8", errors="ignore")
                except Exception:
                    continue
        if out:
            return out
    except Exception:
        pass
    # try zip
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith(".py") and "/test" not in name and "/docs" not in name:
                    try:
                        out[name] = zf.read(name).decode("utf-8", errors="ignore")
                    except Exception:
                        continue
    except Exception:
        pass
    return out


def _module_path_from_file(filepath: str, package: str) -> Optional[str]:
    """Convert a file path inside the archive to a dotted module path."""
    parts = filepath.replace("\\", "/").split("/")
    if package not in parts:
        # find the package root by locating an __init__.py chain; fall back
        if package + "/" not in filepath and not filepath.startswith(package):
            return None
        idx = 0
    else:
        idx = parts.index(package)
    rel = parts[idx:]
    if not rel:
        return None
    if rel[-1] == "__init__.py":
        rel = rel[:-1]
    elif rel[-1].endswith(".py"):
        rel[-1] = rel[-1][:-3]
    else:
        return None
    return ".".join(rel)


class _SurfaceVisitor(ast.NodeVisitor):
    """Extract public classes and functions with signatures from a module AST."""

    def __init__(self, module_path: str):
        self.module = module_path
        self.classes: Set[str] = set()
        self.functions: Dict[str, FuncSig] = {}
        self.names: Set[str] = set()
        self._class_stack: List[str] = []

    def _public(self, name: str) -> bool:
        return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))

    def visit_ClassDef(self, node: ast.ClassDef):
        if not self._public(node.name):
            return
        dotted = ".".join(self._class_stack + [node.name])
        self.classes.add(dotted)
        self.names.add(node.name)
        self._class_stack.append(node.name)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_func(item)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if not self._class_stack:
            self._add_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if not self._class_stack:
            self._add_func(node)

    def _add_func(self, node):
        if not self._public(node.name):
            return
        dotted = ".".join(self._class_stack + [node.name])
        args = node.args
        params = []
        for a in args.posonlyargs + args.args:
            params.append(a.arg)
        for a in args.kwonlyargs:
            params.append(a.arg)
        sig = FuncSig(
            name=dotted,
            params=params,
            has_varargs=args.vararg is not None,
            has_kwargs=args.kwarg is not None,
            is_async=isinstance(node, ast.AsyncFunctionDef),
        )
        self.functions[dotted] = sig
        self.names.add(node.name)


def extract_surface(package: str, version: str) -> Optional[APISurface]:
    """Download a version and extract its public API surface (cached)."""
    key = f"surface:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        surf = APISurface(version=version)
        surf.modules = set(cached["modules"])
        surf.classes = set(cached["classes"])
        surf.names = set(cached["names"])
        surf.functions = {
            k: FuncSig(**v) for k, v in cached["functions"].items()
        }
        return surf

    dist = _find_sdist_or_wheel_url(package, version)
    if not dist:
        return None
    url, kind = dist
    data = _download(url)
    if not data:
        return None

    if kind == "wheel":
        files = _extract_py_files_from_wheel(data, package)
    else:
        files = _extract_py_files_from_sdist(data, package)

    if not files:
        return None

    surface = APISurface(version=version)
    for filepath, src in files.items():
        module_path = _module_path_from_file(filepath, package)
        if not module_path:
            continue
        surface.modules.add(module_path)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        visitor = _SurfaceVisitor(module_path)
        visitor.visit(tree)
        for cls in visitor.classes:
            surface.classes.add(f"{module_path}.{cls}")
        for fname, sig in visitor.functions.items():
            full = f"{module_path}.{fname}"
            sig.name = full
            surface.functions[full] = sig
        surface.names.update(visitor.names)

    cache_set(key, {
        "modules": list(surface.modules),
        "classes": list(surface.classes),
        "names": list(surface.names),
        "functions": {
            k: {"name": v.name, "params": v.params, "has_varargs": v.has_varargs,
                "has_kwargs": v.has_kwargs, "is_async": v.is_async}
            for k, v in surface.functions.items()
        },
    })
    return surface


