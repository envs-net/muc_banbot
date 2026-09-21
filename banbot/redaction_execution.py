"""Redaction request execution, auditing, and summaries."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from xml.etree import ElementTree as ET

from config import ADMIN_ROOM

from .redaction_common import (
    MODERATE_NS,
    REDACTION_IQ_TIMEOUT_SECONDS,
    RETRACT_NS,
    RedactionSummary,
    _redaction_error_is_already_retracted,
    _redaction_error_is_unconfirmed,
    _redaction_exception_summary,
    _RedactionMixinContract,
)
from .utils import bare_jid, safe_jid, validate_jid_format

log = logging.getLogger(__name__)


class RedactionExecutionMixin(_RedactionMixinContract):
    async def _redaction_send_retract(
        self,
        room_jid: str,
        stanza_id: str,
        reason: str | None,
    ) -> None:
        """Send a retraction IQ and accept the live moderation broadcast as confirmation."""
        moderate = ET.Element(f"{{{MODERATE_NS}}}moderate", {"id": stanza_id})
        ET.SubElement(moderate, f"{{{RETRACT_NS}}}retract")
        if reason:
            reason_el = ET.SubElement(moderate, f"{{{MODERATE_NS}}}reason")
            reason_el.text = reason

        iq = self.make_iq_set(ito=room_jid)
        iq.append(moderate)
        timeout = float(
            getattr(self, "redaction_iq_timeout_seconds", REDACTION_IQ_TIMEOUT_SECONDS)
            or REDACTION_IQ_TIMEOUT_SECONDS
        )
        timeout = max(1.0, min(timeout, 30.0))

        key = (str(room_jid).lower(), stanza_id)
        waiters = getattr(self, "_redaction_confirmation_waiters", None)
        if waiters is None:
            waiters = {}
            self._redaction_confirmation_waiters = waiters
        confirmation = asyncio.Event()
        waiters.setdefault(key, set()).add(confirmation)

        confirmation_task = asyncio.create_task(confirmation.wait())
        send_future = None
        try:
            # Slixmpp's Iq.send() returns an asyncio Future, while lightweight
            # test doubles and older integrations may return a coroutine.
            # ensure_future() accepts both and lets the cleanup path consume
            # later IQ errors instead of leaking "Future exception was never
            # retrieved" warnings.
            send_future = asyncio.ensure_future(iq.send(timeout=timeout))
            done, _pending = await asyncio.wait(
                {send_future, confirmation_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if confirmation_task in done and confirmation.is_set():
                log.debug(
                    "Redaction confirmed by moderation broadcast for stanza %s in %s",
                    stanza_id,
                    room_jid,
                )
                return

            try:
                await send_future
            except Exception as send_exc:
                try:
                    await asyncio.wait_for(confirmation.wait(), timeout=min(2.0, timeout))
                except TimeoutError:
                    raise send_exc from None
                log.info(
                    "Redaction IQ did not complete cleanly, but the moderation broadcast "
                    "confirmed stanza %s in %s",
                    stanza_id,
                    room_jid,
                )
        finally:
            registered = waiters.get(key)
            if registered is not None:
                registered.discard(confirmation)
                if not registered:
                    waiters.pop(key, None)

            pending = [confirmation_task]
            if send_future is not None:
                pending.append(send_future)
            for future in pending:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


    async def _redaction_redact_rows(
        self,
        rows: list[tuple[int, str, str, int]],
        reason: str | None,
        actor: str | None,
        alert_on_failure: bool = True,
    ) -> RedactionSummary:
        """Retract all rows and return summary counts."""
        summary: RedactionSummary = {
            "found": len(rows),
            "redacted": 0,
            "unconfirmed": 0,
            "failed": 0,
            "skipped": 0,
            "failure_reasons": {},
            "verified_via_mam": 0,
        }
        if not rows:
            return summary

        concurrency = int(getattr(self, "redaction_retract_concurrency", 10) or 10)
        concurrency = max(1, min(concurrency, 20))
        semaphore = asyncio.Semaphore(concurrency)
        changed_rows: list[int] = []
        skipped_rows: list[int] = []

        async def redact_one(
            row: tuple[int, str, str, int],
        ) -> tuple[str, int | str | None]:
            row_id, room_jid, stanza_id, _created_at = row
            async with semaphore:
                try:
                    await self._redaction_send_retract(room_jid, stanza_id, reason)
                except Exception as exc:
                    if _redaction_error_is_already_retracted(exc):
                        log.info(
                            "Redaction skipped for stanza %s in %s: already retracted",
                            stanza_id,
                            room_jid,
                        )
                        return "skipped", row_id

                    if _redaction_error_is_unconfirmed(exc):
                        return "unconfirmed", row_id

                    error_summary = _redaction_exception_summary(exc)
                    log.warning(
                        "Redaction failed for stanza %s in %s: %s",
                        stanza_id,
                        room_jid,
                        error_summary,
                    )
                    log.debug(
                        "Raw redaction failure for stanza %s in %s",
                        stanza_id,
                        room_jid,
                        exc_info=exc,
                    )
                    if alert_on_failure and hasattr(self, "send_operational_alert"):
                        await self.send_operational_alert(
                            f"redaction_failed:{room_jid}",
                            "Redaction failed",
                            f"Failed to redact stanza {stanza_id} in {room_jid}: {error_summary}",
                            enabled=getattr(self, "alert_on_redaction_failure", True),
                            details={"room": room_jid, "stanza_id": stanza_id, "error": error_summary},
                        )
                    return "failed", error_summary

                return "redacted", row_id

        results = await asyncio.gather(*(redact_one(row) for row in rows))

        failure_reasons = summary["failure_reasons"]

        unconfirmed_rows: list[tuple[int, str, str, int]] = []
        for row, (status, row_value) in zip(rows, results, strict=True):
            if status == "redacted" and isinstance(row_value, int):
                summary["redacted"] += 1
                changed_rows.append(row_value)
            elif status == "skipped" and isinstance(row_value, int):
                summary["skipped"] += 1
                skipped_rows.append(row_value)
            elif status == "unconfirmed":
                unconfirmed_rows.append(row)
            else:
                summary["failed"] += 1
                if isinstance(row_value, str):
                    failure_reasons[row_value] = failure_reasons.get(row_value, 0) + 1

        mam_confirmed = await self._redaction_verify_mam_tombstones(
            [
                (room_jid, stanza_id, created_at)
                for _row_id, room_jid, stanza_id, created_at in unconfirmed_rows
            ]
        )
        unresolved_by_room: dict[str, int] = {}
        for row_id, room_jid, stanza_id, _created_at in unconfirmed_rows:
            key = (str(room_jid).lower(), stanza_id)
            if key in mam_confirmed:
                summary["redacted"] += 1
                summary["verified_via_mam"] += 1
                changed_rows.append(row_id)
                continue

            summary["unconfirmed"] += 1
            unresolved_by_room[room_jid] = unresolved_by_room.get(room_jid, 0) + 1

        for room_jid, count in sorted(unresolved_by_room.items()):
            log.warning(
                "Redaction confirmation remained unavailable for %d stanza(s) in %s; "
                "the server may still have applied them",
                count,
                room_jid,
            )
        if mam_confirmed:
            log.info(
                "Verified %d applied redaction(s) through MAM tombstones",
                len(mam_confirmed),
            )

        db = self._require_db()
        rows_to_mark = changed_rows + skipped_rows
        if rows_to_mark:
            now = int(time.time())
            await db.executemany(
                """
                UPDATE redaction_index
                SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                WHERE id = ?
                """,
                [(now, actor, reason, row_id) for row_id in rows_to_mark],
            )

        await db.commit()
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
        details: dict[str, Any] | None = None,
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
        summary: RedactionSummary,
    ) -> str:
        """Format a redaction summary for the admin room."""
        if summary.get("found", 0) == 0:
            indexed_total = summary.get("indexed_total", 0)
            previously_redacted = summary.get("previously_redacted", 0)

            lines = [
                f"ℹ️ {title}",
                "",
                f"Target: {safe_jid(target)}",
            ]

            if indexed_total > 0:
                lines.append("No redactable indexed stanza IDs found for this JID.")
                if previously_redacted > 0:
                    lines.append(f"Previously redacted messages: {previously_redacted}")
                lines.extend(
                    [
                        "",
                        "Only messages seen by BanBot after redaction indexing was enabled",
                        "and not already redacted can be redacted.",
                    ]
                )
            else:
                lines.extend(
                    [
                        "No indexed stanza IDs found for this JID.",
                        "Only messages seen by BanBot after redaction indexing was enabled can be redacted.",
                    ]
                )

            return "\n".join(lines)

        lines = [
            f"🧹 {title}",
            "",
            f"Target: {safe_jid(target)}",
            f"Reason: {reason or 'not specified'}",
            f"Messages found: {summary.get('found', 0)}",
            f"Redacted: {summary.get('redacted', 0)}",
        ]
        verified_via_mam = summary.get("verified_via_mam", 0)
        if verified_via_mam:
            lines.append(f"Verified via MAM: {verified_via_mam}")
        lines.extend(
            [
                f"Unconfirmed: {summary.get('unconfirmed', 0)}",
                f"Failed: {summary.get('failed', 0)}",
                f"Skipped: {summary.get('skipped', 0)}",
            ]
        )

        found = summary.get("found", 0)
        redacted = summary.get("redacted", 0)
        unconfirmed = summary.get("unconfirmed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        failure_reasons = summary.get("failure_reasons", {})
        if unconfirmed > 0:
            lines.extend(
                [
                    "",
                    "Note: These requests were sent, but BanBot received no IQ result,",
                    "matching live moderation confirmation, or verifiable MAM tombstone.",
                    "They are not counted as failed because the server may still have",
                    "applied the retractions.",
                ]
            )

        if (
            found > 0
            and failed == found
            and redacted == 0
            and unconfirmed == 0
            and skipped == 0
            and isinstance(failure_reasons, dict)
            and failure_reasons
        ):
            if set(failure_reasons) == {"server rejected the redaction request"}:
                lines.extend(
                    [
                        "",
                        "Note: The server rejected all redaction requests.",
                        "This usually means the messages are no longer redactable",
                        "or the bot lacks moderation permissions for those stanza IDs.",
                    ]
                )

        return "\n".join(lines)


    async def redact_jid_messages(
        self,
        jid: str,
        reason: str | None = None,
        actor: str | None = None,
        announce: bool = True,
        title: str = "Redaction completed",
    ) -> RedactionSummary:
        """Redact all indexed messages for a bare JID in protected rooms."""
        target = bare_jid(jid)
        if not target or not validate_jid_format(target):
            log.warning("Refusing redaction for invalid JID: %r", jid)
            return {
                "found": 0,
                "redacted": 0,
                "unconfirmed": 0,
                "failed": 0,
                "skipped": 0,
                "failure_reasons": {},
                "verified_via_mam": 0,
                "indexed_total": 0,
                "previously_redacted": 0,
            }
        await self.flush_redaction_index()
        rows = await self._redaction_fetch_targets_for_jid(target)
        summary = await self._redaction_redact_rows(
            rows,
            reason,
            actor,
            alert_on_failure=not title.startswith("Auto-redaction"),
        )
        if summary.get("found", 0) == 0:
            stats = await self._redaction_index_stats_for_jid(target)
            summary["indexed_total"] = stats["indexed_total"]
            summary["previously_redacted"] = stats["previously_redacted"]

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
                "verified_via_mam": summary.get("verified_via_mam", 0),
                "unconfirmed": summary.get("unconfirmed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "indexed_total": summary.get("indexed_total", 0),
                "previously_redacted": summary.get("previously_redacted", 0),
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
        db = self._require_db()
        summary = {
            "found": 1,
            "redacted": 0,
            "unconfirmed": 0,
            "failed": 0,
            "skipped": 0,
            "verified_via_mam": 0,
        }
        try:
            await self._redaction_send_retract(room_jid, stanza_id, reason)
        except Exception as exc:
            if _redaction_error_is_already_retracted(exc):
                summary["skipped"] = 1
                log.info(
                    "Redaction skipped for stanza %s in %s: already retracted",
                    stanza_id,
                    room_jid,
                )
                await db.execute(
                    """
                    UPDATE redaction_index
                    SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                    WHERE room_jid = ? AND stanza_id = ? AND redacted_at IS NULL
                    """,
                    (int(time.time()), actor, reason, room_jid, stanza_id),
                )
                await db.commit()
            elif _redaction_error_is_unconfirmed(exc):
                mam_confirmed = await self._redaction_verify_mam_tombstones(
                    [(room_jid, stanza_id)]
                )
                if (str(room_jid).lower(), stanza_id) in mam_confirmed:
                    await db.execute(
                        """
                        UPDATE redaction_index
                        SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                        WHERE room_jid = ? AND stanza_id = ? AND redacted_at IS NULL
                        """,
                        (int(time.time()), actor, reason, room_jid, stanza_id),
                    )
                    await db.commit()
                    summary["redacted"] = 1
                    summary["verified_via_mam"] = 1
                    log.info(
                        "Verified applied redaction through MAM for stanza %s in %s",
                        stanza_id,
                        room_jid,
                    )
                else:
                    summary["unconfirmed"] = 1
                    log.warning(
                        "Redaction confirmation remained unavailable for stanza %s in %s; "
                        "the server may still have applied it",
                        stanza_id,
                        room_jid,
                    )
            else:
                summary["failed"] = 1
                error_summary = _redaction_exception_summary(exc)
                log.warning("Redaction failed for stanza %s in %s: %s", stanza_id, room_jid, error_summary)
                log.debug(
                    "Raw redaction failure for stanza %s in %s",
                    stanza_id,
                    room_jid,
                    exc_info=exc,
                )
                if hasattr(self, "send_operational_alert"):
                    await self.send_operational_alert(
                        f"redaction_failed:{room_jid}",
                        "Redaction failed",
                        f"Failed to redact stanza {stanza_id} in {room_jid}: {error_summary}",
                        enabled=getattr(self, "alert_on_redaction_failure", True),
                        details={"room": room_jid, "stanza_id": stanza_id, "error": error_summary},
                    )
        else:
            await db.execute(
                """
                UPDATE redaction_index
                SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                WHERE room_jid = ? AND stanza_id = ? AND redacted_at IS NULL
                """,
                (int(time.time()), actor, reason, room_jid, stanza_id),
            )
            await db.commit()
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
                "verified_via_mam": summary.get("verified_via_mam", 0),
                "unconfirmed": summary.get("unconfirmed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
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
                + (
                    f"Verified via MAM: {summary['verified_via_mam']}\n"
                    if summary.get("verified_via_mam")
                    else ""
                )
                + f"Unconfirmed: {summary['unconfirmed']}\n"
                f"Failed: {summary['failed']}\n"
                f"Skipped: {summary['skipped']}"
            ),
            mtype="groupchat",
        )
        return summary
