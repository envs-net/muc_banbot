"""XMPP groupchat command routing and help text."""

import logging
import time

from config import ADMIN_ROOM, NICK

from ._version import __version__
from .utils import parse_duration, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)

# PUBLIC_COMMANDS used for ratelimits
PUBLIC_COMMANDS = {"help", "whoami", "banlist", "why", "rules", "policy"}


class CommandMixin:
    def user_cmds_allowed(self, room: str) -> bool:
        """Check if user commands are allowed in this room."""
        return (
            room == ADMIN_ROOM or
            (room in self.protected_rooms and self.allow_user_cmds)
        )


    async def on_message(self, msg) -> None:
        """
        Handles incoming messages in MUCs.
        - Ignores own messages
        - Parses commands
        - Delegates to user/admin handlers
        """
        if msg["mucnick"].lower() == NICK.lower():
            return  # Ignore own messages

        encrypted = False
        if hasattr(self, "_decrypt_incoming_omemo_message"):
            msg, encrypted = await self._decrypt_incoming_omemo_message(msg)
            if msg is None:
                return

        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()

        if not body:
            return

        if not body.startswith(self.command_prefix):
            return

        parts = body.split()
        raw_cmd = parts[0]

        cmd = raw_cmd[len(self.command_prefix):].lower()
        args = parts[1:]

        token = self._set_reply_encryption_context(encrypted)
        try:
            handled = await self._handle_user_command(msg, room, nick, cmd, args)
            if handled:
                return

            handled = await self._handle_admin_command(msg, room, nick, cmd, args)
            if handled:
                return

            await self._handle_unknown_command(msg, room, cmd)
        finally:
            self._reset_reply_encryption_context(token)


    async def _handle_unknown_command(self, msg, room: str, cmd: str) -> None:
        """
        Inform admins about unknown commands and point to help.

        In protected rooms unknown commands are ignored silently. This avoids
        noisy bot replies for normal chat lines that happen to start with the
        command prefix, e.g. "!?".
        """
        p = self.command_prefix

        # In admin room: only answer admins.
        if room == ADMIN_ROOM:
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
        if room == ADMIN_ROOM or cmd not in PUBLIC_COMMANDS:
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
        args: list[str]
    ) -> bool:
        if room != ADMIN_ROOM and cmd in PUBLIC_COMMANDS and self.user_cmds_allowed(room):
            allowed, retry_after = self.check_public_command_rate_limit(room, nick, cmd)
            if not allowed:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"⏳ Rate limit: please wait {retry_after}s before using "
                        f"{self.command_prefix}{cmd} again."
                    ),
                    mtype="groupchat"
                )
                return True

        if cmd == "help":
            if room == ADMIN_ROOM and self.is_authorized(msg):
                text = self._admin_help_text()
            elif self.user_cmds_allowed(room):
                text = await self._user_help_text()
            else:
                return True

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
            return True

        if cmd == "banlist" and self.user_cmds_allowed(room):
            show_all = wants_all_pages(args)
            args = without_all_pages_arg(args)
            if args and args[0].lower() == "rtbl":
                if room != ADMIN_ROOM:
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
                                mbody=f"❌ Usage: {self.command_prefix}banlist rtbl [all|page|last]",
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
                            mbody=f"❌ Usage: {self.command_prefix}banlist [rtbl] [all|page|last]",
                            mtype="groupchat",
                        )
                        return True
            await self.cmd_banlist(room, page=page, show_all=show_all)
            return True

        if cmd == "why" and len(args) >= 1 and self.user_cmds_allowed(room):
            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami":
            await self._cmd_whoami(room, nick)
            return True

        if cmd in ("rules", "policy"):
            # In the admin room, !policy is handled by the admin command below.
            # In protected rooms, !rules / !policy show the public policy text.
            if room == ADMIN_ROOM:
                return False

            if self.user_cmds_allowed(room):
                await self._cmd_public_policy_show(room)
                return True

            return True

        return False


    async def _handle_admin_command(
        self,
        msg,
        room: str,
        nick: str,
        cmd: str,
        args: list[str]
    ) -> bool:
        admin_commands = {
            "config",
            "reloadconfig",
            "status",
            "checkupdate",
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
            "ignore",
            "whitelist",
            "policy",
        }

        if cmd not in admin_commands:
            return False

        if room != ADMIN_ROOM:
            return True

        if not self.is_authorized(msg):
            await self.bot_send_message(
                mto=room,
                mbody="❌ You are not authorized to use this admin command.",
                mtype="groupchat"
            )
            return True

        if cmd == "config":
            await self._cmd_config(room)
            return True

        if cmd == "reloadconfig":
            await self._cmd_reloadconfig(room)
            return True

        if cmd == "status":
            await self._cmd_status(room)
            return True

        if cmd == "checkupdate":
            is_update, remote_version, error_message = await self.check_for_updates_once(announce=False)

            if error_message:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Update check failed: {error_message}",
                    mtype="groupchat"
                )
            elif is_update:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"⬆️ New bot version available: {remote_version} (current: {__version__})\n"
                        f"Release page: {self.version_check_url}"
                    ),
                    mtype="groupchat"
                )
            else:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"✅ Bot is up to date ({__version__})",
                    mtype="groupchat"
                )
            return True

        if cmd == "room":
            if len(args) >= 1:
                await self.cmd_room(args, room)
            return True

        if cmd == "ban":
            if len(args) >= 1:
                actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
                comment = " ".join(args[1:]) if len(args) > 1 else None
                await self.ban_all(args[0], None, actor_jid, comment)
            return True

        if cmd == "tempban":
            if len(args) >= 2:
                try:
                    until = int(time.time()) + parse_duration(args[1])
                except Exception:
                    await self.bot_send_message(
                        mto=room,
                        mbody=f"❌ Invalid duration format ({self.command_prefix}tempban user 10m)",
                        mtype="groupchat"
                    )
                    return True

                actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
                comment = " ".join(args[2:]) if len(args) > 2 else None
                await self.ban_all(args[0], until, actor_jid, comment)
            return True

        if cmd == "unban":
            if len(args) >= 1:
                actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
                await self.unban_all(args[0], actor_jid)
            return True

        if cmd == "bansearch":
            if len(args) >= 1:
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

        if cmd == "sync":
            await self.sync_rooms_and_bans()
            return True

        if cmd == "syncadmins":
            await self.sync_admins(announce=True)
            return True

        if cmd == "syncbans":
            await self.sync_bans()
            return True

        if cmd == "audit":
            await self.cmd_audit(args, room)
            return True

        if cmd == "export":
            success, message = await self.export_bans_to_csv()
            await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
            return True

        if cmd == "import":
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}import <filename>",
                    mtype="groupchat"
                )
                return True

            filename = args[0]
            successful, skipped, errors = await self.import_bans_from_csv(filename)

            result_msg = (
                f"📥 Import Results:\n"
                f"✅ Successful: {successful}\n"
                f"⚠️ Skipped: {skipped}"
            )

            if self.last_import_backup_file:
                result_msg += f"\n💾 Backup before import: {self.last_import_backup_file}"

            if errors:
                result_msg += f"\n\n❌ Errors ({len(errors)}):\n"
                result_msg += "\n".join(errors[:10])
                if len(errors) > 10:
                    result_msg += f"\n... and {len(errors) - 10} more errors"

            await self.bot_send_message(mto=room, mbody=result_msg, mtype="groupchat")
            log.info(
                "Import completed: %d successful, %d skipped, %d errors",
                successful,
                skipped,
                len(errors)
            )
            self.log_event(logging.INFO, "import_completed", actor=nick, filename=filename, successful=successful, skipped=skipped, errors=len(errors), backup=self.last_import_backup_file)
            await self.audit_event("import_completed", actor=nick, details={"filename": filename, "successful": successful, "skipped": skipped, "errors": len(errors), "backup": self.last_import_backup_file})
            return True

        if cmd == "rtbl":
            if not getattr(self, "rtbl_enabled", False):
                await self.bot_send_message(
                    mto=room,
                    mbody="❌ RTBL is disabled. Set RTBL_ENABLED = True in config.py and restart.",
                    mtype="groupchat",
                )
                return True
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_rtbl(args, room, actor=actor_jid)
            return True

        if cmd in ("ignore", "whitelist"):
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_ignore(args, room, actor=actor_jid, command_name=cmd)
            return True

        if cmd == "policy":
            await self.cmd_policy(args, room)
            return True

        return True


    def _format_public_policy_text(self, text: str, room: str) -> str:
        """Format public policy text with simple placeholders."""
        replacements = {
            "bot_name": "muc_banbot",
            "prefix": self.command_prefix,
            "room": room,
            "room_count": str(len(getattr(self, "protected_rooms", []))),
            "admin_room": ADMIN_ROOM,
        }

        formatted = text

        for key, value in replacements.items():
            formatted = formatted.replace("{" + key + "}", value)

        # Allow admins to enter multiline text via literal \n in chat.
        formatted = formatted.replace("\\n", "\n")

        return formatted.strip()

    async def _cmd_public_policy_show(self, room: str) -> None:
        """Show public policy text in a protected room."""
        enabled, text = await self.get_public_policy()

        # In protected rooms this should be quiet when disabled/unset.
        # Unknown commands are already silent there, so keep this optional too.
        if not enabled or not text.strip():
            return

        await self.bot_send_message(
            mto=room,
            mbody=self._format_public_policy_text(text, room),
            mtype="groupchat",
        )

    async def cmd_policy(self, args: list[str], room: str) -> None:
        """Admin command to manage the public policy/rules text."""
        p = self.command_prefix

        if not args or args[0].lower() in {"show", "list"}:
            enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        "ℹ️ No public policy text is configured.\n\n"
                        f"Usage:\n"
                        f"  {p}policy set <text>\n"
                        f"  {p}policy enable\n"
                        f"  {p}policy disable\n"
                        f"  {p}policy clear\n\n"
                        "Supported placeholders:\n"
                        "  {prefix}, {room}, {room_count}, {admin_room}, {bot_name}\n"
                        "Use literal \\n for line breaks."
                    ),
                    mtype="groupchat",
                )
                return

            status = "enabled" if enabled else "disabled"
            preview = self._format_public_policy_text(text, room)

            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"📜 Public policy is currently {status}.\n\n"
                    f"{preview}\n\n"
                    f"Commands:\n"
                    f"  {p}policy set <text>\n"
                    f"  {p}policy enable\n"
                    f"  {p}policy disable\n"
                    f"  {p}policy clear\n\n"
                    "Supported placeholders:\n"
                    "  {prefix}, {room}, {room_count}, {admin_room}, {bot_name}\n"
                    "Use literal \\n for line breaks."
                ),
                mtype="groupchat",
            )
            return

        action = args[0].lower()

        if action == "set":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"❌ Usage: {p}policy set <text>\n"
                        "Use literal \\n for line breaks.\n"
                        "Placeholders: {prefix}, {room}, {room_count}, {admin_room}, {bot_name}"
                    ),
                    mtype="groupchat",
                )
                return

            text = " ".join(args[1:]).strip()
            await self.set_public_policy_text(text, enabled=True)

            await self.bot_send_message(
                mto=room,
                mbody=(
                    "✅ Public policy text saved and enabled.\n\n"
                    f"{self._format_public_policy_text(text, room)}"
                ),
                mtype="groupchat",
            )
            return

        if action == "enable":
            enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody=f"⚠️ No public policy text is configured. Use {p}policy set <text> first.",
                    mtype="groupchat",
                )
                return

            if enabled:
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ Public policy command is already enabled.",
                    mtype="groupchat",
                )
                return

            await self.set_public_policy_enabled(True)
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy command enabled.",
                mtype="groupchat",
            )
            return

        if action == "disable":
            enabled, _text = await self.get_public_policy()

            if not enabled:
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ Public policy command is already disabled.",
                    mtype="groupchat",
                )
                return

            await self.set_public_policy_enabled(False)
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy command disabled.",
                mtype="groupchat",
            )
            return

        if action == "clear":
            enabled, text = await self.get_public_policy()

            if not enabled and not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ No public policy text is configured.",
                    mtype="groupchat",
                )
                return

            await self.clear_public_policy()
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy text cleared and disabled.",
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                f"❌ Unknown policy action: {action}\n"
                f"Available: show / set / enable / disable / clear"
            ),
            mtype="groupchat",
        )


    async def _user_help_text(self) -> str:
        p = self.command_prefix
        lines = [
            f"{p}help - show this help",
            f"{p}whoami - show your affiliation/role and permissions",
            f"{p}banlist [page] - show temporary bans",
            f"{p}why <jid|nick|domain> - show ban reason",
        ]

        policy_enabled, policy_text = await self.get_public_policy()
        if policy_enabled and policy_text.strip():
            lines.append(f"{p}rules / {p}policy - show room moderation policy")

        return "\n".join(lines)


    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            f"{p}help - show this help\n"
            f"{p}config - show current configuration\n"
            f"{p}reloadconfig - reload config.py at runtime\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}checkupdate - check if a newer bot release is available\n"
            f"{p}whoami - show your affiliation/role\n"
            f"{p}policy show/set/clear/enable/disable - manage public rules/policy text\n"
            f"{p}audit [all|page|last|query] - show recent audit events\n\n"
            f"{p}room add/remove - manage protected rooms\n"
            f"{p}room list [all|page] - list protected rooms\n\n"
            f"{p}ban <jid|nick> [comment] - ban user from all protected rooms\n"
            f"{p}tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
            f"{p}unban <jid|nick> - remove ban\n\n"
            f"{p}banlist [all|page|last] - show all active bans with remaining time and comments\n"
            f"{p}banlist rtbl [all|page|last] - show RTBL hash and domain entries\n"
            f"{p}bansearch <query> [all|page|last] - search bans by nick, domain, jid or RTBL reason\n"
            f"{p}why <nick|jid> - show the reason and remaining time for a ban\n\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"
            f"{p}ignore list [all|page] - show global ignorelist (alias: {p}whitelist)\n"
            f"{p}ignore add <jid|domain> [reason] - protect from all bans\n"
            f"{p}ignore remove <jid|domain> - remove from ignorelist\n\n"
            f"{p}rtbl list - show active RTBL subscriptions\n"
            f"{p}rtbl add <service> <node> - subscribe to a RTBL node\n"
            f"{p}rtbl delete <service> [node] - remove a RTBL subscription\n"
            f"{p}rtbl refresh [service_jid] [node] - Refresh RTBL subscriptions now\n"
            f"{p}rtbl publish status - Status of your own RTBL feed\n"
            f"{p}rtbl publish sync - Publish all current bans to your own feed\n\n"
            f"{p}export - export all bans to a CSV file\n"
            f"{p}import <filename> - import bans from a CSV file\n"
        )
