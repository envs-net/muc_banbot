"""Incoming redaction confirmation parsing and waiter handling."""

from __future__ import annotations

import logging
from typing import Any

from envs_xmpp_core.runtime.diagnostics import exception_summary

from .redaction_common import _RedactionMixinContract, _xml_local_name
from .utils import bare_jid

log = logging.getLogger(__name__)


class RedactionConfirmationMixin(_RedactionMixinContract):
    @staticmethod
    def _redaction_confirmation_ids(msg: Any) -> set[str]:
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


    def _redaction_confirm_from_message(self, msg: Any) -> int:
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
            log.debug("Could not resolve room for redaction confirmation: %s", exception_summary(exc))
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


    def _redaction_incoming_filter(self, stanza: Any) -> Any:
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


    def _handle_redaction_confirmation_stanza(self, msg: Any) -> None:
        """Compatibility callback for older embedding and test integrations."""
        self._redaction_confirm_from_message(msg)


    async def on_redaction_confirmation_message(self, msg: Any) -> None:
        """Compatibility event callback used by tests and embedding users."""
        self._redaction_confirm_from_message(msg)
