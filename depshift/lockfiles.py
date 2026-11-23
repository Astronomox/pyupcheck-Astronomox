"""Read exact versions from lockfiles.

A lockfile says what the project actually runs, which is a better baseline
than a version range or whatever happens to be installed where pyupcheck
runs. Supported: poetry.lock, uv.lock, pdm.lock, pylock.toml (PEP 751),
Pipfile.lock, conda-lock.yml and pixi.lock.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from ._toml import tomllib
from .deps import normalize_name


@dataclass
class LockedPackage:
    name: str      # PEP 503 normalized
    version: str
    source: str    # lockfile it came from


# checked in order; the first lockfile that lists a package wins
LOCKFILES = ["uv.lock", "poetry.lock", "pdm.lock", "pylock.toml", "Pipfile.lock",
             "conda-lock.yml", "conda-lock.yaml", "pixi.lock"]
_CONDA_LOCKS = {"conda-lock.yml", "conda-lock.yaml", "pixi.lock"}


def _toml_packages(path: str, key: str = "package") -> List[dict]:
    if tomllib is None:
        return []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return []
    pkgs = data.get(key)
    return pkgs if isinstance(pkgs, list) else []


def _is_project_itself(pkg: dict) -> bool:
    """uv/pdm list the project being locked as an editable/virtual package."""
    src = pkg.get("source")
    return isinstance(src, dict) and ("editable" in src or "virtual" in src)


def parse_toml_lock(path: str) -> List[LockedPackage]:
    """poetry.lock, uv.lock, pdm.lock ([[package]]) and pylock.toml ([[packages]])."""
    key = "packages" if os.path.basename(path).startswith("pylock") else "package"
    out = []
    for pkg in _toml_packages(path, key):
        name, version = pkg.get("name"), pkg.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or _is_project_itself(pkg):
            continue
        out.append(LockedPackage(normalize_name(name), version, os.path.basename(path)))
    return out


def parse_pipfile_lock(path: str) -> List[LockedPackage]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for section in ("default", "develop"):
        for name, info in (data.get(section) or {}).items():
            ver = (info or {}).get("version", "") if isinstance(info, dict) else ""
            if isinstance(ver, str) and ver.startswith("=="):
                out.append(LockedPackage(normalize_name(name), ver[2:], "Pipfile.lock"))
    return out


def find_lockfiles(directory: str) -> List[str]:
    paths = [os.path.join(directory, n) for n in LOCKFILES]
    # PEP 751 also allows pylock.<name>.toml
    try:
        extra = sorted(n for n in os.listdir(directory)
                       if n.startswith("pylock.") and n.endswith(".toml") and n != "pylock.toml")
    except OSError:
        extra = []
    paths += [os.path.join(directory, n) for n in extra]
    return [p for p in paths if os.path.isfile(p)]


def locked_versions(directory: str) -> Dict[str, LockedPackage]:
    """{normalized name: LockedPackage} from every lockfile in directory."""
    out: Dict[str, LockedPackage] = {}
    for path in find_lockfiles(directory):
        for pkg in parse_lockfile(path):
            out.setdefault(pkg.name, pkg)
    return out


def parse_lockfile(path: str) -> List[LockedPackage]:
    name = os.path.basename(path)
    if name in _CONDA_LOCKS:
        from .conda import lockfile_versions
        return [LockedPackage(normalize_name(n), v, name) for n, v in lockfile_versions(path).items()]
    if name == "Pipfile.lock":
        return parse_pipfile_lock(path)
    return parse_toml_lock(path)


def locked_version(directory: str, package: str) -> Optional[str]:
    pkg = locked_versions(directory).get(normalize_name(package))
    return pkg.version if pkg else None
