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
from depshift.changelog import get_pypi_info, get_pypi_version_info


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


MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024  # skip absurdly large distributions

_SDIST_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")
_SOURCE_SUFFIXES = (".py", ".pyi")
_EXT_SUFFIXES = (".so", ".pyd")
# directories inside an sdist that are never part of the importable package
_SDIST_SKIP_DIRS = {"test", "tests", "testing", "docs", "doc", "examples", "example",
                    "benchmarks", "benchmark", "scripts", "tools", "ci", ".github"}


def _wheel_score(filename: str) -> int:
    """Rank wheels: pure-python first, then CPython 3 / manylinux, then anything."""
    fn = filename.lower()
    if fn.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl", "-py3.py2-none-any.whl")):
        return 0
    if "-none-any.whl" in fn:
        return 1
    if "cp3" in fn and ("manylinux" in fn and "x86_64" in fn):
        return 2
    if "cp3" in fn:
        return 3
    return 4


def _find_sdist_or_wheel_url(package: str, version: str) -> Optional[Tuple[str, str]]:
    """Return (url, kind) for a downloadable wheel or sdist for this version.

    Wheels are preferred because their layout maps directly onto import paths.
    Any wheel works (including platform-specific ones) since only the
    Python sources and extension module names are read, never executed.
    """
    key = f"disturl2:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        return tuple(cached) if cached else None

    files = []
    try:
        files = get_pypi_version_info(package, version).get("urls", []) or []
    except Exception:
        files = []
    if not files:
        try:
            files = get_pypi_info(package).get("releases", {}).get(version, []) or []
        except Exception:
            files = []

    wheels = []
    sdist = None
    for f in files:
        if f.get("yanked"):
            continue
        fn = f.get("filename", "")
        url = f.get("url", "")
        size = f.get("size") or 0
        if size and size > MAX_DOWNLOAD_BYTES:
            continue
        if fn.endswith(".whl"):
            wheels.append((_wheel_score(fn), url))
        elif fn.lower().endswith(_SDIST_SUFFIXES) and sdist is None:
            sdist = (url, "sdist")

    result = None
    if wheels:
        wheels.sort(key=lambda w: w[0])
        result = (wheels[0][1], "wheel")
    elif sdist:
        result = sdist
    # Cache a "not found" result as [] rather than None: cache_get() cannot
    # distinguish a cached None from a cache miss, so None here would defeat
    # negative caching and re-trigger a network fetch on every call.
    cache_set(key, list(result) if result else [])
    return result


def _download(url: str) -> Optional[bytes]:
    try:
        resp = httpx.get(url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _wheel_rel_path(name: str) -> Optional[str]:
    """Path of a wheel member relative to the install root, or None to skip."""
    parts = name.replace("\\", "/").split("/")
    if parts[0].endswith(".dist-info"):
        return None
    if parts[0].endswith(".data"):
        # only purelib/platlib end up importable
        if len(parts) > 2 and parts[1] in ("purelib", "platlib"):
            return "/".join(parts[2:])
        return None
    return "/".join(parts)


def _sdist_rel_path(name: str) -> Optional[str]:
    """Path of an sdist member relative to its import root, or None to skip."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if len(parts) < 2:
        return None
    parts = parts[1:]  # drop the "name-version/" top directory
    if parts[0] in ("src", "lib", "python") and len(parts) > 1:
        parts = parts[1:]
    if len(parts) == 1 and parts[0] in ("setup.py", "conftest.py", "noxfile.py",
                                        "tasks.py", "fabfile.py", "versioneer.py"):
        return None
    if any(p in _SDIST_SKIP_DIRS for p in parts[:-1]):
        return None
    return "/".join(parts)


def _module_path(rel: str) -> Optional[str]:
    """Convert 'pkg/sub/mod.py' (or .pyi/.so/.pyd) to 'pkg.sub.mod'."""
    parts = rel.split("/")
    last = parts[-1]
    if last.endswith(_SOURCE_SUFFIXES):
        stem = last.rsplit(".", 1)[0]
    elif last.endswith(_EXT_SUFFIXES):
        stem = last.split(".", 1)[0]  # foo.cpython-311-x86_64-linux-gnu.so -> foo
    else:
        return None
    parts = parts[:-1] if stem == "__init__" else parts[:-1] + [stem]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def _collect(members: List[Tuple[str, bytes]]) -> Tuple[Dict[str, List[str]], Set[str]]:
    """Return ({module_path: [sources]}, {compiled extension module paths})."""
    sources: Dict[str, List[str]] = {}
    extensions: Set[str] = set()
    for rel, raw in members:
        mod = _module_path(rel)
        if not mod:
            continue
        if rel.endswith(_EXT_SUFFIXES):
            extensions.add(mod)
            continue
        sources.setdefault(mod, []).append(raw.decode("utf-8", errors="ignore"))
    return sources, extensions


def _wanted(rel: Optional[str]) -> bool:
    return bool(rel) and rel.endswith(_SOURCE_SUFFIXES + _EXT_SUFFIXES)


def _read_wheel(data: bytes) -> List[Tuple[str, bytes]]:
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                rel = _wheel_rel_path(name)
                if not _wanted(rel):
                    continue
                try:
                    raw = b"" if rel.endswith(_EXT_SUFFIXES) else zf.read(name)
                    out.append((rel, raw))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _read_sdist(data: bytes) -> List[Tuple[str, bytes]]:
    out = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                rel = _sdist_rel_path(member.name)
                if not _wanted(rel):
                    continue
                try:
                    f = tf.extractfile(member)
                    if f:
                        out.append((rel, f.read()))
                except Exception:
                    continue
        if out:
            return out
    except Exception:
        pass
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                rel = _sdist_rel_path(name)
                if not _wanted(rel):
                    continue
                try:
                    out.append((rel, zf.read(name)))
                except Exception:
                    continue
    except Exception:
        pass
    return out


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
    key = f"surface2:{package}:{version}"
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

    members = _read_wheel(data) if kind == "wheel" else _read_sdist(data)
    sources, extensions = _collect(members)
    if not sources and not extensions:
        return None

    surface = APISurface(version=version)
    surface.modules.update(extensions)
    for module_path, srcs in sources.items():
        surface.modules.add(module_path)
        for src in srcs:  # a module may have both a .py and a .pyi stub
            try:
                tree = ast.parse(src)
            except (SyntaxError, ValueError):
                continue  # e.g. Python 2-only sources
            visitor = _SurfaceVisitor(module_path)
            visitor.visit(tree)
            for cls in visitor.classes:
                surface.classes.add(f"{module_path}.{cls}")
            for fname, sig in visitor.functions.items():
                full = f"{module_path}.{fname}"
                sig.name = full
                existing = surface.functions.get(full)
                if existing is not None:
                    # merge overloads / stub+impl: union of params, most permissive flags
                    for p in sig.params:
                        if p not in existing.params:
                            existing.params.append(p)
                    existing.has_varargs = existing.has_varargs or sig.has_varargs
                    existing.has_kwargs = existing.has_kwargs or sig.has_kwargs
                else:
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


def _is_public_api(dotted: str) -> bool:
    """True if no segment of the dotted path is private (underscore-prefixed)."""
    for seg in dotted.split("."):
        if seg.startswith("_") and not (seg.startswith("__") and seg.endswith("__")):
            return False
    return True


def diff_surfaces(old: APISurface, new: APISurface) -> List[APIChange]:
    """Compute precise API changes from old to new surface.

    Only public API is reported — private modules and underscore-prefixed
    names are internal and users shouldn't depend on them.
    """
    changes: List[APIChange] = []

    # removed modules
    for mod in old.modules - new.modules:
        if not _is_public_api(mod):
            continue
        changes.append(APIChange(
            kind="removed_module", api=mod,
            detail=f"Module '{mod}' was removed in {new.version}",
        ))

    # removed classes
    for cls in old.classes - new.classes:
        if not _is_public_api(cls):
            continue
        mod = cls.rsplit(".", 1)[0]
        if mod in new.modules or mod not in (old.modules - new.modules):
            changes.append(APIChange(
                kind="removed_class", api=cls,
                detail=f"Class '{cls}' was removed in {new.version}",
            ))

    # functions removed or changed
    for fname, old_sig in old.functions.items():
        if not _is_public_api(fname):
            continue
        new_sig = new.functions.get(fname)
        if new_sig is None:
            parent, short = fname.rsplit(".", 1)
            if short.startswith("__") and short.endswith("__"):
                continue  # dunders (module __getattr__, __repr__, ...) are not API calls
            # report only if the enclosing module/class still exists; otherwise the
            # removal of the parent is the finding
            if parent in new.modules or parent in new.classes:
                changes.append(APIChange(
                    kind="removed_function", api=fname,
                    detail=f"'{fname}()' was removed in {new.version}",
                ))
            continue

        # parameter removals (ignore if new sig accepts **kwargs)
        old_params = set(old_sig.params) - {"self", "cls"}
        new_params = set(new_sig.params) - {"self", "cls"}
        removed_params = old_params - new_params
        if removed_params and not new_sig.has_kwargs:
            for p in sorted(removed_params):
                changes.append(APIChange(
                    kind="removed_param", api=fname, param=p,
                    detail=f"Parameter '{p}' removed from '{fname}()' in {new.version}",
                ))

    return changes


def get_api_changes(package: str, from_version: str, to_version: str) -> Optional[List[APIChange]]:
    """Extract both surfaces and diff them. Returns None if extraction fails."""
    old = extract_surface(package, from_version)
    new = extract_surface(package, to_version)
    if old is None or new is None:
        return None
    return diff_surfaces(old, new)
