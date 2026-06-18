"""Direct-message and MUC-PM policy for admin read-only commands."""

from contextlib import asynccontextmanager
import inspect

from config import ADMIN_ROOM
from ._version import __version__
from .utils import wants_all_pages, without_all_pages_arg


LAST_PAGE_MARKER = -1
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"


class DirectMessageMixin:
    def _direct_message_sender_info(self, msg) -> tuple[bool, str, str]:
        """Return (is_admin, reply_to, sender_bare) for a DM or MUC-PM."""
        sender = msg["from"].bare
        sender_full = str(msg["from"])
        sender_resource = msg["from"].resource

        known_rooms = self.protected_rooms | {ADMIN_ROOM}

        # A real MUC PM looks like: room@conference.example/Nick
        is_muc_pm = sender in known_rooms and sender_resource is not None
        reply_to = sender_full if is_muc_pm else sender
        sender_bare = sender
        is_admin = False

        if is_muc_pm:
            room = sender
            nick = sender_resource
            info = self.occupants.get(room, {}).get(nick)

            if info and info.get("affiliation") in ("owner", "admin"):
                is_admin = True

            # Optional fallback: if the PM came from another known room,
            # also check whether this user's real JID is admin in ADMIN_ROOM.
            real_jid = info.get("jid") if info else None
            real_bare = self.bare_jid(real_jid) if real_jid else None

            if real_bare:
                sender_bare = real_bare

            if not is_admin and real_bare:
                for admin_info in self.occupants.get(ADMIN_ROOM, {}).values():
                    admin_jid = admin_info.get("jid")
                    if (
                        admin_jid
                        and self.bare_jid(admin_jid) == real_bare
                        and admin_info.get("affiliation") in ("owner", "admin")
                    ):
                        is_admin = True
                        break

        else:
            # Regular direct DM: user@example/resource or user@example
            sender_bare = self.bare_jid(str(msg["from"]))

            for admin_info in self.occupants.get(ADMIN_ROOM, {}).values():
                admin_jid = admin_info.get("jid")
                if (
                    admin_jid
                    and self.bare_jid(admin_jid) == sender_bare
                    and admin_info.get("affiliation") in ("owner", "admin")
                ):
                    is_admin = True
                    break

        return is_admin, reply_to, sender_bare


    async def _send_direct_message(self, reply_to: str, body: str) -> None:
        """Send a plain chat response to a direct-message requester."""
        await self.bot_send_message(mto=reply_to, mbody=body, mtype="chat")


    @asynccontextmanager
    async def _redirect_command_output_to_dm(self, reply_to: str):
        """Temporarily redirect existing command output helpers to a DM reply."""
        original_send = self.bot_send_message

        async def send_to_dm(**kwargs):
            kwargs["mto"] = reply_to
            kwargs["mtype"] = "chat"
            await original_send(**kwargs)

        self.bot_send_message = send_to_dm
        try:
            yield
        finally:
            self.bot_send_message = original_send


    @staticmethod
    def _is_page_or_list_arg(arg: str) -> bool:
        """Return True for list-command paging arguments."""
        value = arg.lower()
        if value in {"all", "last", "list"}:
            return True
        return value.isdigit()


    async def _handle_admin_dm_readonly_command(
        self,
        *,
        reply_to: str,
        sender_bare: str,
        cmd: str,
        args: list[str],
    ) -> bool:
        """Handle admin-only read-only commands in direct messages."""
        p = self.command_prefix

        async with self._redirect_command_output_to_dm(reply_to):
            if cmd == "help":
                help_text = self._admin_help_response(args)
                await self._send_direct_message(reply_to, help_text)
                return True

            if cmd == "config":
                config_args = args or []
                if config_args:
                    first = config_args[0].lower()
                    is_readonly = (
                        first in {"show", "list", "search", "find", "diff", "all", "last"}
                        or first.isdigit()
                    )
                else:
                    is_readonly = True

                if not is_readonly:
                    await self._send_direct_message(
                        reply_to,
                        (
                            "❌ Direct-message config commands are read-only. "
                            f"Allowed: {p}config [all|page|last], {p}config show [all|page|last], {p}config search/find <query>, {p}config diff [all|page|last]"
                        ),
                    )
                    return True

                sig = inspect.signature(self._cmd_config)
                if len(sig.parameters) >= 2:
                    await self._cmd_config(reply_to, config_args)
                else:
                    await self._cmd_config(reply_to)
                return True

            if cmd in ("protections", "protection"):
                allowed_args = args or ["list"]
                if allowed_args[0].lower() == "list" or all(
                    self._is_page_or_list_arg(arg) for arg in allowed_args
                ):
                    list_args = (
                        allowed_args
                        if allowed_args[0].lower() == "list"
                        else ["list", *allowed_args]
                    )
                    if any(not self._is_page_or_list_arg(arg) for arg in list_args[1:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}protections list [all|page|last]",
                        )
                        return True
                    await self.cmd_protections_list(reply_to, list_args[1:])
                    return True

                await self._send_direct_message(
                    reply_to,
                    (
                        "❌ Direct-message protection commands are read-only. "
                        f"Allowed: {p}protections list [all|page|last]"
                    ),
                )
                return True

            if cmd == "omemo":
                action = args[0].lower() if args else "status"
                if action in ("status", "devices", "device", "help", "usage"):
                    await self.cmd_omemo(args, reply_to, actor=sender_bare)
                    return True
                await self._send_direct_message(
                    reply_to,
                    f"❌ Direct-message OMEMO commands are read-only. Allowed: {p}omemo status, {p}omemo devices, {p}omemo help",
                )
                return True

            if cmd == "status":
                await self._cmd_status(reply_to)
                return True

            if cmd in ("checkupdate", "updatecheck"):
                is_update, remote_version, error_message = await self.check_for_updates_once(announce=False)

                if error_message:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Update check failed: {error_message}",
                    )
                elif is_update:
                    await self._send_direct_message(
                        reply_to,
                        (
                            f"⬆️ New bot version available: {remote_version} (current: {__version__})\n"
                            f"Release page: {self.version_check_url}"
                        ),
                    )
                else:
                    await self._send_direct_message(
                        reply_to,
                        f"✅ Bot is up to date ({__version__})",
                    )
                return True

            if cmd in ("banlist", "blacklist"):
                show_all = wants_all_pages(args)
                args = without_all_pages_arg(args)
                if args and args[0].lower() == "rtbl":
                    page = 1
                    if len(args) >= 2:
                        if args[1].lower() == "last":
                            page = LAST_PAGE_MARKER
                        else:
                            try:
                                page = max(1, int(args[1]))
                            except ValueError:
                                await self._send_direct_message(
                                    reply_to,
                                    f"❌ Usage: {p}{cmd} rtbl [all|page|last]",
                                )
                                return True
                    await self.cmd_banlist_rtbl(reply_to, page=page, show_all=show_all)
                    return True

                page = 1
                if args:
                    if args[0].lower() == "last":
                        page = LAST_PAGE_MARKER
                    else:
                        try:
                            page = max(1, int(args[0]))
                        except ValueError:
                            await self._send_direct_message(
                                reply_to,
                                f"❌ Usage: {p}{cmd} [all|page|last]",
                            )
                            return True
                await self.cmd_banlist(reply_to, page=page, show_all=show_all)
                return True

            if cmd == "room":
                def valid_room_list_arg(arg: str) -> bool:
                    value = arg.lower()
                    return value in {"all", "last"} or value.isdigit()

                if args and args[0].lower() == "list":
                    if any(not valid_room_list_arg(arg) for arg in args[1:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}room list [all|page|last]",
                        )
                        return True
                    await self.cmd_room(args, reply_to)
                    return True

                if len(args) >= 2 and args[0].lower() == "invite" and args[1].lower() == "list":
                    if any(not valid_room_list_arg(arg) for arg in args[2:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}room invite list [all|page|last]",
                        )
                        return True
                    await self.cmd_room(args, reply_to)
                    return True

                await self._send_direct_message(
                    reply_to,
                    (
                        "❌ Direct-message room commands are read-only.\n"
                        f"Allowed: {p}room list [all|page|last], "
                        f"{p}room invite list [all|page|last]"
                    ),
                )
                return True

            if cmd in ("ignore", "whitelist"):
                allowed_args = args or ["list"]
                if allowed_args[0].lower() == "list":
                    if any(not self._is_page_or_list_arg(arg) for arg in allowed_args[1:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}{cmd} list [all|page|last]",
                        )
                        return True
                    await self.cmd_ignore(allowed_args, reply_to, actor=sender_bare, command_name=cmd)
                    return True

                if all(self._is_page_or_list_arg(arg) for arg in allowed_args):
                    await self.cmd_ignore(allowed_args, reply_to, actor=sender_bare, command_name=cmd)
                    return True

                if allowed_args[0].lower() in {
                    "add",
                    "remove",
                    "delete",
                    "del",
                    "rm",
                    "clear",
                    "cleanup",
                    "set",
                    "enable",
                    "disable",
                }:
                    await self._send_direct_message(
                        reply_to,
                        (
                            "❌ Direct-message ignorelist commands are read-only.\n"
                            f"Allowed: {p}{cmd} [list] [all|page|last]"
                        ),
                    )
                else:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Usage: {p}{cmd} [list] [all|page|last]",
                    )
                return True

            if cmd == "rtbl":
                if args and args[0].lower() == "list":
                    if any(not self._is_page_or_list_arg(arg) for arg in args[1:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}rtbl list [all|page|last]",
                        )
                        return True
                    await self.cmd_rtbl(args, reply_to, actor=sender_bare)
                    return True

                await self._send_direct_message(
                    reply_to,
                    f"❌ Direct-message RTBL commands are read-only. Allowed: {p}rtbl list [all|page|last]",
                )
                return True

            if cmd == "audit":
                await self.cmd_audit(args, reply_to)
                return True

            if cmd == "why":
                if not args:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Usage: {p}why <nick|jid>",
                    )
                    return True

                await self.cmd_why(args[0], reply_to)
                return True

            if cmd == "bansearch":
                if not args:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Usage: {p}bansearch <query> [all|page|last]",
                    )
                    return True

                show_all = wants_all_pages(args)
                args = without_all_pages_arg(args)
                page = 1
                query_args = args
                if args and args[-1].lower() == "last":
                    page = LAST_PAGE_MARKER
                    query_args = args[:-1]
                elif args:
                    try:
                        page = max(1, int(args[-1]))
                        query_args = args[:-1]
                    except ValueError:
                        # Last argument is not a page number; treat it as part of the query.
                        pass

                if not query_args:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Usage: {p}bansearch <query> [all|page|last]",
                    )
                    return True

                query = " ".join(query_args)
                bansearch_params = inspect.signature(self.cmd_bansearch).parameters
                if "reply_to" in bansearch_params:
                    await self.cmd_bansearch(
                        query,
                        page=page,
                        show_all=show_all,
                        reply_to=reply_to,
                    )
                else:
                    await self.cmd_bansearch(query, page=page, show_all=show_all)
                return True

        return False


    async def on_direct_message(self, msg) -> None:
        """
        Handle regular DMs and MUC PMs.

        Admins may use a small read-only command subset in DMs when enabled.
        Mutating admin commands still require the admin room for auditability and safety.
        """
        # Ignore own messages
        if msg["from"].bare == self.boundjid.bare:
            return

        # Only process direct messages
        if msg["type"] not in ("chat", "normal"):
            return

        # Direct MUC invites are normal/chat messages and are handled separately.
        if hasattr(self, "handle_room_invite_message") and await self.handle_room_invite_message(msg):
            return

        encrypted = False
        if hasattr(self, "_decrypt_incoming_omemo_message"):
            msg, encrypted = await self._decrypt_incoming_omemo_message(msg)
            if msg is None:
                return

        is_admin, reply_to, sender_bare = self._direct_message_sender_info(msg)

        try:
            body = msg["body"].strip()
        except Exception:
            body = ""

        if is_admin and not getattr(self, "allow_admin_commands_in_dms", False):
            await self._send_direct_message(
                reply_to,
                f"🤖 Nice try, admin! But I only take commands directly in the admin room. "
                f"Please use {ADMIN_ROOM}.\nSee you there! 😉"
            )
            return

        if is_admin and body.startswith(self.command_prefix):
            parts = body.split()
            cmd = parts[0][len(self.command_prefix):].lower()
            args = parts[1:]

            token = None
            if hasattr(self, "_set_reply_encryption_context"):
                token = self._set_reply_encryption_context(encrypted)
            try:
                handled = await self._handle_admin_dm_readonly_command(
                    reply_to=reply_to,
                    sender_bare=sender_bare,
                    cmd=cmd,
                    args=args,
                )
            finally:
                if hasattr(self, "_reset_reply_encryption_context"):
                    self._reset_reply_encryption_context(token)

            if handled:
                return

            p = self.command_prefix
            readonly_commands = ", ".join(
                (
                    f"{p}help",
                    f"{p}config",
                    f"{p}status",
                    f"{p}protections list",
                    f"{p}omemo status",
                    f"{p}omemo devices",
                    f"{p}checkupdate",
                    f"{p}updatecheck",
                    f"{p}banlist",
                    f"{p}bansearch",
                    f"{p}why",
                    f"{p}room list",
                    f"{p}room invite list",
                    f"{p}ignore list",
                    f"{p}whitelist list",
                    f"{p}rtbl list",
                    f"{p}audit",
                )
            )
            await self._send_direct_message(
                reply_to,
                (
                    "❌ Direct-message admin commands are read-only.\n"
                    f"Allowed read-only commands: {readonly_commands}.\n"
                    f"Use {ADMIN_ROOM} for mutating commands."
                ),
            )
            return

        if is_admin:
            response = (
                "🤖 Admin DM support is read-only.\n"
                f"Allowed: {self.command_prefix}help, "
                f"{self.command_prefix}config, {self.command_prefix}status, "
                f"{self.command_prefix}protections list, "
                f"{self.command_prefix}omemo status, {self.command_prefix}omemo devices, "
                f"{self.command_prefix}checkupdate, {self.command_prefix}updatecheck, "
                f"{self.command_prefix}banlist, {self.command_prefix}bansearch, "
                f"{self.command_prefix}why, {self.command_prefix}room list, "
                f"{self.command_prefix}room invite list, {self.command_prefix}ignore list, "
                f"{self.command_prefix}whitelist list, {self.command_prefix}rtbl list, "
                f"{self.command_prefix}audit.\n"
                f"Use {ADMIN_ROOM} for mutating commands."
            )
        else:
            response = (
                "❌ I'm a ban management bot and only operate in designated rooms. "
                "I only listen to admins."
            )

        await self._send_direct_message(reply_to, response)
