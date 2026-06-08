"""OMEMO reset command helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path

from .helpers import _backup_existing_path, _current_omemo_identity, _omemo_identity_metadata_path, _write_omemo_identity_metadata

log = logging.getLogger(__name__)

class OmemoResetMixin:

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

    def _schedule_omemo_reset_restart(self) -> None:
        """Schedule a delayed restart after OMEMO reset confirmation."""
        task = asyncio.create_task(self._restart_after_omemo_reset())
        self._restart_task = task

        clear_restart_task = getattr(self, "_clear_restart_task", None)
        if callable(clear_restart_task):
            task.add_done_callback(clear_restart_task)

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
        lines = [
            "✅ OMEMO storage reset prepared.",
            "OMEMO is disabled for this running process until restart.",
        ]
        if restart_available:
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

        if restart_available:
            self._schedule_omemo_reset_restart()

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
