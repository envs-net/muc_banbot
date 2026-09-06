"""Helpers for managed runtime files such as backups and exports."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from envs_xmpp_core.formatting import format_bytes


@dataclass(frozen=True)
class ManagedFile:
    """Metadata for one managed file."""

    path: pathlib.Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S")


def format_file_size(size: int) -> str:
    """Return a human-readable file size."""
    return format_bytes(size, negative_label=None, max_unit="MiB")


def is_relative_to(path: pathlib.Path, directory: pathlib.Path) -> bool:
    """Return True when path resolves inside directory."""
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def list_managed_files(
    directory: pathlib.Path,
    pattern: str,
    *,
    exclude_suffixes: Iterable[str] = (),
    predicate: Callable[[pathlib.Path], bool] | None = None,
) -> list[ManagedFile]:
    """List matching managed files sorted newest first."""
    if not directory.exists():
        return []

    excluded = tuple(exclude_suffixes)
    files: list[ManagedFile] = []
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        if excluded and path.name.endswith(excluded):
            continue
        if predicate is not None and not predicate(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(ManagedFile(path=path, size=stat.st_size, mtime=stat.st_mtime))

    files.sort(key=lambda item: (item.mtime, item.name), reverse=True)
    return files


def resolve_managed_file(
    directory: pathlib.Path,
    query: str,
    files: list[ManagedFile],
    *,
    predicate: Callable[[pathlib.Path], bool] | None = None,
) -> pathlib.Path | None:
    """Resolve a managed file by basename, contained path, or latest."""
    value = str(query).strip()
    if not value or not files:
        return None

    if value.lower() == "latest":
        return files[0].path

    for managed_file in files:
        if value == managed_file.name or value == str(managed_file.path):
            return managed_file.path

    base_dir = directory.resolve()
    candidate = pathlib.Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None

    if not is_relative_to(resolved, base_dir):
        return None
    if not resolved.is_file():
        return None
    if predicate is not None and not predicate(resolved):
        return None
    return resolved


async def prune_managed_files(
    files: list[ManagedFile],
    *,
    keep: int,
    preserve: pathlib.Path | None = None,
    delete_companions: Callable[[pathlib.Path], list[pathlib.Path]] | None = None,
) -> list[pathlib.Path]:
    """Delete managed files beyond the retention limit."""
    preserve_resolved = preserve.resolve() if preserve is not None and preserve.exists() else None
    removed: list[pathlib.Path] = []
    kept = 0

    for managed_file in files:
        try:
            managed_resolved = managed_file.path.resolve()
        except OSError:
            managed_resolved = managed_file.path

        if preserve_resolved is not None and managed_resolved == preserve_resolved:
            kept += 1
            continue
        if kept < keep:
            kept += 1
            continue

        managed_file.path.unlink()
        removed.append(managed_file.path)
        if delete_companions is not None:
            for companion in delete_companions(managed_file.path):
                if companion.exists():
                    companion.unlink()
                    removed.append(companion)
    return removed
