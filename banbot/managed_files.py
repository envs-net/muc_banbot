"""Compatibility facade for shared managed runtime-file helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from envs_xmpp_core.formatting import format_bytes
from envs_xmpp_core.storage.managed import (
    ManagedFile,
    is_relative_to,
    prune_managed_files,
)
from envs_xmpp_core.storage.managed import (
    list_managed_files as _core_list_managed_files,
)
from envs_xmpp_core.storage.managed import (
    resolve_managed_file as _core_resolve_managed_file,
)

__all__ = [
    "ManagedFile",
    "format_file_size",
    "is_relative_to",
    "list_managed_files",
    "prune_managed_files",
    "resolve_managed_file",
]


def _is_safe_managed_path(directory: Path, path: Path) -> bool:
    """Return whether one managed entry is a contained regular non-symlink file."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        base = directory.resolve()
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return is_relative_to(resolved, base)


def _managed_predicate(
    directory: Path,
    predicate: Callable[[Path], bool] | None,
) -> Callable[[Path], bool]:
    def accepted(path: Path) -> bool:
        if not _is_safe_managed_path(directory, path):
            return False
        return predicate(path) if predicate is not None else True

    return accepted


def list_managed_files(
    directory: Path,
    pattern: str,
    *,
    exclude_suffixes: Iterable[str] = (),
    predicate: Callable[[Path], bool] | None = None,
) -> list[ManagedFile]:
    """List managed files while rejecting symlinks and containment escapes.

    The shared core owns ordering, metadata and filtering semantics.  BanBot's
    facade adds the runtime-file policy that backup/export entries must be real
    files below their configured directory rather than symlinks.
    """
    return _core_list_managed_files(
        directory,
        pattern,
        exclude_suffixes=exclude_suffixes,
        predicate=_managed_predicate(directory, predicate),
    )


def resolve_managed_file(
    directory: Path,
    query: str,
    files: list[ManagedFile],
    *,
    predicate: Callable[[Path], bool] | None = None,
    latest_aliases: Iterable[str] = ("latest",),
) -> Path | None:
    """Resolve one managed file and revalidate it against replacement races."""
    path = _core_resolve_managed_file(
        directory,
        query,
        files,
        predicate=_managed_predicate(directory, None),
        latest_aliases=latest_aliases,
    )
    if path is None or not _is_safe_managed_path(directory, path):
        return None
    if predicate is not None and not predicate(path):
        return None
    return path


def format_file_size(size: int) -> str:
    """Return a human-readable file size."""
    return format_bytes(size, negative_label=None, max_unit="MiB")
