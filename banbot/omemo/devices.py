"""OMEMO device inspection helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from envs_xmpp_core.xmpp.omemo import collect_storage_device_hints, format_device_ids

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ..contracts import OmemoDeviceMixinHost

    class _OmemoDeviceMixinContract(OmemoDeviceMixinHost):
        pass
else:
    class _OmemoDeviceMixinContract:
        pass


class OmemoDeviceMixin(_OmemoDeviceMixinContract):

    def _collect_omemo_storage_device_hints(self) -> dict[str, set[str]]:
        return collect_storage_device_hints(self.omemo_storage_file)

    @staticmethod
    def _format_omemo_device_ids(ids: set[str], *, limit: int = 12) -> str:
        return format_device_ids(ids, limit=limit)

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
