"""Fetch changelog and deprecation data for a package version."""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx


@dataclass
class ChangeEntry:
    """A single breaking or deprecated API change."""
    kind: str  # "removed", "deprecated", "changed", "renamed"
    api: str  # the affected API path, e.g. "requests.get.verify"
    description: str
    version: str  # version where this change happened


PYPI_API = "https://pypi.org/pypi/{package}/json"
PYPI_VERSION_API = "https://pypi.org/pypi/{package}/{version}/json"
GITHUB_RELEASES_API = "https://api.github.com/repos/{owner}/{repo}/releases"
GITHUB_TAGS_API = "https://api.github.com/repos/{owner}/{repo}/tags"


from depshift.cache import cache_get, cache_set


def get_pypi_info(package: str) -> dict:
    """Fetch full package info from PyPI (cached)."""
    key = f"pypi:{package}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    url = PYPI_API.format(package=package)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    cache_set(key, data)
    return data


def get_pypi_version_info(package: str, version: str) -> dict:
    """Fetch info for a specific version (cached)."""
    key = f"pypi:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    url = PYPI_VERSION_API.format(package=package, version=version)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    cache_set(key, data)
    return data


def get_available_versions(package: str) -> List[str]:
    """Get all available versions for a package."""
    info = get_pypi_info(package)
    return list(info.get("releases", {}).keys())


