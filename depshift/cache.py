"""Local response cache with TTL."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(os.environ.get("PYUPCHECK_CACHE_DIR", Path.home() / ".cache" / "pyupcheck"))
DEFAULT_TTL = 24 * 3600  # 24 hours

_disabled = False


def disable_cache():
    global _disabled
    _disabled = True


def _key_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:32]
    return CACHE_DIR / f"{h}.json"


def cache_get(key: str, ttl: int = DEFAULT_TTL) -> Any | None:
    if _disabled:
        return None
    path = _key_path(key)
    try:
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        if time.time() - entry["ts"] > ttl:
            path.unlink(missing_ok=True)
            return None
        return entry["data"]
    except Exception:
        return None


_dir_ready: Path | None = None


def _ensure_dir() -> None:
    """The cache folder, readable only by you: with a token it can hold the
    file listings of private repositories. A folder made by an older version
    is tightened too (once per run)."""
    global _dir_ready
    if _dir_ready == CACHE_DIR:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CACHE_DIR, 0o700)  # mkdir's mode is masked by the umask
    except OSError:
        pass
    _dir_ready = CACHE_DIR


def cache_set(key: str, data: Any):
    if _disabled:
        return
    tmp = None
    try:
        _ensure_dir()
        path = _key_path(key)
        # dumps, not dump: dump streams through the pure-Python encoder, which
        # took a third of a cold deep check for a large package
        text = json.dumps({"ts": time.time(), "data": data}, separators=(",", ":"))
        # write then rename: a reader on another thread never sees half a file.
        # The temporary file is created new (never an existing file or link)
        # and private to you.
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def cache_clear() -> int:
    """Delete all cache entries. Returns count removed."""
    count = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
    return count
