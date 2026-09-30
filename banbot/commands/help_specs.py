"""Read-only admin help metadata, independent of handler dispatch and permissions."""

from __future__ import annotations

from envs_xmpp_core.commands import CommandSpec, resolve_help_topic

# Map documented help subjects to existing bot-specific formatting handlers.
HELP_TOPIC_METHODS: dict[str, str] = {
    'help': '_help_usage_text',
    'room': '_room_usage_text',
    'room invite': '_room_invite_usage_text',
    'redact': '_redact_usage_text',
    'policy': '_policy_usage_text',
    'backup': '_backup_usage_text',
    'restore': '_restore_usage_text',
    'export': '_export_usage_text',
    'import': '_import_usage_text',
    'rtbl': '_rtbl_usage_text',
    'rtbl publish': '_rtbl_publish_usage_text',
    'ignore': '_ignore_usage_text',
    'config': '_config_usage_text',
    'audit': '_audit_usage_text',
    'ban': '_ban_usage_text',
    'tempban': '_tempban_usage_text',
    'unban': '_unban_usage_text',
    'banlist': '_banlist_usage_text',
    'bansearch': '_bansearch_usage_text',
    'baninfo': '_baninfo_usage_text',
    'history': '_history_usage_text',
    'banedit': '_banedit_usage_text',
    'why': '_why_usage_text',
    'restart': '_restart_usage_text',
    'reload': '_reload_usage_text',
    'checkupdate': '_checkupdate_usage_text',
    'status': '_status_usage_text',
    'tasks': '_tasks_usage_text',
    'whoami': '_whoami_usage_text',
    'sync': '_sync_usage_text',
    'syncadmins': '_syncadmins_usage_text',
    'syncbans': '_syncbans_usage_text',
    'omemo': '_omemo_usage_text',
    'protection': '_protection_usage_text',
    'protections': '_protection_usage_text',
    'report': '_report_usage_text',
}

# Only presentation aliases are mapped here; runtime routing stays in BanBot.
HELP_TOPIC_ALIASES: dict[str, str] = {
    'blacklist': 'banlist',
    'rooms': 'room',
    'rules': 'policy',
    'whitelist': 'ignore',
    'reloadconfig': 'reload',
    'updatecheck': 'checkupdate',
    'del': 'delete',
    'rm': 'remove',
    'room invites': 'room invite',
    'invite': 'room invite',
    'invites': 'room invite',
    'rtbl pub': 'rtbl publish',
}

HELP_COMMAND_SPECS: tuple[CommandSpec[object], ...] = tuple(
    CommandSpec(
        name=name,
        aliases=tuple(alias for alias, target in HELP_TOPIC_ALIASES.items() if target == name),
        context="admin room",
    )
    for name in HELP_TOPIC_METHODS
)
HELP_COMMANDS_BY_NAME = {spec.name: spec for spec in HELP_COMMAND_SPECS}


def find_help_command(topic: str | list[str]) -> CommandSpec[object] | None:
    """Resolve a documented topic or alias to its immutable command spec."""
    return HELP_COMMANDS_BY_NAME.get(resolve_help_topic(topic, HELP_TOPIC_ALIASES))
