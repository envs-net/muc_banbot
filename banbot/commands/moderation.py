"""Moderation and ban query admin command dispatch."""

import time

from ..locks import ban_state_lock
from ..utils import parse_duration, wants_all_pages, without_all_pages_arg


class CommandModerationMixin:
    async def _dispatch_ban_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}ban <jid|nick|*.domain.tld> [comment]",
                mtype="groupchat",
            )
            return

        actor_jid = self._actor_jid_from_room_nick(room, nick)
        comment = " ".join(args[1:]) if len(args) > 1 else None
        await self.ban_all(args[0], None, actor_jid, comment, notify_policy=False)

    async def _dispatch_tempban_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if len(args) < 2:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}tempban <jid|nick> <10m|2h|1d> [comment]",
                mtype="groupchat",
            )
            return

        try:
            until = int(time.time()) + parse_duration(args[1])
        except (TypeError, ValueError):
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Invalid duration format ({self.command_prefix}tempban user 10m)",
                mtype="groupchat",
            )
            return

        actor_jid = self._actor_jid_from_room_nick(room, nick)
        comment = " ".join(args[2:]) if len(args) > 2 else None
        await self.ban_all(args[0], until, actor_jid, comment, notify_policy=False)

    async def _dispatch_unban_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}unban <jid|nick|*.domain.tld>",
                mtype="groupchat",
            )
            return

        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.unban_all(args[0], actor_jid, notify_policy=False)

    async def _dispatch_bansearch_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}bansearch <query> [all|page|last]",
                mtype="groupchat",
            )
            return

        # Last arg is page number or "last", rest is query.  A standalone
        # "all" disables pagination and may appear before or after query text.
        show_all = wants_all_pages(args)
        args = without_all_pages_arg(args)
        page = 1
        query_args = args
        if args and args[-1].lower() == "last":
            page = -1
            query_args = args[:-1]
        elif args:
            try:
                page = max(1, int(args[-1]))
                query_args = args[:-1]
            except ValueError:
                pass  # No page number — use all args as query
        if not query_args:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}bansearch <query> [all|page|last]",
                mtype="groupchat",
            )
            return
        query = " ".join(query_args)
        await self.cmd_bansearch(query, page=page, show_all=show_all)

    async def _dispatch_redact_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_redact(args, room, actor=actor_jid)

    async def _dispatch_sync_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        async with ban_state_lock(self):
            await self.sync_rooms_and_bans()

    async def _dispatch_syncadmins_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        await self.sync_admins(announce=True)

    async def _dispatch_syncbans_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        async with ban_state_lock(self):
            await self.sync_bans()

    async def _dispatch_audit_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        await self.cmd_audit(args, room)
