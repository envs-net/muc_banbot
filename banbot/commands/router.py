"""Top-level command routing for public and admin commands."""

import logging
import time

from ..utils import wants_all_pages, without_all_pages_arg
from .constants import ADMIN_COMMANDS, PUBLIC_COMMANDS
from .context import admin_room
from .registry import ADMIN_COMMAND_HANDLERS

log = logging.getLogger(__name__)


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
        if room == admin_room():
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
        if room == admin_room() or cmd not in PUBLIC_COMMANDS:
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
        if room != admin_room() and cmd in PUBLIC_COMMANDS:
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
            if room == admin_room() and self.is_authorized(msg):
                text = self._admin_help_response(args)
            elif self.user_cmds_allowed(room):
                text = await self._user_help_text()
            else:
                return True

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
            return True

        if cmd in ("banlist", "blacklist") and (room == admin_room() or self.user_cmds_allowed(room)):
            show_all = wants_all_pages(args)
            args = without_all_pages_arg(args)
            if args and args[0].lower() == "rtbl":
                if room != admin_room():
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

        if cmd == "why" and (room == admin_room() or self.user_cmds_allowed(room)):
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}why <nick|jid>",
                    mtype="groupchat",
                )
                return True

            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami" and (room == admin_room() or self.user_cmds_allowed(room)):
            await self._cmd_whoami(room, nick)
            return True

        if cmd == "report" and (room == admin_room() or self.user_cmds_allowed(room)):
            if hasattr(self, "cmd_protection_report"):
                await self.cmd_protection_report(room, nick, args)
            return True

        if cmd in ("rules", "policy"):
            # In the admin room, !policy is handled by the admin command below.
            # In protected rooms, !rules / !policy show the public policy text.
            if room == admin_room():
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

        if room != admin_room():
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

        handler_name = ADMIN_COMMAND_HANDLERS.get(cmd)
        if handler_name:
            await getattr(self, handler_name)(room, nick, args, cmd)
            return True

        log.error("Unhandled admin command routed without handler: %s", cmd)
        raise RuntimeError(
            f"Internal routing error: admin command '{cmd}' recognized but not implemented"
        )
