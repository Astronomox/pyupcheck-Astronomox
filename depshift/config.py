"""Load pyupcheck configuration from [tool.pyupcheck] in pyproject.toml and .pyupcheckignore."""

import os
from dataclasses import dataclass, field
from typing import Set

try:
    import tomllib
except ImportError:
    tomllib = None


@dataclass
class Config:
    exclude_dirs: Set[str] = field(default_factory=set)
    ignore_packages: Set[str] = field(default_factory=set)
    fail_on: str = "breaking"  # breaking | deprecated | any | never
    min_severity: str = "warning"  # breaking | deprecated | warning
    cache: bool = True


def load_config(directory: str) -> Config:
    cfg = Config()

    pyproject = os.path.join(directory, "pyproject.toml")
    if tomllib is not None and os.path.isfile(pyproject):
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            section = data.get("tool", {}).get("pyupcheck", {})
            cfg.exclude_dirs.update(section.get("exclude", []) or [])
            cfg.ignore_packages.update(p.lower() for p in section.get("ignore", []) or [])
            cfg.fail_on = section.get("fail_on", cfg.fail_on)
            cfg.min_severity = section.get("min_severity", cfg.min_severity)
            cfg.cache = section.get("cache", cfg.cache)
        except Exception:
            pass

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
        except OSError:
            pass

    return cfg
