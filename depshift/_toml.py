"""TOML loading with a backport fallback for Python < 3.11."""

import sys
from types import ModuleType


def _find_tomllib() -> ModuleType | None:
    if sys.version_info >= (3, 11):
        import tomllib as stdlib
        return stdlib
    try:  # pragma: no cover
        import tomli as backport
    except ModuleNotFoundError:  # pragma: no cover
        return None
    return backport  # pragma: no cover


tomllib = _find_tomllib()
TOML_AVAILABLE = tomllib is not None

MISSING_TOML_HINT = (
    "TOML support is unavailable. On Python 3.10 and older, install the "
    "backport with: pip install tomli"
)


class _NoToml(ValueError):
    """Placeholder so callers can reference the exception unconditionally."""


TOMLDecodeError: type[ValueError] = tomllib.TOMLDecodeError if tomllib is not None else _NoToml

__all__ = ["MISSING_TOML_HINT", "TOML_AVAILABLE", "TOMLDecodeError", "tomllib"]
