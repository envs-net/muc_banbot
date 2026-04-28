"""CSV ban import/export, pre-import database backups, and import transactions."""

import asyncio
import csv
import logging
import pathlib
import shutil
from datetime import datetime

from config import DB_FILE

from .utils import normalize_ban_target, validate_domain_ban, validate_jid_format

log = logging.getLogger(__name__)


class ImportExportMixin:
    async def export_bans_to_csv(self) -> tuple[bool, str]:
        """
        Export all bans to a CSV file.
        Returns: (success: bool, message: str)
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"bans_export_{timestamp}.csv"

            # Use cache if available (instant!) otherwise query DB
            if self.ban_cache:
                rows = [(v[0], v[1], v[2], v[3], v[4]) for v in self.ban_cache.values()]
                log.info("📤 Export using cache (%d bans)", len(rows))
            else:
                async with self.db.execute(
                    "SELECT jid, nick, until, issuer, comment FROM bans"
                ) as cursor:
                    rows = await cursor.fetchall()
                log.info("📤 Export using database query (%d bans)", len(rows))

            if not rows:
                return False, "❌ No bans to export."

            try:
                with open(filename, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(["jid", "nick", "until", "issuer", "comment"])
                    for jid, nick, until, issuer, comment in rows:
                        writer.writerow([
                            jid or "",
                            nick or "",
                            until if until is not None else "",
                            issuer or "",
                            comment or ""
                        ])
                log.info("✅ Exported %d bans to %s", len(rows), filename)
                return True, f"✅ Exported {len(rows)} bans to {filename}"
            except IOError as e:
                log.error("File I/O error during export: %s", e)
                return False, f"❌ Failed to write file: {e}"

        except Exception as e:
            log.error("Export error: %s", e)
            return False, f"❌ Export failed: {e}"


    async def backup_database_before_import(self) -> tuple[bool, str]:
        """Create a timestamped SQLite DB backup before an import changes bans."""
        self.last_import_backup_file = None
        db_path = pathlib.Path(DB_FILE)
        if not db_path.exists():
            return False, f"Database file does not exist: {DB_FILE}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path.with_name(f"{db_path.name}.backup-before-import-{timestamp}")

        try:
            if self.db:
                await self.db.commit()
            await asyncio.to_thread(shutil.copy2, db_path, backup_path)
            self.last_import_backup_file = str(backup_path)
            log.info("✅ Created DB backup before import: %s", backup_path)
            self.log_event(logging.INFO, "db_backup_created", backup=str(backup_path), reason="before_import")
            await self.audit_event("db_backup_created", details={"backup": str(backup_path), "reason": "before_import"})
            return True, str(backup_path)
        except Exception as e:
            log.error("Failed to create DB backup before import: %s", e)
            return False, str(e)


    async def import_bans_from_csv(self, filename: str) -> tuple[int, int, list[str]]:
        """
        Import bans from a CSV file.
        Returns: (successful_count, skipped_count, error_messages)
        """
        successful = 0
        skipped = 0
        errors = []
        bans_to_insert = []

        try:
            if not pathlib.Path(filename).exists():
                errors.append(f"❌ File not found: {filename}")
                return 0, 0, errors

            try:
                with open(filename, "r", encoding="utf-8") as csvfile:
                    reader = csv.DictReader(csvfile)

                    if not reader.fieldnames or set(reader.fieldnames) != {"jid", "nick", "until", "issuer", "comment"}:
                        errors.append("❌ Invalid CSV header. Expected: jid,nick,until,issuer,comment")
                        return 0, 0, errors

                    rows = list(reader)

            except IOError as e:
                errors.append(f"❌ File I/O error: {e}")
                log.error("Import file error: %s", e)
                return 0, 0, errors

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
                            log.info(
                                "Row %d: resolving nick-only import %s to existing JID ban %s",
                                row_num,
                                normalized_nick,
                                existing_jid,
                            )
                            target_type = "jid"
                            target = existing_jid
                            normalized_jid = existing_jid

                    lookup_key = f"*.{target}" if target_type == "domain" else target
                    if lookup_key in self.ban_cache:
                        existing = self.ban_cache[lookup_key]
                        existing_until = existing[2]
                        if existing_until <= 0 and until <= 0:
                            log.info("Row %d: Ban already exists for %s (permanent), skipping", row_num, lookup_key)
                            skipped += 1
                            continue

                    bans_to_insert.append((
                        target_type,
                        target,
                        normalized_jid,
                        normalized_nick,
                        until,
                        issuer,
                        comment,
                    ))
                    self._cache_ban(normalized_jid, normalized_nick, until, issuer, comment)
                    successful += 1

                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")
                    skipped += 1

            if bans_to_insert:
                backup_ok, backup_message = await self.backup_database_before_import()
                if not backup_ok:
                    await self.load_bans_from_db()
                    errors.append(f"❌ Import aborted: failed to create DB backup before import: {backup_message}")
                    return 0, 0, errors

                try:
                    await self.db.execute("BEGIN")
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
                    log.info("✅ Batch upserted %d bans", len(bans_to_insert))
                except Exception as e:
                    log.error("Batch insert failed, rolling back import transaction: %s", e)
                    try:
                        await self.db.rollback()
                    except Exception as rollback_error:
                        log.error("Rollback after failed import also failed: %s", rollback_error)
                    await self.load_bans_from_db()
                    errors.append(f"❌ Database batch insert failed: {e}")
                    return 0, 0, errors

            log.info("Import complete: %d successful, %d skipped", successful, skipped)

        except Exception as e:
            errors.append(f"❌ Import failed: {e}")
            log.error("Import error: %s", e)

        return successful, skipped, errors
