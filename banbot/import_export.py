"""CSV ban import/export and import transactions with managed full backups."""

from __future__ import annotations

import csv
import logging
import os
import pathlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import config
except ModuleNotFoundError:
    config = None

from .utils import normalize_ban_target, validate_domain_ban, validate_jid_format

log = logging.getLogger(__name__)

from .locks import get_ban_state_lock, get_database_file_lock


@dataclass(frozen=True)
class ExportFile:
    path: pathlib.Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S")


class ImportExportMixin:
    def _export_config_value(self, name: str, default: Any) -> Any:
        if config is None:
            return default
        return getattr(config, name, default)

    def _export_dir(self) -> pathlib.Path:
        raw_dir = str(self._export_config_value("EXPORT_DIR", "data/exports")).strip()
        return pathlib.Path(raw_dir or "data/exports").expanduser()

    def _export_keep(self) -> int:
        try:
            return max(1, int(self._export_config_value("EXPORT_KEEP", 15)))
        except Exception:
            return 15

    def _format_export_size(self, size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MiB"
        if size >= 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size} B"

    def list_export_files(self) -> list[ExportFile]:
        export_dir = self._export_dir()
        if not export_dir.exists():
            return []
        files: list[ExportFile] = []
        for path in export_dir.glob("bans_export_*.csv"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(ExportFile(path=path, size=stat.st_size, mtime=stat.st_mtime))
        files.sort(key=lambda item: (item.mtime, item.name), reverse=True)
        return files

    def _format_export_entry(self, export_file: ExportFile, index: int | None = None) -> str:
        prefix = f"{index}. " if index is not None else ""
        return f"{prefix}{export_file.name} ({self._format_export_size(export_file.size)}, {export_file.mtime_text})"

    async def prune_export_files(self, *, preserve: pathlib.Path | None = None) -> list[pathlib.Path]:
        keep = self._export_keep()
        preserve_resolved = preserve.resolve() if preserve is not None and preserve.exists() else None
        removed: list[pathlib.Path] = []
        kept = 0
        for export_file in self.list_export_files():
            try:
                export_resolved = export_file.path.resolve()
            except OSError:
                export_resolved = export_file.path
            if preserve_resolved is not None and export_resolved == preserve_resolved:
                kept += 1
                continue
            if kept < keep:
                kept += 1
                continue
            try:
                export_file.path.unlink()
                removed.append(export_file.path)
                log.info("Deleted old export file: %s", export_file.path)
            except OSError as exc:
                log.warning("Failed to delete old export file %s: %s", export_file.path, exc)
        return removed

    def resolve_export_file(self, name: str) -> pathlib.Path | None:
        query = str(name).strip()
        if not query:
            return None
        exports = self.list_export_files()
        if query.lower() == "latest":
            return exports[0].path if exports else None
        for export_file in exports:
            if query == export_file.name or query == str(export_file.path):
                return export_file.path
        export_dir = self._export_dir().resolve()
        candidate = pathlib.Path(query).expanduser()
        if not candidate.is_absolute():
            candidate = self._export_dir() / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if export_dir not in (resolved, *resolved.parents):
            return None
        if not resolved.is_file() or not resolved.name.startswith("bans_export_") or resolved.suffix != ".csv":
            return None
        return resolved

    async def export_bans_to_csv(self) -> tuple[bool, str]:
        """Export all bans to a managed CSV file."""
        try:
            export_dir = self._export_dir()
            export_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(export_dir, 0o700)
            except OSError as exc:
                log.debug("Failed to restrict export directory permissions for %s: %s", export_dir, exc)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = export_dir / f"bans_export_{timestamp}.csv"

            if self.ban_cache:
                rows = [(v[0], v[1], v[2], v[3], v[4]) for v in self.ban_cache.values()]
                log.info("📤 Export using cache (%d bans)", len(rows))
            else:
                async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
                    rows = await cursor.fetchall()
                log.info("📤 Export using database query (%d bans)", len(rows))

            if not rows:
                return False, "❌ No bans to export."

            with open(filename, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["jid", "nick", "until", "issuer", "comment"])
                for jid, nick, until, issuer, comment in rows:
                    writer.writerow([jid or "", nick or "", until if until is not None else "", issuer or "", comment or ""])
            try:
                os.chmod(filename, 0o600)
            except OSError as exc:
                log.debug("Failed to restrict export file permissions for %s: %s", filename, exc)
            await self.prune_export_files(preserve=filename)
            log.info("✅ Exported %d bans to %s", len(rows), filename)
            return True, f"✅ Exported {len(rows)} bans to {filename}"
        except IOError as e:
            log.error("File I/O error during export: %s", e)
            return False, f"❌ Failed to write file: {e}"
        except Exception as e:
            log.error("Export error: %s", e)
            return False, f"❌ Export failed: {e}"

    async def cmd_export(self, args: list[str], room: str) -> None:
        """Handle !export commands."""
        args = args or []
        action = args[0].lower() if args else "create"
        if action in ("create", "now"):
            _success, message = await self.export_bans_to_csv()
            await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
            return
        if action == "list":
            exports = self.list_export_files()
            lines = ["📦 Managed Ban Exports", f"Directory: {self._export_dir()}", f"Keep: {self._export_keep()}"]
            if not exports:
                lines.append("\nNo managed export files found.")
            else:
                lines.append("")
                for index, export_file in enumerate(exports[:20], start=1):
                    lines.append(self._format_export_entry(export_file, index))
                if len(exports) > 20:
                    lines.append(f"... and {len(exports) - 20} more exports")
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return
        if action == "delete":
            if len(args) < 2:
                await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}export delete <filename|latest>", mtype="groupchat")
                return
            path = self.resolve_export_file(args[1])
            if path is None:
                await self.bot_send_message(mto=room, mbody=f"❌ Export not found: {args[1]}", mtype="groupchat")
                return
            try:
                path.unlink()
            except OSError as exc:
                await self.bot_send_message(mto=room, mbody=f"❌ Failed to delete export: {exc}", mtype="groupchat")
                return
            await self.bot_send_message(mto=room, mbody=f"✅ Export deleted: {path.name}", mtype="groupchat")
            return
        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}export\n"
                f"  {self.command_prefix}export list\n"
                f"  {self.command_prefix}export delete <filename|latest>"
            ),
            mtype="groupchat",
        )

    async def _stage_ban_import_rows(self, filename: str) -> tuple[list[tuple], int, list[str]]:
        """Read and validate CSV import rows. Returns staged rows, skipped count and errors."""
        skipped = 0
        errors: list[str] = []
        bans_to_insert: list[tuple] = []
        staged_lookup_keys: set[str] = set()
        path = pathlib.Path(filename)
        if not path.exists():
            return [], 0, [f"❌ File not found: {filename}"]
        try:
            with open(path, "r", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                if not reader.fieldnames or set(reader.fieldnames) != {"jid", "nick", "until", "issuer", "comment"}:
                    return [], 0, ["❌ Invalid CSV header. Expected: jid,nick,until,issuer,comment"]
                rows = list(reader)
        except IOError as e:
            log.error("Import file error: %s", e)
            return [], 0, [f"❌ File I/O error: {e}"]

        for row_num, row in enumerate(rows, start=2):
            try:
                jid = (row.get("jid") or "").strip() or None
                nick = (row.get("nick") or "").strip() or None
                until_str = (row.get("until") or "").strip()
                issuer = (row.get("issuer") or "").strip() or "import"
                comment = (row.get("comment") or "").strip() or None
                if not jid and not nick:
                    errors.append(f"Row {row_num}: At least one of jid or nick required")
                    skipped += 1
                    continue
                if jid and jid.startswith("*."):
                    is_valid, error_msg = validate_domain_ban(jid)
                    if not is_valid:
                        errors.append(f"Row {row_num}: {error_msg}")
                        skipped += 1
                        continue
                elif jid and not validate_jid_format(jid):
                    errors.append(f"Row {row_num}: Invalid JID format: {jid}")
                    skipped += 1
                    continue
                try:
                    until = int(until_str) if until_str else 0
                    if until < 0:
                        errors.append(f"Row {row_num}: until must be >= 0 (got {until})")
                        skipped += 1
                        continue
                except ValueError:
                    errors.append(f"Row {row_num}: until must be a valid number or empty (got '{until_str}')")
                    skipped += 1
                    continue
                target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid, nick)
                if target_type == "domain" and normalized_jid is None:
                    normalized_jid = f"*.{target}"
                if target_type == "nick" and normalized_nick:
                    existing_jid_ban = await self.find_active_jid_ban_by_nick(normalized_nick)
                    if existing_jid_ban:
                        existing_jid, _existing_until, _existing_issuer, _existing_comment = existing_jid_ban
                        log.info("Row %d: resolving nick-only import %s to existing JID ban %s", row_num, normalized_nick, existing_jid)
                        target_type = "jid"
                        target = existing_jid
                        normalized_jid = existing_jid
                lookup_key = f"*.{target}" if target_type == "domain" else target
                if lookup_key in staged_lookup_keys:
                    log.info("Row %d: Duplicate staged import row for %s, skipping", row_num, lookup_key)
                    skipped += 1
                    continue
                if lookup_key in self.ban_cache:
                    existing = self.ban_cache[lookup_key]
                    existing_until = existing[2]
                    if existing_until <= 0 and until <= 0:
                        log.info("Row %d: Ban already exists for %s (permanent), skipping", row_num, lookup_key)
                        skipped += 1
                        continue
                bans_to_insert.append((target_type, target, normalized_jid, normalized_nick, until, issuer, comment))
                staged_lookup_keys.add(lookup_key)
            except Exception as e:
                errors.append(f"Row {row_num}: {e}")
                skipped += 1
        return bans_to_insert, skipped, errors

    async def import_bans_from_csv(
        self,
        filename: str,
        *,
        actor: str | None = None,
        dry_run: bool = False,
    ) -> tuple[int, int, list[str]]:
        """Import bans from a CSV file. Returns successful/skipped/errors."""
        errors: list[str] = []
        try:
            async with get_database_file_lock(self):
                async with get_ban_state_lock(self):
                    return await self._import_bans_from_csv_locked(
                        filename,
                        actor=actor,
                        dry_run=dry_run,
                    )
        except Exception as e:
            errors.append(f"❌ Import failed: {e}")
            log.error("Import error: %s", e)
            return 0, 0, errors

    async def _import_bans_from_csv_locked(
        self,
        filename: str,
        *,
        actor: str | None = None,
        dry_run: bool = False,
    ) -> tuple[int, int, list[str]]:
        bans_to_insert, skipped, errors = await self._stage_ban_import_rows(filename)
        successful = len(bans_to_insert)
        if dry_run:
            log.info("Import dry-run complete: %d staged, %d skipped", successful, skipped)
            return successful, skipped, errors
        if not bans_to_insert:
            log.info("Import complete: 0 successful, %d skipped", skipped)
            return 0, skipped, errors
        create_backup = getattr(self, "create_database_backup", None)
        if not callable(create_backup):
            await self.load_bans_from_db()
            errors.append("❌ Import aborted: managed full backups are unavailable.")
            return 0, skipped, errors
        backup_ok, backup_message = await create_backup("before-import", actor=actor or "import", lock=False)
        if not backup_ok:
            await self.load_bans_from_db()
            errors.append(f"❌ Import aborted: failed to create full backup before import: {backup_message}")
            return 0, skipped, errors
        try:
            await self.db.executemany(
                """
                INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
                ON CONFLICT(target_type, target) DO UPDATE SET
                    jid = excluded.jid,
                    nick = excluded.nick,
                    until = excluded.until,
                    issuer = excluded.issuer,
                    comment = excluded.comment,
                    updated_at = strftime('%s','now')
                """,
                bans_to_insert,
            )
            await self.db.commit()
            for _target_type, _target, normalized_jid, normalized_nick, until, issuer, comment in bans_to_insert:
                self._cache_ban(normalized_jid, normalized_nick, until, issuer, comment)
            log.info("✅ Batch upserted %d bans", len(bans_to_insert))
        except Exception as e:
            log.error("Batch insert failed, rolling back import transaction: %s", e)
            try:
                await self.db.rollback()
            except Exception as rollback_error:
                log.error("Rollback after failed import also failed: %s", rollback_error)
            await self.load_bans_from_db()
            errors.append(f"❌ Database batch insert failed: {e}")
            return 0, skipped, errors
        log.info("Import complete: %d successful, %d skipped", successful, skipped)
        return successful, skipped, errors
