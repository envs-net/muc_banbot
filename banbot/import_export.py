"""CSV ban import/export and import transactions with managed full backups."""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import pathlib
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

try:
    import config
except ModuleNotFoundError:
    config = None

from .ban_target import BanTarget
from .cache import BanTuple
from .utils import (
    get_list_page_size,
    paginate_lines,
    resolve_page,
    validate_domain_ban,
    validate_jid_format,
    wants_all_pages,
    without_all_pages_arg,
)

log = logging.getLogger(__name__)

from .locks import ban_state_lock, database_file_lock, database_mutation_locks
from .managed_files import ManagedFile, format_file_size, list_managed_files, prune_managed_files, resolve_managed_file
from .managed_io import run_blocking_io

type ImportBanRow = tuple[str, str, str | None, str | None, int, str | None, str | None]


if TYPE_CHECKING:
    from .contracts import ImportExportMixinHost

    class _ImportExportMixinContract(ImportExportMixinHost):
        pass
else:
    class _ImportExportMixinContract:
        pass


class ImportExportMixin(_ImportExportMixinContract):
    def _export_config_value(self, name: str, default: Any) -> Any:
        if config is None:
            return default
        value = getattr(config, name, default)
        return default if value is None else value

    def _export_dir(self) -> pathlib.Path:
        raw_dir = str(self._export_config_value("EXPORT_DIR", "data/exports")).strip()
        return pathlib.Path(raw_dir or "data/exports").expanduser()

    def _export_keep(self) -> int:
        try:
            return max(1, int(self._export_config_value("EXPORT_KEEP", 15)))
        except Exception:
            return 15

    def _format_export_size(self, size: int) -> str:
        return format_file_size(size)

    def _is_export_file(self, path: pathlib.Path) -> bool:
        return path.is_file() and path.name.startswith("bans_export_") and path.suffix == ".csv"

    def list_export_files(self) -> list[ManagedFile]:
        return list_managed_files(
            self._export_dir(),
            "bans_export_*.csv",
            predicate=self._is_export_file,
        )

    def _format_export_entry(self, export_file: ManagedFile, index: int | None = None) -> str:
        prefix = f"{index}. " if index is not None else ""
        return f"{prefix}{export_file.name} ({self._format_export_size(export_file.size)}, {export_file.mtime_text})"

    async def prune_export_files(self, *, preserve: pathlib.Path | None = None) -> list[pathlib.Path]:
        try:
            removed = await prune_managed_files(
                self.list_export_files(),
                keep=self._export_keep(),
                preserve=preserve,
            )
        except OSError as exc:
            log.warning("Failed to prune export files: %s", exc)
            return []
        for path in removed:
            log.info("Deleted old export file: %s", path)
        return removed

    def resolve_export_file(self, name: str) -> pathlib.Path | None:
        return resolve_managed_file(
            self._export_dir(),
            name,
            self.list_export_files(),
            predicate=self._is_export_file,
        )

    @staticmethod
    def _write_export_csv_sync(filename: pathlib.Path, rows: list[BanTuple]) -> None:
        """Write one CSV export completely before returning to the event loop."""
        with filename.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["jid", "nick", "until", "issuer", "comment"])
            for jid, nick, until, issuer, comment in rows:
                writer.writerow(
                    [
                        jid or "",
                        nick or "",
                        until if until is not None else "",
                        issuer or "",
                        comment or "",
                    ]
                )

    async def export_bans_to_csv(self) -> tuple[bool, str]:
        """Export a consistent ban snapshot to a managed CSV file."""
        try:
            async with database_file_lock(self):
                export_dir = self._export_dir()
                export_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(export_dir, 0o700)
                except OSError as exc:
                    log.debug(
                        "Failed to restrict export directory permissions for %s: %s",
                        export_dir,
                        exc,
                    )

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = export_dir / f"bans_export_{timestamp}.csv"
                counter = 2
                while filename.exists():
                    filename = export_dir / f"bans_export_{timestamp}-{counter}.csv"
                    counter += 1

                async with ban_state_lock(self):
                    if self.ban_cache:
                        rows: list[BanTuple] = list(self.ban_cache.values())
                        log.info("📤 Export using cache (%d bans)", len(rows))
                    else:
                        db = self.db
                        if db is not None:
                            async with db.execute(
                                "SELECT jid, nick, until, issuer, comment FROM bans"
                            ) as cursor:
                                fetched = await cursor.fetchall()
                            rows = [
                                (jid, nick, int(until or 0), issuer, comment)
                                for jid, nick, until, issuer, comment in fetched
                            ]
                            log.info("📤 Export using database query (%d bans)", len(rows))
                        else:
                            rows = []
                            log.info(
                                "📤 Export skipped: no cache entries and no database connection"
                            )

                if not rows:
                    return False, "❌ No bans to export."

                try:
                    await run_blocking_io(self._write_export_csv_sync, filename, rows)
                except asyncio.CancelledError:
                    await run_blocking_io(filename.unlink, missing_ok=True)
                    raise
                try:
                    os.chmod(filename, 0o600)
                except OSError as exc:
                    log.debug(
                        "Failed to restrict export file permissions for %s: %s",
                        filename,
                        exc,
                    )
                await self.prune_export_files(preserve=filename)
                log.info("✅ Exported %d bans to %s", len(rows), filename)
                return True, f"✅ Exported {len(rows)} bans to {filename}"
        except OSError as exc:
            log.error("File I/O error during export: %s", exc)
            return False, f"❌ Failed to write file: {exc}"
        except Exception as exc:
            log.error("Export error: %s", exc)
            return False, f"❌ Export failed: {exc}"

    async def delete_export_file(self, name: str) -> tuple[bool, str]:
        """Delete one managed export while serialized with other file operations."""
        async with database_file_lock(self):
            path = self.resolve_export_file(name)
            if path is None:
                return False, f"❌ Export not found: {name}"
            try:
                await run_blocking_io(path.unlink)
            except OSError as exc:
                return False, f"❌ Failed to delete export: {exc}"
            return True, path.name

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
                            mbody=f"❌ Usage: {self.command_prefix}export list [all|page|last]",
                            mtype="groupchat",
                        )
                        return

            lines = ["📦 Managed Ban Exports", f"Directory: {self._export_dir()}", f"Keep: {self._export_keep()}"]
            if not exports:
                lines.append("\nNo managed export files found.")
            else:
                entries = [self._format_export_entry(export_file, index) for index, export_file in enumerate(exports, start=1)]
                if show_all:
                    lines[0] = f"📦 Managed Ban Exports ({len(entries)}) - All"
                    page_entries = entries
                else:
                    per_page = get_list_page_size(self)
                    page = resolve_page(page, len(entries), per_page)
                    page_entries, current_page, total_pages, total_items = paginate_lines(entries, page, per_page=per_page)
                    lines[0] = f"📦 Managed Ban Exports ({total_items}) - Page {current_page}/{total_pages}"

                lines.append("")
                lines.extend(page_entries)
                lines.append("")
                lines.append(f"Delete with: {self.command_prefix}export delete <filename|latest>")
                if not show_all and current_page < total_pages:
                    lines.append(f"Next page: {self.command_prefix}export list {current_page + 1}")
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return
        if action == "show":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}export show <filename|latest>",
                    mtype="groupchat",
                )
                return

            path = self.resolve_export_file(args[1])
            if path is None:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Export not found: {args[1]}",
                    mtype="groupchat",
                )
                return

            try:
                stat = path.stat()
                size = stat.st_size
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                body = (
                    "📦 Managed Ban Export\n"
                    f"Filename: {path.name}\n"
                    f"Directory: {self._export_dir()}\n"
                    f"Size: {size} bytes\n"
                    f"Modified: {modified}"
                )
            except OSError as exc:
                body = f"❌ Failed to inspect export: {exc}"

            await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")
            return

        if action in ("delete", "remove", "del", "rm"):
            if len(args) < 2:
                await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}export delete <filename|latest>", mtype="groupchat")
                return
            ok, result = await self.delete_export_file(args[1])
            body = f"✅ Export deleted: {result}" if ok else result
            await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")
            return
        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}export\n"
                f"  {self.command_prefix}export list [all|page|last]\n"
                f"  {self.command_prefix}export show <filename|latest>\n"
                f"  {self.command_prefix}export delete/remove/del/rm <filename|latest>"
            ),
            mtype="groupchat",
        )

    @staticmethod
    def _read_import_csv_sync(
        path: pathlib.Path,
    ) -> tuple[Sequence[str] | None, list[dict[str, str | None]]]:
        with path.open(encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            return reader.fieldnames, list(reader)

    async def _stage_ban_import_rows(
        self, filename: str
    ) -> tuple[list[ImportBanRow], int, list[str]]:
        """Read and validate CSV import rows. Returns staged rows, skipped count and errors."""
        skipped = 0
        errors: list[str] = []
        staged_rows: dict[str, ImportBanRow] = {}
        path = pathlib.Path(filename)
        if not path.exists():
            return [], 0, [f"❌ File not found: {filename}"]
        try:
            fieldnames, rows = await run_blocking_io(self._read_import_csv_sync, path)
            if not fieldnames or set(fieldnames) != {"jid", "nick", "until", "issuer", "comment"}:
                return [], 0, ["❌ Invalid CSV header. Expected: jid,nick,until,issuer,comment"]
        except OSError as exc:
            log.error("Import file error: %s", exc)
            return [], 0, [f"❌ File I/O error: {exc}"]

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
                ban_target = BanTarget.from_parts(jid, nick)
                if ban_target.kind == "nick" and ban_target.nick:
                    existing_jid_ban = await self.find_active_jid_ban_by_nick(ban_target.nick)
                    if existing_jid_ban:
                        existing_jid, _existing_until, _existing_issuer, _existing_comment = existing_jid_ban
                        log.info(
                            "Row %d: resolving nick-only import %s to existing JID ban %s",
                            row_num,
                            ban_target.nick,
                            existing_jid,
                        )
                        ban_target = BanTarget.from_parts(existing_jid, ban_target.nick)

                target_type = ban_target.kind
                target = ban_target.value
                normalized_jid = ban_target.jid
                normalized_nick = ban_target.nick
                lookup_key = ban_target.identifier

                existing = self.ban_cache.get(lookup_key)
                if existing:
                    existing_until = int(existing[2] or 0)
                    if existing_until <= 0:
                        log.info(
                            "Row %d: Existing permanent ban for %s is stronger, skipping",
                            row_num,
                            lookup_key,
                        )
                        skipped += 1
                        continue
                    if until > 0 and until <= existing_until:
                        log.info(
                            "Row %d: Existing temporary ban for %s expires later, skipping",
                            row_num,
                            lookup_key,
                        )
                        skipped += 1
                        continue

                candidate: ImportBanRow = (target_type, target, normalized_jid, normalized_nick, until, issuer, comment)
                previous = staged_rows.get(lookup_key)
                if previous:
                    previous_until = int(previous[4] or 0)
                    if previous_until <= 0:
                        log.info(
                            "Row %d: Existing staged permanent import for %s is stronger, skipping",
                            row_num,
                            lookup_key,
                        )
                        skipped += 1
                        continue
                    if until > 0 and until <= previous_until:
                        log.info(
                            "Row %d: Existing staged temporary import for %s expires later, skipping",
                            row_num,
                            lookup_key,
                        )
                        skipped += 1
                        continue
                    log.info(
                        "Row %d: Replacing weaker staged import row for %s",
                        row_num,
                        lookup_key,
                    )
                    skipped += 1

                staged_rows[lookup_key] = candidate
            except Exception as e:
                errors.append(f"Row {row_num}: {e}")
                skipped += 1
        return list(staged_rows.values()), skipped, errors

    async def import_bans_from_csv(
        self,
        filename: str,
        *,
        actor: str | None = None,
        dry_run: bool = False,
    ) -> tuple[int, int, list[str]]:
        """Import bans from a CSV file.

        Returns (successful_or_staged, skipped, errors). In normal mode,
        successful_or_staged is the number of bans actually imported. In dry-run
        mode, it is the number of bans staged and would be imported.
        """
        errors: list[str] = []
        try:
            async with database_mutation_locks(self):
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
        """Run CSV import while required locks are held.

        Returns (successful_or_staged, skipped, errors). In dry-run mode,
        successful_or_staged is the count of staged rows that would be imported.
        """
        bans_to_insert, skipped, errors = await self._stage_ban_import_rows(filename)
        successful = len(bans_to_insert)
        if dry_run:
            log.info("Import dry-run complete: %d staged, %d skipped", successful, skipped)
            return successful, skipped, errors
        if not bans_to_insert:
            log.info("Import complete: 0 successful, %d skipped", skipped)
            return 0, skipped, errors
        try:
            db = self._require_db()
        except RuntimeError as exc:
            errors.append(f"❌ Import aborted: {exc}")
            return 0, skipped, errors

        backup_ok, backup_message = await self.create_database_backup(
            "before-import",
            actor=actor or "import",
            lock=False,
        )
        if not backup_ok:
            await self.load_bans_from_db()
            errors.append(
                f"❌ Import aborted: failed to create full backup before import: {backup_message}"
            )
            return 0, skipped, errors

        try:
            await db.executemany(
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
            await db.commit()
        except Exception as exc:
            log.error("Batch insert failed, rolling back import transaction: %s", exc)
            try:
                await db.rollback()
            except Exception as rollback_error:
                log.error("Rollback after failed import also failed: %s", rollback_error)
            await self.load_bans_from_db()
            errors.append(f"❌ Database batch insert failed: {exc}")
            return 0, skipped, errors

        # The commit above is the point of no return. Cache refresh and optional
        # redaction are post-commit side effects; failures there must never make
        # the command claim that zero bans were imported or attempt a fake
        # rollback of already-persisted rows.
        try:
            for (
                _target_type,
                _target,
                normalized_jid,
                normalized_nick,
                until,
                issuer,
                comment,
            ) in bans_to_insert:
                self._cache_ban(normalized_jid, normalized_nick, until, issuer, comment)
        except Exception as exc:
            log.error("Import committed but cache update failed; reloading cache: %s", exc)
            await self.load_bans_from_db()
            errors.append(f"⚠️ Import committed, but cache refresh needed recovery: {exc}")

        for (
            target_type,
            _target,
            normalized_jid,
            _normalized_nick,
            until,
            issuer,
            comment,
        ) in bans_to_insert:
            if target_type != "jid" or not normalized_jid or int(until or 0) > 0:
                continue
            try:
                await self.maybe_auto_redact_after_imported_ban(
                    normalized_jid,
                    comment,
                    actor=actor or issuer or "import",
                )
            except Exception as exc:
                log.error(
                    "Imported %s but automatic redaction failed: %s",
                    normalized_jid,
                    exc,
                )
                errors.append(
                    "⚠️ Import committed, but automatic redaction failed for "
                    f"{normalized_jid}: {exc}"
                )

        log.info("✅ Batch upserted %d bans", len(bans_to_insert))
        log.info("Import complete: %d successful, %d skipped", successful, skipped)
        return successful, skipped, errors
