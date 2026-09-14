"""Static host contracts for BanBot's cooperative mixins.

BanBot is assembled from many small mixins.  At runtime those mixins rely on
attributes and methods supplied by sibling mixins or :class:`slixmpp.ClientXMPP`.
These Protocols document those cross-mixin dependencies without changing the
runtime MRO.  Individual mixins inherit them only while type checking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from .cache import BanTuple


class DatabaseMixinHost(Protocol):
    """Capabilities supplied to :class:`DatabaseMixin` by the composed bot."""

    db: aiosqlite.Connection | None
    protected_rooms: set[str]
    ban_cache: dict[str, BanTuple]
    ban_index_by_jid: dict[str, BanTuple]
    ban_index_by_nick: dict[str, BanTuple]
    ban_index_by_domain: dict[str, list[BanTuple]]
    bare_jid: Callable[[object | None], str | None]

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...


class ModerationMixinHost(Protocol):
    """Capabilities supplied to :class:`ModerationMixin` by sibling mixins."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    muc_write_semaphore: asyncio.Semaphore
    ban_cache: dict[str, BanTuple]
    allow_user_cmds: bool
    show_ban_in_muc: bool
    max_tempban_days: int
    unban_check_interval: float
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def is_bot_admin_or_owner(self, room: str) -> bool: ...

    async def maybe_auto_redact_after_ban(
        self,
        jid: str,
        comment: str | None,
        *,
        actor: str | None = None,
    ) -> Any: ...

    async def find_active_jid_ban_by_nick(
        self,
        nick: str | None,
    ) -> tuple[str, int, str | None, str | None] | None: ...

    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]: ...

    def is_ignored_target(
        self,
        target: str | None,
        *,
        include_domain_for_jid: bool = False,
    ) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def load_bans_from_db(self) -> None: ...

    async def cleanup_old_audit_logs(self) -> int: ...

    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None: ...

    async def rtbl_retract_ban(
        self,
        jid: str | None,
        domain: str | None,
    ) -> None: ...

    async def notify_protected(self, room: str, message: str) -> None: ...

    def log_event(self, level: int, event: str, **fields: Any) -> None: ...

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

    def _remove_domain_bans_from_cache(self, domain: str) -> None: ...

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...
