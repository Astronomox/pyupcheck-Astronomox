#!/usr/bin/env python3
"""Bump the pyupcheck version across all files in one command.

Usage:
    python bump.py 0.4.2
    python bump.py patch    # increments patch
    python bump.py minor    # increments minor, resets patch
    python bump.py major    # increments major, resets minor+patch
    python bump.py minor --yes   # no confirmation prompt

Also turns the CHANGELOG's "## [Unreleased]" section into
"## [X.Y.Z] - YYYY-MM-DD" and opens a new empty Unreleased section.
See RELEASING.md for the full release flow.
"""

import re
import sys
from datetime import date
from pathlib import Path


FILES = [
    ("pyproject.toml",          r'version = "([^"]+)"',           'version = "{}"'),
    ("depshift/__init__.py",    r'__version__ = "([^"]+)"',        '__version__ = "{}"'),
]


def read_current() -> str:
    text = Path("pyproject.toml").read_text()
    m = re.search(r'version = "([^"]+)"', text)
    if not m:
        raise ValueError("Could not find version in pyproject.toml")
    return m.group(1)


def bump(current: str, part: str) -> str:
    major, minor, patch = map(int, current.split("."))
    if part == "major":
        return f"{major+1}.0.0"
    if part == "minor":
        return f"{major}.{minor+1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch+1}"
    return part  # explicit version string


def apply(new_version: str):
    for filepath, pattern, template in FILES:
        path = Path(filepath)
        text = path.read_text()
        current_match = re.search(pattern, text)
        if not current_match:
            print(f"  Warning: version pattern not found in {filepath}")
            continue
        old = current_match.group(0)
        new = template.format(new_version)
        path.write_text(text.replace(old, new, 1))
        print(f"  {filepath}: {current_match.group(1)} -> {new_version}")


def date_changelog(new_version: str, path: str = "CHANGELOG.md"):
    p = Path(path)
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    if "## [Unreleased]" not in text:
        print(f"  Warning: no '## [Unreleased]' section in {path}")
        return
    dated = f"## [Unreleased]\n\n## [{new_version}] - {date.today().isoformat()}"
    p.write_text(text.replace("## [Unreleased]", dated, 1), encoding="utf-8")
    print(f"  {path}: [Unreleased] -> [{new_version}]")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--yes"]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)

    arg = args[0]
    current = read_current()
    print(f"Current version: {current}")

    new_version = bump(current, arg)
    print(f"New version:     {new_version}")

    if "--yes" not in sys.argv:
        confirm = input("Apply? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    apply(new_version)
    date_changelog(new_version)
    print(f"""
Done. To release:
  git commit -am "Release {new_version}"
  git tag v{new_version}
  git push origin main v{new_version}
The Release workflow then tests, builds and publishes to PyPI.""")
