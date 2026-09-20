"""Static host contract for message redaction."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import aiosqlite


class RedactionMixinHost(Protocol):
    """XMPP, persistence and alert hooks required by message redaction."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    command_prefix: str
    redaction_enabled: bool
    redaction_index_retention_days: int
    redaction_auto_reasons: list[str]
    redaction_retract_concurrency: int
    redaction_iq_timeout_seconds: float
    auto_redact_on_imported_ban_reason: bool
    auto_redact_on_manual_muc_ban: bool
    alert_on_redaction_failure: bool
    _shutdown_in_progress: bool
    _redaction_index_pending_writes: int
    _redaction_index_last_commit: float
    _redaction_index_flush_task: asyncio.Task[None] | None
    _redaction_index_lock: asyncio.Lock
    _redaction_confirmation_waiters: dict[tuple[str, str], set[asyncio.Event]]

    def _require_db(self) -> aiosqlite.Connection: ...

    def register_handler(self, handler: Any, *args: Any, **kwargs: Any) -> Any: ...

    def make_iq_set(self, *args: Any, **kwargs: Any) -> Any: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def send_operational_alert(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        details: dict[str, Any] | None = None,
    ) -> bool: ...

    async def audit_event(
        self,
        event_type: str,
        actor: str | None = None,
        room: str | None = None,
        target_type: str | None = None,
        target: str | None = None,
        jid: str | None = None,
        nick: str | None = None,
        until: int | None = None,
        comment: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...
