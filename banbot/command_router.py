"""XMPP command dispatch for public and admin commands."""

import logging
import sys
import time

from config import ADMIN_ROOM

from .locks import ban_state_lock
from .utils import parse_duration, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)

# PUBLIC_COMMANDS used for ratelimits
PUBLIC_COMMANDS = {"help", "whoami", "banlist", "blacklist", "why", "rules", "policy"}

ADMIN_COMMANDS = {
    "config",
    "backup",
    "restore",
    "omemo",
    "reload",
    "reloadconfig",
    "restart",
    "status",
    "checkupdate",
    "updatecheck",
    "room",
    "ban",
    "tempban",
    "unban",
    "bansearch",
    "sync",
    "syncadmins",
    "syncbans",
    "export",
    "import",
    "audit",
    "rtbl",
    "redact",
    "ignore",
    "whitelist",
    "policy",
    "rules",
}


def _admin_room() -> str:
    commands_module = sys.modules.get("banbot.commands")
    return getattr(commands_module, "ADMIN_ROOM", ADMIN_ROOM)


class CommandRouterMixin:
    async def _handle_unknown_command(self, msg, room: str, cmd: str) -> None:
        """
        Inform admins about unknown commands and point to help.

        In protected rooms unknown commands are ignored silently. This avoids
        noisy bot replies for normal chat lines that happen to start with the
        command prefix, e.g. "!?".
        """
        p = self.command_prefix

        # In admin room: only answer admins.
        if room == _admin_room():
            if not self.is_authorized(msg):
                return

            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"❌ Unknown command: {p}{cmd}\n"
                    f"Use {p}help to see available admin commands."
                ),
                mtype="groupchat",
            )
            return

        # In protected rooms, unknown commands are intentionally ignored.
        return

    def check_public_command_rate_limit(self, room: str, nick: str, cmd: str) -> tuple[bool, int]:
        """Rate-limit public commands in protected rooms; admin-room use is never limited."""
        if room == _admin_room() or cmd not in PUBLIC_COMMANDS:
            return True, 0

        window = max(1, int(self.public_command_rate_limit_window))
        limit = max(1, int(self.public_command_rate_limit_max))
        now = time.time()
        key = (room, nick.lower(), cmd)

        hits = [t for t in self.public_command_rate_limit_hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            retry_after = max(1, int(window - (now - hits[0])))
            self.public_command_rate_limit_hits[key] = hits
            return False, retry_after

        hits.append(now)
        self.public_command_rate_limit_hits[key] = hits

        # Opportunistic cleanup so long-running bots do not keep stale users forever.
        if len(self.public_command_rate_limit_hits) > 1000:
            cutoff = now - window
            self.public_command_rate_limit_hits = {
                k: [t for t in v if t >= cutoff]
                for k, v in self.public_command_rate_limit_hits.items()
                if any(t >= cutoff for t in v)
            }

        return True, 0

    async def _handle_user_command(
        self,
        msg,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool:
        if room != _admin_room() and cmd in PUBLIC_COMMANDS:
            allowed, retry_after = self.check_public_command_rate_limit(room, nick, cmd)
            if not allowed:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"⏳ Rate limit: please wait {retry_after}s before using "
                        f"{self.command_prefix}{cmd} again."
                    ),
                    mtype="groupchat",
                )
                return True

        if cmd == "help":
            if room == _admin_room() and self.is_authorized(msg):
                text = self._admin_help_response(args)
            elif self.user_cmds_allowed(room):
                text = await self._user_help_text()
            else:
                return True

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
            return True

        if cmd in ("banlist", "blacklist") and (room == _admin_room() or self.user_cmds_allowed(room)):
            show_all = wants_all_pages(args)
            args = without_all_pages_arg(args)
            if args and args[0].lower() == "rtbl":
                if room != _admin_room():
                    return True
                page = 1
                if len(args) >= 2:
                    if args[1].lower() == "last":
                        page = -1  # sentinel: last page
                    else:
                        try:
                            page = max(1, int(args[1]))
                        except ValueError:
                            await self.bot_send_message(
                                mto=room,
                                mbody=f"❌ Usage: {self.command_prefix}{cmd} rtbl [all|page|last]",
                                mtype="groupchat",
                            )
                            return True
                await self.cmd_banlist_rtbl(room, page=page, show_all=show_all)
                return True

            page = 1
            if len(args) >= 1:
                if args[0].lower() == "last":
                    page = -1  # sentinel: last page
                else:
                    try:
                        page = max(1, int(args[0]))
                    except ValueError:
                        await self.bot_send_message(
                            mto=room,
                            mbody=f"❌ Usage: {self.command_prefix}{cmd} [rtbl] [all|page|last]",
                            mtype="groupchat",
                        )
                        return True
            await self.cmd_banlist(room, page=page, show_all=show_all)
            return True

        if cmd == "why" and (room == _admin_room() or self.user_cmds_allowed(room)):
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}why <nick|jid>",
                    mtype="groupchat",
                )
                return True

            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami" and (room == _admin_room() or self.user_cmds_allowed(room)):
            await self._cmd_whoami(room, nick)
            return True

        if cmd in ("rules", "policy"):
            # In the admin room, !policy is handled by the admin command below.
            # In protected rooms, !rules / !policy show the public policy text.
            if room == _admin_room():
                return False

            if self.user_cmds_allowed(room):
                await self._cmd_public_policy_show(room)
                return True

            return False

        return False

    async def _handle_admin_command(
        self,
        msg,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool:
        if cmd not in ADMIN_COMMANDS:
            return False

        if room != _admin_room():
            return True

        if not self.is_authorized(msg):
            await self.bot_send_message(
                mto=room,
                mbody="❌ You are not authorized to use this admin command.",
                mtype="groupchat",
            )
            return True

        if await self._dispatch_runtime_admin_command(room, nick, cmd, args):
            return True

        if cmd == "backup":
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            await self.cmd_backup(args, room, actor=actor_jid)
            return True

        if cmd == "restore":
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            await self.cmd_restore(args, room, actor=actor_jid)
            return True

        if cmd == "omemo":
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            await self.cmd_omemo(args, room, actor=actor_jid)
            return True

        if cmd == "room":
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=self._room_usage_text(),
                    mtype="groupchat",
                )
                return True

            async with ban_state_lock(self):
                await self.cmd_room(args, room)
            return True

        if cmd == "ban":
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}ban <jid|nick|*.domain.tld> [comment]",
                    mtype="groupchat",
                )
                return True

            actor_jid = self._actor_jid_from_room_nick(room, nick)
            comment = " ".join(args[1:]) if len(args) > 1 else None
            async with ban_state_lock(self):
                await self.ban_all(args[0], None, actor_jid, comment)
            return True

        if cmd == "tempban":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}tempban <jid|nick> <10m|2h|1d> [comment]",
                    mtype="groupchat",
                )
                return True

            try:
                until = int(time.time()) + parse_duration(args[1])
            except Exception:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Invalid duration format ({self.command_prefix}tempban user 10m)",
                    mtype="groupchat",
                )
                return True

            actor_jid = self._actor_jid_from_room_nick(room, nick)
            comment = " ".join(args[2:]) if len(args) > 2 else None
            async with ban_state_lock(self):
                await self.ban_all(args[0], until, actor_jid, comment)
            return True

        if cmd == "unban":
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}unban <jid|nick|*.domain.tld>",
                    mtype="groupchat",
                )
                return True

            actor_jid = self._actor_jid_from_room_nick(room, nick)
            async with ban_state_lock(self):
                await self.unban_all(args[0], actor_jid)
            return True

        if cmd == "bansearch":
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}bansearch <query> [all|page|last]",
                    mtype="groupchat",
                )
                return True

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
                return True
            query = " ".join(query_args)
            await self.cmd_bansearch(query, page=page, show_all=show_all)
            return True

        if cmd == "redact":
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            await self.cmd_redact(args, room, actor=actor_jid)
            return True

        if cmd == "sync":
            async with ban_state_lock(self):
                await self.sync_rooms_and_bans()
            return True

        if cmd == "syncadmins":
            await self.sync_admins(announce=True)
            return True

        if cmd == "syncbans":
            async with ban_state_lock(self):
                await self.sync_bans()
            return True

        if cmd == "audit":
            await self.cmd_audit(args, room)
            return True

        if cmd == "export":
            if hasattr(self, "cmd_export"):
                await self.cmd_export(args, room)
            else:
                _success, message = await self.export_bans_to_csv()
                await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
            return True

        if cmd == "import":
            await self._handle_import_command(args, room, nick)
            return True

        if cmd == "rtbl":
            if not getattr(self, "rtbl_enabled", False):
                await self.bot_send_message(
                    mto=room,
                    mbody="❌ RTBL is disabled. Set RTBL_ENABLED = True in config.py and restart.",
                    mtype="groupchat",
                )
                return True
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            async with ban_state_lock(self):
                await self.cmd_rtbl(args, room, actor=actor_jid)
            return True

        if cmd in ("ignore", "whitelist"):
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            async with ban_state_lock(self):
                await self.cmd_ignore(args, room, actor=actor_jid, command_name=cmd)
            return True

        if cmd in ("policy", "rules"):
            await self.cmd_policy(args, room)
            return True

        log.error("Unhandled admin command routed without handler: %s", cmd)
        raise RuntimeError(
            f"Internal routing error: admin command '{cmd}' recognized but not implemented"
        )
