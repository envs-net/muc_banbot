"""Message redaction index and XEP-0425 moderation helpers."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from config import ADMIN_ROOM

from .utils import bare_jid, safe_jid, validate_jid_format
from .task_supervisor import sleep_with_heartbeat

log = logging.getLogger(__name__)

MODERATE_NS = "urn:xmpp:message-moderate:1"
RETRACT_NS = "urn:xmpp:message-retract:1"
FASTEN_NS = "urn:xmpp:fasten:0"
LEGACY_MODERATE_NS = "urn:xmpp:message-moderate:0"
LEGACY_RETRACT_NS = "urn:xmpp:message-retract:0"
SID_NS = "urn:xmpp:sid:0"
MAM_NS = "urn:xmpp:mam:2"
XDATA_NS = "jabber:x:data"
REDACTION_IQ_TIMEOUT_SECONDS = 5
REDACTION_MAM_VERIFY_BATCH_SIZE = 20
REDACTION_MAM_VERIFY_WINDOW_SECONDS = 5
REDACTION_MAM_VERIFY_MAX_MESSAGES = 2000
REDACTION_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

_REDACTION_ALREADY_RETRACTED_CONDITIONS = {
    "item-not-found",
    "gone",
}

_REDACTION_ALREADY_RETRACTED_TEXT = (
    "already redacted",
    "already retracted",
    "item-not-found",
    "message not found",
    "stanza not found",
    "not found",
)


def _redaction_exception_summary(exc: Exception) -> str:
    """Return a compact, admin-safe redaction error summary."""
    if (
        isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        or exc.__class__.__name__ == "IqTimeout"
    ):
        return "redaction request timed out"

    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__

    # slixmpp may render a full IQ stanza for some failures. That is noisy in
    # admin-room alerts and logs, so keep the user-facing text compact.
    if text.startswith("<iq ") or "<moderate " in text or "<retract " in text:
        return "server rejected the redaction request"

    if len(text) > 300:
        return f"{text[:297]}..."
    return text


def _redaction_exception_condition(exc: Exception) -> str | None:
    """Best-effort extraction of an XMPP error condition from an exception."""
    for attr in ("condition", "error_condition"):
        value = getattr(exc, attr, None)
        if value:
            return str(value).strip().lower()

    for attr in ("iq", "stanza", "response"):
        stanza = getattr(exc, attr, None)
        if stanza is None:
            continue

        try:
            error = stanza["error"]
            condition = error["condition"] if hasattr(error, "__getitem__") else None
            if condition:
                return str(condition).strip().lower()
        except Exception as exc:
            log.debug("Could not inspect redaction error stanza condition: %s", exc)

        try:
            error = stanza.get("error")
            if isinstance(error, dict) and error.get("condition"):
                return str(error["condition"]).strip().lower()
        except Exception as exc:
            log.debug("Could not inspect redaction error stanza mapping: %s", exc)

    return None


def _redaction_error_is_already_retracted(exc: Exception) -> bool:
    """Return True when a redaction failure means the stanza is already gone."""
    condition = _redaction_exception_condition(exc)
    if condition in _REDACTION_ALREADY_RETRACTED_CONDITIONS:
        return True

    text = str(exc).lower()
    return any(token in text for token in _REDACTION_ALREADY_RETRACTED_TEXT)


def _redaction_error_is_unconfirmed(exc: Exception) -> bool:
    """Return True when no definitive server confirmation arrived."""
    return (
        isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        or exc.__class__.__name__ == "IqTimeout"
    )


def _xml_local_name(tag: object) -> str:
    """Return an XML element local name without depending on one namespace."""
    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _xml_namespace(tag: object) -> str:
    """Return an XML element namespace or an empty string."""
    text = str(tag or "")
    if text.startswith("{") and "}" in text:
        return text[1:].split("}", 1)[0]
    return ""


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
        except Exception as exc:
            log.debug("Redaction: MUC plugin JID lookup failed: %s", exc)

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

        cursor = await self.db.execute(
            """
            INSERT OR IGNORE INTO redaction_index
                (room_jid, sender_jid, sender_nick, stanza_id, message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room, sender_jid, nick or None, stanza_id, message_id, int(time.time())),
        )

        if cursor.rowcount:
            await self._redaction_maybe_commit_index()

        return True


    async def _redaction_maybe_commit_index(self, force: bool = False) -> None:
        """Batch redaction-index commits to avoid one SQLite commit per message."""
        if not getattr(self, "db", None):
            return

        now = time.monotonic()
        pending = getattr(self, "_redaction_index_pending_writes", 0)
        last_commit = getattr(self, "_redaction_index_last_commit", 0.0)

        if not force:
            pending += 1
            self._redaction_index_pending_writes = pending

        commit_every = int(getattr(self, "redaction_index_commit_every", 50) or 50)
        commit_interval = float(getattr(self, "redaction_index_commit_interval", 2.0) or 2.0)

        if (
            force
            or pending >= commit_every
            or last_commit <= 0
            or now - last_commit >= commit_interval
        ):
            await self.db.commit()
            self._redaction_index_pending_writes = 0
            self._redaction_index_last_commit = now


    async def flush_redaction_index(self) -> None:
        """Flush pending redaction-index writes."""
        if getattr(self, "_redaction_index_pending_writes", 0) > 0:
            await self._redaction_maybe_commit_index(force=True)


    async def _redaction_fetch_targets_for_jid(
        self,
        jid: str,
    ) -> list[tuple[int, str, str, int]]:
        """Return non-redacted indexed message rows for a bare JID in protected rooms."""
        protected_rooms = sorted(getattr(self, "protected_rooms", set()))
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
        async with self.db.execute(query, [bare_jid(jid), *protected_rooms]) as cursor:
            return await cursor.fetchall()


    async def _redaction_index_stats_for_jid(self, jid: str) -> dict[str, int]:
        """Return redaction-index counters for a bare JID in protected rooms."""
        protected_rooms = sorted(getattr(self, "protected_rooms", set()))
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
        async with self.db.execute(query, [bare_jid(jid), *protected_rooms]) as cursor:
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
        await self.db.execute(
            """
            UPDATE redaction_index
            SET redacted_at = ?, redacted_by = ?, redact_reason = ?
            WHERE id = ?
            """,
            (int(time.time()), actor, reason, row_id),
        )


    @staticmethod
    def _redaction_confirmation_ids(msg) -> set[str]:
        """Extract target stanza IDs from XEP-0425 moderation announcements.

        Prosody supports both XEP-0425 v0.2.1 and v0.3.0 and deployed
        module versions may emit mixed namespace layouts. Match the protocol
        structure by local element names while still requiring a moderation
        marker and a retraction marker beneath the element carrying the ID.
        """
        xml = getattr(msg, "xml", None)
        if xml is None:
            return set()

        stanza_ids: set[str] = set()
        for element in xml.iter():
            local_name = _xml_local_name(element.tag)
            stanza_id = element.attrib.get("id")
            if not stanza_id or local_name not in {
                "apply-to",
                "moderate",
                "retract",
                "retracted",
            }:
                continue

            descendant_names = {
                _xml_local_name(descendant.tag)
                for descendant in element.iter()
                if descendant is not element
            }
            has_moderation = "moderated" in descendant_names or local_name == "moderate"
            has_retraction = (
                local_name in {"retract", "retracted"}
                or "retract" in descendant_names
                or "retracted" in descendant_names
            )
            if has_moderation and has_retraction:
                stanza_ids.add(stanza_id)

        return stanza_ids


    def _redaction_confirm_from_message(self, msg) -> int:
        """Set pending confirmation events found in an incoming message stanza."""
        stanza_ids = self._redaction_confirmation_ids(msg)
        if not stanza_ids:
            return 0

        try:
            sender = msg["from"]
            room_jid = str(
                getattr(sender, "bare", None) or bare_jid(str(sender))
            ).lower()
        except Exception as exc:
            log.debug("Could not resolve room for redaction confirmation: %s", exc)
            return 0

        confirmed = 0
        waiters = getattr(self, "_redaction_confirmation_waiters", {})
        for stanza_id in stanza_ids:
            key = (room_jid, stanza_id)
            for event in tuple(waiters.get(key, ())):
                event.set()
                confirmed += 1

        if confirmed:
            log.debug(
                "Matched %d pending redaction confirmation(s) from %s",
                confirmed,
                room_jid,
            )
        return confirmed


    def _redaction_incoming_filter(self, stanza):
        """Inspect every incoming message stanza for moderation confirmation.

        Slixmpp incoming filters run before stream and custom event handlers,
        so this also sees bodyless Prosody XEP-0425 broadcasts that may not
        trigger the normal ``message`` event or a namespace-specific matcher.
        The original stanza is always returned unchanged.
        """
        try:
            xml = getattr(stanza, "xml", None)
            if xml is not None and _xml_local_name(xml.tag) == "message":
                self._redaction_confirm_from_message(stanza)
        except Exception as exc:
            log.debug(
                "Could not inspect incoming stanza for redaction confirmation: %s",
                exc,
            )
        return stanza


    def _handle_redaction_confirmation_stanza(self, msg) -> None:
        """Compatibility callback for older embedding and test integrations."""
        self._redaction_confirm_from_message(msg)


    async def on_redaction_confirmation_message(self, msg) -> None:
        """Compatibility event callback used by tests and embedding users."""
        self._redaction_confirm_from_message(msg)


    @staticmethod
    def _redaction_build_mam_ids_query(
        query_id: str,
        stanza_ids: list[str],
    ) -> ET.Element:
        """Build an XEP-0313 extended query for specific archive IDs."""
        query = ET.Element(f"{{{MAM_NS}}}query", {"queryid": query_id})
        form = ET.SubElement(query, f"{{{XDATA_NS}}}x", {"type": "submit"})

        form_type = ET.SubElement(
            form,
            f"{{{XDATA_NS}}}field",
            {"var": "FORM_TYPE", "type": "hidden"},
        )
        ET.SubElement(form_type, f"{{{XDATA_NS}}}value").text = MAM_NS

        ids_field = ET.SubElement(
            form,
            f"{{{XDATA_NS}}}field",
            {"var": "ids", "type": "list-multi"},
        )
        for stanza_id in stanza_ids:
            ET.SubElement(ids_field, f"{{{XDATA_NS}}}value").text = stanza_id
        return query

    @staticmethod
    def _redaction_mam_tombstone_ids(
        messages,
        requested_ids: set[str],
    ) -> set[str]:
        """Return requested archive IDs whose MAM result contains a tombstone."""
        confirmed: set[str] = set()
        for message in messages or ():
            xml = getattr(message, "xml", None)
            if xml is None:
                continue

            for result in xml.iter():
                if (
                    _xml_local_name(result.tag) != "result"
                    or _xml_namespace(result.tag) != MAM_NS
                ):
                    continue

                candidate_ids = {str(result.attrib.get("id") or "")}
                candidate_ids.update(
                    str(element.attrib.get("id") or "")
                    for element in result.iter()
                    if _xml_local_name(element.tag) == "stanza-id"
                )
                candidate_ids.discard("")
                matching_ids = candidate_ids & requested_ids
                if not matching_ids:
                    continue

                descendant_names = {
                    _xml_local_name(element.tag)
                    for element in result.iter()
                    if element is not result
                }
                if "moderated" in descendant_names and (
                    "retracted" in descendant_names
                    or "retract" in descendant_names
                ):
                    confirmed.update(matching_ids)
        return confirmed

    def _redaction_plugin(self, name: str):
        """Return one registered Slixmpp plugin without assuming mapping type."""
        plugins = getattr(self, "plugin", None)
        if plugins is None:
            return None
        try:
            return plugins[name]
        except (AttributeError, KeyError, TypeError):
            return None

    def _redaction_mam_timeout(self) -> float:
        """Return a bounded timeout for archive verification queries."""
        timeout = float(
            getattr(self, "redaction_iq_timeout_seconds", REDACTION_IQ_TIMEOUT_SECONDS)
            or REDACTION_IQ_TIMEOUT_SECONDS
        )
        return max(2.0, min(timeout * 3.0, 30.0))

    async def _redaction_query_mam_ids(
        self,
        room_jid: str,
        stanza_ids: list[str],
    ) -> tuple[list, Exception | None]:
        """Query one MUC archive for specific IDs using Slixmpp's MAM stanza model."""
        mam = self._redaction_plugin("xep_0313")
        if mam is None or not hasattr(mam, "_pre_mam_retrieve"):
            return [], RuntimeError("XEP-0313 plugin is unavailable")

        try:
            from slixmpp.xmlstream.handler import Collector
            from slixmpp.xmlstream.matcher import MatchXMLMask
        except ImportError as exc:
            return [], exc

        try:
            iq, stanza_mask = mam._pre_mam_retrieve(
                room_jid,
                None,
                None,
                None,
                None,
            )
            iq["mam"]["ids"] = list(stanza_ids)
            query_id = str(iq["id"])
            stanza_mask["mam_result"]["queryid"] = query_id
            collector = Collector(
                f"BanBot_Redaction_MAM_{query_id}",
                MatchXMLMask(str(stanza_mask)),
            )
            self.register_handler(collector)
        except Exception as exc:
            log.debug("Could not prepare MAM ID verification for %s: %s", room_jid, exc)
            return [], exc

        send_error: Exception | None = None
        try:
            await iq.send(timeout=self._redaction_mam_timeout())
        except Exception as exc:
            send_error = exc
        finally:
            messages = list(collector.stop() or ())

        return messages, send_error

    async def _redaction_query_mam_window(
        self,
        room_jid: str,
        start_ts: int,
        end_ts: int,
    ) -> list:
        """Retrieve a bounded MAM time window as fallback for non-extended servers."""
        mam = self._redaction_plugin("xep_0313")
        if mam is None or not hasattr(mam, "iterate"):
            return []

        start = datetime.fromtimestamp(start_ts, tz=UTC)
        end = datetime.fromtimestamp(end_ts, tz=UTC)

        async def collect() -> list:
            messages = []
            async for message in mam.iterate(
                jid=room_jid,
                start=start,
                end=end,
                rsm={"max": 50},
                total=REDACTION_MAM_VERIFY_MAX_MESSAGES,
            ):
                messages.append(message)
            return messages

        try:
            return await asyncio.wait_for(
                collect(),
                timeout=max(self._redaction_mam_timeout(), 10.0),
            )
        except Exception as exc:
            log.debug(
                "MAM time-window verification failed for %s (%s to %s): %s",
                room_jid,
                start.isoformat(),
                end.isoformat(),
                _redaction_exception_summary(exc),
            )
            return []

    async def _redaction_verify_mam_room(
        self,
        room_jid: str,
        entries: dict[str, int | None],
    ) -> set[str]:
        """Verify one room through exact-ID queries and narrow time windows."""
        requested_ids = set(entries)
        if not requested_ids:
            return set()

        confirmed: set[str] = set()
        ordered_ids = list(entries)
        for start in range(0, len(ordered_ids), REDACTION_MAM_VERIFY_BATCH_SIZE):
            batch = ordered_ids[start : start + REDACTION_MAM_VERIFY_BATCH_SIZE]
            messages, error = await self._redaction_query_mam_ids(room_jid, batch)
            confirmed.update(
                self._redaction_mam_tombstone_ids(messages, set(batch))
            )
            if error is not None:
                log.debug(
                    "Exact MAM redaction verification was unavailable for %s "
                    "(%d ID(s)): %s",
                    room_jid,
                    len(batch),
                    _redaction_exception_summary(error),
                )

        unresolved = requested_ids - confirmed
        timestamped = sorted(
            (int(entries[stanza_id]), stanza_id)
            for stanza_id in unresolved
            if entries[stanza_id]
        )
        if not timestamped:
            return confirmed

        # Group nearby indexed messages into small archive windows. The local
        # created_at value is recorded when the live stanza is indexed and is
        # therefore a reliable fallback boundary for normal online operation.
        clusters: list[list[tuple[int, str]]] = []
        for item in timestamped:
            if (
                not clusters
                or item[0] - clusters[-1][-1][0]
                > REDACTION_MAM_VERIFY_WINDOW_SECONDS * 2
            ):
                clusters.append([item])
            else:
                clusters[-1].append(item)

        for cluster in clusters:
            cluster_ids = {stanza_id for _timestamp, stanza_id in cluster}
            start_ts = cluster[0][0] - REDACTION_MAM_VERIFY_WINDOW_SECONDS
            end_ts = cluster[-1][0] + REDACTION_MAM_VERIFY_WINDOW_SECONDS
            messages = await self._redaction_query_mam_window(
                room_jid,
                start_ts,
                end_ts,
            )
            confirmed.update(
                self._redaction_mam_tombstone_ids(messages, cluster_ids)
            )

        return confirmed

    async def _redaction_verify_mam_tombstones(
        self,
        targets: list[tuple[str, str] | tuple[str, str, int | None]],
    ) -> set[tuple[str, str]]:
        """Verify unconfirmed retractions against MUC MAM tombstones."""
        by_room: dict[str, dict[str, int | None]] = {}
        for target in targets:
            room_jid, stanza_id = target[0], target[1]
            created_at = target[2] if len(target) > 2 else None
            room_key = str(room_jid).lower()
            by_room.setdefault(room_key, {}).setdefault(stanza_id, created_at)

        async def verify_room(
            room_jid: str,
            entries: dict[str, int | None],
        ) -> set[tuple[str, str]]:
            ids = await self._redaction_verify_mam_room(room_jid, entries)
            return {(room_jid, stanza_id) for stanza_id in ids}

        if not by_room:
            return set()
        room_results = await asyncio.gather(
            *(verify_room(room, entries) for room, entries in by_room.items())
        )
        confirmed: set[tuple[str, str]] = set()
        for result in room_results:
            confirmed.update(result)
        return confirmed

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
    ) -> dict[str, object]:
        """Retract all rows and return summary counts."""
        summary: dict[str, object] = {
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

        failure_reasons_obj = summary.get("failure_reasons")
        failure_reasons: dict[str, int]
        if isinstance(failure_reasons_obj, dict):
            failure_reasons = failure_reasons_obj
        else:
            failure_reasons = {}
            summary["failure_reasons"] = failure_reasons

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

        rows_to_mark = changed_rows + skipped_rows
        if rows_to_mark:
            now = int(time.time())
            await self.db.executemany(
                """
                UPDATE redaction_index
                SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                WHERE id = ?
                """,
                [(now, actor, reason, row_id) for row_id in rows_to_mark],
            )

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
        summary: dict[str, object],
    ) -> str:
        """Format a redaction summary for the admin room."""
        if summary.get("found", 0) == 0:
            indexed_total = int(summary.get("indexed_total", 0) or 0)
            previously_redacted = int(summary.get("previously_redacted", 0) or 0)

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
        verified_via_mam = int(summary.get("verified_via_mam", 0) or 0)
        if verified_via_mam:
            lines.append(f"Verified via MAM: {verified_via_mam}")
        lines.extend(
            [
                f"Unconfirmed: {summary.get('unconfirmed', 0)}",
                f"Failed: {summary.get('failed', 0)}",
                f"Skipped: {summary.get('skipped', 0)}",
            ]
        )

        found = int(summary.get("found", 0) or 0)
        redacted = int(summary.get("redacted", 0) or 0)
        unconfirmed = int(summary.get("unconfirmed", 0) or 0)
        failed = int(summary.get("failed", 0) or 0)
        skipped = int(summary.get("skipped", 0) or 0)
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
    ) -> dict[str, object]:
        """Redact all indexed messages for a bare JID in protected rooms."""
        target = bare_jid(jid)
        await self.flush_redaction_index()
        rows = await self._redaction_fetch_targets_for_jid(target)
        summary = await self._redaction_redact_rows(
            rows,
            reason,
            actor,
            alert_on_failure=not title.startswith("Auto-redaction"),
        )
        if summary.get("found", 0) == 0:
            summary.update(await self._redaction_index_stats_for_jid(target))

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
                await self.db.execute(
                    """
                    UPDATE redaction_index
                    SET redacted_at = ?, redacted_by = ?, redact_reason = ?
                    WHERE room_jid = ? AND stanza_id = ? AND redacted_at IS NULL
                    """,
                    (int(time.time()), actor, reason, room_jid, stanza_id),
                )
                await self.db.commit()
            elif _redaction_error_is_unconfirmed(exc):
                mam_confirmed = await self._redaction_verify_mam_tombstones(
                    [(room_jid, stanza_id)]
                )
                if (str(room_jid).lower(), stanza_id) in mam_confirmed:
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


    async def _redaction_cleanup_old_entries(
        self,
        actor: str | None = "system",
        *,
        audit: bool = True,
        audit_noop: bool = True,
    ) -> dict[str, int | bool | str]:
        """Delete expired redaction index rows without sending command output.

        ``audit_noop`` controls whether no-op cleanup runs are written to the
        audit log. Manual cleanup keeps no-op audit entries, while automatic
        cleanup only records runs that actually delete rows. This avoids noisy
        recurring audit events when retention is disabled or nothing expired.
        """
        result: dict[str, int | bool | str] = {
            "enabled": bool(getattr(self, "redaction_enabled", False)),
            "retention_days": int(getattr(self, "redaction_index_retention_days", 30) or 0),
            "deleted": 0,
        }

        if not result["enabled"]:
            result["skipped_reason"] = "redaction disabled"
            return result

        days = int(result["retention_days"])
        if days <= 0:
            result["skipped_reason"] = "retention disabled"
            if audit and audit_noop:
                await self._audit_redaction_event(
                    "redact_cleanup",
                    actor=actor,
                    target_type="redaction_index",
                    target="cleanup",
                    comment="retention disabled; keep forever",
                    details={"retention_days": days, "deleted": 0},
                )
            return result

        cutoff = int(time.time()) - days * 86400
        cur = await self.db.execute(
            "DELETE FROM redaction_index WHERE created_at < ?",
            (cutoff,),
        )
        deleted = cur.rowcount or 0
        result["deleted"] = deleted
        await self.db.commit()

        if audit and (deleted > 0 or audit_noop):
            await self._audit_redaction_event(
                "redact_cleanup",
                actor=actor,
                target_type="redaction_index",
                target="cleanup",
                comment=f"retention {days} days",
                details={"retention_days": days, "deleted": deleted},
            )

        return result


    async def run_redaction_cleanup_automatic(self, actor: str | None = "system") -> dict[str, int | bool | str]:
        """Run automatic redaction index cleanup without admin-room noise."""
        try:
            result = await self._redaction_cleanup_old_entries(actor=actor, audit=True, audit_noop=False)
        except Exception as exc:
            log.warning("Automatic redaction index cleanup failed: %s", exc)
            if hasattr(self, "send_operational_alert"):
                await self.send_operational_alert(
                    "redaction_cleanup_failed",
                    "Redaction cleanup failed",
                    f"Automatic redaction index cleanup failed: {exc}",
                    enabled=getattr(self, "alert_on_redaction_failure", True),
                    details={"error": str(exc)},
                )
            return {"enabled": bool(getattr(self, "redaction_enabled", False)), "deleted": 0, "error": str(exc)}

        deleted = int(result.get("deleted", 0) or 0)
        if deleted > 0:
            log.info("Redaction index cleanup removed %s old entr%s", deleted, "y" if deleted == 1 else "ies")
        return result


    async def redaction_cleanup_worker(self) -> None:
        """Run redaction index cleanup every 24 hours."""
        while True:
            await sleep_with_heartbeat(
                self,
                "redaction-cleanup-worker",
                REDACTION_CLEANUP_INTERVAL_SECONDS,
                sleep_func=asyncio.sleep,
            )
            await self.run_redaction_cleanup_automatic(actor="system")


    async def redact_cleanup(self, room: str, actor: str | None = None) -> None:
        """Delete old redaction index rows according to configured retention."""
        if not getattr(self, "redaction_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Redaction is disabled.\n"
                    f"Set REDACTION_ENABLED=True and run {getattr(self, 'command_prefix', '!')}reloadconfig to use it."
                ),
                mtype="groupchat",
            )
            return

        result = await self._redaction_cleanup_old_entries(actor=actor, audit=True)
        days = int(result.get("retention_days", 0) or 0)
        deleted = int(result.get("deleted", 0) or 0)

        if days <= 0:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "🧹 Redaction index cleanup completed\n\n"
                    "Retention: keep forever\n"
                    "Deleted entries: 0\n"
                    "Note: cleanup is disabled when retention is set to keep forever."
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "🧹 Redaction index cleanup completed\n\n"
                f"Retention: {days} days\n"
                f"Deleted entries: {deleted}\n"
                "Note: cleanup only removes entries older than the retention period."
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
                    f"Set REDACTION_ENABLED=True and run {getattr(self, 'command_prefix', '!')}reloadconfig to use it."
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


    async def _maybe_auto_redact_after_reasoned_jid_ban(
        self,
        jid: str | None,
        comment: str | None,
        *,
        actor: str | None = None,
        title: str = "Auto-redaction completed after ban",
    ) -> None:
        """Run auto-redaction for a JID ban when the reason matches config."""
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
            title=title,
        )


    async def maybe_auto_redact_after_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run auto-redaction after a bot-command ban if the reason matches."""
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor,
            title="Auto-redaction completed after ban",
        )


    async def maybe_auto_redact_after_imported_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run configured auto-redaction for imported JID bans."""
        if not getattr(self, "auto_redact_on_imported_ban_reason", False):
            return
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor or "import",
            title="Auto-redaction completed after imported ban",
        )


    async def maybe_auto_redact_after_manual_muc_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run configured auto-redaction for manually discovered MUC bans."""
        if not getattr(self, "auto_redact_on_manual_muc_ban", False):
            return
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor or "sync",
            title="Auto-redaction completed after manual MUC ban",
        )
