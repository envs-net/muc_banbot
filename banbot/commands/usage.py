"""Compatibility adapters for structured BanBot command usage help."""

from typing import TYPE_CHECKING

from envs_xmpp_core.commands import render_command_help_document

from .usage_specs import USAGE_DOCUMENTS

if TYPE_CHECKING:
    from ..contracts import CommandUsageMixinHost

    class _CommandUsageMixinContract(CommandUsageMixinHost):
        pass
else:
    class _CommandUsageMixinContract:
        pass


class CommandUsageMixin(_CommandUsageMixinContract):
    def _protection_usage_text(self) -> str:
        """Return usage text for protection commands."""
        return render_command_help_document(USAGE_DOCUMENTS['protection'], self.command_prefix)

    def _report_usage_text(self) -> str:
        """Return usage text for trusted reporter command."""
        return render_command_help_document(USAGE_DOCUMENTS['report'], self.command_prefix)

    def _policy_usage_text(self) -> str:
        """Return usage text for the admin policy command."""
        return render_command_help_document(USAGE_DOCUMENTS['policy'], self.command_prefix)

    def _room_usage_text(self) -> str:
        """Return usage text for the admin room command."""
        return render_command_help_document(USAGE_DOCUMENTS['room'], self.command_prefix)

    def _room_invite_usage_text(self) -> str:
        """Return usage text for room invite commands."""
        return render_command_help_document(USAGE_DOCUMENTS['room invite'], self.command_prefix)

    def _redact_usage_text(self) -> str:
        """Return usage text for the admin redact command."""
        return render_command_help_document(USAGE_DOCUMENTS['redact'], self.command_prefix)

    def _help_usage_text(self) -> str:
        """Return usage text for help itself."""
        return render_command_help_document(USAGE_DOCUMENTS['help'], self.command_prefix)

    def _backup_usage_text(self) -> str:
        """Return usage text for backup and restore commands."""
        return render_command_help_document(USAGE_DOCUMENTS['backup'], self.command_prefix)

    def _restore_usage_text(self) -> str:
        """Return usage text for restore command."""
        return render_command_help_document(USAGE_DOCUMENTS['restore'], self.command_prefix)

    def _export_usage_text(self) -> str:
        """Return usage text for export and import commands."""
        return render_command_help_document(USAGE_DOCUMENTS['export'], self.command_prefix)

    def _import_usage_text(self) -> str:
        """Return usage text for import command."""
        return render_command_help_document(USAGE_DOCUMENTS['import'], self.command_prefix)

    def _ignore_usage_text(self) -> str:
        """Return usage text for ignore/whitelist commands."""
        return render_command_help_document(USAGE_DOCUMENTS['ignore'], self.command_prefix)

    def _rtbl_usage_text(self) -> str:
        """Return usage text for RTBL commands."""
        return render_command_help_document(USAGE_DOCUMENTS['rtbl'], self.command_prefix)

    def _rtbl_publish_usage_text(self) -> str:
        """Return usage text for RTBL publish subcommands."""
        return render_command_help_document(USAGE_DOCUMENTS['rtbl publish'], self.command_prefix)

    def _config_usage_text(self) -> str:
        """Return usage text for config command."""
        return render_command_help_document(USAGE_DOCUMENTS['config'], self.command_prefix)

    def _audit_usage_text(self) -> str:
        """Return usage text for audit command."""
        return render_command_help_document(USAGE_DOCUMENTS['audit'], self.command_prefix)

    def _ban_usage_text(self) -> str:
        """Return usage text for ban command."""
        return render_command_help_document(USAGE_DOCUMENTS['ban'], self.command_prefix)

    def _baninfo_usage_text(self) -> str:
        """Render documented command usage."""
        return render_command_help_document(USAGE_DOCUMENTS['baninfo'], self.command_prefix)

    def _history_usage_text(self) -> str:
        """Render documented command usage."""
        return render_command_help_document(USAGE_DOCUMENTS['history'], self.command_prefix)

    def _banedit_usage_text(self) -> str:
        """Render documented command usage."""
        return render_command_help_document(USAGE_DOCUMENTS['banedit'], self.command_prefix)

    def _tempban_usage_text(self) -> str:
        """Return usage text for tempban command."""
        return render_command_help_document(USAGE_DOCUMENTS['tempban'], self.command_prefix)

    def _unban_usage_text(self) -> str:
        """Return usage text for unban command."""
        return render_command_help_document(USAGE_DOCUMENTS['unban'], self.command_prefix)

    def _banlist_usage_text(self) -> str:
        """Return usage text for banlist/blacklist command."""
        return render_command_help_document(USAGE_DOCUMENTS['banlist'], self.command_prefix)

    def _bansearch_usage_text(self) -> str:
        """Return usage text for bansearch command."""
        return render_command_help_document(USAGE_DOCUMENTS['bansearch'], self.command_prefix)

    def _why_usage_text(self) -> str:
        """Return usage text for why command."""
        return render_command_help_document(USAGE_DOCUMENTS['why'], self.command_prefix)

    def _restart_usage_text(self) -> str:
        """Return usage text for restart command."""
        return render_command_help_document(USAGE_DOCUMENTS['restart'], self.command_prefix)

    def _reload_usage_text(self) -> str:
        """Return usage text for reload command."""
        return render_command_help_document(USAGE_DOCUMENTS['reload'], self.command_prefix)

    def _checkupdate_usage_text(self) -> str:
        """Return usage text for checkupdate command."""
        return render_command_help_document(USAGE_DOCUMENTS['checkupdate'], self.command_prefix)

    def _status_usage_text(self) -> str:
        """Return usage text for status command."""
        return render_command_help_document(USAGE_DOCUMENTS['status'], self.command_prefix)

    def _tasks_usage_text(self) -> str:
        """Return usage text for background task diagnostics."""
        return render_command_help_document(USAGE_DOCUMENTS['tasks'], self.command_prefix)

    def _whoami_usage_text(self) -> str:
        """Return usage text for whoami command."""
        return render_command_help_document(USAGE_DOCUMENTS['whoami'], self.command_prefix)

    def _sync_usage_text(self) -> str:
        """Return usage text for sync command."""
        return render_command_help_document(USAGE_DOCUMENTS['sync'], self.command_prefix)

    def _syncadmins_usage_text(self) -> str:
        """Return usage text for syncadmins command."""
        return render_command_help_document(USAGE_DOCUMENTS['syncadmins'], self.command_prefix)

    def _syncbans_usage_text(self) -> str:
        """Return usage text for syncbans command."""
        return render_command_help_document(USAGE_DOCUMENTS['syncbans'], self.command_prefix)

    def _omemo_usage_text(self) -> str:
        """Return usage text for OMEMO command."""
        return render_command_help_document(USAGE_DOCUMENTS['omemo'], self.command_prefix)
