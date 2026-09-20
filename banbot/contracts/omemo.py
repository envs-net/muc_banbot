"""Static host contracts for OMEMO lifecycle, devices, reset, and commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from slixmpp import JID

from .common import ActorJidResolverHost


class OmemoCoreMixinHost(Protocol):
    """Slixmpp/runtime capabilities required by the OMEMO core helpers."""

    plugin: Any
    boundjid: Any
    occupants: dict[str, dict[str, dict[str, Any]]]
    omemo_enabled: bool
    omemo_storage_file: str
    omemo_auto_encrypt_admin_room: bool
    omemo_plaintext_fallback: bool
    omemo_reset_on_identity_change: bool
    omemo_reset_pending_restart: bool
    omemo_ready_timeout: int
    omemo_ready: asyncio.Event

    def register_plugin(
        self,
        plugin: str,
        config: dict[str, Any] | None = None,
        module: Any = None,
    ) -> Any: ...

    def add_event_handler(
        self,
        name: str,
        handler: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def make_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str,
        **kwargs: Any,
    ) -> Any: ...



class OmemoDeviceMixinHost(Protocol):
    """Capabilities required by OMEMO device diagnostics."""

    omemo_enabled: bool
    omemo_storage_file: str

    async def _omemo_recipients_for_room(self, room_jid: str) -> set[JID]: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class OmemoResetMixinHost(Protocol):
    """State and command hooks required by OMEMO reset handling."""

    command_prefix: str
    omemo_storage_file: str
    omemo_enabled: bool
    omemo_reset_pending_restart: bool
    omemo_ready: asyncio.Event

    def _schedule_restart_task(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
    ) -> bool: ...

    def _restart_task_pending(self) -> bool: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

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

    async def _cmd_omemo_status(self, room: str) -> None: ...

    async def _cmd_omemo_devices(self, room: str) -> None: ...



class OmemoStatusMixinHost(Protocol):
    """State and output hook required by OMEMO status rendering."""

    omemo_storage_file: str
    omemo_ready: asyncio.Event

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class CommandOmemoMixinHost(ActorJidResolverHost, Protocol):
    """Capabilities required by the top-level ``!omemo`` dispatcher."""

    async def cmd_omemo(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...
