"""Local response cache with TTL."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(os.environ.get("PYUPCHECK_CACHE_DIR", Path.home() / ".cache" / "pyupcheck"))
DEFAULT_TTL = 24 * 3600  # 24 hours

_disabled = False


def disable_cache():
    global _disabled
    _disabled = True


def _key_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:32]
    return CACHE_DIR / f"{h}.json"


def cache_get(key: str, ttl: int = DEFAULT_TTL) -> Optional[Any]:
    if _disabled:
        return None
    path = _key_path(key)
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        if time.time() - entry["ts"] > ttl:
            path.unlink(missing_ok=True)
            return None
        return entry["data"]
    except Exception:
        return None


def cache_set(key: str, data: Any):
    if _disabled:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _key_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception:
        pass


def cache_clear() -> int:
    """Delete all cache entries. Returns count removed."""
    count = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
    return count
