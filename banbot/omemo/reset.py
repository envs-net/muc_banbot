"""OMEMO reset command helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .helpers import (
    _backup_existing_path,
    _current_omemo_identity,
    _omemo_identity_metadata_path,
    _write_omemo_identity_metadata,
)

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ..contracts import OmemoResetMixinHost

    class _OmemoResetMixinContract(OmemoResetMixinHost):
        pass
else:
    class _OmemoResetMixinContract:
        pass


class OmemoResetMixin(_OmemoResetMixinContract):

    async def _restart_after_omemo_reset(self) -> None:
        """Restart the bot after OMEMO storage was reset."""
        import banbot.omemo as omemo_package

        await asyncio.sleep(omemo_package.OMEMO_RESET_RESTART_DELAY_SECONDS)

        restart = getattr(self, "_restart_process", None)
        if not callable(restart):
            log.warning(
                "OMEMO: reset completed but no restart helper is available; "
                "restart the bot manually"
            )
            return

        result = restart()
        if inspect.isawaitable(result):
            restart_result = await result
            if restart_result is not None:
                log.debug("OMEMO: reset restart helper returned %r", restart_result)

    def _schedule_omemo_reset_restart(self) -> bool:
        """Schedule the delayed OMEMO restart through the shared restart slot."""
        return self._schedule_restart_task(
            self._restart_after_omemo_reset,
            name="omemo-reset-restart",
        )

    async def _cmd_omemo_reset(self, room: str, actor: str | None, confirm: bool) -> None:
        if not confirm:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "⚠️ This rotates the local OMEMO storage and identity metadata to .bak-* files.\n"
                    "A restart is required afterwards so the OMEMO plugin creates a fresh identity.\n\n"
                    f"Confirm with: {getattr(self, 'command_prefix', '!')}omemo reset confirm"
                ),
                mtype="groupchat",
                encrypted=False,
            )
            return

        if getattr(self, "omemo_reset_pending_restart", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "ℹ️ OMEMO reset is already prepared and waiting for restart. "
                    "No additional storage rotation was performed."
                ),
                mtype="groupchat",
                encrypted=False,
            )
            return

        storage_path = Path(str(getattr(self, "omemo_storage_file", "data/omemo.json"))).expanduser()
        metadata_path = _omemo_identity_metadata_path(storage_path)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        storage_backup = _backup_existing_path(storage_path, timestamp)
        metadata_backup = _backup_existing_path(metadata_path, timestamp)
        identity = _current_omemo_identity(__import__("config"))
        _write_omemo_identity_metadata(metadata_path, identity)

        self.omemo_ready.clear()
        self.omemo_enabled = False
        self.omemo_reset_pending_restart = True
        restart_available = callable(getattr(self, "_restart_process", None))
        restart_pending = restart_available and self._restart_task_pending()
        lines = [
            "✅ OMEMO storage reset prepared.",
            "OMEMO is disabled for this running process until restart.",
        ]
        if restart_pending:
            lines.append(
                "A process restart is already scheduled; it will create and publish "
                "the fresh OMEMO identity."
            )
        elif restart_available:
            lines.append(
                f"Restarting in {__import__('banbot.omemo', fromlist=['OMEMO_RESET_RESTART_DELAY_SECONDS']).OMEMO_RESET_RESTART_DELAY_SECONDS} seconds "
                "to create and publish a fresh OMEMO identity."
            )
        else:
            lines.append("Restart the bot now to create and publish a fresh OMEMO identity.")
        if storage_backup:
            lines.append(f"Old storage backup: {storage_backup}")
        if metadata_backup:
            lines.append(f"Old metadata backup: {metadata_backup}")
        if not storage_backup and not metadata_backup:
            lines.append("No existing storage/metadata files had to be rotated.")

        try:
            await self.audit_event(
                "omemo_reset",
                actor=actor or "unknown",
                details={"storage_backup": str(storage_backup) if storage_backup else None, "metadata_backup": str(metadata_backup) if metadata_backup else None},
            )
        except Exception as exc:
            log.debug("Failed to audit OMEMO reset: %s", exc)

        await self.bot_send_message(
            mto=room,
            mbody="\n".join(lines),
            mtype="groupchat",
            encrypted=False,
        )

        if restart_available and not restart_pending:
            if not self._schedule_omemo_reset_restart():
                log.info("OMEMO: process restart became pending while reset completed")

    async def cmd_omemo(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Admin command entry point for OMEMO diagnostics and reset."""
        action = args[0].lower() if args else "status"
        if action == "status":
            await self._cmd_omemo_status(room)
            return
        if action in ("devices", "device"):
            await self._cmd_omemo_devices(room)
            return
        if action == "reset":
            await self._cmd_omemo_reset(room, actor, confirm=len(args) > 1 and args[1].lower() == "confirm")
            return
        if action in ("help", "usage"):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "Usage:\n"
                    f"  {getattr(self, 'command_prefix', '!')}omemo status\n"
                    f"  {getattr(self, 'command_prefix', '!')}omemo devices\n"
                    f"  {getattr(self, 'command_prefix', '!')}omemo reset [confirm]\n"
                    f"  {getattr(self, 'command_prefix', '!')}omemo help"
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {getattr(self, 'command_prefix', '!')}omemo status\n"
                f"  {getattr(self, 'command_prefix', '!')}omemo devices\n"
                f"  {getattr(self, 'command_prefix', '!')}omemo reset [confirm]\n"
                f"  {getattr(self, 'command_prefix', '!')}omemo help"
            ),
            mtype="groupchat",
        )
