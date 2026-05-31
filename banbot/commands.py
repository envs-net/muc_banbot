"""XMPP groupchat command routing and help text."""

import asyncio
import inspect
import logging
import os
import time

from config import ADMIN_ROOM, NICK

from ._version import __version__
from .locks import get_ban_state_lock
from .utils import parse_duration, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)

# PUBLIC_COMMANDS used for ratelimits
PUBLIC_COMMANDS = {"help", "whoami", "banlist", "blacklist", "why", "rules", "policy"}


class CommandMixin:
    def user_cmds_allowed(self, room: str) -> bool:
        """Check if user commands are allowed in protected rooms."""
        return room in self.protected_rooms and self.allow_user_cmds


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

        if hasattr(self, "_redaction_index_message"):
            await self._redaction_index_message(msg)

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
        if room != ADMIN_ROOM and cmd in PUBLIC_COMMANDS:
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

        if cmd in ("banlist", "blacklist") and (room == ADMIN_ROOM or self.user_cmds_allowed(room)):
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

        if cmd == "why" and (room == ADMIN_ROOM or self.user_cmds_allowed(room)):
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}why <nick|jid>",
                    mtype="groupchat",
                )
                return True

            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami" and (room == ADMIN_ROOM or self.user_cmds_allowed(room)):
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

            return False

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
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self._cmd_config(room, args, actor=actor_jid)
            return True

        if cmd == "backup":
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_backup(args, room, actor=actor_jid)
            return True

        if cmd == "restore":
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_restore(args, room, actor=actor_jid)
            return True

        if cmd == "omemo":
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_omemo(args, room, actor=actor_jid)
            return True

        if cmd in ("reload", "reloadconfig"):
            await self._cmd_reloadconfig(room)
            return True

        if cmd == "restart":
            await self._cmd_restart(room, args)
            return True

        if cmd == "status":
            await self._cmd_status(room)
            return True

        if cmd in ("checkupdate", "updatecheck"):
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
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        "Usage:\n"
                        f"  {self.command_prefix}room list [all|page]\n"
                        f"  {self.command_prefix}room add <room_jid>\n"
                        f"  {self.command_prefix}room remove <room_jid>\n"
                        f"  {self.command_prefix}room invite list [all|page|last]\n"
                        f"  {self.command_prefix}room invite accept <id>\n"
                        f"  {self.command_prefix}room invite decline <id>\n"
                        f"  {self.command_prefix}room invite cleanup"
                    ),
                    mtype="groupchat",
                )
                return True

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

            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            comment = " ".join(args[1:]) if len(args) > 1 else None
            async with get_ban_state_lock(self):
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
                    mtype="groupchat"
                )
                return True

            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            comment = " ".join(args[2:]) if len(args) > 2 else None
            async with get_ban_state_lock(self):
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

            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            async with get_ban_state_lock(self):
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
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_redact(args, room, actor=actor_jid)
            return True

        if cmd == "sync":
            async with get_ban_state_lock(self):
                await self.sync_rooms_and_bans()
            return True

        if cmd == "syncadmins":
            await self.sync_admins(announce=True)
            return True

        if cmd == "syncbans":
            async with get_ban_state_lock(self):
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
            if len(args) < 1:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}import <filename> [dryrun]",
                    mtype="groupchat"
                )
                return True

            filename = args[0]
            dry_run = len(args) >= 2 and args[1].lower() in {"dryrun", "dry-run", "check"}
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            previous_backup = getattr(self, "last_database_backup_file", None)
            try:
                successful, skipped, errors = await self.import_bans_from_csv(
                    filename,
                    actor=actor_jid,
                    dry_run=dry_run,
                )
            except TypeError:
                # Lightweight tests and older mixins may not support dry_run yet.
                successful, skipped, errors = await self.import_bans_from_csv(
                    filename,
                    actor=actor_jid,
                )
            import_backup = getattr(self, "last_database_backup_file", None)
            if import_backup == previous_backup:
                import_backup = None

            heading = "📥 Import Dry-Run Results" if dry_run else "📥 Import Results"
            result_msg = (
                f"{heading}:\n"
                f"✅ Successful: {successful}\n"
                f"⚠️ Skipped: {skipped}"
            )
            if dry_run:
                result_msg += "\nNo backup created and no database changes made."

            if import_backup:
                result_msg += f"\n💾 Full backup before import: {import_backup}"

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
            self.log_event(
                logging.INFO,
                "import_completed",
                actor=actor_jid,
                filename=filename,
                dry_run=dry_run,
                successful=successful,
                skipped=skipped,
                errors=len(errors),
                backup=import_backup,
            )
            await self.audit_event(
                "import_completed",
                actor=actor_jid,
                target_type="import",
                target=filename,
                details={
                    "filename": filename,
                    "dry_run": dry_run,
                    "successful": successful,
                    "skipped": skipped,
                    "errors": len(errors),
                    "backup": import_backup,
                },
            )
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
            async with get_ban_state_lock(self):
                await self.cmd_rtbl(args, room, actor=actor_jid)
            return True

        if cmd in ("ignore", "whitelist"):
            actor_jid = self.occupants.get(room, {}).get(nick, {}).get("jid", nick)
            await self.cmd_ignore(args, room, actor=actor_jid, command_name=cmd)
            return True

        if cmd in ("policy", "rules"):
            await self.cmd_policy(args, room)
            return True

        log.error("Unhandled admin command routed without handler: %s", cmd)
        raise RuntimeError(
            f"Internal routing error: admin command '{cmd}' recognized but not implemented"
        )


    async def _cmd_restart(self, room: str, args: list[str]) -> None:
        """Admin command to exit cleanly so a supervisor such as systemd can restart the bot."""
        p = self.command_prefix

        if not args or args[0].lower() != "confirm":
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "⚠️ This will stop the bot process. "
                    "If it is managed by systemd or another supervisor, it should restart automatically.\n\n"
                    f"Confirm with: {p}restart confirm"
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody="♻️ Restart confirmed. Shutting down now; supervisor should restart the bot.",
            mtype="groupchat",
            encrypted=False,
        )

        restart_task = asyncio.create_task(self._restart_process())
        self._restart_task = restart_task

        if isinstance(restart_task, asyncio.Task):
            restart_task.add_done_callback(self._clear_restart_task)


    def _clear_restart_task(self, task: asyncio.Task) -> None:
        """Drop the stored restart task reference once it has completed."""
        if getattr(self, "_restart_task", None) is task:
            self._restart_task = None


    async def _restart_process(self) -> None:
        """Flush state, disconnect, and terminate the process for supervisor restart."""
        # Give the confirmation message a short chance to leave the XMPP stream.
        await asyncio.sleep(0.5)

        try:
            if hasattr(self, "flush_redaction_index"):
                await self.flush_redaction_index()
        except Exception as exc:
            log.warning("Restart: failed to flush redaction index: %s", exc)

        try:
            if hasattr(self, "stop_background_tasks"):
                await self.stop_background_tasks()
        except Exception as exc:
            log.warning("Restart: failed to stop background tasks cleanly: %s", exc)

        try:
            disconnect = getattr(self, "disconnect", None)
            if callable(disconnect):
                try:
                    result = disconnect(wait=False)
                except TypeError:
                    result = disconnect()

                if inspect.isawaitable(result):
                    await result
        except Exception as exc:
            log.warning("Restart: failed to disconnect cleanly: %s", exc)

        log.info("Restart: exiting process now")
        os._exit(0)


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

    def _policy_usage_text(self) -> str:
        """Return usage text for the admin policy command."""
        p = self.command_prefix
        return (
            f"Usage:\n"
            f"  {p}policy show\n"
            f"  {p}policy set <text>\n"
            f"  {p}policy enable\n"
            f"  {p}policy disable\n"
            f"  {p}policy clear\n\n"
            "Supported placeholders:\n"
            "  {prefix}, {room}, {room_count}, {admin_room}, {bot_name}\n"
            "Use literal \\n for line breaks."
        )

    async def cmd_policy(self, args: list[str], room: str) -> None:
        """Admin command to manage the public policy/rules text."""
        p = self.command_prefix

        if args and args[0].lower() in {"help", "usage"}:
            await self.bot_send_message(
                mto=room,
                mbody=self._policy_usage_text(),
                mtype="groupchat",
            )
            return

        if not args or args[0].lower() in {"show", "list"}:
            enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        "ℹ️ No public policy text is configured.\n\n"
                        f"{self._policy_usage_text()}"
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
                    f"{self._policy_usage_text().replace('Usage:', 'Commands:', 1)}"
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
                f"Available: show / set / enable / disable / clear / help"
            ),
            mtype="groupchat",
        )


    async def _user_help_text(self) -> str:
        p = self.command_prefix
        lines = [
            f"{p}help - show this help",
            f"{p}whoami - show your affiliation/role and permissions",
            f"{p}banlist / {p}blacklist [page] - show temporary bans",
            f"{p}why <jid|nick|domain> - show ban reason",
        ]

        policy_enabled, policy_text = await self.get_public_policy()
        if policy_enabled and policy_text.strip():
            lines.append(f"{p}rules / {p}policy - show room moderation policy")

        return "\n".join(lines)


    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            "🛠️ Core / Runtime\n"
            f"{p}help - show this help\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}config [show|set|unset] - show/edit runtime config\n"
            f"{p}reload / {p}reloadconfig - reload config.py at runtime\n"
            f"{p}restart confirm - stop the bot so a supervisor can restart it\n"
            f"{p}checkupdate / {p}updatecheck - check if a newer bot release is available\n"
            f"{p}whoami - show your affiliation/role\n"
            f"{p}audit [all|page|last|query] - show recent audit events\n\n"

            "💾 Backup / Restore\n"
            f"{p}backup - create a full backup\n"
            f"{p}backup list - list full backups\n"
            f"{p}restore <filename|latest> confirm - restore a full backup\n\n"

            "🏠 Rooms / Policy\n"
            f"{p}room add/remove - manage protected rooms\n"
            f"{p}room list [all|page] - list protected rooms\n"
            f"{p}room invite list [all|page|last] - list pending room invites\n"
            f"{p}room invite accept/decline <id> - accept or decline a room invite\n"
            f"{p}policy / {p}rules show/set/clear/enable/disable - manage public rules/policy text\n\n"

            "🛡️ Moderation\n"
            f"{p}ban <jid|nick> [comment] - ban user from all protected rooms\n"
            f"{p}tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
            f"{p}unban <jid|nick> - remove ban\n"
            f"{p}redact <jid> [reason] - redact indexed messages from a JID in protected rooms\n"
            f"{p}redact id <room_jid> <stanza_id> [reason] - redact one known stanza ID\n"
            f"{p}redact cleanup - cleanup old redaction index entries\n\n"

            "🔎 Ban Queries\n"
            f"{p}banlist / {p}blacklist [all|page|last] - show all active bans with remaining time and comments\n"
            f"{p}banlist / {p}blacklist rtbl [all|page|last] - show RTBL hash and domain entries\n"
            f"{p}bansearch <query> [all|page|last] - search bans by nick, domain, jid or RTBL reason\n"
            f"{p}why <nick|jid> - show the reason and remaining time for a ban\n\n"

            "🔄 Sync\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"

            "✅ Ignorelist / Whitelist\n"
            f"{p}ignore [list|all|page] - show global ignorelist (alias: {p}whitelist)\n"
            f"{p}ignore add <jid|domain> [reason] - protect from all bans\n"
            f"{p}ignore remove <jid|domain> - remove from ignorelist\n"
            f"{p}whitelist [list|all|add|remove] - alias for {p}ignore\n\n"

            "🔐 OMEMO\n"
            f"{p}omemo status - show OMEMO state\n"
            f"{p}omemo devices - show admin-room recipients and storage hints\n"
            f"{p}omemo reset [confirm] - rotate OMEMO storage after confirmation\n\n"

            "🛡️ RTBL\n"
            f"{p}rtbl list - show active RTBL subscriptions\n"
            f"{p}rtbl add <service> <node> - subscribe to a RTBL node\n"
            f"{p}rtbl delete <service> [node] - remove a RTBL subscription\n"
            f"{p}rtbl refresh [service_jid] [node] - refresh RTBL subscriptions now\n"
            f"{p}rtbl publish status - status of your own RTBL feed\n"
            f"{p}rtbl publish sync - publish all current bans to your own feed\n\n"

            "📦 Import / Export\n"
            f"{p}export [list|delete] - export/list/delete managed CSV ban exports\n"
            f"{p}import <filename> [dryrun] - import bans from a CSV file\n"
        )
