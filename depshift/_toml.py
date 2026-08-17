"""TOML loading with a backport fallback for Python < 3.11."""

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

TOML_AVAILABLE = tomllib is not None

MISSING_TOML_HINT = (
    "TOML support is unavailable. On Python 3.10 and older, install the "
    "backport with: pip install tomli"
)

if tomllib is not None:
    TOMLDecodeError = tomllib.TOMLDecodeError
else:  # pragma: no cover
    class TOMLDecodeError(ValueError):  # type: ignore[no-redef]
        """Placeholder so callers can reference the exception unconditionally."""

__all__ = ["tomllib", "TOML_AVAILABLE", "TOMLDecodeError", "MISSING_TOML_HINT"]
