"""Focused usage text helpers for BanBot commands."""


class CommandUsageMixin:

    def _protection_usage_text(self) -> str:
        """Return usage text for protection commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}protections list [all|page|last]\n"
            f"  {p}protection enable <name>\n"
            f"  {p}protection disable <name>\n"
            f"  {p}protections <name> config/show\n"
            f"  {p}protections <name> set <key> <value>\n"
            f"  {p}protections <name> reset\n"
            f"  {p}protections <name> observe <on|off>\n"
            f"  {p}protections reporters add/remove/list <jid>\n\n"
            "Examples:\n"
            f"  {p}protection enable FloodSpamProtection\n"
            f"  {p}protections MentionLimitProtection set max_mentions 5\n"
            f"  {p}protections FloodSpamProtection set tempban_seconds 1h\n"
            f"  {p}protections reporters add alice@example.org"
        )

    def _report_usage_text(self) -> str:
        """Return usage text for trusted reporter command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}report <nick|jid> [reason]\n\n"
            "Reports only count when TrustedReporters is enabled and the sender JID is configured as trusted."
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
            f"  {p}policy clear/delete/remove\n"
            f"  {p}policy help/usage\n\n"
            "Supported placeholders:\n"
            "  {prefix}, {room}, {room_count}, {admin_room}, {bot_name}\n"
            "Use literal \\n for line breaks."
        )

    def _room_usage_text(self) -> str:
        """Return usage text for the admin room command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}room list [all|page]\n"
            f"  {p}room add <room_jid>\n"
            f"  {p}room remove/delete/rm/del <room_jid>\n"
            f"  {p}room invite list [all|page|last]\n"
            f"  {p}room invite accept <id>\n"
            f"  {p}room invite decline/remove/delete/del/rm <id>\n"
            f"  {p}room invite cleanup [expired]"
        )

    def _room_invite_usage_text(self) -> str:
        """Return usage text for room invite commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}room invite list [all|page|last]\n"
            f"  {p}room invite accept <id>\n"
            f"  {p}room invite decline/remove/delete/del/rm <id>\n"
            f"  {p}room invite cleanup [expired]"
        )

    def _redact_usage_text(self) -> str:
        """Return usage text for the admin redact command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}redact <jid> [reason]\n"
            f"  {p}redact id <room_jid> <stanza_id> [reason]\n"
            f"  {p}redact cleanup"
        )

    def _help_usage_text(self) -> str:
        """Return usage text for help itself."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}help [all|page|last]\n"
            f"  {p}help <command>\n\n"
            "Examples:\n"
            f"  {p}help room\n"
            f"  {p}help redact\n"
            f"  {p}help backup\n"
            f"  {p}help room invite\n"
            f"  {p}help rtbl publish\n\n"
        )

    def _backup_usage_text(self) -> str:
        """Return usage text for backup and restore commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}backup\n"
            f"  {p}backup list [all|page|last]\n"
            f"  {p}backup show <filename|latest>\n"
            f"  {p}backup verify <filename|latest>\n"
            f"  {p}backup delete/remove/del/rm <filename|latest>\n"
            f"  {p}restore <filename|latest> confirm"
        )

    def _restore_usage_text(self) -> str:
        """Return usage text for restore command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}restore <filename|latest> confirm\n\n"
            "Restores a full backup. The confirm argument is required intentionally."
        )

    def _export_usage_text(self) -> str:
        """Return usage text for export and import commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}export\n"
            f"  {p}export list [all|page|last]\n"
            f"  {p}export show <filename|latest>\n"
            f"  {p}export delete/remove/del/rm <filename|latest>\n"
            f"  {p}import <filename> [dryrun]"
        )

    def _import_usage_text(self) -> str:
        """Return usage text for import command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}import <filename> [dryrun]\n\n"
            "Use dryrun/dry-run/check to validate an import without changing the database."
        )

    def _ignore_usage_text(self) -> str:
        """Return usage text for ignore/whitelist commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}ignore [list|all|page|last]\n"
            f"  {p}ignore add <jid|domain> [reason]\n"
            f"  {p}ignore remove/delete/del/rm <jid|domain>\n"
            f"  {p}whitelist ... - alias for {p}ignore"
        )

    def _rtbl_usage_text(self) -> str:
        """Return usage text for RTBL commands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}rtbl list [all|page|last]\n"
            f"  {p}rtbl add <service_jid> <node>\n"
            f"  {p}rtbl delete/remove/del/rm <service_jid> [node]\n"
            f"  {p}rtbl refresh [service_jid] [node]\n"
            f"  {p}rtbl publish status\n"
            f"  {p}rtbl publish sync"
        )

    def _rtbl_publish_usage_text(self) -> str:
        """Return usage text for RTBL publish subcommands."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}rtbl publish status\n"
            f"  {p}rtbl publish sync"
        )

    def _config_usage_text(self) -> str:
        """Return usage text for config command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}config [all|page|last]\n"
            f"  {p}config show [all|page|last]\n"
            f"  {p}config search/find <query>\n"
            f"  {p}config diff [all|page|last]\n"
            f"  {p}config set <KEY> <value>\n"
            f"  {p}config unset <KEY>"
        )

    def _audit_usage_text(self) -> str:
        """Return usage text for audit command."""
        p = self.command_prefix
        return f"Usage: {p}audit [all|page|last|query]"

    def _ban_usage_text(self) -> str:
        """Return usage text for ban command."""
        p = self.command_prefix
        return f"Usage: {p}ban <jid|nick|*.domain.tld> [comment]"

    def _baninfo_usage_text(self) -> str:
        p = self.command_prefix
        return f"Usage: {p}baninfo <jid|nick|*.domain.tld>"

    def _history_usage_text(self) -> str:
        p = self.command_prefix
        return f"Usage: {p}history <jid|nick|*.domain.tld> [all|page|last]"

    def _banedit_usage_text(self) -> str:
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}banedit <target> reason <text>\n"
            f"  {p}banedit <target> duration <10m|2h|1d>\n"
            f"  {p}banedit <target> extend <duration>\n"
            f"  {p}banedit <target> reduce <duration>\n"
            f"  {p}banedit <target> permanent\n"
            f"  {p}banedit <target> temp <duration>\n"
            f"  {p}banedit <nick> jid <user@domain.tld>"
        )

    def _tempban_usage_text(self) -> str:
        """Return usage text for tempban command."""
        p = self.command_prefix
        return f"Usage: {p}tempban <jid|nick> <10m|2h|1d> [comment]"

    def _unban_usage_text(self) -> str:
        """Return usage text for unban command."""
        p = self.command_prefix
        return f"Usage: {p}unban <jid|nick|*.domain.tld>"

    def _banlist_usage_text(self) -> str:
        """Return usage text for banlist/blacklist command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}banlist [all|page|last]\n"
            f"  {p}banlist rtbl [all|page|last]\n"
            f"  {p}blacklist ... - alias for {p}banlist"
        )

    def _bansearch_usage_text(self) -> str:
        """Return usage text for bansearch command."""
        p = self.command_prefix
        return f"Usage: {p}bansearch <query> [all|page|last]"

    def _why_usage_text(self) -> str:
        """Return usage text for why command."""
        p = self.command_prefix
        return f"Usage: {p}why <nick|jid>"

    def _restart_usage_text(self) -> str:
        """Return usage text for restart command."""
        p = self.command_prefix
        return f"Usage: {p}restart confirm"

    def _reload_usage_text(self) -> str:
        """Return usage text for reload command."""
        p = self.command_prefix
        return f"Usage: {p}reload / {p}reloadconfig"

    def _checkupdate_usage_text(self) -> str:
        """Return usage text for checkupdate command."""
        p = self.command_prefix
        return f"Usage: {p}checkupdate / {p}updatecheck"

    def _status_usage_text(self) -> str:
        """Return usage text for status command."""
        p = self.command_prefix
        return f"Usage: {p}status"

    def _whoami_usage_text(self) -> str:
        """Return usage text for whoami command."""
        p = self.command_prefix
        return f"Usage: {p}whoami"

    def _sync_usage_text(self) -> str:
        """Return usage text for sync command."""
        p = self.command_prefix
        return f"Usage: {p}sync"

    def _syncadmins_usage_text(self) -> str:
        """Return usage text for syncadmins command."""
        p = self.command_prefix
        return f"Usage: {p}syncadmins"

    def _syncbans_usage_text(self) -> str:
        """Return usage text for syncbans command."""
        p = self.command_prefix
        return f"Usage: {p}syncbans"

    def _omemo_usage_text(self) -> str:
        """Return usage text for OMEMO command."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}omemo status\n"
            f"  {p}omemo devices\n"

            f"  {p}omemo reset [confirm]\n"
            f"  {p}omemo help"
        )
