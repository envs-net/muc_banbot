"""Message indexing and persistent redaction index operations."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from envs_xmpp_core.runtime.diagnostics import exception_summary

from .redaction_common import SID_NS, _RedactionMixinContract
from .utils import bare_jid

log = logging.getLogger(__name__)


class RedactionIndexMixin(_RedactionMixinContract):
    _redaction_index_pending_writes: int
    _redaction_index_last_commit: float
    _redaction_index_flush_task: asyncio.Task[None] | None
    _redaction_index_lock: asyncio.Lock

    def _redaction_index_lock_obj(self) -> asyncio.Lock:
        """Return the process-local lock that serializes index writes/flushes."""
        lock = getattr(self, "_redaction_index_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._redaction_index_lock = lock
        return lock

    def _redaction_protected_rooms(self) -> list[str]:
        """Return protected room JIDs in the canonical form used by the index."""
        return sorted(
            {
                normalized
                for room in self.protected_rooms
                if (normalized := str(room).strip().lower())
            }
        )

    def _redaction_auto_reason_matches(self, comment: str | None) -> str | None:
        """Return the matching auto-redaction reason, if any."""
        if not getattr(self, "redaction_enabled", False):
            return None

        comment_text = (comment or "").strip().lower()
        if not comment_text:
            return None

        for reason in getattr(self, "redaction_auto_reasons", []) or []:
            reason_text = str(reason or "").strip().lower()
            if not reason_text:
                continue

            # Match complete words/phrases rather than arbitrary substrings.
            # This prevents short reasons such as "troll", "cp" or "spam"
            # from matching unrelated text like "trollish", "script" or
            # "spammy" while still allowing phrases inside a longer comment.
            pattern = rf"(?<!\w){re.escape(reason_text)}(?!\w)"
            if re.search(pattern, comment_text, flags=re.IGNORECASE):
                return reason_text

        return None


    def _redaction_extract_stanza_id(self, msg) -> str | None:
        """Extract the room-assigned XEP-0359 stanza-id from a message."""
        room = getattr(msg.get("from"), "bare", None) if hasattr(msg, "get") else None
        xml = getattr(msg, "xml", None)
        if xml is None:
            return None

        stanza_ids = list(xml.findall(f".//{{{SID_NS}}}stanza-id"))
        if not stanza_ids:
            return None

        # Prefer the stanza-id assigned by the MUC itself.
        for element in stanza_ids:
            if room and element.attrib.get("by", "").lower() == str(room).lower():
                return element.attrib.get("id") or None

        return stanza_ids[0].attrib.get("id") or None


    def _redaction_extract_sender_jid(self, msg: Any, room: str, nick: str) -> str | None:
        """Best-effort extraction of the real sender bare JID for a MUC message."""
        room_key = room.casefold()
        room_occupants = self.occupants.get(room, {})
        if not room_occupants:
            for candidate_room, candidate_occupants in self.occupants.items():
                if str(candidate_room).casefold() == room_key:
                    room_occupants = candidate_occupants
                    break

        occupant_info = room_occupants.get(nick)
        if occupant_info is None:
            nick_key = nick.casefold()
            occupant_info = next(
                (info for candidate, info in room_occupants.items() if str(candidate).casefold() == nick_key),
                None,
            )
        if occupant_info and occupant_info.get("jid"):
            occupant_jid = bare_jid(occupant_info.get("jid"))
            if occupant_jid:
                return occupant_jid

        # Some slixmpp MUC message stanzas expose real JID via the muc plugin.
        # If the cache contains a malformed real JID, still try the stanza
        # instead of treating the unusable cache value as authoritative.
        try:
            muc_jid = msg["muc"].get("jid")
            if muc_jid:
                return bare_jid(str(muc_jid))
        except Exception as exc:
            log.debug("Redaction: MUC plugin JID lookup failed: %s", exception_summary(exc))

        return None


    async def _redaction_index_message(self, msg: Any) -> bool:
        """Index a MUC message for possible later redaction."""
        if not self.redaction_enabled or getattr(self, "_shutdown_in_progress", False):
            return False

        db = self.db
        if db is None:
            return False

        try:
            room = str(msg["from"].bare or "").strip().lower()
            nick = str(msg.get("mucnick", "") or "")
        except Exception:
            return False

        if not room:
            return False
        if room not in self._redaction_protected_rooms():
            return False

        stanza_id = self._redaction_extract_stanza_id(msg)
        if not stanza_id:
            return False

        sender_jid = self._redaction_extract_sender_jid(msg, room, nick)
        if not sender_jid:
            return False

        try:
            message_id = msg.get("id") or None
        except Exception:
            message_id = None

        # Serialize the write with explicit/shutdown flushes. The second
        # shutdown check closes the race where a stanza passed the first check
        # just before shutdown began and then waited for an in-flight flush.
        async with self._redaction_index_lock_obj():
            if getattr(self, "_shutdown_in_progress", False) or self.db is not db:
                return False
            active_db = self._require_db()
            cursor = await active_db.execute(
                """
                INSERT OR IGNORE INTO redaction_index
                    (room_jid, sender_jid, sender_nick, stanza_id, message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (room, sender_jid, nick or None, stanza_id, message_id, int(time.time())),
            )

            if cursor.rowcount:
                await self._redaction_maybe_commit_index_locked()

        return True


    def _redaction_schedule_index_flush(self, delay: float) -> None:
        """Guarantee that a partially filled redaction batch is committed.

        SQLite starts an implicit write transaction on the first index insert.
        Waiting only for the *next* MUC message to decide whether the batch is
        old enough can therefore leave that write transaction open forever in
        a quiet room.  BanBot's durable outbox uses a second connection to the
        same database, so an abandoned batch also keeps the outbox writer
        locked out.
        """
        task = getattr(self, "_redaction_index_flush_task", None)
        if task is not None and not task.done():
            return

        async def _flush_after_delay() -> None:
            try:
                await asyncio.sleep(max(0.001, float(delay)))
                if getattr(self, "_redaction_index_pending_writes", 0) > 0:
                    await self._redaction_maybe_commit_index(
                        force=True,
                        _from_scheduled_flush=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "Redaction: delayed index commit failed: %s",
                    exception_summary(exc),
                )
            finally:
                current = asyncio.current_task()
                if getattr(self, "_redaction_index_flush_task", None) is current:
                    self._redaction_index_flush_task = None

        flush_task = asyncio.create_task(
            _flush_after_delay(),
            name="redaction-index-flush",
        )
        self._redaction_index_flush_task = flush_task


    async def _redaction_cancel_index_flush(self) -> None:
        """Cancel an outstanding delayed commit without leaking its task."""
        task = getattr(self, "_redaction_index_flush_task", None)
        if task is None or task.done() or task is asyncio.current_task():
            return
        self._redaction_index_flush_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


    async def _redaction_maybe_commit_index_locked(
        self,
        force: bool = False,
        *,
        _from_scheduled_flush: bool = False,
    ) -> None:
        """Update batch state and commit while the redaction index lock is held."""
        db = self.db
        if db is None:
            return

        now = time.monotonic()
        pending = self._redaction_index_pending_writes
        last_commit = self._redaction_index_last_commit

        if not force:
            pending += 1
            self._redaction_index_pending_writes = pending

        commit_every = int(getattr(self, "redaction_index_commit_every", 50) or 50)
        commit_interval = float(getattr(self, "redaction_index_commit_interval", 2.0) or 2.0)
        should_commit = (
            force
            or pending >= commit_every
            or last_commit <= 0
            or now - last_commit >= commit_interval
        )

        if should_commit:
            await db.commit()
            self._redaction_index_pending_writes = 0
            self._redaction_index_last_commit = time.monotonic()
            return

        remaining = commit_interval - max(0.0, now - last_commit)
        self._redaction_schedule_index_flush(remaining)


    async def _redaction_maybe_commit_index(
        self,
        force: bool = False,
        *,
        _from_scheduled_flush: bool = False,
    ) -> None:
        """Batch redaction-index commits with a bounded transaction lifetime."""
        async with self._redaction_index_lock_obj():
            await self._redaction_maybe_commit_index_locked(
                force=force,
                _from_scheduled_flush=_from_scheduled_flush,
            )


    async def flush_redaction_index(self) -> None:
        """Flush pending redaction-index writes and stop a delayed flush task."""
        await self._redaction_cancel_index_flush()
        async with self._redaction_index_lock_obj():
            if self._redaction_index_pending_writes > 0:
                await self._redaction_maybe_commit_index_locked(force=True)


    async def _redaction_fetch_targets_for_jid(
        self,
        jid: str,
    ) -> list[tuple[int, str, str, int]]:
        """Return non-redacted indexed message rows for a bare JID in protected rooms."""
        protected_rooms = self._redaction_protected_rooms()
        if not protected_rooms:
            return []

        placeholders = ",".join("?" for _ in protected_rooms)
        query = f"""
            SELECT id, room_jid, stanza_id, created_at
            FROM redaction_index
            WHERE sender_jid = ?
              AND redacted_at IS NULL
              AND room_jid IN ({placeholders})
            ORDER BY created_at ASC, id ASC
        """
        db = self._require_db()
        async with db.execute(query, [jid, *protected_rooms]) as cursor:
            rows = await cursor.fetchall()
        return [(int(row[0]), str(row[1]), str(row[2]), int(row[3])) for row in rows]


    async def _redaction_index_stats_for_jid(self, jid: str) -> dict[str, int]:
        """Return redaction-index counters for a bare JID in protected rooms."""
        protected_rooms = self._redaction_protected_rooms()
        if not protected_rooms:
            return {"indexed_total": 0, "previously_redacted": 0}

        placeholders = ",".join("?" for _ in protected_rooms)
        query = f"""
            SELECT
                COUNT(*) AS indexed_total,
                SUM(CASE WHEN redacted_at IS NOT NULL THEN 1 ELSE 0 END) AS previously_redacted
            FROM redaction_index
            WHERE sender_jid = ?
              AND room_jid IN ({placeholders})
        """
        db = self._require_db()
        async with db.execute(query, [jid, *protected_rooms]) as cursor:
            row = await cursor.fetchone()

        if not row:
            return {"indexed_total": 0, "previously_redacted": 0}

        return {
            "indexed_total": int(row[0] or 0),
            "previously_redacted": int(row[1] or 0),
        }


    async def _redaction_mark_row(
        self,
        row_id: int,
        actor: str | None,
        reason: str | None,
    ) -> None:
        db = self._require_db()
        await db.execute(
            """
            UPDATE redaction_index
            SET redacted_at = ?, redacted_by = ?, redact_reason = ?
            WHERE id = ?
            """,
            (int(time.time()), actor, reason, row_id),
        )
