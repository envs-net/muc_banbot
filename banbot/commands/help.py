"""Help text generation for BanBot commands."""

from ..utils import get_list_page_size, paginate_lines, resolve_page, wants_all_pages, without_all_pages_arg


class CommandHelpMixin:
    def _admin_topic_help_text(self, topic: str | list[str]) -> str:
        """Return focused help for one admin command topic."""
        if isinstance(topic, str):
            parts = topic.split()
        else:
            parts = [str(part) for part in topic]

        parts = [part.lower().strip() for part in parts if str(part).strip()]
        raw_topic = " ".join(parts)
        normalized = raw_topic
        aliases = {
            "blacklist": "banlist",
            "rules": "policy",
            "whitelist": "ignore",
            "reloadconfig": "reload",
            "updatecheck": "checkupdate",
            "del": "delete",
            "rm": "remove",
            "room invites": "room invite",
            "invite": "room invite",
            "invites": "room invite",
            "rtbl pub": "rtbl publish",
        }
        normalized = aliases.get(normalized, normalized)
        first = normalized.split()[0] if normalized else ""
        normalized = aliases.get(first, normalized) if len(normalized.split()) == 1 else normalized

        room_invite_help = getattr(self, "_room_invite_usage", None)
        if room_invite_help is None:
            room_invite_help = self._room_invite_usage_text

        topic_help = {
            "help": self._help_usage_text,
            "room": self._room_usage_text,
            "room invite": room_invite_help,
            "redact": self._redact_usage_text,
            "policy": self._policy_usage_text,
            "backup": self._backup_usage_text,
            "restore": self._restore_usage_text,
            "export": self._export_usage_text,
            "import": self._import_usage_text,
            "rtbl": self._rtbl_usage_text,
            "rtbl publish": self._rtbl_publish_usage_text,
            "ignore": self._ignore_usage_text,
            "config": self._config_usage_text,
            "audit": self._audit_usage_text,
            "ban": self._ban_usage_text,
            "tempban": self._tempban_usage_text,
            "unban": self._unban_usage_text,
            "banlist": self._banlist_usage_text,
            "bansearch": self._bansearch_usage_text,
            "why": self._why_usage_text,
            "restart": self._restart_usage_text,
            "reload": self._reload_usage_text,
            "checkupdate": self._checkupdate_usage_text,
            "status": self._status_usage_text,
            "whoami": self._whoami_usage_text,
            "sync": self._sync_usage_text,
            "syncadmins": self._syncadmins_usage_text,
            "syncbans": self._syncbans_usage_text,
            "omemo": self._omemo_usage_text,
        }

        help_factory = topic_help.get(normalized)
        if help_factory:
            return help_factory()

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

        return "\n".join(lines)

    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            "🛠️ Core / Runtime\n"
            f"{p}help - show this help\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}config [all|page|last] / show/set/unset - show/edit runtime config\n"
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
            f"{p}room list [all|page|last] - list protected rooms\n"
            f"{p}room invite list [all|page|last] - list pending room invites\n"
            f"{p}room invite accept/decline/remove/delete/del/rm <id> - accept or remove a room invite\n"
            f"{p}room invite cleanup [expired] - cleanup pending or expired room invites\n"
            f"{p}policy / {p}rules show/set/clear/delete/remove/enable/disable/help/usage - manage public rules/policy text\n\n"

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

            "✅ Ignorelist / Whitelist\n"
            f"{p}ignore [list|all|page|last] - show global ignorelist (alias: {p}whitelist)\n"
            f"{p}ignore add <jid|domain> [reason] - protect from all bans\n"
            f"{p}ignore remove/delete/del/rm <jid|domain> - remove from ignorelist\n"
            f"{p}whitelist [list|all|page|last|add|remove|delete|del|rm] - alias for {p}ignore\n\n"

            "🔄 Sync\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"

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
