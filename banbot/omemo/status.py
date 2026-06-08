"""OMEMO status rendering helpers."""

from __future__ import annotations

import logging
from pathlib import Path

from .helpers import _current_omemo_identity, _omemo_identity_metadata_path, _read_omemo_identity_metadata

log = logging.getLogger(__name__)

class OmemoStatusMixin:

    def _omemo_storage_status_lines(self) -> list[str]:
        """Return human-readable status lines for the configured OMEMO store."""
        storage_path = Path(str(getattr(self, "omemo_storage_file", "data/omemo.json"))).expanduser()
        metadata_path = _omemo_identity_metadata_path(storage_path)
        identity = _current_omemo_identity(__import__("config"))
        try:
            stored_identity = _read_omemo_identity_metadata(metadata_path)
        except Exception as exc:
            stored_identity = None
            metadata_error = str(exc)
        else:
            metadata_error = None

        import banbot.omemo as omemo_package

        lines = [
            f"🔐 OMEMO enabled: {getattr(self, 'omemo_enabled', False)}",
            f"📦 Optional dependencies available: {omemo_package.OMEMO_AVAILABLE}",
            f"✅ OMEMO ready: {bool(getattr(self, 'omemo_ready', None) and self.omemo_ready.is_set())}",
            f"📁 Storage file: {storage_path}",
            f"🪪 Identity reset on change: {getattr(self, 'omemo_reset_on_identity_change', True)}",
            f"♻️ Reset pending restart: {getattr(self, 'omemo_reset_pending_restart', False)}",
            f"🔁 Admin-room auto encryption: {getattr(self, 'omemo_auto_encrypt_admin_room', True)}",
            f"🧯 Plaintext fallback: {getattr(self, 'omemo_plaintext_fallback', False)}",
        ]

        if storage_path.exists():
            try:
                stat = storage_path.stat()
                lines.append(f"💾 Storage size: {stat.st_size} bytes")
                lines.append(f"🔒 Storage permissions: {oct(stat.st_mode & 0o777)}")
            except Exception as exc:
                lines.append(f"⚠️ Storage stat failed: {exc}")
        else:
            lines.append("⚠️ Storage file does not exist yet")

        lines.append(
            "🧬 Current identity: "
            f"jid={identity.get('jid') or '-'} resource={identity.get('resource') or '-'} nick={identity.get('nick') or '-'}"
        )
        if stored_identity:
            lines.append(
                "🧬 Stored identity:  "
                f"jid={stored_identity.get('jid') or '-'} resource={stored_identity.get('resource') or '-'} nick={stored_identity.get('nick') or '-'}"
            )
            lines.append(f"✅ Identity matches: {stored_identity == identity}")
        elif metadata_error:
            lines.append(f"⚠️ Identity metadata could not be read: {metadata_error}")
        else:
            lines.append("ℹ️ No identity metadata exists yet")

        return lines

    async def _cmd_omemo_status(self, room: str) -> None:
        await self.bot_send_message(
            mto=room,
            mbody="\n".join(["🔐 OMEMO Status", "", *self._omemo_storage_status_lines()]),
            mtype="groupchat",
        )
