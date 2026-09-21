"""Shared redaction constants, types, contracts, and error helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from envs_xmpp_core.runtime.diagnostics import exception_summary
from envs_xmpp_core.xmpp import iq_error_summary, iq_error_text

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .contracts import RedactionMixinHost

    class _RedactionMixinContract(RedactionMixinHost):
        _redaction_protected_rooms: Callable[..., Any]
        _redaction_auto_reason_matches: Callable[..., Any]
        flush_redaction_index: Callable[..., Any]
        _redaction_fetch_targets_for_jid: Callable[..., Any]
        _redaction_index_stats_for_jid: Callable[..., Any]
        _redaction_verify_mam_tombstones: Callable[..., Any]
        _audit_redaction_event: Callable[..., Any]
        redact_jid_messages: Callable[..., Any]
        redact_single_stanza: Callable[..., Any]
        redact_cleanup: Callable[..., Any]
else:
    class _RedactionMixinContract:
        pass


class RedactionSummary(TypedDict):
    """Counters produced by a bulk redaction operation."""

    found: int
    redacted: int
    unconfirmed: int
    failed: int
    skipped: int
    failure_reasons: dict[str, int]
    verified_via_mam: int
    indexed_total: NotRequired[int]
    previously_redacted: NotRequired[int]

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
    if exc.__class__.__name__ == "IqError":
        return iq_error_summary(exc)

    text = str(exc).strip()

    # slixmpp may render a full IQ stanza for some failures. That is noisy in
    # admin-room alerts and logs, so keep the user-facing text compact.
    if text.startswith("<iq ") or "<moderate " in text or "<retract " in text:
        return "server rejected the redaction request"

    return exception_summary(exc, max_length=300)


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
            log.debug("Could not inspect redaction error stanza condition: %s", exception_summary(exc))

        try:
            error = stanza.get("error")
            if isinstance(error, dict) and error.get("condition"):
                return str(error["condition"]).strip().lower()
        except Exception as exc:
            log.debug("Could not inspect redaction error stanza mapping: %s", exception_summary(exc))

    return None


def _redaction_error_is_already_retracted(exc: Exception) -> bool:
    """Return True when a redaction failure means the stanza is already gone."""
    condition = _redaction_exception_condition(exc)
    if condition in _REDACTION_ALREADY_RETRACTED_CONDITIONS:
        return True

    text = (
        iq_error_text(exc).lower()
        if exc.__class__.__name__ == "IqError"
        else str(exc).lower()
    )
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
