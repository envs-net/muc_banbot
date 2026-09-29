"""BanBot compatibility wrappers for shared OMEMO storage helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from envs_xmpp_core.xmpp.omemo import (
    backup_existing_path,
    backup_path,
    ensure_identity_metadata,
    identity_metadata_path,
    prepare_storage_file,
    read_identity_metadata,
    write_identity_metadata,
)

_backup_existing_path = backup_existing_path
_backup_path = backup_path
_omemo_identity_metadata_path = identity_metadata_path
_read_omemo_identity_metadata = read_identity_metadata
_write_omemo_identity_metadata = write_identity_metadata


def _current_omemo_identity(config_module: Any) -> dict[str, str]:
    resource = getattr(config_module, "RESOURCE", None)
    if resource is None:
        resource = getattr(config_module, "RESSOURCE", None)
    return {
        "jid": str(getattr(config_module, "JID", "")).strip(),
        "resource": str(resource or "").strip(),
        "nick": str(getattr(config_module, "NICK", "")).strip(),
    }


def _ensure_omemo_identity_metadata(
    storage_path: Path,
    identity: dict[str, str],
    *,
    reset_on_change: bool,
) -> Path | None:
    backup, _changed = ensure_identity_metadata(
        storage_path,
        identity,
        reset_on_change=reset_on_change,
    )
    return backup


def _prepare_omemo_storage_file(path: str) -> Path:
    return prepare_storage_file(Path(path))
