"""OMEMO mixin helpers."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from slixmpp import JID

log = logging.getLogger(__name__)


def _omemo_identity_metadata_path(storage_path: Path) -> Path:
    """Return the metadata path tied to an OMEMO storage file."""
    if storage_path.suffix:
        return storage_path.with_name(f"{storage_path.stem}.identity.json")
    return storage_path.with_name(f"{storage_path.name}.identity.json")

def _current_omemo_identity(config_module: Any) -> dict[str, str]:
    """Return the config identity that makes an OMEMO store safe to reuse."""
    resource = getattr(config_module, "RESOURCE", None)
    if resource is None:
        resource = getattr(config_module, "RESSOURCE", None)

    return {
        "jid": str(getattr(config_module, "JID", "")).strip(),
        "resource": str(resource or "").strip(),
        "nick": str(getattr(config_module, "NICK", "")).strip(),
    }

def _read_omemo_identity_metadata(path: Path) -> dict[str, str] | None:
    """Read identity metadata; return None when it does not exist."""
    if not path.exists():
        return None

    with path.open(encoding="utf8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError(f"OMEMO identity metadata is not an object: {path}")

    return {
        "jid": str(data.get("jid", "")).strip(),
        "resource": str(data.get("resource", "")).strip(),
        "nick": str(data.get("nick", "")).strip(),
    }

def _write_omemo_identity_metadata(path: Path, identity: dict[str, str]) -> None:
    """Write identity metadata with private file permissions."""
    parent = path.parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp_path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump(identity, f, sort_keys=True)
        f.write("\n")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    os.chmod(path, 0o600)

def _backup_path(path: Path, timestamp: str) -> Path:
    """Return a non-existing timestamped backup path for path."""
    candidate = path.with_name(f"{path.name}.bak-{timestamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{timestamp}-{counter}")
        counter += 1
    return candidate

def _backup_existing_path(path: Path, timestamp: str) -> Path | None:
    """Move an existing file to a timestamped backup path."""
    if not path.exists():
        return None
    backup = _backup_path(path, timestamp)
    shutil.move(str(path), str(backup))
    os.chmod(backup, 0o600)
    return backup

def _ensure_omemo_identity_metadata(
    storage_path: Path,
    identity: dict[str, str],
    *,
    reset_on_change: bool,
) -> Path | None:
    """Ensure the OMEMO store identity matches the current config.

    A stored OMEMO identity is bound to the bot account/resource and MUC nick.
    When that identity changes and reset_on_change is enabled, rotate the old
    storage and metadata files to timestamped backups so a fresh OMEMO store is
    created for the new identity.
    """
    metadata_path = _omemo_identity_metadata_path(storage_path)
    previous_identity = _read_omemo_identity_metadata(metadata_path)

    if previous_identity is None:
        storage_backup = None
        if reset_on_change and storage_path.exists() and storage_path.is_file():
            try:
                existing_content = storage_path.read_text(encoding="utf8").strip()
            except UnicodeDecodeError:
                existing_content = "<binary>"

            if existing_content and existing_content != "{}":
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                storage_backup = _backup_existing_path(storage_path, timestamp)
                if storage_backup is not None:
                    log.warning(
                        "OMEMO: existing storage had no identity metadata; moved it to %s",
                        storage_backup,
                    )

        _write_omemo_identity_metadata(metadata_path, identity)
        log.info("OMEMO: wrote identity metadata %s", metadata_path)
        return storage_backup

    if previous_identity == identity:
        return None

    log.warning(
        "OMEMO: identity changed (old jid=%s resource=%s nick=%s; new jid=%s resource=%s nick=%s)",
        previous_identity.get("jid", ""),
        previous_identity.get("resource", ""),
        previous_identity.get("nick", ""),
        identity.get("jid", ""),
        identity.get("resource", ""),
        identity.get("nick", ""),
    )

    if not reset_on_change:
        log.warning(
            "OMEMO: keeping existing storage because OMEMO_RESET_ON_IDENTITY_CHANGE=False"
        )
        return None

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    storage_backup = _backup_existing_path(storage_path, timestamp)
    _backup_existing_path(metadata_path, timestamp)
    _write_omemo_identity_metadata(metadata_path, identity)

    if storage_backup is not None:
        log.warning("OMEMO: storage moved to %s after identity change", storage_backup)
    else:
        log.warning("OMEMO: identity metadata reset for new identity")

    return storage_backup

def _prepare_omemo_storage_file(path: str) -> Path:
    """Create and secure the OMEMO JSON storage path.

    OMEMO storage contains identity keys, session state and trust data, so the
    parent directory is kept private (0700) and the JSON file itself is kept
    readable/writable only by the bot user (0600).
    """
    storage_path = Path(path).expanduser()

    if not str(storage_path).strip():
        raise RuntimeError("OMEMO storage path must not be empty")

    parent = storage_path.parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)

    if storage_path.exists():
        if storage_path.is_dir():
            raise RuntimeError(f"OMEMO storage path is a directory: {storage_path}")
        os.chmod(storage_path, 0o600)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(storage_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write("{}\n")

    return storage_path
