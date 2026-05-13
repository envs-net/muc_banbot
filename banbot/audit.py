"""Structured event logging, SQLite audit log helpers, and database status stats."""

import json
import logging
import pathlib
import time
from datetime import datetime, timezone

from config import DB_FILE

from .utils import resolve_page, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)


class AuditMixin:
    def log_event(self, level: int, event: str, **fields) -> None:
        """Emit a structured JSON event log when enabled."""
        if not self.structured_event_logs:
            log.log(level, "%s: %s", event, fields)
            return
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "event": event, **fields}
        try:
            log.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:
            log.log(level, "%s: %s", event, fields)


    async def audit_event(self, event_type: str, actor: str | None = None, room: str | None = None,
                          target_type: str | None = None, target: str | None = None,
                          jid: str | None = None, nick: str | None = None, until: int | None = None,
                          comment: str | None = None, details: dict | None = None) -> None:
        """Persist an audit event in SQLite. Audit failures must never break moderation."""
        if not self.audit_log_enabled or not self.db:
            return
        try:
            await self.db.execute(
                """
                INSERT INTO audit_log
                    (event_type, actor, room, target_type, target, jid, nick, until, comment, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_type, actor, room, target_type, target, jid, nick, until, comment,
                 json.dumps(details or {}, ensure_ascii=False, sort_keys=True, default=str)),
            )
            await self.db.commit()
        except Exception as e:
            log.warning("Failed to write audit event %s: %s", event_type, e)


    async def cleanup_old_audit_logs(self) -> int:
        """Delete audit log entries older than AUDIT_LOG_RETENTION_DAYS."""
        if not self.db or not self.audit_log_enabled:
            return 0
        cutoff = int(time.time()) - (self.audit_log_retention_days * 86400)
        try:
            cur = await self.db.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
            await self.db.commit()
            deleted = cur.rowcount or 0
            self.last_audit_cleanup_count = deleted
            self.last_audit_cleanup_run = time.time()
            if deleted:
                self.log_event(logging.INFO, "audit_cleanup", deleted=deleted, retention_days=self.audit_log_retention_days)
            return deleted
        except Exception as e:
            log.warning("Audit cleanup failed: %s", e)
            return 0


    async def get_db_stats(self) -> dict[str, object]:
        """Return lightweight DB statistics for !status."""
        now = int(time.time())
        stats = {"permanent_bans": 0, "temporary_bans": 0, "expired_ban_rows": 0, "audit_events": 0, "db_size_bytes": 0}
        try:
            async with self.db.execute(
                """
                SELECT
                    SUM(CASE WHEN until <= 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN until > ? THEN 1 ELSE 0 END),
                    SUM(CASE WHEN until > 0 AND until <= ? THEN 1 ELSE 0 END)
                FROM bans
                """,
                (now, now),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    stats["permanent_bans"] = int(row[0] or 0)
                    stats["temporary_bans"] = int(row[1] or 0)
                    stats["expired_ban_rows"] = int(row[2] or 0)
            async with self.db.execute("SELECT COUNT(*) FROM audit_log") as cursor:
                row = await cursor.fetchone()
                if row:
                    stats["audit_events"] = int(row[0] or 0)
            db_path = pathlib.Path(DB_FILE)
            if db_path.exists():
                stats["db_size_bytes"] = db_path.stat().st_size
        except Exception as e:
            log.debug("Could not collect DB stats: %s", e)
        return stats


    def _format_audit_row(self, row) -> str:
        created_at, event_type, actor, target_type, target, jid, nick, until, comment, details = row
        ts = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")
        display_target = target or jid or nick or "-"
        actor_display = actor or "system"
        until_text = f", until {datetime.fromtimestamp(until).strftime('%Y-%m-%d %H:%M')}" if until and until > 0 else ""
        comment_text = f", {comment}" if comment else ""
        return f"{ts} · {event_type} · {actor_display} · {target_type or '-'}:{display_target}{until_text}{comment_text}"


    async def cmd_audit(self, args: list[str], room: str) -> None:
        """Show recent audit events. Usage: !audit [all] [page|last|query]."""
        show_all = wants_all_pages(args)
        args = without_all_pages_arg(args)
        page = 1
        query = None
        if args:
            if args[0].lower() == "last":
                page = -1
            else:
                try:
                    page = max(1, int(args[0]))
                except ValueError:
                    query = " ".join(args).strip().lower()

        params: list[object] = []
        where = ""
        if query:
            like = f"%{query}%"
            where = """
                WHERE LOWER(event_type) LIKE ?
                   OR LOWER(COALESCE(actor, '')) LIKE ?
                   OR LOWER(COALESCE(target, '')) LIKE ?
                   OR LOWER(COALESCE(jid, '')) LIKE ?
                   OR LOWER(COALESCE(nick, '')) LIKE ?
                   OR LOWER(COALESCE(comment, '')) LIKE ?
                   OR LOWER(COALESCE(details, '')) LIKE ?
            """
            params = [like] * 7

        async with self.db.execute(f"SELECT COUNT(*) FROM audit_log {where}", params) as cursor:
            row = await cursor.fetchone()
            total = int(row[0] or 0) if row else 0

        if show_all:
            total_pages = 1
            async with self.db.execute(
                f"""
                SELECT created_at, event_type, actor, target_type, target, jid, nick, until, comment, details
                FROM audit_log
                {where}
                ORDER BY created_at DESC, id DESC
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            page = resolve_page(page, total, per_page=10)
            total_pages = max(1, (total + 9) // 10)
            page = max(1, min(page, total_pages))
            offset = (page - 1) * 10

            async with self.db.execute(
                f"""
                SELECT created_at, event_type, actor, target_type, target, jid, nick, until, comment, details
                FROM audit_log
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT 10 OFFSET ?
                """,
                [*params, offset],
            ) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            text = "🧾 Audit log:\nNo matching events."
        else:
            title = f"🧾 Audit log ({total}) - All" if show_all else f"🧾 Audit log ({total}) - Page {page}/{total_pages}"
            if query:
                title += f" - query: {query}"
            text = title + ":\n" + "\n".join(self._format_audit_row(row) for row in rows)
            if not show_all and page < total_pages and not query:
                text += f"\n\nUse {self.command_prefix}audit {page + 1} for the next page."

        await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
