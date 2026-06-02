"""Top-level admin command dispatch registry."""

# Values are method names on CommandMixin.  They all use the same call signature:
# ``await handler(room, nick, args, cmd)``.
ADMIN_COMMAND_HANDLERS = {
    "backup": "_dispatch_backup_command",
    "restore": "_dispatch_restore_command",
    "omemo": "_dispatch_omemo_command",
    "room": "_dispatch_room_command",
    "ban": "_dispatch_ban_command",
    "tempban": "_dispatch_tempban_command",
    "unban": "_dispatch_unban_command",
    "bansearch": "_dispatch_bansearch_command",
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
}
