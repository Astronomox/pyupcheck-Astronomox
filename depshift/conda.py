"""conda-lock and pixi: lockfiles and pixi.toml.

* ``conda-lock.yml``: the lockfile ``conda-lock`` writes from environment.yml
  (``package:`` list with name, version, manager, platform, dependencies)
* ``pixi.lock``: pixi's lockfile (``packages:`` list; conda entries name a
  package archive URL, ``name``/``version`` in older versions of the format)
* ``pixi.toml`` and ``[tool.pixi]`` in pyproject.toml: declared dependencies

A conda environment also locks things that aren't Python packages
(``openssl``, ``libblas``, ``python`` itself). Only conda packages that
depend on ``python`` are kept, so nothing goes looking for ``libblas`` on
PyPI. A few conda packages are published under another name on PyPI
(``pytorch`` is ``torch``), and get that name.

The lockfiles are YAML written by tools, in one shape: a top-level list of
mappings. They are read with a small reader for that shape, so no YAML
library is needed.
"""

import os
import platform
import re
import sys

# conda package name -> PyPI project name, where they differ
CONDA_TO_PYPI = {
    "pytorch": "torch",
    "pytorch-cpu": "torch",
    "pytorch-gpu": "torch",
    "matplotlib-base": "matplotlib",
    "py-xgboost": "xgboost",
    "py-opencv": "opencv-python",
    "tensorflow-base": "tensorflow",
    "pyqt": "PyQt5",
    "msgpack-python": "msgpack",
    "pytables": "tables",
    "graphviz-python": "graphviz",
    "brotli-python": "brotli",
    "python-build": "build",
    "python-fastjsonschema": "fastjsonschema",
}
# conda packages that are never a PyPI project used from Python code
_NOT_PYTHON = {"python", "python_abi", "cpython", "pip", "setuptools", "wheel"}


def pypi_name(conda_name: str) -> str:
    return CONDA_TO_PYPI.get(conda_name.lower(), conda_name)


def current_platform() -> str:
    """This machine as a conda platform (linux-64, osx-arm64, win-64...)."""
    machine = platform.machine().lower()
    arch = {"x86_64": "64", "amd64": "64", "arm64": "arm64", "aarch64": "aarch64",
            "ppc64le": "ppc64le"}.get(machine, "64")
    if sys.platform.startswith("darwin"):
        return f"osx-{'arm64' if arch in ('arm64', 'aarch64') else '64'}"
    if sys.platform.startswith("win"):
        return "win-arm64" if arch in ("arm64", "aarch64") else "win-64"
    return f"linux-{arch}"


# ── a reader for "key:\n- item mapping\n- item mapping" YAML ─────────────────

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v.split(" #")[0].strip()


_CHILD_NAME = re.compile(r"^['\"]?([A-Za-z0-9_][A-Za-z0-9._-]*)")


def _names(entries: list[str]) -> list[str]:
    """Package names at the start of each entry ("libblas >=3.9", "python: '>=3'")."""
    return [m.group(1) for m in map(_CHILD_NAME.match, entries) if m]


def yaml_items(text: str, key: str) -> list[dict]:
    """The mappings in the top-level list ``key``. Each has its scalar keys,
    and ``_children[k]``: the lines under a nested key (each list entry, or
    each ``key: value`` of a mapping), as text."""
    items: list[dict] = []
    in_list = False
    item: dict | None = None
    list_indent: int | None = None
    key_indent = 0
    nested = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and not stripped.startswith("-"):
            in_list = stripped == f"{key}:"
            item = None
            list_indent = None
            continue
        if not in_list:
            continue
        dash = stripped == "-" or stripped.startswith("- ")
        if dash and (list_indent is None or indent == list_indent):
            list_indent = indent
            item = {"_children": {}}
            items.append(item)
            key_indent = indent + 2
            nested = None
            stripped = stripped[1:].strip()
            if not stripped:
                continue
            indent = key_indent
            dash = False
        if item is None:
            continue
        if dash or indent > key_indent:
            # under a nested key: a list entry or a mapping key
            if nested is not None:
                entry = stripped[2:] if stripped.startswith("- ") else stripped
                item["_children"][nested].append(_unquote(entry) if dash else entry)
            continue
        if indent == key_indent:
            k, _, v = stripped.partition(":")
            k = k.strip()
            if v.strip():
                item[k] = _unquote(v)
                nested = None
            else:
                nested = k
                item["_children"][k] = []
    return items


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _is_python_package(name: str, deps: list[str]) -> bool:
    deps = _names(deps)
    return name.lower() not in _NOT_PYTHON and (
        "python" in deps or "python_abi" in deps or name.lower() in CONDA_TO_PYPI)


_PURL = re.compile(r"^pkg:pypi/([^@?#\s]+)")


def _pypi_identity(item: dict, conda_name: str) -> str | None:
    """The PyPI name of a conda package: from its package URLs (``purls``,
    written by pixi) when present, since they are exact; else guessed from
    its dependencies. None for something that isn't a Python package."""
    if conda_name.lower() in _NOT_PYTHON:
        return None
    if item.get("purls") == "[]":
        return None  # pixi looked it up: not on PyPI
    purls = [m.group(1) for m in map(_PURL.match, item["_children"].get("purls", [])) if m]
    if purls:
        return purls[0]
    deps = item["_children"].get("depends") or item["_children"].get("dependencies") or []
    return pypi_name(conda_name) if _is_python_package(conda_name, deps) else None


def _version_key(v: str):
    from packaging.version import InvalidVersion, Version
    try:
        return (1, Version(v))
    except InvalidVersion:
        return (0, Version("0"))


def _pick(found: dict[str, list[tuple]]) -> dict[str, str]:
    """One version per name: this machine's platform first, then noarch,
    then any. Several versions there (pixi locks several environments, and
    one may hold an old version for a tool) give the highest, which is the
    one the main environment normally runs."""
    here = current_platform()
    out = {}
    for name, entries in found.items():
        def rank(e, here=here):  # this platform first, then noarch
            return (e[1] != here, e[1] != "noarch")
        best = min(rank(e) for e in entries)
        out[name] = max((e[0] for e in entries if rank(e) == best), key=_version_key)
    return out


def parse_conda_lock(path: str) -> dict[str, str]:
    """{PyPI name: version} from a conda-lock.yml."""
    text = _read(path)
    if text is None:
        return {}
    found: dict[str, list[tuple]] = {}
    for it in yaml_items(text, "package"):
        name, version = it.get("name"), it.get("version")
        if not name or not version:
            continue
        if it.get("manager", "conda") == "conda":
            name = _pypi_identity(it, name)
            if not name:
                continue
        found.setdefault(name, []).append((version, it.get("platform", "")))
    return _pick(found)


_ARCHIVE = re.compile(r"/([^/]+?)(?:\.conda|\.tar\.bz2)$")
_PLATFORM_IN_URL = re.compile(r"/((?:linux|osx|win)-[\w]+|noarch)/")
_WHEEL_OR_SDIST = re.compile(r"/([A-Za-z0-9_.]+?)-(\d[^-/]*?)(?:-[^/]*\.whl|\.tar\.gz|\.zip)$")


def _pixi_entries(text: str):
    """(conda name or None for a PyPI entry, PyPI name or None, version,
    platform) for each package in a pixi.lock."""
    for it in yaml_items(text, "packages"):
        kind = "pypi" if "pypi" in it or it.get("kind") == "pypi" else "conda"
        url = it.get(kind) or it.get("url") or ""
        name, version = it.get("name"), it.get("version")
        m = _PLATFORM_IN_URL.search(url) if url else None
        plat = m.group(1) if m else ""
        if kind == "pypi":
            if not url.startswith(("http://", "https://")) and not version:
                continue  # the project itself, or a local path
            if not (name and version):
                m = _WHEEL_OR_SDIST.search(url)
                if not m:
                    continue
                name, version = m.group(1), m.group(2)
            yield None, name, version, ""
            continue
        if not (name and version):
            m = _ARCHIVE.search(url)
            if not m:
                continue
            parts = m.group(1).rsplit("-", 2)  # name-version-build
            if len(parts) != 3:
                continue
            name, version = parts[0], parts[1]
        yield name, _pypi_identity(it, name), version, plat


def parse_pixi_lock(path: str) -> dict[str, str]:
    """{PyPI name: version} from a pixi.lock (format versions 4 to 7)."""
    text = _read(path)
    if text is None:
        return {}
    found: dict[str, list[tuple]] = {}
    for _conda, pypi, version, plat in _pixi_entries(text):
        if pypi:
            found.setdefault(pypi, []).append((version, plat))
    return _pick(found)


def pixi_lock_names(path: str) -> dict[str, str | None]:
    """{conda name: PyPI name, or None if it isn't a Python package}, as the
    pixi.lock next to a manifest records them."""
    text = _read(path)
    if text is None:
        return {}
    out: dict[str, str | None] = {}
    for conda, pypi, _v, _p in _pixi_entries(text):
        if conda:
            out[conda.lower()] = out.get(conda.lower()) or pypi
    return out


# ── pixi.toml ─────────────────────────────────────────────────────────────────

def _conda_spec_to_pep440(spec: str) -> str:
    """A conda version spec as a PEP 440 specifier, as far as one exists:
    "2.2.*" -> "==2.2.*", "1.26.4" -> "==1.26.4", "=1.2" -> "==1.2.*",
    ">=1.26,<2" unchanged."""
    spec = spec.strip()
    if not spec or spec == "*" or "|" in spec:
        return ""  # any version, or an "or" PEP 440 can't say
    if spec[0].isdigit():
        return f"=={spec}"
    if spec.startswith("=") and not spec.startswith("=="):
        v = spec[1:].strip()  # conda "=1.2" means 1.2.*
        return f"=={v}" if v.endswith("*") else f"=={v}.*"
    return spec


def pixi_requirements(data: dict, names: dict[str, str | None] | None = None) -> list[str]:
    """Requirement strings from a pixi manifest's tables (pixi.toml, or
    pyproject.toml's [tool.pixi]): conda and PyPI dependencies of the
    default environment and of every feature, environment and target.

    ``names`` (from :func:`pixi_lock_names`) gives each conda package's PyPI
    name, and drops the ones that aren't Python packages (rust, git...)."""
    tables = []

    def collect(section: dict):
        tables.append(("conda", section.get("dependencies") or {}))
        tables.append(("pypi", section.get("pypi-dependencies") or {}))
        for target in (section.get("target") or {}).values():
            if isinstance(target, dict):
                tables.append(("conda", target.get("dependencies") or {}))
                tables.append(("pypi", target.get("pypi-dependencies") or {}))

    collect(data)
    for group in ("feature", "environments"):
        for section in (data.get(group) or {}).values():
            if isinstance(section, dict):
                collect(section)

    out = []
    for kind, table in tables:
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            if kind == "conda":
                if name.lower() in _NOT_PYTHON:
                    continue
                if names is not None and name.lower() in names:
                    if not names[name.lower()]:
                        continue  # the lock says: not a Python package
                    name = names[name.lower()]
                else:
                    name = pypi_name(name)
            if isinstance(spec, dict):
                if any(k in spec for k in ("path", "git", "url")):
                    continue  # a local or VCS package: no PyPI versions to compare
                spec = spec.get("version", "*")
            if not isinstance(spec, str):
                continue
            pep = _conda_spec_to_pep440(spec) if kind == "conda" else ("" if spec.strip() == "*" else spec.strip())
            out.append(f"{name}{pep}")
    return out


_TABLE = re.compile(r"^\[([^\[\]]+)\]\s*(?:#.*)?$")
_DEP_TABLE = re.compile(r"(?:^|\.)(dependencies|pypi-dependencies)$")
_KEY_VALUE = re.compile(r"""^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\{.*))""")
_INLINE_VERSION = re.compile(r"""\bversion\s*=\s*["']([^"']*)["']""")


def _lenient_pixi_requirements(text: str, names=None) -> list[str]:
    """Dependencies read line by line, for a manifest tomllib can't parse
    (pixi accepts TOML 1.1, such as an inline table over several lines).
    Only the dependency tables are read; nothing else is needed."""
    tables: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        m = _TABLE.match(stripped)
        if m:
            name = m.group(1).replace('"', "").replace(" ", "")
            current = name if _DEP_TABLE.search(name) else None
            continue
        if current is None:
            continue
        kv = _KEY_VALUE.match(stripped)
        if not kv:
            continue
        key, value = kv.group(1), kv.group(2) if kv.group(2) is not None else kv.group(3)
        if value is None:  # an inline table
            inline = kv.group(4)
            if any(re.search(rf"\b{k}\s*=", inline) for k in ("path", "git", "url")):
                continue
            vm = _INLINE_VERSION.search(inline)
            value = vm.group(1) if vm else "*"
        tables.setdefault(current, {})[key] = value
    out = []
    for name, table in tables.items():
        section = {name.rsplit(".", 1)[-1]: table}  # {"dependencies": {...}}
        out.extend(pixi_requirements(section, names))
    return out


def read_pixi_toml(path: str) -> list[str]:
    from ._toml import tomllib
    text = _read(path)
    if text is None:
        return []
    lock = os.path.join(os.path.dirname(path), "pixi.lock")
    names = pixi_lock_names(lock) if os.path.isfile(lock) else None
    if tomllib is not None:
        try:
            return list(dict.fromkeys(pixi_requirements(tomllib.loads(text), names)))
        except ValueError:
            pass
    return list(dict.fromkeys(_lenient_pixi_requirements(text, names)))


def lockfile_versions(path: str) -> dict[str, str]:
    name = os.path.basename(path)
    if name == "pixi.lock":
        return parse_pixi_lock(path)
    return parse_conda_lock(path)
