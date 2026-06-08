"""Managed backup mixin helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from typing import Any

import config

from ..locks import database_file_lock
from ..managed_files import list_managed_files, prune_managed_files
from ..utils import get_list_page_size, paginate_lines, resolve_page, wants_all_pages, without_all_pages_arg
from .common import (
    DatabaseBackup,
    _BACKUP_CONFIG_ENTRY,
    _BACKUP_DATABASE_ENTRY,
    _BACKUP_FORMAT,
    _BACKUP_MANIFEST_ENTRY,
    _BACKUP_OMEMO_ENTRY,
    _BACKUP_SAFE_RE,
)

log = logging.getLogger(__name__)

class BackupCommandMixin:

    async def cmd_backup(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !backup commands."""
        args = args or []
        action = args[0].lower() if args else "create"

        if action in ("create", "now"):
            ok, message = await self.create_database_backup("manual", actor=actor or "unknown")
            if ok:
                companions = self._backup_companion_names(pathlib.Path(message))
                companion_note = f"\nIncluded archive entries: {', '.join(companions)}" if companions else "\nIncluded archive entries: none"
                body = f"✅ Full backup archive created:\n{message}{companion_note}"
            else:
                body = f"❌ Database backup failed: {message}"
            await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")
            return

        if action == "list":
            backups = self.list_database_backups()
            show_all = wants_all_pages(args[1:])
            list_args = without_all_pages_arg(args[1:])
            page = 1
            if list_args:
                if list_args[0].lower() == "last":
                    page = -1
                else:
                    try:
                        page = max(1, int(list_args[0]))
                    except ValueError:
                        await self.bot_send_message(
                            mto=room,
                            mbody=f"❌ Usage: {self.command_prefix}backup list [all|page|last]",
                            mtype="groupchat",
                        )
                        return

            lines = ["💾 Managed Full Backups"]
            lines.append(f"Directory: {self._database_backup_dir()}")
            lines.append(f"Keep: {self._database_backup_keep()}")
            if not backups:
                lines.append("\nNo managed database backups found.")
            else:
                entries = [self._format_backup_entry(backup, index) for index, backup in enumerate(backups, start=1)]
                if show_all:
                    lines[0] = f"💾 Managed Full Backups ({len(entries)}) - All"
                    page_entries = entries
                else:
                    per_page = get_list_page_size(self)
                    page = resolve_page(page, len(entries), per_page)
                    page_entries, current_page, total_pages, total_items = paginate_lines(entries, page, per_page=per_page)
                    lines[0] = f"💾 Managed Full Backups ({total_items}) - Page {current_page}/{total_pages}"

                lines.append("")
                lines.extend(page_entries)
                lines.append("")
                lines.append(f"Restore with: {self.command_prefix}restore <filename|latest> confirm")
                lines.append(f"Delete with: {self.command_prefix}backup delete <filename|latest>")
                if not show_all and current_page < total_pages:
                    lines.append(f"Next page: {self.command_prefix}backup list {current_page + 1}")
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        if action == "show":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}backup show <filename|latest>",
                    mtype="groupchat",
                )
                return
            backup = self.resolve_database_backup(args[1])
            if backup is None:
                await self.bot_send_message(mto=room, mbody=f"❌ Backup not found: {args[1]}", mtype="groupchat")
                return
            await self.bot_send_message(mto=room, mbody=self._format_backup_details(backup), mtype="groupchat")
            return

        if action == "verify":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}backup verify <filename|latest>",
                    mtype="groupchat",
                )
                return
            ok, message = await self.verify_database_backup(args[1])
            prefix = "✅ Backup verified." if ok else "❌ Backup verification failed."
            await self.bot_send_message(mto=room, mbody=f"{prefix}\n{message}", mtype="groupchat")
            return

        if action in ("delete", "remove", "del", "rm"):
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}backup delete <filename|latest>",
                    mtype="groupchat",
                )
                return
            ok, message = await self.delete_database_backup(args[1], actor=actor)
            prefix = "✅ Backup deleted." if ok else "❌ Backup deletion failed."
            await self.bot_send_message(mto=room, mbody=f"{prefix}\n{message}", mtype="groupchat")
            return

        if action == "restore":
            await self.cmd_restore(args[1:], room, actor=actor)
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}backup\n"
                f"  {self.command_prefix}backup list [all|page|last]\n"
                f"  {self.command_prefix}backup show <filename|latest>\n"
                f"  {self.command_prefix}backup verify <filename|latest>\n"
                f"  {self.command_prefix}backup delete/remove <filename|latest>\n"
                f"  {self.command_prefix}restore <filename|latest> confirm"
            ),
            mtype="groupchat",
        )

    async def cmd_restore(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !restore and the legacy !backup restore alias."""
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}restore <filename|latest> confirm",
                mtype="groupchat",
            )
            return

        backup_name = args[0]
        backup = self.resolve_database_backup(backup_name)
        if backup is None:
            await self.bot_send_message(mto=room, mbody=f"❌ Backup not found: {backup_name}", mtype="groupchat")
            return

        if len(args) < 2 or args[1].lower() != "confirm":
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "⚠️ This will replace the current SQLite database"
                    " and restore config.py/OMEMO archive entries when present.\n\n"
                    f"Backup selected:\n{self._format_backup_entry(backup)}\n\n"
                    "A safety backup of the current DB will be created first.\n"
                    f"Confirm with: {self.command_prefix}restore {backup.name} confirm"
                ),
                mtype="groupchat",
            )
            return

        ok, message = await self.restore_database_backup(backup.name, actor=actor)
        prefix = "✅ Restore complete." if ok else "❌ Restore failed."
        await self.bot_send_message(mto=room, mbody=f"{prefix}\n{message}", mtype="groupchat")
