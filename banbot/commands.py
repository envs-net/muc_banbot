"""XMPP message command routing and admin/public command handlers."""

import asyncio
import os
import logging
import re
import time

import psutil
from config import ADMIN_ROOM, DB_FILE, JID, NICK
from slixmpp.exceptions import IqError, IqTimeout

from .config_utils import get_config_resource
from .utils import human_time, paginate_lines, parse_duration
from .version import __version__

log = logging.getLogger(__name__)

# PUBLIC_COMMANDS used for ratelimits
PUBLIC_COMMANDS = {"help", "whoami", "banlist", "why"}


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

        handled = await self._handle_user_command(msg, room, nick, cmd, args)
        if handled:
            return

        handled = await self._handle_admin_command(msg, room, nick, cmd, args)
        if handled:
            return

        await self._handle_unknown_command(msg, room, cmd)


    async def _handle_unknown_command(self, msg, room: str, cmd: str) -> None:
        """
        Inform users/admins about unknown commands and point to help.
        """
        p = self.command_prefix

        # In admin room: only answer admins
        if room == ADMIN_ROOM:
            if not self.is_authorized(msg):
                return

            self.send_message(
                mto=room,
                mbody=f"❌ Unknown command: {p}{cmd}\nUse {p}help to see available admin commands.",
                mtype="groupchat"
            )
            return

        # In protected rooms: only answer if user commands are allowed
        if self.user_cmds_allowed(room):
            self.send_message(
                mto=room,
                mbody=f"❌ Unknown command: {p}{cmd}\nUse {p}help to see available commands.",
                mtype="groupchat"
            )


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
                self.send_message(
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
                text = self._user_help_text()
            else:
                return True

            self.send_message(mto=room, mbody=text, mtype="groupchat")
            return True

        if cmd == "banlist" and self.user_cmds_allowed(room):
            page = 1
            if len(args) >= 1:
                try:
                    page = max(1, int(args[0]))
                except ValueError:
                    self.send_message(
                        mto=room,
                        mbody=f"❌ Usage: {self.command_prefix}banlist [page]",
                        mtype="groupchat"
                    )
                    return True

            await self.cmd_banlist(room, page=page)
            return True

        if cmd == "why" and len(args) >= 1 and self.user_cmds_allowed(room):
            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami":
            await self._cmd_whoami(room, nick)
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
        }

        if cmd not in admin_commands:
            return False

        if room != ADMIN_ROOM:
            return True

        if not self.is_authorized(msg):
            self.send_message(
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
                self.send_message(
                    mto=room,
                    mbody=f"❌ Update check failed: {error_message}",
                    mtype="groupchat"
                )
            elif is_update:
                self.send_message(
                    mto=room,
                    mbody=(
                        f"⬆️ New bot version available: {remote_version} (current: {__version__})\n"
                        f"Release page: {self.version_check_url}"
                    ),
                    mtype="groupchat"
                )
            else:
                self.send_message(
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
                comment = " ".join(args[1:]) if len(args) > 1 else None
                await self.ban_all(args[0], None, nick, comment)
            return True

        if cmd == "tempban":
            if len(args) >= 2:
                try:
                    until = int(time.time()) + parse_duration(args[1])
                except Exception:
                    self.send_message(
                        mto=room,
                        mbody=f"❌ Invalid duration format ({self.command_prefix}tempban user 10m)",
                        mtype="groupchat"
                    )
                    return True

                comment = " ".join(args[2:]) if len(args) > 2 else None
                await self.ban_all(args[0], until, nick, comment)
            return True

        if cmd == "unban":
            if len(args) >= 1:
                await self.unban_all(args[0], nick)
            return True

        if cmd == "bansearch":
            if len(args) >= 1:
                query = " ".join(args)
                await self.cmd_bansearch(query)
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
            self.send_message(mto=room, mbody=message, mtype="groupchat")
            return True

        if cmd == "import":
            if len(args) < 1:
                self.send_message(
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

            self.send_message(mto=room, mbody=result_msg, mtype="groupchat")
            log.info(
                "Import completed: %d successful, %d skipped, %d errors",
                successful,
                skipped,
                len(errors)
            )
            self.log_event(logging.INFO, "import_completed", actor=nick, filename=filename, successful=successful, skipped=skipped, errors=len(errors), backup=self.last_import_backup_file)
            await self.audit_event("import_completed", actor=nick, details={"filename": filename, "successful": successful, "skipped": skipped, "errors": len(errors), "backup": self.last_import_backup_file})
            return True

        return True


    def _user_help_text(self) -> str:
        p = self.command_prefix
        return (
            f"{p}help - show this help\n"
            f"{p}whoami - show your affiliation/role and permissions\n"
            f"{p}banlist [page] - show temporary bans\n"
            f"{p}why <nick> - show ban reason"
        )


    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            f"{p}help - show this help\n"
            f"{p}config - show current configuration\n"
            f"{p}reloadconfig - reload config.py at runtime\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}checkupdate - check if a newer bot release is available\n"
            f"{p}whoami - show your affiliation/role\n\n"
            f"{p}room add/remove - manage protected rooms\n"
            f"{p}room list [page] - list protected rooms\n\n"
            f"{p}ban <jid|nick> [comment] - ban user from all protected rooms\n"
            f"{p}tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
            f"{p}unban <jid|nick> - remove ban\n\n"
            f"{p}audit [page|query] - show recent audit events\n"
            f"{p}banlist [page] - show all active bans with remaining time and comments\n"
            f"{p}bansearch <query> - search bans by nick, domain or jid\n"
            f"{p}why <nick|jid> - show the reason and remaining time for a ban\n\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"
            f"{p}export - export all bans to a CSV file\n"
            f"{p}import <filename> - import bans from a CSV file"
        )


    async def _cmd_config(self, room: str) -> None:
        config_lines = ["📋 Current Bot Configuration:\n"]

        config_lines.append(f"🤖 Bot Version: {__version__}")
        config_lines.append(f"💾 Database: {DB_FILE}")
        config_lines.append(f"🔐 JID: {JID}")
        config_lines.append(f"📦 Resource: {get_config_resource() or 'None'}")
        config_lines.append(f"👤 Nick: {NICK}")
        config_lines.append("")
        config_lines.append(f"⌨️ Command Prefix: {self.command_prefix}")
        config_lines.append(f"🧱 Structured Event Logs: {self.structured_event_logs}")
        config_lines.append(f"🧾 Audit Log: {self.audit_log_enabled} ({self.audit_log_retention_days}d retention)")
        config_lines.append(f"📢 Announce Startup: {self.announce_startup}")
        config_lines.append(f"📊 Announce Sync Details: {self.announce_sync_details}")
        config_lines.append(f"📣 Show Bans in MUC: {self.show_ban_in_muc}")
        config_lines.append(f"✅ Allow User Commands: {self.allow_user_cmds}")
        config_lines.append("")
        config_lines.append(f"⏰ Health Check Interval: {self.health_check_interval}s")
        config_lines.append(f"⏱️ Unban Check Interval: {self.unban_check_interval}s")
        config_lines.append(f"📅 Max Tempban Days: {self.max_tempban_days}")
        config_lines.append(f"🚦 Public Command Rate Limit: {self.public_command_rate_limit_max}/{self.public_command_rate_limit_window}s")
        config_lines.append(f"🔌 MUC Write Semaphore: {self.muc_write_limit}")
        config_lines.append("")
        config_lines.append(f"🔄 Version Check Enabled: {self.version_check_enabled}")
        config_lines.append(f"🕒 Version Check Interval: {self.version_check_interval}s")
        config_lines.append(f"🌐 Version Check URL: {self.version_check_url or 'None'}")

        self.send_message(
            mto=room,
            mbody="\n".join(config_lines),
            mtype="groupchat"
        )


    async def _cmd_reloadconfig(self, room: str) -> None:
        try:
            changes, errors, warnings = await self.reload_runtime_config()

            if errors:
                msg = self._format_config_validation(errors, warnings)
                self.send_message(
                    mto=room,
                    mbody=f"❌ Config reload aborted. Old config is still active.\n\n{msg}",
                    mtype="groupchat"
                )
                log.error("Config reload aborted: %s", errors)
                return

            lines = ["✅ Config reloaded successfully."]

            if warnings:
                lines.append("\n⚠️ Warnings:")
                lines.extend(f"- {w}" for w in warnings)

            if changes:
                lines.append("\nChanged:")
                lines.extend(changes)
            else:
                lines.append("\nNo runtime config changes detected.")

            self.send_message(
                mto=room,
                mbody="\n".join(lines),
                mtype="groupchat"
            )
            log.info("Config reloaded at runtime. Changes: %s", changes or "none")
        except Exception as e:
            self.send_message(
                mto=room,
                mbody=f"❌ Failed to reload config: {e}",
                mtype="groupchat"
            )
            log.error("Failed to reload config: %s", e)


    async def _cmd_status(self, room: str) -> None:
        status_lines = ["✅ Bot is online and healthy."]

        # version
        status_lines.append(f"🤖 Bot Version: {__version__}")
        if self.last_version_check_result:
            status_lines.append(f"🏷️ Latest Release Version: {self.last_version_check_result}")

        # uptime
        bot_uptime = int(time.time()) - self.bot_start_time
        status_lines.append(f"\n⏱️ Bot Uptime: {human_time(bot_uptime)}")

        if self.server_connect_time:
            server_uptime = int(time.time()) - self.server_connect_time
            status_lines.append(f"🌐 Server Connected: {human_time(server_uptime)}")

        # mem info
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            status_lines.append(f"💾 Memory Usage: {memory_mb:.1f} MB")
        except Exception as e:
            log.debug("Could not get memory info: %s", e)

        # cpu info
        try:
            process = psutil.Process(os.getpid())
            loop = asyncio.get_running_loop()

            # psutil samples over 1 second; run in executor so the event loop stays responsive
            cpu_percent = await loop.run_in_executor(None, process.cpu_percent, 1.0)
            cpu_load = psutil.getloadavg()[0]
            cpu_count = psutil.cpu_count()

            status_lines.append(f"🧠 CPU Usage: {cpu_percent:.1f}% (Process)")
            status_lines.append(f"⚙️ System Load: {cpu_load:.2f} ({cpu_count} cores)")
        except Exception as e:
            log.debug("Could not get CPU info: %s", e)

        # database stats
        db_stats = await self.get_db_stats()
        permanent_bans = db_stats.get("permanent_bans", 0)
        temporary_bans = db_stats.get("temporary_bans", 0)
        expired_ban_rows = db_stats.get("expired_ban_rows", 0)
        audit_events = db_stats.get("audit_events", 0)
        db_size_kib = int(db_stats.get("db_size_bytes", 0)) / 1024

        status_lines.append(f"\n📊 Active Bans: {permanent_bans} permanent, {temporary_bans} temporary")
        status_lines.append(f"🧹 Expired tempbans pending auto-unban: {expired_ban_rows}")
        status_lines.append(f"🧾 Audit Events: {audit_events} (retention: {self.audit_log_retention_days}d)")
        status_lines.append(f"💽 DB Size: {db_size_kib:.1f} KiB")
        if self.admin_affiliation_query_forbidden_rooms:
            status_lines.append(f"⚠️ Affiliation-query fallback rooms: {len(self.admin_affiliation_query_forbidden_rooms)}")
        if self.last_import_backup_file:
            status_lines.append(f"💾 Last Import Backup: {self.last_import_backup_file}")

        # admins
        admin_infos = self.occupants.get(ADMIN_ROOM, {})
        admins = sorted(set(
            self.safe_jid(info.get("jid", "unknown"))
            for info in admin_infos.values()
            if info.get("affiliation") in ("owner", "admin")
        ))
        status_lines.append(
            "\n🛡️ Admins/Owners in Admin-Room:\n" + "\n".join(admins)
            if admins else "\n⚠️ No admins/owners found in Admin-Room."
        )

        # protected rooms
        if self.protected_rooms:
            rooms = sorted(self.protected_rooms)
            preview_count = 10
            preview_rooms = rooms[:preview_count]

            status_lines.append(
                f"\n🔒 Protected Rooms ({len(rooms)}):\n" +
                "\n".join(preview_rooms)
            )

            remaining = len(rooms) - len(preview_rooms)
            if remaining > 0:
                status_lines.append(
                    f"\n... and {remaining} more.\n"
                    f"Use {self.command_prefix}room list [page] to view all protected rooms."
                )
        else:
            status_lines.append("\n⚠️ No protected rooms configured.")

        self.send_message(mto=room, mbody="\n".join(status_lines), mtype="groupchat")


    async def _cmd_whoami(self, room: str, nick: str) -> None:
        info = self.occupants.get(room, {}).get(nick, {})
        affiliation = info.get("affiliation", "none")
        role = info.get("role", "none")
        jid = info.get("jid", "unknown")

        permissions = []
        if affiliation in ("owner", "admin"):
            permissions.append("✅ Can ban/kick users")
            permissions.append("✅ Can manage room")
        elif role == "moderator":
            permissions.append("✅ Can kick users")
        else:
            permissions.append("❌ Regular participant")

        perms_text = "\n".join(permissions)

        if room == ADMIN_ROOM:
            message = (
                f"👤 **Your Status:**\n"
                f"  Nick: {nick}\n"
                f"  JID: {jid}\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )
        else:
            emoji = "🔑" if affiliation in ("owner", "admin") else "👤"
            message = (
                f"{emoji} **Your Status:**\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )

        self.send_message(mto=room, mbody=message, mtype="groupchat")


    async def on_direct_message(self, msg) -> None:
        """
        Reject direct messages (regular DMs and MUC PMs).
        MUC PM admin detection is based on room + nick from the MUC occupant cache.
        """
        # Ignore own messages
        if msg["from"].bare == self.boundjid.bare:
            return

        # Only process direct messages
        if msg["type"] not in ("chat", "normal"):
            return

        sender = msg["from"].bare
        sender_full = str(msg["from"])
        sender_resource = msg["from"].resource

        known_rooms = self.protected_rooms | {ADMIN_ROOM}

        # A real MUC PM looks like: room@conference.example/Nick
        is_muc_pm = sender in known_rooms and sender_resource is not None

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

        if is_admin:
            response = (
                "🤖 Nice try, admin! But I only take commands directly in the admin room. "
                f"Please use {ADMIN_ROOM}.\nSee you there! 😉"
            )
        else:
            response = (
                "❌ I'm a ban management bot and only operate in designated rooms. "
                "I only listen to admins."
            )

        self.send_message(
            mto=sender_full if is_muc_pm else sender,
            mbody=response,
            mtype="chat"
        )


    async def validate_room_jid(self, room_jid: str) -> tuple[bool, str]:
        """
        Validate a room JID in two steps:
        1. Format validation (name@domain.tld)
        2. Service Discovery check (XEP-0030)

        Returns: (is_valid: bool, error_message: str)
        """
        room_jid = room_jid.strip().lower()

        # --- Step 1: Format Validation ---
        if not room_jid:
            return False, "❌ Room JID cannot be empty."

        if "@" not in room_jid:
            return False, "❌ Invalid JID format. Expected: name@muc.example.com"

        parts = room_jid.split("@")
        if len(parts) != 2:
            return False, "❌ Invalid JID format. Expected: name@muc.example.com"

        room_name, domain = parts

        # Check for valid characters (alphanumeric, dots, hyphens, underscores)
        if not re.match(r"^[a-z0-9._-]+$", room_name):
            return False, f"❌ Invalid room name '{room_name}'. Use alphanumeric, dots, hyphens, underscores only."

        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            return False, f"❌ Invalid domain '{domain}'. Expected: domain.tld"

        # --- Step 2: Service Discovery Check (XEP-0030) ---
        try:
            info = await self.plugin["xep_0030"].get_info(jid=room_jid, timeout=5)

            # Check if it's a MUC (Multi-User Chat)
            identities = info["disco_info"]["identities"]
            is_muc = any(
                identity[0] == "conference" and identity[1] == "text"
                for identity in identities
            )

            if not is_muc:
                return False, f"❌ '{room_jid}' exists but is not a Multi-User Chat room."

            log.info("✅ Room validated: %s (is MUC)", room_jid)
            return True, ""

        except IqTimeout:
            return False, f"❌ Service Discovery timeout for '{room_jid}'. Room may not exist or server is unresponsive."
        except IqError as e:
            error_msg = str(e.iq["error"]["type"]) if e.iq and e.iq["error"] else "Unknown error"
            return False, f"❌ Service Discovery error for '{room_jid}': {error_msg}"
        except Exception as e:
            return False, f"❌ Failed to validate room: {str(e)}"


    async def cmd_room(self, args: list[str], room: str) -> None:
        """
        Manage protected rooms.
        Commands: list, add <room>, remove <room>

        The `add` command now validates:
        - JID format (name@domain.tld)
        - Room existence via Service Discovery (XEP-0030)
        """
        if not args:
            return

        action = args[0].lower()

        if action == "list":
            page = 1
            if len(args) >= 2:
                try:
                    page = max(1, int(args[1]))
                except ValueError:
                    self.send_message(
                        mto=room,
                        mbody=f"❌ Usage: {self.command_prefix}room list [page]",
                        mtype="groupchat"
                    )
                    return

            if self.protected_rooms:
                rooms = sorted(self.protected_rooms)
                page_lines, current_page, total_pages, total_items = paginate_lines(rooms, page, per_page=10)

                text = (
                    f"🔒 Protected Rooms ({total_items}) - Page {current_page}/{total_pages}:\n"
                    + "\n".join(page_lines)
                )

                if current_page < total_pages:
                    text += f"\n\nUse {self.command_prefix}room list {current_page + 1} for the next page."
            else:
                text = "🔒 Protected Rooms:\nNo protected rooms."

            self.send_message(mto=room, mbody=text, mtype="groupchat")

        elif action in ("add", "remove") and len(args) >= 2:
            target = args[1].lower()

            if action == "add":
                # --- Validate Room JID before adding ---
                is_valid, error_msg = await self.validate_room_jid(target)
                if not is_valid:
                    self.send_message(mto=room, mbody=error_msg, mtype="groupchat")
                    log.warning("Room validation failed for %s: %s", target, error_msg)
                    return

                if target not in self.protected_rooms:
                    # --- In-Memory and DB ---
                    self.protected_rooms.add(target)
                    await self.db.execute("INSERT OR REPLACE INTO rooms (room) VALUES (?)", (target,))
                    await self.db.commit()
                    self.send_message(mto=room, mbody=f"✅ Room added: {target}", mtype="groupchat")

                    # --- Event handler for new occupants ---
                    if target not in self.registered_rooms:
                        self.add_event_handler(f"muc::{target}::got_online", self.muc_online)
                        self.add_event_handler(f"muc::{target}::got_offline", self.muc_offline)
                        self.registered_rooms.add(target)

                    self.plugin["xep_0045"].join_muc(target, NICK)

                    # --- Ensure the bot itself is online ---
                    async def wait_for_bot_online():
                        # Wait until the bot itself is recognized as Nick.
                        for _ in range(10):  # max 10 second timeout
                            occ = self.occupants.get(target, {})
                            if NICK in occ:
                                break
                            await asyncio.sleep(1)
                        # --- Start ban sync for this new room ---
                        await self.sync_bans_to_rooms_for_single_room(target)

                        # --- Optional: Check all other rooms for new bans ---
                        other_rooms = self.protected_rooms - {target}
                        if other_rooms:
                            log.info("🔄 Applying existing bans to other rooms due to new room addition")
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"🔄 Applying existing bans to other rooms due to new room addition",
                                mtype="groupchat"
                            )
                            for room in other_rooms:
                                await self.sync_bans_to_rooms_for_single_room(room)

                    await wait_for_bot_online()
                else:
                    self.send_message(mto=room, mbody=f"⚠️ Room already in protected list: {target}", mtype="groupchat")

            elif action == "remove":
                self.protected_rooms.discard(target)
                await self.db.execute("DELETE FROM rooms WHERE room=?", (target,))
                await self.db.commit()
                self.send_message(mto=room, mbody=f"✅ Room removed: {target}", mtype="groupchat")

                # --- Bot leaves the room immediately ---
                try:
                    self.plugin["xep_0045"].leave_muc(target, NICK)
                except Exception as e:
                    log.warning("⚠️ Failed to leave room %s: %s", target, e)


    def _format_ban_match(self, jid, nick, until, issuer, comment, now):
        """Format a ban match for display in bansearch results."""
        remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
        emoji = "⏳" if until > 0 else "🔒"
        return f"{emoji} {jid or nick or 'Unknown'} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"


    async def cmd_bansearch(self, query: str) -> None:
        """
        Searches bans by JID, nick, domain, issuer, or comment/reason.
        Supports optional filters:
        jid:<query>, nick:<query>, domain:<query>, issuer:<query>, by:<query>,
        comment:<query>, reason:<query>
        """
        raw_query = query.strip()
        q = raw_query.lower()
        matches = []
        seen = set()
        now = int(time.time())

        field = None
        value = q

        for prefix in ("jid:", "nick:", "domain:", "issuer:", "by:", "comment:", "reason:"):
            if q.startswith(prefix):
                field = prefix[:-1]
                value = q[len(prefix):].strip()
                break

        if field == "by":
            field = "issuer"
        if field == "reason":
            field = "comment"

        if not value:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Usage: {self.command_prefix}bansearch <query>",
                mtype="groupchat"
            )
            return

        def add_match(ban):
            jid, nick, until, issuer, comment = ban
            key = (jid or "", nick or "", until, issuer or "", comment or "")
            if key not in seen:
                seen.add(key)
                matches.append(self._format_ban_match(jid, nick, until, issuer, comment, now))

        # Direct index lookup for unfiltered broad searches.
        if field is None:
            if value in self.ban_index_by_jid:
                add_match(self.ban_index_by_jid[value])

            if value in self.ban_index_by_nick:
                add_match(self.ban_index_by_nick[value])

            domain_value = value[2:] if value.startswith("*.") else value
            if domain_value in self.ban_index_by_domain:
                for ban in self.ban_index_by_domain[domain_value]:
                    add_match(ban)

        for _, (jid, nick, until, issuer, comment) in self.ban_cache.items():
            jid_value = jid or ""
            nick_value = nick or ""
            issuer_value = issuer or ""
            comment_value = comment or ""

            if jid_value.startswith("*."):
                domain_value = jid_value[2:]
            elif "@" in jid_value:
                domain_value = jid_value.split("@", 1)[1]
            else:
                domain_value = ""

            fields = {
                "jid": jid_value.lower(),
                "nick": nick_value.lower(),
                "domain": domain_value.lower(),
                "issuer": issuer_value.lower(),
                "comment": comment_value.lower(),
            }

            if field:
                haystack = fields.get(field, "")
            else:
                haystack = " ".join(v for v in fields.values() if v)

            if value in haystack:
                add_match((jid, nick, until, issuer, comment))

        if matches:
            msg = "🔍 Ban search results:\n" + "\n".join(matches[:20])
            if len(matches) > 20:
                msg += f"\n... and {len(matches) - 20} more matches"
        else:
            msg = f"❌ No bans found matching '{query}'."

        self.send_message(
            mto=ADMIN_ROOM,
            mbody=msg,
            mtype="groupchat"
        )


    async def cmd_audit(self, args: list[str], room: str) -> None:
        """Show recent audit events. Usage: !audit [page|query]."""
        page = 1
        query = None
        if args:
            try:
                page = max(1, int(args[0]))
            except ValueError:
                query = " ".join(args).strip().lower()

        params: list[object] = []
        where = ""
        if query:
            like = f"%{query}%"
            where = """
                WHERE LOWER(event_type) LIKE ?
                   OR LOWER(COALESCE(actor, '')) LIKE ?
                   OR LOWER(COALESCE(target, '')) LIKE ?
                   OR LOWER(COALESCE(jid, '')) LIKE ?
                   OR LOWER(COALESCE(nick, '')) LIKE ?
                   OR LOWER(COALESCE(comment, '')) LIKE ?
                   OR LOWER(COALESCE(details, '')) LIKE ?
            """
            params = [like] * 7

        async with self.db.execute(f"SELECT COUNT(*) FROM audit_log {where}", params) as cursor:
            row = await cursor.fetchone()
            total = int(row[0] or 0) if row else 0

        total_pages = max(1, (total + 9) // 10)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * 10

        async with self.db.execute(
            f"""
            SELECT created_at, event_type, actor, target_type, target, jid, nick, until, comment, details
            FROM audit_log
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT 10 OFFSET ?
            """,
            [*params, offset],
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            text = "🧾 Audit log:\nNo matching events."
        else:
            title = f"🧾 Audit log ({total}) - Page {page}/{total_pages}"
            if query:
                title += f" - query: {query}"
            text = title + ":\n" + "\n".join(self._format_audit_row(row) for row in rows)
            if page < total_pages and not query:
                text += f"\n\nUse {self.command_prefix}audit {page + 1} for the next page."

        self.send_message(mto=room, mbody=text, mtype="groupchat")


    async def cmd_banlist(self, room: str, page: int = 1) -> None:
        """
        Show bans with pagination.
        Admin Room: full info (all bans)
        Protected Rooms: temporary bans only, anonymized
        """
        async with self.db.execute(
            "SELECT jid, nick, until, issuer, comment FROM bans ORDER BY "
            "CASE WHEN until <= 0 THEN 1 ELSE 0 END, until ASC, LOWER(COALESCE(nick, jid)) ASC"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            text = "📋 Banlist:\nNo active bans."
        else:
            now = int(time.time())
            entries = []

            for jid, nick, until, issuer, comment in rows:
                # Skip expired tempbans
                if until > 0 and until <= now:
                    continue

                # Skip permanent bans in protected rooms
                if room != ADMIN_ROOM and until <= 0:
                    continue

                remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
                emoji = "⏳" if until > 0 else "🔒"

                if jid and jid.startswith("*."):
                    display = jid
                elif room == ADMIN_ROOM:
                    display = jid or nick or "Unknown"
                else:
                    display = nick or (jid.split("@")[0] if jid else "Unknown")

                entry = f"{emoji} {display} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"
                entries.append(entry)

            if not entries:
                text = "📋 Banlist:\nNo active temporary bans." if room != ADMIN_ROOM else "📋 Banlist:\nNo active bans."
            else:
                page_lines, current_page, total_pages, total_items = paginate_lines(entries, page, per_page=10)

                header = f"📋 Banlist ({total_items}) - Page {current_page}/{total_pages}:"
                text = header + "\n" + "\n".join(page_lines)

                if current_page < total_pages:
                    text += f"\n\nUse {self.command_prefix}banlist {current_page + 1} for the next page."

        self.send_message(mto=room, mbody=text, mtype="groupchat")


    async def cmd_why(self, identifier: str, room: str) -> None:
        """
        Show reason for a ban.
        Admin Room: full info (JID/nick)
        Protected Rooms: only nick, JID anonymized
        """
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()
        row = None

        # Check JID
        if ban_jid:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE jid=?", (ban_jid,)
            ) as cursor:
                row = await cursor.fetchone()

        # Check nick
        if not row:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE LOWER(nick)=?", (ban_nick,)
            ) as cursor:
                row = await cursor.fetchone()

        # Fallback nick-only check against JIDs
        if not row and ban_nick:
            async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
                async for jid_db, nick_db, until, issuer, comment in cursor:
                    if jid_db and self.bare_jid(jid_db).split("@")[0].lower() == ban_nick:
                        row = (jid_db, nick_db, until, issuer, comment)
                        break

        if row:
            jid_db, nick_db, until, issuer, comment = row
            now = int(time.time())
            remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
            emoji = "⏳" if until > 0 else "🔒"

            if room == ADMIN_ROOM:
                display = jid_db or nick_db or identifier
            else:
                display = nick_db or (jid_db.split("@")[0] if jid_db else identifier)

            msg = f"{emoji} {display} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"
        else:
            msg = f"No ban found for {identifier}"

        if room == ADMIN_ROOM:
            q = identifier.lower().strip()
            like = f"%{q}%"
            async with self.db.execute(
                """
                SELECT created_at, event_type, actor, target_type, target, jid, nick, until, comment, details
                FROM audit_log
                WHERE LOWER(COALESCE(target, '')) LIKE ?
                   OR LOWER(COALESCE(jid, '')) LIKE ?
                   OR LOWER(COALESCE(nick, '')) LIKE ?
                   OR LOWER(COALESCE(details, '')) LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT 3
                """,
                (like, like, like, like),
            ) as cursor:
                audit_rows = await cursor.fetchall()
            if audit_rows:
                msg += "\n\nRecent audit history:\n" + "\n".join(self._format_audit_row(r) for r in audit_rows)

        self.send_message(mto=room, mbody=msg, mtype="groupchat")
