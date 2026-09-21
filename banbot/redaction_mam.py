"""MAM query construction and tombstone verification for redaction."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

from envs_xmpp_core.runtime.diagnostics import exception_summary

from .redaction_common import (
    MAM_NS,
    REDACTION_IQ_TIMEOUT_SECONDS,
    REDACTION_MAM_VERIFY_BATCH_SIZE,
    REDACTION_MAM_VERIFY_MAX_MESSAGES,
    REDACTION_MAM_VERIFY_WINDOW_SECONDS,
    XDATA_NS,
    _redaction_exception_summary,
    _RedactionMixinContract,
    _xml_local_name,
    _xml_namespace,
)

log = logging.getLogger(__name__)


class RedactionMamMixin(_RedactionMixinContract):
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
        messages: Any,
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

    def _redaction_plugin(self, name: str) -> Any | None:
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
    ) -> tuple[list[Any], Exception | None]:
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
            log.debug("Could not prepare MAM ID verification for %s: %s", room_jid, exception_summary(exc))
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
    ) -> list[Any]:
        """Retrieve a bounded MAM time window as fallback for non-extended servers."""
        mam = self._redaction_plugin("xep_0313")
        if mam is None or not hasattr(mam, "iterate"):
            return []

        start = datetime.fromtimestamp(start_ts, tz=UTC)
        end = datetime.fromtimestamp(end_ts, tz=UTC)

        async def collect() -> list[Any]:
            messages: list[Any] = []
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
        timestamped: list[tuple[int, str]] = []
        for stanza_id in unresolved:
            created_at = entries[stanza_id]
            if created_at is not None:
                timestamped.append((created_at, stanza_id))
        timestamped.sort()
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
