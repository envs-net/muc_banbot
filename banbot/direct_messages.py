"""Direct-message and MUC-PM policy for admin read-only commands."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp.messaging import is_muc_private_message
from envs_xmpp_core.xmpp.occupants import (
    find_occupant_by_jid,
    find_occupant_by_nick,
    occupant_is_admin_or_owner,
)

from config import ADMIN_ROOM

from ._version import __version__
from .utils import wants_all_pages, without_all_pages_arg

LAST_PAGE_MARKER = -1
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"

if TYPE_CHECKING:
    from .contracts import DirectMessageMixinHost

    class _DirectMessageMixinContract(DirectMessageMixinHost):
        pass
else:
    class _DirectMessageMixinContract:
        pass


class DirectMessageMixin(_DirectMessageMixinContract):
    def _direct_message_room_occupants(
        self,
        room: str,
    ) -> dict[str, dict[str, Any]]:
        """Return one room's occupant cache using case-insensitive JID matching."""
        occupants = self.occupants.get(room)
        if occupants is not None:
            return occupants
        room_key = room.casefold()
        for cached_room, cached_occupants in self.occupants.items():
            if cached_room.casefold() == room_key:
                return cached_occupants
        return {}

    def _direct_message_sender_info(
        self,
        msg: Any,
    ) -> tuple[bool, str, str] | None:
        """Return ``(is_admin, reply_to, sender_bare)`` for a DM or MUC-PM.

        DM authorization deliberately follows the same trust boundary as room
        commands: the admin room is the single source of operator identity.
        Being owner/admin in another protected room is not sufficient.
        """
        try:
            from_jid = msg["from"]
            message_type = msg["type"]
        except Exception:
            return None

        sender = str(getattr(from_jid, "bare", "") or "").strip()
        sender_full = str(from_jid or "").strip()
        sender_resource = str(getattr(from_jid, "resource", "") or "").strip()
        if not sender or not sender_full:
            return None

        known_rooms = {
            str(room).strip().casefold()
            for room in self.protected_rooms | {ADMIN_ROOM}
            if str(room).strip()
        }
        sender_room_key = sender.casefold()

        # A real MUC PM looks like: room@conference.example/Nick.  Compare the
        # configured room set case-insensitively, then use the configured key
        # for occupant-cache lookups.
        is_muc_pm = is_muc_private_message(
            message_type,
            sender_room_key,
            sender_resource,
            known_rooms,
        )

        if is_muc_pm:
            room = sender
            reply_to = sender_full
            occupant = find_occupant_by_nick(
                self._direct_message_room_occupants(room),
                sender_resource,
                room=room,
            )
            real_bare = self.bare_jid(occupant.jid) if occupant is not None else None
            sender_bare = real_bare or sender

            if room.casefold() == ADMIN_ROOM.casefold():
                is_admin = bool(occupant and occupant_is_admin_or_owner(occupant))
            elif real_bare:
                admin_occupant = find_occupant_by_jid(
                    self._direct_message_room_occupants(ADMIN_ROOM),
                    real_bare,
                    room=ADMIN_ROOM,
                )
                is_admin = bool(
                    admin_occupant and occupant_is_admin_or_owner(admin_occupant)
                )
            else:
                is_admin = False

            return is_admin, reply_to, sender_bare

        # Regular direct DM: user@example/resource or user@example.
        direct_bare = self.bare_jid(sender_full)
        if not direct_bare:
            return None

        admin_occupant = find_occupant_by_jid(
            self._direct_message_room_occupants(ADMIN_ROOM),
            direct_bare,
            room=ADMIN_ROOM,
        )
        is_admin = bool(admin_occupant and occupant_is_admin_or_owner(admin_occupant))
        return is_admin, direct_bare, direct_bare


    async def _send_direct_message(self, reply_to: str, body: str) -> None:
        """Send a plain chat response to a direct-message requester."""
        await self.bot_send_message(mto=reply_to, mbody=body, mtype="chat")


    @asynccontextmanager
    async def _redirect_command_output_to_dm(self, reply_to: str) -> AsyncIterator[None]:
        """Route command output to a DM without mutating shared bot methods."""
        set_target = getattr(self, "_set_reply_target_context", None)
        reset_target = getattr(self, "_reset_reply_target_context", None)

        if not callable(set_target) or not callable(reset_target):
            # Lightweight embedders that use DirectMessageMixin without the
            # central MessagingMixin already pass the DM target to handlers.
            yield
            return

        token = set_target(reply_to, "chat")
        try:
            yield
        finally:
            reset_target(token)


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

                await self._cmd_config(reply_to, config_args)
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
                await self._cmd_status(reply_to, args)
                return True

            if cmd == "tasks":
                await self._cmd_tasks(reply_to, args, mtype="chat")
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
                    await self.cmd_banlist_rtbl(ADMIN_ROOM, page=page, show_all=show_all)
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
                await self.cmd_banlist(ADMIN_ROOM, page=page, show_all=show_all)
                return True

            if cmd in ("room", "rooms"):
                def valid_room_list_arg(arg: str) -> bool:
                    value = arg.lower()
                    return value in {"all", "last", "joined", "offline", "problems"} or value.isdigit()

                if args and args[0].lower() == "list":
                    if any(not valid_room_list_arg(arg) for arg in args[1:]):
                        await self._send_direct_message(
                            reply_to,
                            f"❌ Usage: {p}room list [joined|offline|problems] [all|page|last]",
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
                        f"Allowed: {p}room list [joined|offline|problems] [all|page|last], "
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

            if cmd == "baninfo":
                if not args:
                    await self._send_direct_message(reply_to, f"❌ Usage: {p}baninfo <jid|nick|*.domain.tld>")
                    return True
                await self.cmd_baninfo(args[0], ADMIN_ROOM)
                return True

            if cmd == "history":
                if not args:
                    await self._send_direct_message(reply_to, f"❌ Usage: {p}history <jid|nick|*.domain.tld> [all|page|last]")
                    return True
                await self.cmd_history(args[0], ADMIN_ROOM, args[1:])
                return True

            if cmd == "why":
                if not args:
                    await self._send_direct_message(
                        reply_to,
                        f"❌ Usage: {p}why <nick|jid>",
                    )
                    return True

                await self.cmd_why(args[0], ADMIN_ROOM)
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
                await self.cmd_bansearch(query, page=page, show_all=show_all)
                return True

        return False


    async def on_direct_message(self, msg: Any) -> None:
        """
        Handle regular DMs and MUC PMs.

        Admins may use a small read-only command subset in DMs when enabled.
        Mutating admin commands still require the admin room for auditability and safety.
        """
        # Treat stanza sender/type fields as untrusted input at the DM boundary.
        try:
            sender_bare = self.bare_jid(getattr(msg["from"], "bare", None))
            message_type = str(msg["type"] or "").strip().lower()
        except Exception:
            return

        if not sender_bare:
            return

        own_bare = self.bare_jid(getattr(self.boundjid, "bare", None))
        if own_bare and sender_bare == own_bare:
            return

        # Only process direct messages.
        if message_type not in ("chat", "normal"):
            return

        # Direct MUC invites are normal/chat messages and are handled separately.
        if hasattr(self, "handle_room_invite_message") and await self.handle_room_invite_message(msg):
            return

        encrypted = False
        if hasattr(self, "_decrypt_incoming_omemo_message"):
            msg, encrypted = await self._decrypt_incoming_omemo_message(msg)
            if msg is None:
                return

        sender_info = self._direct_message_sender_info(msg)
        if sender_info is None:
            return
        is_admin, reply_to, sender_bare = sender_info

        try:
            body = str(msg["body"] or "").strip()
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
                    f"{p}tasks",
                    f"{p}protections list",
                    f"{p}omemo status",
                    f"{p}omemo devices",
                    f"{p}checkupdate",
                    f"{p}updatecheck",
                    f"{p}banlist",
                    f"{p}bansearch",
                    f"{p}baninfo",
                    f"{p}history",
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
                f"{self.command_prefix}tasks, {self.command_prefix}protections list, "
                f"{self.command_prefix}omemo status, {self.command_prefix}omemo devices, "
                f"{self.command_prefix}checkupdate, {self.command_prefix}updatecheck, "
                f"{self.command_prefix}banlist, {self.command_prefix}bansearch, "
                f"{self.command_prefix}baninfo, {self.command_prefix}history, "
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
