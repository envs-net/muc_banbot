"""Top-level admin command dispatch registry."""

from typing import Final, Literal

from .constants import ADMIN_COMMANDS

AdminCommandHandlerName = Literal[
    "_dispatch_backup_command",
    "_dispatch_restore_command",
    "_dispatch_omemo_command",
    "_dispatch_room_command",
    "_dispatch_ban_command",
    "_dispatch_tempban_command",
    "_dispatch_unban_command",
    "_dispatch_bansearch_command",
    "_dispatch_baninfo_command",
    "_dispatch_history_command",
    "_dispatch_banedit_command",
    "_dispatch_redact_command",
    "_dispatch_sync_command",
    "_dispatch_syncadmins_command",
    "_dispatch_syncbans_command",
    "_dispatch_audit_command",
    "_dispatch_export_command",
    "_dispatch_import_command",
    "_dispatch_rtbl_command",
    "_dispatch_ignore_command",
    "_dispatch_policy_command",
    "_dispatch_protections_command",
]

RUNTIME_ADMIN_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "config",
        "reload",
        "reloadconfig",
        "restart",
        "status",
        "tasks",
        "checkupdate",
        "updatecheck",
    }
)

# Values are method names on CommandMixin.  They all use the same call signature:
# ``await handler(room, nick, args, cmd)``.
ADMIN_COMMAND_HANDLERS: Final[dict[str, AdminCommandHandlerName]] = {
    "backup": "_dispatch_backup_command",
    "restore": "_dispatch_restore_command",
    "omemo": "_dispatch_omemo_command",
    "room": "_dispatch_room_command",
    "rooms": "_dispatch_room_command",
    "ban": "_dispatch_ban_command",
    "tempban": "_dispatch_tempban_command",
    "unban": "_dispatch_unban_command",
    "bansearch": "_dispatch_bansearch_command",
    "baninfo": "_dispatch_baninfo_command",
    "history": "_dispatch_history_command",
    "banedit": "_dispatch_banedit_command",
    "redact": "_dispatch_redact_command",
    "sync": "_dispatch_sync_command",
    "syncadmins": "_dispatch_syncadmins_command",
    "syncbans": "_dispatch_syncbans_command",
    "audit": "_dispatch_audit_command",
    "export": "_dispatch_export_command",
    "import": "_dispatch_import_command",
    "rtbl": "_dispatch_rtbl_command",
    "ignore": "_dispatch_ignore_command",
    "whitelist": "_dispatch_ignore_command",
    "policy": "_dispatch_policy_command",
    "rules": "_dispatch_policy_command",
    "protection": "_dispatch_protections_command",
    "protections": "_dispatch_protections_command",
}


def _validate_admin_command_registry() -> None:
    """Fail fast when recognized admin commands are not routed exactly once."""
    registered = set(ADMIN_COMMAND_HANDLERS)
    overlap = registered & RUNTIME_ADMIN_COMMANDS
    missing = ADMIN_COMMANDS - registered - RUNTIME_ADMIN_COMMANDS
    extra = registered - ADMIN_COMMANDS
    if overlap or missing or extra:
        details = []
        if overlap:
            details.append(f"double-routed={sorted(overlap)}")
        if missing:
            details.append(f"missing={sorted(missing)}")
        if extra:
            details.append(f"unknown={sorted(extra)}")
        raise RuntimeError("Invalid admin command registry: " + ", ".join(details))


_validate_admin_command_registry()
