"""OMEMO mixin helpers."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from slixmpp import JID

log = logging.getLogger(__name__)

from .helpers import (
    _backup_existing_path,
    _current_omemo_identity,
    _ensure_omemo_identity_metadata,
    _omemo_identity_metadata_path,
    _prepare_omemo_storage_file,
    _read_omemo_identity_metadata,
)

class OmemoDeviceMixin:

    def _collect_omemo_storage_device_hints(self) -> dict[str, set[str]]:
        """Best-effort extract JID/device-id hints from the JSON storage file.

        The local OMEMO storage format is an implementation detail and can also
        contain pre-key IDs, counters, booleans and session metadata.  This
        helper therefore only collects values that are clearly associated with a
        device-like key.  It intentionally does not treat arbitrary integers
        below a JID context as device IDs.
        """
        storage_path = Path(str(getattr(self, "omemo_storage_file", "data/omemo.json"))).expanduser()
        if not storage_path.exists() or not storage_path.is_file():
            return {}

        try:
            data = json.loads(storage_path.read_text(encoding="utf8") or "{}")
        except Exception:
            return {}

        devices: dict[str, set[str]] = {}
        jid_re = re.compile(r"([A-Za-z0-9_.%+\-]+@[A-Za-z0-9.\-]+)")
        device_key_re = re.compile(r"(?:^|[_\-:./])(?:device[_\-]?id|device|dev)(?:$|[_\-:./])", re.I)
        device_with_id_re = re.compile(r"(?:device[_\-]?id|device|dev)[^0-9]{0,8}([0-9]{3,12})", re.I)

        def add_device(jid: str | None, value: Any) -> None:
            if not jid or isinstance(value, bool):
                return
            if isinstance(value, int):
                text = str(value)
            elif isinstance(value, str):
                text = value.strip()
            else:
                return
            if text.isdigit() and 0 < int(text) < 10_000_000_000:
                devices.setdefault(jid, set()).add(text)

        def walk(obj: Any, context_jid: str | None = None) -> None:
            if isinstance(obj, dict):
                local_jid = context_jid
                for key, value in obj.items():
                    key_text = str(key)
                    jid_match = jid_re.search(key_text)
                    if jid_match:
                        local_jid = jid_match.group(1).lower()
                        devices.setdefault(local_jid, set())

                    keyed_device = device_with_id_re.search(key_text)
                    if keyed_device and local_jid:
                        add_device(local_jid, keyed_device.group(1))
                    elif device_key_re.search(key_text) and local_jid:
                        add_device(local_jid, value)

                    walk(value, local_jid)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, context_jid)
            elif isinstance(obj, str):
                jid_match = jid_re.search(obj)
                if jid_match:
                    devices.setdefault(jid_match.group(1).lower(), set())
                dev_match = device_with_id_re.search(obj)
                if dev_match and context_jid:
                    add_device(context_jid, dev_match.group(1))

        walk(data)
        return devices

    @staticmethod
    def _format_omemo_device_ids(ids: set[str], *, limit: int = 12) -> str:
        """Return a compact, stable display string for local device-id hints."""
        if not ids:
            return "storage entry found, exact device IDs not visible"

        numeric_ids = [int(device_id) for device_id in ids if str(device_id).isdigit()]
        sorted_ids = [str(device_id) for device_id in sorted(numeric_ids)]
        if not sorted_ids:
            return "storage entry found, exact device IDs not visible"

        shown = sorted_ids[:limit]
        suffix = "" if len(sorted_ids) <= limit else f", … ({len(sorted_ids)} hints)"
        return f"{', '.join(shown)}{suffix}"

    async def _cmd_omemo_devices(self, room: str) -> None:
        lines = ["🔐 OMEMO Devices", ""]

        if getattr(self, "omemo_enabled", False):
            try:
                recipients = await self._omemo_recipients_for_room(__import__("config").ADMIN_ROOM)
            except Exception as exc:
                lines.append(f"⚠️ Could not collect current admin-room recipients: {exc}")
            else:
                lines.append(f"Current admin-room recipients: {len(recipients)}")
                if recipients:
                    for jid in sorted(str(j.bare) for j in recipients):
                        lines.append(f"• {jid}")
                else:
                    lines.append("• none")
        else:
            lines.append("OMEMO is disabled.")

        lines.append("")
        lines.append("Local storage hints:")
        devices = self._collect_omemo_storage_device_hints()
        if devices:
            for jid in sorted(devices):
                lines.append(f"• {jid}: {self._format_omemo_device_ids(devices[jid])}")
        else:
            lines.append("• no clear device-id hints found")

        lines.extend(
            [
                "",
                "Note:",
                "Local storage hints are best-effort only and may be stale.",
                "They are not a guaranteed list of currently active OMEMO devices.",
            ]
        )

        await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
