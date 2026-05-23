"""Message redaction index and XEP-0425 moderation helpers."""

from __future__ import annotations

import logging
import time
from xml.etree import ElementTree as ET

from config import ADMIN_ROOM

from .utils import bare_jid, safe_jid, validate_jid_format

log = logging.getLogger(__name__)

MODERATE_NS = "urn:xmpp:message-moderate:1"
RETRACT_NS = "urn:xmpp:message-retract:1"
SID_NS = "urn:xmpp:sid:0"


class RedactionMixin:
    def _redaction_auto_reason_matches(self, comment: str | None) -> str | None:
        """Return the matching auto-redaction reason, if any."""
        if not getattr(self, "redaction_enabled", False):
            return None

        comment_text = (comment or "").strip().lower()
        if not comment_text:
            return None

        for reason in getattr(self, "redaction_auto_reasons", []) or []:
            reason_text = str(reason or "").strip().lower()
            if reason_text and reason_text in comment_text:
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


    def _redaction_extract_sender_jid(self, msg, room: str, nick: str) -> str | None:
        """Best-effort extraction of the real sender bare JID for a MUC message."""
        room_occupants = getattr(self, "occupants", {}).get(room, {})
        occupant_info = room_occupants.get(nick) or room_occupants.get(nick.lower())
        if occupant_info and occupant_info.get("jid"):
            return bare_jid(occupant_info.get("jid"))

        # Some slixmpp MUC message stanzas expose real JID via the muc plugin.
        try:
            muc_jid = msg["muc"].get("jid")
            if muc_jid:
                return bare_jid(str(muc_jid))
        except Exception:
            pass

        return None


    async def _redaction_index_message(self, msg) -> bool:
        """Index a MUC message for possible later redaction."""
        if not getattr(self, "redaction_enabled", False):
            return False
        if not getattr(self, "db", None):
            return False

        try:
            room = msg["from"].bare
            nick = str(msg.get("mucnick", "") or "")
        except Exception:
            return False

        if room not in getattr(self, "protected_rooms", set()):
            return False

        stanza_id = self._redaction_extract_stanza_id(msg)
        if not stanza_id:
            return False

        sender_jid = self._redaction_extract_sender_jid(msg, room, nick)
        if not sender_jid:
            return False

        message_id = None
        try:
            message_id = msg.get("id") or None
        except Exception:
            message_id = None

        await self.db.execute(
            """
            INSERT OR IGNORE INTO redaction_index
                (room_jid, sender_jid, sender_nick, stanza_id, message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room, sender_jid, nick or None, stanza_id, message_id, int(time.time())),
        )
        await self.db.commit()
        return True


    async def _redaction_fetch_targets_for_jid(self, jid: str) -> list[tuple[int, str, str]]:
        """Return non-redacted indexed message rows for a bare JID in protected rooms."""
        protected_rooms = sorted(getattr(self, "protected_rooms", set()))
        if not protected_rooms:
            return []

        placeholders = ",".join("?" for _ in protected_rooms)
        query = f"""
            SELECT id, room_jid, stanza_id
            FROM redaction_index
            WHERE sender_jid = ?
              AND redacted_at IS NULL
              AND room_jid IN ({placeholders})
            ORDER BY created_at ASC, id ASC
        """
        async with self.db.execute(query, [bare_jid(jid), *protected_rooms]) as cursor:
            return await cursor.fetchall()


    async def _redaction_mark_row(
        self,
        row_id: int,
        actor: str | None,
        reason: str | None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE redaction_index
            SET redacted_at = ?, redacted_by = ?, redact_reason = ?
            WHERE id = ?
            """,
            (int(time.time()), actor, reason, row_id),
        )


    async def _redaction_send_retract(self, room_jid: str, stanza_id: str, reason: str | None) -> None:
        """Send a XEP-0425 moderated message retraction IQ for one stanza-id."""
        moderate = ET.Element(f"{{{MODERATE_NS}}}moderate", {"id": stanza_id})
        ET.SubElement(moderate, f"{{{RETRACT_NS}}}retract")
        if reason:
            reason_el = ET.SubElement(moderate, f"{{{MODERATE_NS}}}reason")
            reason_el.text = reason

        iq = self.make_iq_set(ito=room_jid)
        iq.append(moderate)
        await iq.send(timeout=10)


    async def _redaction_redact_rows(
        self,
        rows: list[tuple[int, str, str]],
        reason: str | None,
        actor: str | None,
    ) -> dict[str, int]:
        """Retract all rows and return summary counts."""
        summary = {"found": len(rows), "redacted": 0, "failed": 0, "skipped": 0}
        for row_id, room_jid, stanza_id in rows:
            try:
                await self._redaction_send_retract(room_jid, stanza_id, reason)
            except Exception as exc:
                summary["failed"] += 1
                log.warning(
                    "Redaction failed for stanza %s in %s: %s",
                    stanza_id,
                    room_jid,
                    exc,
                )
                continue

            await self._redaction_mark_row(row_id, actor, reason)
            summary["redacted"] += 1

        await self.db.commit()
        return summary


    async def _audit_redaction_event(
        self,
        event_type: str,
        actor: str | None = None,
        room: str | None = None,
        target_type: str | None = None,
        target: str | None = None,
        jid: str | None = None,
        comment: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Write a redaction audit event when audit logging is available."""
        if not hasattr(self, "audit_event"):
            return

        await self.audit_event(
            event_type,
            actor=actor,
            room=room,
            target_type=target_type,
            target=target,
            jid=jid,
            comment=comment,
            details=details or {},
        )


    def _redaction_summary_text(
        self,
        title: str,
        target: str,
        reason: str | None,
        summary: dict[str, int],
    ) -> str:
        """Format a redaction summary for the admin room."""
        if summary.get("found", 0) == 0:
            return (
                f"ℹ️ {title}\n\n"
                f"Target: {safe_jid(target)}\n"
                "No indexed stanza IDs found for this JID.\n"
                "Only messages seen by BanBot after redaction indexing was enabled can be redacted."
            )

        return (
            f"🧹 {title}\n\n"
            f"Target: {safe_jid(target)}\n"
            f"Reason: {reason or 'not specified'}\n"
            f"Messages found: {summary.get('found', 0)}\n"
            f"Redacted: {summary.get('redacted', 0)}\n"
            f"Failed: {summary.get('failed', 0)}\n"
            f"Skipped: {summary.get('skipped', 0)}"
        )


    async def redact_jid_messages(
        self,
        jid: str,
        reason: str | None = None,
        actor: str | None = None,
        announce: bool = True,
        title: str = "Redaction completed",
    ) -> dict[str, int]:
        """Redact all indexed messages for a bare JID in protected rooms."""
        target = bare_jid(jid)
        rows = await self._redaction_fetch_targets_for_jid(target)
        summary = await self._redaction_redact_rows(rows, reason, actor)

        await self._audit_redaction_event(
            "auto_redact_jid" if title.startswith("Auto-redaction") else "redact_jid",
            actor=actor,
            target_type="jid",
            target=target,
            jid=target,
            comment=reason,
            details={
                "found": summary.get("found", 0),
                "redacted": summary.get("redacted", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "announce": announce,
            },
        )

        if announce:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=self._redaction_summary_text(title, target, reason, summary),
                mtype="groupchat",
            )

        return summary


    async def redact_single_stanza(
        self,
        room_jid: str,
        stanza_id: str,
        reason: str | None = None,
        actor: str | None = None,
    ) -> dict[str, int]:
        """Redact exactly one stanza-id in one room."""
        summary = {"found": 1, "redacted": 0, "failed": 0, "skipped": 0}
        try:
            await self._redaction_send_retract(room_jid, stanza_id, reason)
        except Exception as exc:
            summary["failed"] = 1
            log.warning("Redaction failed for stanza %s in %s: %s", stanza_id, room_jid, exc)
        else:
            await self.db.execute(
                """
                UPDATE redaction_index
                SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                WHERE room_jid = ? AND stanza_id = ? AND redacted_at IS NULL
                """,
                (int(time.time()), actor, reason, room_jid, stanza_id),
            )
            await self.db.commit()
            summary["redacted"] = 1

        await self._audit_redaction_event(
            "redact_stanza",
            actor=actor,
            room=room_jid,
            target_type="stanza_id",
            target=stanza_id,
            comment=reason,
            details={
                "room_jid": room_jid,
                "stanza_id": stanza_id,
                "redacted": summary.get("redacted", 0),
                "failed": summary.get("failed", 0),
            },
        )

        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                "🧹 Redaction completed\n\n"
                f"Room: {room_jid}\n"
                f"Stanza ID: {stanza_id}\n"
                f"Reason: {reason or 'not specified'}\n"
                f"Redacted: {summary['redacted']}\n"
                f"Failed: {summary['failed']}"
            ),
            mtype="groupchat",
        )
        return summary


    async def redact_cleanup(self, room: str, actor: str | None = None) -> None:
        """Delete old redaction index rows according to configured retention."""
        if not getattr(self, "redaction_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Redaction is disabled.\n"
                    "Set REDACTION_ENABLED=True and run !reloadconfig to use it."
                ),
                mtype="groupchat",
            )
            return

        days = int(getattr(self, "redaction_index_retention_days", 30) or 0)
        if days <= 0:
            await self._audit_redaction_event(
                "redact_cleanup",
                actor=actor,
                target_type="redaction_index",
                target="cleanup",
                comment="retention disabled; keep forever",
                details={"retention_days": days, "deleted": 0},
            )
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "🧹 Redaction index cleanup completed\n\n"
                    "Retention: keep forever\n"
                    "Deleted entries: 0"
                ),
                mtype="groupchat",
            )
            return

        cutoff = int(time.time()) - days * 86400
        cur = await self.db.execute(
            "DELETE FROM redaction_index WHERE created_at < ?",
            (cutoff,),
        )
        deleted = cur.rowcount or 0
        await self.db.commit()

        await self._audit_redaction_event(
            "redact_cleanup",
            actor=actor,
            target_type="redaction_index",
            target="cleanup",
            comment=f"retention {days} days",
            details={"retention_days": days, "deleted": deleted},
        )

        await self.bot_send_message(
            mto=room,
            mbody=(
                "🧹 Redaction index cleanup completed\n\n"
                f"Retention: {days} days\n"
                f"Deleted entries: {deleted}"
            ),
            mtype="groupchat",
        )


    async def cmd_redact(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !redact admin command."""
        if not getattr(self, "redaction_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Redaction is disabled.\n"
                    "Set REDACTION_ENABLED=True and run !reloadconfig to use it."
                ),
                mtype="groupchat",
            )
            return

        if not args:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "Usage:\n"
                    f"  {self.command_prefix}redact <jid> [reason]\n"
                    f"  {self.command_prefix}redact id <room_jid> <stanza_id> [reason]\n"
                    f"  {self.command_prefix}redact cleanup"
                ),
                mtype="groupchat",
            )
            return

        subcmd = args[0].lower()
        if subcmd == "cleanup":
            await self.redact_cleanup(room, actor=actor)
            return

        if subcmd == "id":
            if len(args) < 3:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}redact id <room_jid> <stanza_id> [reason]",
                    mtype="groupchat",
                )
                return
            room_jid = args[1].strip().lower()
            stanza_id = args[2].strip()
            reason = " ".join(args[3:]).strip() or None
            if room_jid not in getattr(self, "protected_rooms", set()):
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Refusing redaction: {room_jid} is not a protected room.",
                    mtype="groupchat",
                )
                return
            await self.redact_single_stanza(room_jid, stanza_id, reason, actor)
            return

        target = bare_jid(args[0])
        if not target or not validate_jid_format(target):
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}redact <jid> [reason]",
                mtype="groupchat",
            )
            return

        reason = " ".join(args[1:]).strip() or None
        await self.redact_jid_messages(target, reason=reason, actor=actor, announce=True)


    async def maybe_auto_redact_after_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run auto-redaction after a ban if the ban reason is configured."""
        if not jid or jid.startswith("*."):
            return
        match = self._redaction_auto_reason_matches(comment)
        if not match:
            return

        await self.redact_jid_messages(
            jid,
            reason=comment or match,
            actor=actor,
            announce=True,
            title="Auto-redaction completed after ban",
        )
