"""Help text generation for BanBot commands."""

from typing import TYPE_CHECKING

from ..utils import get_list_page_size, paginate_lines, resolve_page, wants_all_pages, without_all_pages_arg
from .help_specs import HELP_TOPIC_METHODS, find_help_command

if TYPE_CHECKING:
    from ..contracts import CommandHelpMixinHost

    class _CommandHelpMixinContract(CommandHelpMixinHost):
        pass
else:
    class _CommandHelpMixinContract:
        pass


class CommandHelpMixin(_CommandHelpMixinContract):
    def _admin_topic_help_text(self, topic: str | list[str]) -> str:
        """Return focused help for one admin command topic."""
        raw_topic = " ".join(topic.split()) if isinstance(topic, str) else " ".join(map(str, topic))
        command = find_help_command(topic)
        if command is not None:
            return getattr(self, HELP_TOPIC_METHODS[command.name])()

        return (
            f"❌ Unknown help topic: {raw_topic or topic}\n"
            f"Use {self.command_prefix}help to see available admin commands."
        )

    @staticmethod
    def _is_help_page_arg(arg: str) -> bool:
        value = str(arg).lower().strip()
        return value in {"all", "last"} or value.isdigit()

    def _admin_help_should_paginate(self, args: list[str]) -> bool:
        if wants_all_pages(args):
            return False
        return getattr(self, "help_output_mode", "all") == "paginate"

    def _admin_help_page_from_args(self, args: list[str], total_items: int, per_page: int) -> int:
        args = without_all_pages_arg(args)
        page = 1
        for arg in args:
            value = str(arg).lower().strip()
            if value == "last":
                page = -1
            elif value.isdigit():
                page = int(value)
        return resolve_page(page, total_items, per_page)

    def _admin_help_response(self, args: list[str] | None = None) -> str:
        args = args or []
        if args and not all(self._is_help_page_arg(arg) for arg in args):
            return self._admin_topic_help_text(args)

        help_lines = self._admin_help_text().splitlines()
        if not self._admin_help_should_paginate(args):
            return "\n".join(help_lines)

        per_page = get_list_page_size(self)
        page = self._admin_help_page_from_args(args, len(help_lines), per_page)
        page_lines, current_page, total_pages, _total_items = paginate_lines(help_lines, page, per_page)
        return "\n".join([
            f"🛠️ Admin Help (page {current_page}/{total_pages})",
            *page_lines,
            "",
            f"Use {self.command_prefix}help all for the full output.",
        ])

    async def _user_help_text(self) -> str:
        p = self.command_prefix
        lines = [
            f"{p}help - show this help",
            f"{p}whoami - show your affiliation/role and permissions",
            f"{p}banlist / {p}blacklist [all|page|last] - show temporary bans",
            f"{p}why <jid|nick|domain> - show ban reason",
        ]

        policy_enabled, policy_text = await self.get_public_policy()
        if policy_enabled and policy_text.strip():
            lines.append(f"{p}rules / {p}policy - show room moderation policy")

        if getattr(self, "protection_enabled", lambda _name: False)("TrustedReporters"):
            lines.append(f"{p}report <nick|jid> [reason] - report abuse as trusted reporter")

        return "\n".join(lines)

    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            "🛠️ Core / Runtime\n"
            f"{p}help - show this help\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}tasks [all|full|failed|stale|restarting|restarted|problems] - show supervised background tasks and watchdog state\n"
            f"{p}config [all|page|last] / show/search/find/diff/set/unset - show/edit runtime config\n"
            f"{p}reload / {p}reloadconfig - reload config.py at runtime\n"
            f"{p}restart confirm - stop the bot so a supervisor can restart it\n"
            f"{p}checkupdate / {p}updatecheck - check if a newer bot release is available\n"
            f"{p}whoami - show your affiliation/role\n"
            f"{p}audit [all|page|last|query] - show recent audit events\n\n"

            "💾 Backup / Restore\n"
            f"{p}backup - create a full backup\n"
            f"{p}backup list [all|page|last] - list full backups\n"
            f"{p}backup show <filename|latest> - show backup details\n"
            f"{p}backup verify <filename|latest> - verify a backup\n"
            f"{p}backup delete/remove/del/rm <filename|latest> - delete a backup\n"
            f"{p}restore <filename|latest> confirm - restore a full backup\n\n"

            "🏠 Rooms / Policy\n"
            f"{p}room add/remove/delete/del/rm - manage protected rooms\n"
            f"{p}room/rooms list [joined|offline|problems] [all|page|last] - list protected rooms\n"
            f"{p}room rejoin <room|all> - retry selected protected-room joins\n"
            f"{p}room invite list [all|page|last] - list pending room invites\n"
            f"{p}room invite accept/decline/remove/delete/del/rm <id> - accept or remove a room invite\n"
            f"{p}room invite cleanup [expired] - cleanup pending or expired room invites\n"
            f"{p}policy / {p}rules show/set/clear/delete/remove/enable/disable/help/usage - manage public rules/policy text\n\n"

            "🛡️ Moderation\n"
            f"{p}ban <jid|nick> [comment] - ban or update an existing permanent ban reason\n"
            f"{p}tempban <jid|nick> <10m|2h|1d> [comment] - add/update tempban; omitted reason is preserved\n"
            f"{p}unban <jid|nick> - remove ban\n"
            f"{p}banedit <target> <operation> ... - edit reason, duration or ban type\n"
            f"{p}redact <jid> [reason] - redact indexed messages from a JID in protected rooms\n"
            f"{p}redact id <room_jid> <stanza_id> [reason] - redact one known stanza ID\n"
            f"{p}redact cleanup - cleanup old redaction index entries\n\n"

            "🔎 Ban Queries\n"
            f"{p}banlist / {p}blacklist [all|page|last] - show all active bans with remaining time and comments\n"
            f"{p}banlist rtbl / {p}blacklist rtbl [all|page|last] - show RTBL hash and domain entries\n"
            f"{p}bansearch <query> [all|page|last] - search bans by nick, domain, jid or RTBL reason\n"
            f"{p}why <nick|jid> - show the reason and remaining time for a ban\n"
            f"{p}baninfo <target> - show complete current ban metadata\n"
            f"{p}history <target> [all|page|last] - show moderation history\n\n"

            "✅ Ignorelist / Whitelist\n"
            f"{p}ignore [list] [all|page|last] - show global ignorelist (alias: {p}whitelist)\n"
            f"{p}ignore add <jid|domain> [reason] - protect from all bans\n"
            f"{p}ignore remove/delete/del/rm <jid|domain> - remove from ignorelist\n"
            f"{p}whitelist [list|all|page|last|add|remove|delete|del|rm] - alias for {p}ignore\n\n"

            "🔄 Sync\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"

            "🛡️ Protections\n"
            f"{p}protections list [all|page|last] - list available protections with enabled/disabled state\n"
            f"{p}protection enable/disable <name> - enable or disable a protection\n"
            f"{p}protections <name> show/config - show protection configuration\n"
            f"{p}protections <name> set <key> <value> - update protection configuration\n"
            f"{p}protections <name> reset - reset a protection to defaults\n"
            f"{p}protections <name> observe <on|off> - toggle consequence-free observation\n"
            f"{p}protections reporters add/remove/list <jid> - manage trusted reporters\n"
            f"{p}report <nick|jid> [reason] - report abuse as trusted reporter\n\n"

            "🔐 OMEMO\n"
            f"{p}omemo status - show OMEMO state\n"
            f"{p}omemo devices - show admin-room recipients and storage hints\n"
            f"{p}omemo reset [confirm] - rotate OMEMO storage after confirmation\n\n"

            "🛡️ RTBL\n"
            f"{p}rtbl list [all|page|last] - show active RTBL subscriptions\n"
            f"{p}rtbl add <service> <node> - subscribe to a RTBL node\n"
            f"{p}rtbl delete/remove/del/rm <service> [node] - remove a RTBL subscription\n"
            f"{p}rtbl refresh [service_jid] [node] - refresh RTBL subscriptions now\n"
            f"{p}rtbl publish status - status of your own RTBL feed\n"
            f"{p}rtbl publish sync - publish all current bans to your own feed\n\n"

            "📦 Import / Export\n"
            f"{p}export [list|show|delete|remove|del|rm] [all|page|last] - export/list/show/delete managed CSV ban exports\n"
            f"{p}import <filename> [dryrun] - import bans from a CSV file\n"
        )
