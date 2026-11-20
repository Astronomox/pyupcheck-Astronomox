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

from depshift.http import http_get

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
    required: List[str] = field(default_factory=list)  # params with no default (excl. self/cls)
    kwonly: List[str] = field(default_factory=list)     # keyword-only params
    posonly: List[str] = field(default_factory=list)    # positional-only params
    returns: Optional[str] = None  # simple class name of the annotated return type


@dataclass
class APISurface:
    """The extracted public API of one version of a package."""
    version: str
    modules: Set[str] = field(default_factory=set)       # importable module paths
    classes: Set[str] = field(default_factory=set)       # dotted class names
    functions: Dict[str, FuncSig] = field(default_factory=dict)  # dotted name -> sig
    names: Set[str] = field(default_factory=set)          # all top-level public names
    # module -> names it makes importable without defining them (from x import y, __all__)
    exports: Dict[str, Set[str]] = field(default_factory=dict)
    # dotted class -> base class simple names
    bases: Dict[str, List[str]] = field(default_factory=dict)
    # public module-level / class-level constants, dotted
    attributes: Set[str] = field(default_factory=set)


@dataclass
class APIChange:
    """A precise difference between two API surfaces."""
    kind: str  # "removed_function", "removed_class", "removed_module",
               # "removed_param", "signature_changed"
    api: str   # dotted path
    detail: str
    param: Optional[str] = None
    replacement: Optional[str] = None  # where a moved/renamed API now lives
    position: Optional[int] = None     # for became_keyword_only: positional index lost
    still_at: List[str] = field(default_factory=list)  # import paths that keep working
    member_of: Optional[str] = None  # short class name when the API is a method/attribute


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
    key = f"disturl3:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        return tuple(cached) if cached else None

    files = []
    lookup_failed = False
    try:
        files = get_pypi_version_info(package, version).get("urls", []) or []
    except Exception:
        files = []
        lookup_failed = True
    if not files:
        try:
            files = get_pypi_info(package).get("releases", {}).get(version, []) or []
            lookup_failed = False
        except Exception:
            files = []
            lookup_failed = True

    # A yanked release is still the code that release contained; use it when
    # it is all there is (the user asked for this exact version).
    usable = [f for f in files if not f.get("yanked")] or files

    wheels = []
    sdist = None
    for f in usable:
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
    if result or not lookup_failed:  # never remember a network failure as "no files"
        cache_set(key, list(result) if result else [])
    return result


def _download(url: str) -> Optional[bytes]:
    try:
        resp = http_get(url, timeout=120, follow_redirects=True)
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
    """Extract public classes, functions with signatures, re-exports and
    constants from a module AST."""

    def __init__(self, module_path: str):
        self.module = module_path
        self.classes: Set[str] = set()
        self.functions: Dict[str, FuncSig] = {}
        self.names: Set[str] = set()
        self.exports: Set[str] = set()
        self.bases: Dict[str, List[str]] = {}
        self.attributes: Set[str] = set()
        self._class_stack: List[str] = []
        self._depth = 0  # >0 would mean inside a function body (never visited)
        self._literals: Dict[str, Set[str]] = {}  # module-level NAME = [..]/{..} string literals
        self._deferred: Set[str] = set()          # names __getattr__ checks `in` (resolved later)
        self.star_imports: List[Tuple[int, str]] = []  # (relative level, module) of `from x import *`
        self.all_names: Optional[Set[str]] = None      # __all__, when the module defines one

    def finalize(self):
        """Resolve names a module ``__getattr__`` serves from module-level literals."""
        for ref in self._deferred:
            self.exports.update(n for n in self._literals.get(ref, ()) if self._public(n))

    def _getattr_names(self, node):
        """Names a PEP 562 module ``__getattr__(name)`` answers for.

        Packages use it to keep deprecated names importable (pandas'
        Int64Index, Pillow's ANTIALIAS, flask's Markup); those names are API
        until they disappear from it.
        """
        if not node.args.args:
            return
        param = node.args.args[0].arg
        local: Dict[str, Set[str]] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 \
                    and isinstance(sub.targets[0], ast.Name):
                lit = _string_literals(sub.value)
                if lit:
                    local[sub.targets[0].id] = lit
        # `if name == "load_boston": raise ImportError(...)` is a tombstone:
        # the name is gone and the module only explains where it went
        tombstones = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.If):
                body = [n for stmt in sub.body for n in ast.walk(stmt)]
                raises = any(isinstance(n, ast.Raise) for n in body)
                returns = any(isinstance(n, (ast.Return, ast.Yield)) for n in body)
                if raises and not returns:
                    tombstones.update(id(n) for n in ast.walk(sub.test) if isinstance(n, ast.Compare))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare) or not isinstance(sub.left, ast.Name) \
                    or sub.left.id != param or id(sub) in tombstones:
                continue
            for op, comp in zip(sub.ops, sub.comparators):
                if isinstance(op, (ast.Eq, ast.In)):
                    lit = _string_literals(comp)
                    if lit:
                        self.exports.update(n for n in lit if self._public(n))
                    elif isinstance(comp, ast.Name):
                        if comp.id in local:
                            self.exports.update(n for n in local[comp.id] if self._public(n))
                        else:
                            self._deferred.add(comp.id)

    def _public(self, name: str) -> bool:
        return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))

    def visit_ClassDef(self, node: ast.ClassDef):
        if self._depth or not self._public(node.name):
            return
        dotted = ".".join(self._class_stack + [node.name])
        self.classes.add(dotted)
        self.names.add(node.name)
        self.bases[dotted] = [_base_name(b) for b in node.bases if _base_name(b)]
        self._class_stack.append(node.name)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_func(item)
            elif isinstance(item, ast.ClassDef):
                self.visit_ClassDef(item)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                self._add_assign(item)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if not self._class_stack and not self._depth:
            self._add_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if not self._class_stack and not self._depth:
            self._add_func(node)

    def visit_Import(self, node: ast.Import):
        if self._class_stack or self._depth:
            return
        for a in node.names:
            if a.asname and self._public(a.asname):
                self.exports.add(a.asname)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if self._class_stack or self._depth:
            return
        for a in node.names:
            if a.name == "*":
                self.star_imports.append((node.level or 0, node.module or ""))
                continue
            local = a.asname or a.name
            if self._public(local):
                self.exports.add(local)

    def visit_Assign(self, node):
        if not self._class_stack and not self._depth:
            self._add_assign(node)

    def visit_AnnAssign(self, node):
        if not self._class_stack and not self._depth:
            self._add_assign(node)

    def _add_assign(self, node):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            for n in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if not isinstance(n, ast.Name):
                    continue
                if n.id == "__all__" and not self._class_stack:
                    val = getattr(node, "value", None)
                    if isinstance(val, (ast.List, ast.Tuple)):
                        self.all_names = set(self.all_names or ())
                        for el in val.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                self.exports.add(el.value)
                                self.all_names.add(el.value)
                    continue
                if not self._class_stack:
                    lit = _string_literals(getattr(node, "value", None))
                    if lit:
                        self._literals[n.id] = lit
                if n.id.startswith("_"):
                    continue
                self.attributes.add(".".join(self._class_stack + [n.id]))

    def _add_func(self, node):
        if node.name == "__getattr__" and not self._class_stack:
            self._getattr_names(node)
            return
        if not self._public(node.name):
            return
        dotted = ".".join(self._class_stack + [node.name])
        args = node.args
        positional = list(args.posonlyargs) + list(args.args)
        params = [a.arg for a in positional] + [a.arg for a in args.kwonlyargs]
        n_no_default = len(positional) - len(args.defaults)
        required = [a.arg for a in positional[:n_no_default]]
        required += [a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None]
        is_method = bool(self._class_stack) and not any(
            _base_name(d) == "staticmethod" for d in node.decorator_list)
        if is_method and positional and positional[0].arg in ("self", "cls", "mcs", "metacls"):
            required = [r for r in required if r != positional[0].arg]
        sig = FuncSig(
            name=dotted,
            params=params,
            has_varargs=args.vararg is not None,
            has_kwargs=args.kwarg is not None,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            required=required,
            kwonly=[a.arg for a in args.kwonlyargs],
            posonly=[a.arg for a in args.posonlyargs],
            returns=_return_name(node.returns),
        )
        if any(_base_name(d) == "overload" for d in node.decorator_list):
            sig.required = []  # overload variants don't define what's required
        self.functions[dotted] = sig
        self.names.add(node.name)
        # bodies are not visited: nested defs are implementation details


def _return_name(ann) -> Optional[str]:
    """Class name a return annotation names: ``-> Engine``, ``-> "Engine"``,
    ``-> Optional[Engine]``, ``-> Engine | None``. None for builtins/unknown."""
    if ann is None:
        return None
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            ann = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(ann, ast.Subscript) and _base_name(ann.value) == "Optional":
        return _return_name(ann.slice)
    if isinstance(ann, ast.BinOp):
        return _return_name(ann.left) or _return_name(ann.right)
    if isinstance(ann, (ast.Name, ast.Attribute)):
        name = _base_name(ann)
        if name and name[:1].isupper() and name not in ("None", "Any", "Self"):
            return name
    return None


def _string_literals(node) -> Set[str]:
    """String elements of a list/tuple/set literal, or keys of a dict literal."""
    out: Set[str] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.add(node.value)
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        out.update(e.value for e in node.elts
                   if isinstance(e, ast.Constant) and isinstance(e.value, str))
    elif isinstance(node, ast.Dict):
        out.update(k.value for k in node.keys
                   if isinstance(k, ast.Constant) and isinstance(k.value, str))
    return {n for n in out if n.isidentifier()}


def _base_name(node) -> Optional[str]:
    """Simple name of a base class / decorator expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _base_name(node.func)
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return None


def build_surface(version: str, sources: Dict[str, List[str]],
                  extensions: Optional[Set[str]] = None) -> APISurface:
    """Build an API surface from module sources ({dotted module: [source, ...]})."""
    extensions = extensions or set()
    stars: Dict[str, List[Tuple[int, str]]] = {}
    all_of: Dict[str, Set[str]] = {}
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
            visitor.finalize()
            if visitor.star_imports:
                stars.setdefault(module_path, []).extend(visitor.star_imports)
            if visitor.all_names is not None:
                all_of[module_path] = visitor.all_names
            if visitor.exports:
                surface.exports.setdefault(module_path, set()).update(visitor.exports)
            for cls, bases in visitor.bases.items():
                surface.bases[f"{module_path}.{cls}"] = bases
            for attr in visitor.attributes:
                surface.attributes.add(f"{module_path}.{attr}")
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
                    # stub + impl / overloads: only what every variant requires
                    existing.required = [r for r in existing.required if r in sig.required]
                    existing.kwonly = [k for k in existing.kwonly if k in sig.kwonly]
                else:
                    surface.functions[full] = sig
            surface.names.update(visitor.names)

    _resolve_star_imports(surface, stars, all_of)
    return surface


def _resolve_star_imports(surface: APISurface, stars: Dict[str, List[Tuple[int, str]]],
                          all_of: Dict[str, Set[str]]):
    """``from ._quadrature import *`` in scipy/integrate/__init__.py makes
    every public name of _quadrature importable from scipy.integrate."""
    if not stars:
        return
    by_module: Dict[str, Set[str]] = {}
    for coll in (surface.functions, surface.classes, surface.attributes):
        for dotted in coll:
            mod, _, name = dotted.rpartition(".")
            by_module.setdefault(mod, set()).add(name)

    def public_names(mod: str) -> Set[str]:
        if mod in all_of:
            return set(all_of[mod])
        names = by_module.get(mod, set()) | surface.exports.get(mod, set())
        return {n for n in names if not n.startswith("_")}

    def resolve(mod: str, level: int, target: str) -> Optional[str]:
        if level == 0:
            return target if target in surface.modules else None
        # a package's __init__ is stored under the package name, so try the
        # module itself as the package first, then its parent
        for base in (mod, mod.rsplit(".", 1)[0] if "." in mod else ""):
            parts = base.split(".") if base else []
            if level > 1:
                parts = parts[:len(parts) - (level - 1)]
            cand = ".".join(parts + ([target] if target else []))
            if cand in surface.modules:
                return cand
        return None

    for _ in range(5):  # chains: a imports * from b which imports * from c
        changed = False
        for mod, imports in stars.items():
            have = surface.exports.setdefault(mod, set())
            for level, target in imports:
                src = resolve(mod, level, target)
                if not src or src == mod:
                    continue
                new_names = public_names(src) - have
                if new_names:
                    have.update(new_names)
                    changed = True
        if not changed:
            break


def extract_surface(package: str, version: str) -> Optional[APISurface]:
    """Download a version and extract its public API surface (cached)."""
    key = f"surface7:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        surf = APISurface(version=version)
        surf.modules = set(cached["modules"])
        surf.classes = set(cached["classes"])
        surf.names = set(cached["names"])
        surf.functions = {
            k: FuncSig(**v) for k, v in cached["functions"].items()
        }
        surf.exports = {k: set(v) for k, v in cached.get("exports", {}).items()}
        surf.bases = cached.get("bases", {})
        surf.attributes = set(cached.get("attributes", []))
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

    surface = build_surface(version, sources, extensions)

    cache_set(key, {
        "modules": list(surface.modules),
        "classes": list(surface.classes),
        "names": list(surface.names),
        "functions": {
            k: {"name": v.name, "params": v.params, "has_varargs": v.has_varargs,
                "has_kwargs": v.has_kwargs, "is_async": v.is_async,
                "required": v.required, "kwonly": v.kwonly, "posonly": v.posonly,
                "returns": v.returns}
            for k, v in surface.functions.items()
        },
        "exports": {k: sorted(v) for k, v in surface.exports.items()},
        "bases": surface.bases,
        "attributes": sorted(surface.attributes),
    })
    return surface


def _is_public_api(dotted: str) -> bool:
    """True if no segment of the dotted path is private (underscore-prefixed)
    or a bundled test suite (``sklearn.datasets.tests``)."""
    for seg in dotted.split("."):
        if seg.startswith("_") and not (seg.startswith("__") and seg.endswith("__")):
            return False
        if seg in ("tests", "test"):
            return False
    return True


def _reachable(surface: APISurface, dotted: str) -> bool:
    """Is ``dotted`` still importable/accessible in ``surface``?

    True if it's defined there, re-exported by its parent module, or (for a
    method) provided by a base class.
    """
    if dotted in surface.functions or dotted in surface.classes or dotted in surface.modules \
            or dotted in surface.attributes:
        return True
    if "." not in dotted:
        return False
    parent, short = dotted.rsplit(".", 1)
    if short in surface.exports.get(parent, ()):
        return True
    if parent in surface.classes:
        return _inherited(surface, parent, short) is not None
    return False


def _classes_by_short(surface: APISurface) -> Dict[str, List[str]]:
    idx: Dict[str, List[str]] = {}
    for c in surface.classes:
        idx.setdefault(c.rsplit(".", 1)[-1], []).append(c)
    return idx


def _inherited(surface: APISurface, cls: str, member: str,
               _seen: Optional[Set[str]] = None) -> Optional[str]:
    """Dotted path of ``member`` found on a base class of ``cls``, if any."""
    _seen = _seen if _seen is not None else set()
    if cls in _seen:
        return None
    _seen.add(cls)
    idx = _classes_by_short(surface)
    for base in surface.bases.get(cls, []):
        for cand in idx.get(base, []):
            for path in (f"{cand}.{member}",):
                if path in surface.functions or path in surface.attributes:
                    return path
            found = _inherited(surface, cand, member, _seen)
            if found:
                return found
    return None


def _positional_index(sig: FuncSig, param: str) -> Optional[int]:
    """Index of param among positional arguments a caller passes (self/cls
    excluded); None when it can only be passed by keyword."""
    positional = [p for p in sig.params if p not in sig.kwonly]
    if positional and positional[0] in ("self", "cls"):
        positional = positional[1:]
    return positional.index(param) if param in positional else None


def _member_of(surface: APISurface, dotted: str) -> Optional[str]:
    parent = dotted.rsplit(".", 1)[0]
    return parent.rsplit(".", 1)[-1] if parent in surface.classes else None


def _still_at(new: APISurface, short: str) -> List[str]:
    """Import paths through which ``short`` is still reachable in ``new``."""
    paths = [f"{m}.{short}" for m, names in new.exports.items() if short in names]
    paths += [k for k in list(new.functions) + list(new.classes) + list(new.attributes)
              if k.rsplit(".", 1)[-1] == short]
    return sorted(set(paths))


def _package_of(dotted: str) -> str:
    """Package a definition's module sits in, ignoring privacy underscores:
    numpy._core.fromnumeric.round_ -> numpy.core."""
    module = dotted.rsplit(".", 1)[0]
    parent = module.rsplit(".", 1)[0] if "." in module else module
    return ".".join(seg.lstrip("_") for seg in parent.split("."))


def _new_home(new: APISurface, old: str, kind: str) -> Optional[str]:
    """Where a name that vanished from ``old`` lives now: a unique definition
    of the same name in the same package (pkg.urls.f -> pkg.newurls.f). A
    same-named function elsewhere (numpy.ma.core.round_) is a different API."""
    short = old.rsplit(".", 1)[-1]
    pool = new.functions if kind == "function" else new.classes
    hits = [k for k in pool if k.rsplit(".", 1)[-1] == short and _is_public_api(k)
            and _package_of(k) == _package_of(old)]
    return hits[0] if len(hits) == 1 else None


def diff_surfaces(old: APISurface, new: APISurface) -> List[APIChange]:
    """Compute precise API changes from old to new surface.

    Only public API is reported — private modules and underscore-prefixed
    names are internal and users shouldn't depend on them. Re-exports and
    inherited members count as still available, so a function moved between
    modules (but still importable from the same place) is not a removal.
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

    removed_modules = old.modules - new.modules

    # removed classes
    for cls in old.classes - new.classes:
        if not _is_public_api(cls) or _reachable(new, cls):
            continue
        mod = cls.rsplit(".", 1)[0]
        if mod in new.modules or mod not in removed_modules:
            home = _new_home(new, cls, "class")
            changes.append(APIChange(
                kind="removed_class", api=cls,
                detail=f"Class '{cls}' was removed in {new.version}"
                       + (f" (now at '{home}')" if home else ""),
                replacement=home, still_at=_still_at(new, cls.rsplit(".", 1)[-1]),
            ))

    # functions removed or changed
    for fname, old_sig in old.functions.items():
        if not _is_public_api(fname):
            continue
        new_sig = new.functions.get(fname)
        parent, short = fname.rsplit(".", 1)
        if new_sig is None and parent in new.classes:
            inherited = _inherited(new, parent, short)
            if inherited:
                new_sig = new.functions.get(inherited)
                if new_sig is None:
                    continue  # became an attribute on a base: still accessible
        if new_sig is None:
            if short.startswith("__") and short.endswith("__"):
                continue  # dunders (module __getattr__, __repr__, ...) are not API calls
            if _reachable(new, fname):
                continue  # re-exported: same import path still works
            # report only if the enclosing module/class still exists; otherwise the
            # removal of the parent is the finding
            if parent in new.modules or parent in new.classes:
                home = _new_home(new, fname, "function")
                changes.append(APIChange(
                    kind="removed_function", api=fname,
                    detail=f"'{fname}()' was removed in {new.version}"
                           + (f" (now at '{home}')" if home else ""),
                    replacement=home, still_at=_still_at(new, short),
                    member_of=_member_of(old, fname),
                ))
            continue

        old_params = set(old_sig.params) - {"self", "cls"}
        new_params = set(new_sig.params) - {"self", "cls"}

        # parameter removals (ignore if new sig accepts **kwargs)
        removed_params = old_params - new_params
        if removed_params and not new_sig.has_kwargs:
            for p in sorted(removed_params):
                changes.append(APIChange(
                    kind="removed_param", api=fname, param=p,
                    detail=f"Parameter '{p}' removed from '{fname}()' in {new.version}",
                ))

        # new parameters without a default: every existing call breaks
        for p in new_sig.required:
            if p in old_params or p in ("self", "cls"):
                continue
            changes.append(APIChange(
                kind="added_required_param", api=fname, param=p,
                position=_positional_index(new_sig, p),
                detail=f"'{fname}()' now requires a '{p}' argument ({new.version})",
            ))

        # a parameter that had a default and no longer does (yaml.load's Loader)
        for p in new_sig.required:
            if p in old_params and p not in old_sig.required and p not in ("self", "cls"):
                changes.append(APIChange(
                    kind="added_required_param", api=fname, param=p,
                    position=_positional_index(new_sig, p),
                    detail=f"'{p}' is now a required argument of '{fname}()' ({new.version})",
                ))

        # a parameter that used to be positional is now keyword-only
        old_positional = [x for x in old_sig.params if x not in old_sig.kwonly]
        if old_positional and old_positional[0] in ("self", "cls"):
            old_positional = old_positional[1:]
        for idx, p in enumerate(old_positional):
            if p in new_sig.kwonly and p not in old_sig.kwonly:
                changes.append(APIChange(
                    kind="became_keyword_only", api=fname, param=p, position=idx,
                    detail=f"'{p}' of '{fname}()' is keyword-only since {new.version}: "
                           f"pass it as {p}=...",
                ))
            if p in new_sig.posonly and p not in old_sig.posonly:
                changes.append(APIChange(
                    kind="became_positional_only", api=fname, param=p, position=idx,
                    detail=f"'{p}' of '{fname}()' is positional-only since {new.version}",
                ))

    for c in changes:
        # constructor changes are reached as pkg.Cls(...), so they aren't "members"
        if c.param is not None and not c.api.endswith(".__init__"):
            c.member_of = _member_of(old, c.api)

    # re-exports that disappeared: `from flask import Markup` stops working
    already = {c.api for c in changes}
    for mod, names in old.exports.items():
        if not _is_public_api(mod) or mod not in new.modules:
            continue
        for name in sorted(names):
            dotted = f"{mod}.{name}"
            if not _is_public_api(dotted) or dotted in already or _reachable(new, dotted):
                continue
            if name in new.names and dotted in new.exports.get(mod, ()):
                continue
            changes.append(APIChange(
                kind="removed_export", api=dotted,
                detail=f"'{name}' can no longer be imported from '{mod}' ({new.version})",
            ))

    # removed public constants / attributes
    for attr in old.attributes - new.attributes:
        if not _is_public_api(attr) or _reachable(new, attr):
            continue
        parent = attr.rsplit(".", 1)[0]
        if parent in new.modules or parent in new.classes:
            changes.append(APIChange(
                kind="removed_attribute", api=attr,
                detail=f"'{attr}' was removed in {new.version}",
                member_of=_member_of(old, attr),
                still_at=_still_at(new, attr.rsplit(".", 1)[-1]),
            ))

    return changes


def get_return_types(package: str, *versions: str) -> Dict[str, str]:
    """{function short name: class short name it returns}, from annotations in
    any of the given versions. Lets ``e = create_engine(); e.execute()`` be
    matched against changes to ``Engine.execute``. Ambiguous names are dropped."""
    seen: Dict[str, Set[str]] = {}
    for v in versions:
        surf = extract_surface(package, v)
        if surf is None:
            continue
        for fname, sig in surf.functions.items():
            if sig.returns and _is_public_api(fname):
                seen.setdefault(fname.rsplit(".", 1)[-1], set()).add(sig.returns)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def get_api_changes(package: str, from_version: str, to_version: str) -> Optional[List[APIChange]]:
    """Extract both surfaces and diff them. Returns None if extraction fails."""
    old = extract_surface(package, from_version)
    new = extract_surface(package, to_version)
    if old is None or new is None:
        return None
    return diff_surfaces(old, new)
