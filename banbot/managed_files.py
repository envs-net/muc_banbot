"""Compatibility facade for shared managed runtime-file helpers."""

from __future__ import annotations

from envs_xmpp_core.formatting import format_bytes
from envs_xmpp_core.storage.managed import (
    ManagedFile,
    is_relative_to,
    list_managed_files,
    prune_managed_files,
    resolve_managed_file,
)

__all__ = [
    "ManagedFile",
    "format_file_size",
    "is_relative_to",
    "list_managed_files",
    "prune_managed_files",
    "resolve_managed_file",
]


def format_file_size(size: int) -> str:
    """Return a human-readable file size."""
    return format_bytes(size, negative_label=None, max_unit="MiB")
