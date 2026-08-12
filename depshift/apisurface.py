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


