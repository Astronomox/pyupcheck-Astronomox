"""Load pyupcheck configuration from [tool.pyupcheck] in pyproject.toml and .pyupcheckignore."""

import os
import warnings
from dataclasses import dataclass, field
from typing import Set

from ._toml import MISSING_TOML_HINT, TOMLDecodeError, tomllib


@dataclass
class Config:
    exclude_dirs: Set[str] = field(default_factory=set)
    ignore_packages: Set[str] = field(default_factory=set)
    fail_on: str = "breaking"  # breaking | deprecated | any | never
    min_severity: str = "warning"  # breaking | deprecated | warning
    cache: bool = True


def _load_pyproject_section(path: str) -> dict:
    """Return [tool.pyupcheck] from a pyproject.toml, or {} if unreadable."""
    if tomllib is None:
        warnings.warn(
            "Found {} but cannot read [tool.pyupcheck] from it. {}".format(
                path, MISSING_TOML_HINT
            ),
            RuntimeWarning,
            stacklevel=3,
        )
        return {}

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except TOMLDecodeError as exc:
        warnings.warn(
            "Could not parse {} as TOML: {}".format(path, exc),
            RuntimeWarning,
            stacklevel=3,
        )
        return {}
    except OSError as exc:
        warnings.warn(
            "Could not read {}: {}".format(path, exc),
            RuntimeWarning,
            stacklevel=3,
        )
        return {}

    tool = data.get("tool")
    if not isinstance(tool, dict):
        return {}
    section = tool.get("pyupcheck")
    if not isinstance(section, dict):
        return {}
    return section


def load_config(directory: str) -> Config:
    cfg = Config()

    pyproject = os.path.join(directory, "pyproject.toml")
    if os.path.isfile(pyproject):
        section = _load_pyproject_section(pyproject)

        exclude = section.get("exclude") or []
        if isinstance(exclude, (list, tuple)):
            cfg.exclude_dirs.update(str(e).rstrip("/") for e in exclude if str(e).strip())

        ignore = section.get("ignore") or []
        if isinstance(ignore, (list, tuple)):
            cfg.ignore_packages.update(str(p).lower() for p in ignore if str(p).strip())

        fail_on = section.get("fail_on")
        if isinstance(fail_on, str):
            cfg.fail_on = fail_on

        min_severity = section.get("min_severity")
        if isinstance(min_severity, str):
            cfg.min_severity = min_severity

        cache = section.get("cache")
        if isinstance(cache, bool):
            cfg.cache = cache

    ignore_file = os.path.join(directory, ".pyupcheckignore")
    if os.path.isfile(ignore_file):
        try:
            with open(ignore_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if not line:
                        continue
                    if line.endswith("/"):
                        cfg.exclude_dirs.add(line.rstrip("/"))
                    else:
                        cfg.ignore_packages.add(line.lower())
        except OSError as exc:
            warnings.warn(
                "Could not read {}: {}".format(ignore_file, exc),
                RuntimeWarning,
                stacklevel=2,
            )

    return cfg
