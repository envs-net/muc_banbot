"""Resolve read-only assets shipped with source and wheel installations."""

from __future__ import annotations

from pathlib import Path

_BUNDLED_DIR = Path(__file__).resolve().parent / "bundled"


def resolve_bundled_asset(value: str | Path) -> Path:
    """Resolve a configured file with a packaged plain-filename fallback.

    BanBot historically resolves relative operator paths from the process working
    directory. Preserve that behavior. Only a missing plain filename falls back
    to the copy embedded in the installed :mod:`banbot` package, which is the
    normal wheel-install layout.
    """
    path = Path(value).expanduser()
    if path.is_absolute() or path.exists() or len(path.parts) != 1:
        return path

    packaged_path = (_BUNDLED_DIR / path.name).resolve()
    return packaged_path if packaged_path.exists() else path


def bundled_asset(name: str) -> Path:
    """Return a required packaged/source asset by plain filename."""
    return resolve_bundled_asset(name)
