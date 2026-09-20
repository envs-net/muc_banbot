"""Static host contracts for group and direct messaging."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class MessagingMixinHost(Protocol):
    """Transport hooks required by the centralized outbound messaging layer."""

    omemo_plaintext_fallback: bool

    def send_message(self, **kwargs: Any) -> Any: ...

    async def _send_omemo_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class DirectMessageMixinHost(Protocol):
    """State and command hooks required by the DM/MUC-PM entry point."""

    boundjid: Any
    command_prefix: str
    allow_admin_commands_in_dms: bool
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    version_check_url: str | None
    bare_jid: Callable[[object | None], str | None]

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _set_reply_target_context(self, mto: str, mtype: str) -> Any: ...

    def _reset_reply_target_context(self, token: Any) -> None: ...

    def _set_reply_encryption_context(self, encrypted: bool | None) -> Any: ...

    def _reset_reply_encryption_context(self, token: Any) -> None: ...

    def _admin_help_response(self, args: list[str]) -> str: ...

    async def _cmd_config(
        self,
        room: str,
        args: list[str] | None = None,
        actor: str | None = None,
    ) -> None: ...

    async def _cmd_status(self, room: str, args: list[str] | None = None) -> None: ...

    async def _cmd_tasks(
        self,
        room: str,
        args: list[str],
        *,
        mtype: str = "groupchat",
    ) -> None: ...

    async def check_for_updates_once(
        self,
        announce: bool = True,
    ) -> tuple[bool, str | None, str | None]: ...

    async def cmd_protections_list(self, room: str, args: list[str]) -> None: ...

    async def cmd_omemo(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...

    async def cmd_banlist_rtbl(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_banlist(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...

    async def cmd_ignore(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
        command_name: str = "ignore",
    ) -> None: ...

    async def cmd_rtbl(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
    ) -> None: ...

    async def cmd_audit(self, args: list[str], room: str) -> None: ...

    async def cmd_baninfo(self, identifier: str, room: str) -> None: ...

    async def cmd_history(
        self,
        identifier: str,
        room: str,
        args: list[str] | None = None,
    ) -> None: ...

    async def cmd_why(self, identifier: str, room: str) -> None: ...

    async def cmd_bansearch(
        self,
        query: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...
