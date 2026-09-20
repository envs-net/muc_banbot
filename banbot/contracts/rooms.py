"""Static host contracts for room lifecycle, occupants, invites, and administration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from ..cache import BanTuple


class CommandRoomsMixinHost(Protocol):
    """Capabilities required by the top-level room command dispatcher."""

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _room_usage_text(self) -> str: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...



class BotOccupantMixinHost(Protocol):
    """Runtime identity state used by the canonical bot-occupant lookup."""

    occupants: dict[str, dict[str, dict[str, Any]]]
    room_bot_nicks: dict[str, str]
    boundjid: Any
    bare_jid: Callable[[object | None], str | None]



class ProtectedRoomMixinHost(Protocol):
    """Capabilities required by protected-room persistence and commands."""

    db: aiosqlite.Connection | None
    plugin: Any
    command_prefix: str
    protected_rooms: set[str]
    registered_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    bot_admin_state: dict[str, bool]

    def _require_db(self) -> aiosqlite.Connection: ...

    def _bot_occupant_entry(
        self,
        room: str,
    ) -> tuple[str | None, dict[str, Any] | None]: ...

    def add_event_handler(self, name: str, handler: Callable[..., Any]) -> Any: ...

    async def muc_online(self, presence: Any) -> None: ...

    async def muc_offline(self, presence: Any) -> None: ...

    async def ensure_muc_joined(
        self,
        room: str,
        *,
        nick: str = ...,
        timeout: float | None = None,
        retries: int | None = None,
        force: bool = False,
    ) -> bool: ...

    async def sync_bans_to_rooms_for_single_room(self, room: str) -> Any: ...

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool: ...

    async def cmd_room_invite(self, args: list[str], room: str) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class RoomInviteMixinHost(Protocol):
    """Capabilities required by protected-room invite persistence and commands."""

    db: aiosqlite.Connection | None
    command_prefix: str
    protected_rooms: set[str]
    bare_jid: Callable[[object | None], str | None]

    async def validate_room_jid(self, room_jid: str) -> tuple[bool, str]: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class MucMixinHost(Protocol):
    """Cross-subsystem state and helpers required by the MUC lifecycle mixin."""

    db: aiosqlite.Connection | None
    plugin: Any
    boundjid: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    bot_admin_state: dict[str, bool]
    room_join_time: dict[str, float]
    room_bot_nicks: dict[str, str]
    room_join_events: dict[str, asyncio.Event]
    ban_index_by_jid: dict[str, BanTuple]
    ban_index_by_nick: dict[str, BanTuple]
    ban_index_by_domain: dict[str, list[BanTuple]]
    show_ban_in_muc: bool
    reconnecting: bool
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    def connect(self, *args: Any, **kwargs: Any) -> Any: ...

    def connect_with_config(self) -> bool: ...

    def disconnect(self, *args: Any, **kwargs: Any) -> Any: ...

    def is_admin_or_owner(
        self,
        room: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> bool: ...

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def load_bans_from_db(self) -> None: ...

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

    async def verify_admin_rights(self, room: str) -> bool: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class AdminMixinHost(BotOccupantMixinHost, Protocol):
    """Cross-subsystem state required by admin/owner authorization helpers."""

    plugin: Any
    protected_rooms: set[str]
    admin_affiliation_query_forbidden_rooms: set[str]
    _admin_affiliation_cache_entries: dict[str, tuple[float, frozenset[str]]]
    bot_admin_state: dict[str, bool]

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...
