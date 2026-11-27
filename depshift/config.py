"""Load pyupcheck configuration from [tool.pyupcheck] in pyproject.toml and .pyupcheckignore.

Mistakes are reported, not silently ignored: an unknown key ("ignores") or a
value pyupcheck doesn't accept (``fail_on = "breakng"``) becomes a message in
``Config.problems``, with a suggestion when one is close, and the setting
keeps its default.
"""

import difflib
import os
from dataclasses import dataclass, field

from ._toml import MISSING_TOML_HINT, TOMLDecodeError, tomllib

FAIL_ON = ("breaking", "deprecated", "any", "never")
SEVERITIES = ("breaking", "deprecated", "warning")
KNOWN_KEYS = ("exclude", "ignore", "fail_on", "min_severity", "cache")


@dataclass
class Config:
    exclude_dirs: set[str] = field(default_factory=set)
    ignore_packages: set[str] = field(default_factory=set)
    fail_on: str = "breaking"  # breaking | deprecated | any | never
    min_severity: str = "warning"  # breaking | deprecated | warning
    cache: bool = True
    # what was wrong with the configuration, for the user to fix
    problems: list[str] = field(default_factory=list)


def _suggest(word: str, options) -> str:
    close = difflib.get_close_matches(word, options, n=1, cutoff=0.6)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _load_pyproject_section(path: str, problems: list[str]) -> dict:
    """Return [tool.pyupcheck] from a pyproject.toml, or {} if unreadable."""
    if tomllib is None:
        problems.append(f"can't read [tool.pyupcheck] from {path}. {MISSING_TOML_HINT}")
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except TOMLDecodeError as exc:
        problems.append(f"{path} is not valid TOML ({exc}): [tool.pyupcheck] ignored")
        return {}
    except OSError as exc:
        problems.append(f"could not read {path}: {exc}")
        return {}

    tool = data.get("tool")
    section = tool.get("pyupcheck") if isinstance(tool, dict) else None
    if section is None:
        return {}
    if not isinstance(section, dict):
        problems.append("[tool.pyupcheck] must be a table: ignored")
        return {}
    return section


def _string_list(section: dict, key: str, problems: list[str]) -> list[str]:
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        problems.append(f"{key} must be a list of strings, like {key} = [\"name\"]: ignored")
        return []
    return [v.strip() for v in value if v.strip()]


def _choice(section: dict, key: str, options, problems: list[str]) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if value not in options:
        problems.append(f"{key} = {value!r} is not one of {', '.join(options)}{_suggest(str(value), options)}: "
                        "the default is used")
        return None
    return value


def load_config(directory: str) -> Config:
    cfg = Config()

    pyproject = os.path.join(directory, "pyproject.toml")
    if os.path.isfile(pyproject):
        section = _load_pyproject_section(pyproject, cfg.problems)
        for key in section:
            if key not in KNOWN_KEYS:
                cfg.problems.append(f"unknown key {key!r} in [tool.pyupcheck]{_suggest(key, KNOWN_KEYS)}: ignored")

        cfg.exclude_dirs.update(e.rstrip("/") for e in _string_list(section, "exclude", cfg.problems))
        cfg.ignore_packages.update(p.lower() for p in _string_list(section, "ignore", cfg.problems))
        cfg.fail_on = _choice(section, "fail_on", FAIL_ON, cfg.problems) or cfg.fail_on
        cfg.min_severity = _choice(section, "min_severity", SEVERITIES, cfg.problems) or cfg.min_severity
        cache = section.get("cache")
        if isinstance(cache, bool):
            cfg.cache = cache
        elif cache is not None:
            cfg.problems.append("cache must be true or false: the default (true) is used")

    ignore_file = os.path.join(directory, ".pyupcheckignore")
    if os.path.isfile(ignore_file):
        try:
            with open(ignore_file, encoding="utf-8") as f:
                for raw in f:
                    entry = raw.split("#")[0].strip()
                    if not entry:
                        continue
                    if entry.endswith("/"):
                        cfg.exclude_dirs.add(entry.rstrip("/"))
                    else:
                        cfg.ignore_packages.add(entry.lower())
        except OSError as exc:
            cfg.problems.append(f"could not read {ignore_file}: {exc}")

    return cfg
