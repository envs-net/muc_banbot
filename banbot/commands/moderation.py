"""Moderation and ban query admin command dispatch."""

import time

from ..locks import ban_state_lock
from ..utils import parse_duration, validate_jid_format, wants_all_pages, without_all_pages_arg


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


    async def _dispatch_baninfo_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if not args:
            await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}baninfo <jid|nick|*.domain.tld>", mtype="groupchat")
            return
        await self.cmd_baninfo(args[0], room)

    async def _dispatch_history_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if not args:
            await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}history <jid|nick|*.domain.tld> [all|page|last]", mtype="groupchat")
            return
        await self.cmd_history(args[0], room, args[1:])

    async def _dispatch_banedit_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        usage = (
            f"Usage:\n"
            f"  {self.command_prefix}banedit <target> reason <text>\n"
            f"  {self.command_prefix}banedit <target> duration <10m|2h|1d>\n"
            f"  {self.command_prefix}banedit <target> extend <10m|2h|1d>\n"
            f"  {self.command_prefix}banedit <target> reduce <10m|2h|1d>\n"
            f"  {self.command_prefix}banedit <target> permanent\n"
            f"  {self.command_prefix}banedit <target> temp <10m|2h|1d>\n"
            f"  {self.command_prefix}banedit <nick> jid <user@domain.tld>"
        )
        if len(args) < 2:
            await self.bot_send_message(mto=room, mbody=f"❌ {usage}", mtype="groupchat")
            return
        target, operation = args[0], args[1].lower()
        row = await self._find_ban_record(target)
        if not row:
            await self.bot_send_message(mto=room, mbody=f"❌ No ban found for {target}", mtype="groupchat")
            return
        actor = self._actor_jid_from_room_nick(room, nick)
        until = int(row[5] or 0)
        comment = row[7]
        if operation == "jid":
            if len(args) != 3:
                await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}banedit <nick> jid <user@domain.tld>", mtype="groupchat")
                return
            if row[1] != "nick":
                await self.bot_send_message(mto=room, mbody="❌ Only a nick ban can be converted to a JID ban.", mtype="groupchat")
                return
            new_jid = self.bare_jid(args[2].strip().lower())
            if not validate_jid_format(new_jid):
                await self.bot_send_message(mto=room, mbody=f"❌ Invalid JID format: {args[2]}", mtype="groupchat")
                return
            protected, reason = await self.is_protected_admin_target(new_jid, nick=row[4], jid=new_jid)
            if protected:
                await self.bot_send_message(mto=room, mbody=f"❌ Refusing conversion: {reason}", mtype="groupchat")
                return
            if self.is_ignored_target(new_jid):
                await self.bot_send_message(mto=room, mbody=f"⛔ Refusing conversion: {new_jid} is on the ignorelist.", mtype="groupchat")
                return
            async with self.db.execute("SELECT 1 FROM bans WHERE target_type = 'jid' AND target = ?", (new_jid,)) as cursor:
                if await cursor.fetchone():
                    await self.bot_send_message(mto=room, mbody=f"❌ A JID ban already exists for {new_jid}", mtype="groupchat")
                    return
            old_target = row[2]
            async with ban_state_lock(self):
                await self.db.execute(
                    "UPDATE bans SET target_type = 'jid', target = ?, jid = ?, updated_at = strftime('%s','now') WHERE id = ?",
                    (new_jid, new_jid, row[0]),
                )
                await self.db.commit()
                self._remove_ban_from_cache(old_target, ban_nick=row[4])
                self._cache_ban(new_jid, row[4], until, row[6], comment)
                for protected_room in self.protected_rooms:
                    await self.apply_ban_to_room(protected_room, new_jid, row[4], comment, issuer=actor)
                if until <= 0:
                    await self.rtbl_publish_ban(jid=new_jid, domain=None, comment=comment)
            details = {"old_target_type": "nick", "old_target": old_target, "new_target_type": "jid", "new_target": new_jid}
            self.log_event(20, "ban_target_converted", actor=actor, target_type="jid", target=new_jid, jid=new_jid, nick=row[4], until=until, comment=comment, details=details)
            await self.audit_event("ban_target_converted", actor=actor, target_type="jid", target=new_jid, jid=new_jid, nick=row[4], until=until, comment=comment, details=details)
            kind = "tempban" if until > 0 else "permanent ban"
            await self.bot_send_message(mto=room, mbody=f"🔄 Converted nick ban {old_target} to JID ban {new_jid} ({kind}) by {actor}", mtype="groupchat")
            return
        if operation == "reason":
            if len(args) < 3 or not " ".join(args[2:]).strip():
                await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}banedit <target> reason <text>", mtype="groupchat")
                return
            await self.ban_all(target, until if until > 0 else None, actor, " ".join(args[2:]).strip(), notify_policy=False)
            return
        if operation == "permanent":
            await self.ban_all(target, None, actor, comment, notify_policy=False)
            return
        if operation in {"duration", "temp", "extend", "reduce"}:
            if len(args) < 3:
                await self.bot_send_message(mto=room, mbody=f"❌ {usage}", mtype="groupchat")
                return
            try:
                seconds = parse_duration(args[2])
            except (TypeError, ValueError):
                await self.bot_send_message(mto=room, mbody="❌ Invalid duration format", mtype="groupchat")
                return
            now = int(time.time())
            if operation in {"duration", "temp"}:
                new_until = now + seconds
            elif operation == "extend":
                new_until = max(now, until) + seconds
            else:
                if until <= 0:
                    await self.bot_send_message(mto=room, mbody="❌ A permanent ban cannot be reduced. Convert it to a tempban first.", mtype="groupchat")
                    return
                new_until = until - seconds
                if new_until <= now:
                    await self.bot_send_message(mto=room, mbody="❌ Reduction would expire the ban immediately. Use unban instead.", mtype="groupchat")
                    return
            await self.ban_all(target, new_until, actor, comment, notify_policy=False)
            return
        await self.bot_send_message(mto=room, mbody=f"❌ Unknown banedit operation: {operation}\n{usage}", mtype="groupchat")

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
