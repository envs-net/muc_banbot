"""Static host contracts for moderation, database, sync, and audit mixins."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from ..cache import BanTuple
from .common import ActorJidResolverHost


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

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

    async def maybe_auto_redact_after_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None: ...

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



class CommandModerationMixinHost(ActorJidResolverHost, Protocol):
    """Capabilities required by the moderation command dispatcher."""

    db: aiosqlite.Connection | None
    command_prefix: str
    protected_rooms: set[str]
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

    async def ban_all(
        self,
        identifier: str,
        until: int | None,
        issuer: str,
        comment: str | None = None,
        *,
        auto_redact: bool = True,
        notify_policy: bool = True,
    ) -> None: ...

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    async def cmd_bansearch(
        self,
        query: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_baninfo(self, identifier: str, room: str) -> None: ...

    async def cmd_history(
        self,
        identifier: str,
        room: str,
        args: list[str] | None = None,
    ) -> None: ...

    async def _find_ban_record(
        self,
        identifier: str,
    ) -> tuple[
        int,
        str,
        str,
        str | None,
        str | None,
        int,
        str | None,
        str | None,
        int,
        int,
    ] | None: ...

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

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
        log_success: bool = True,
    ) -> None: ...

    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None: ...

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

    async def cmd_redact(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...

    async def sync_rooms_and_bans(self) -> None: ...

    async def sync_admins(self, announce: bool = False) -> bool: ...

    async def sync_bans(self) -> None: ...

    async def cmd_audit(self, args: list[str], room: str) -> None: ...



class SyncMixinHost(Protocol):
    """Capabilities supplied to :class:`SyncMixin` by the composed bot."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    room_join_time: dict[str, float]
    bot_admin_state: dict[str, bool]
    bare_jid: Callable[[object | None], str | None]
    safe_jid: Callable[[object], str]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
        log_success: bool = True,
    ) -> None: ...

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def maybe_auto_redact_after_manual_muc_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None: ...



class AuditMixinHost(Protocol):
    """Database/messaging state required by audit persistence and commands."""

    db: aiosqlite.Connection | None
    structured_event_logs: bool
    audit_log_enabled: bool
    audit_log_retention_days: int
    last_audit_cleanup_count: int
    last_audit_cleanup_run: float
    command_prefix: str

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...
