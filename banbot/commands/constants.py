"""Command name constants for BanBot command dispatch."""

# PUBLIC_COMMANDS is also used for public-room rate limits.
PUBLIC_COMMANDS = {"help", "whoami", "banlist", "blacklist", "why", "rules", "policy", "report"}

ADMIN_COMMANDS = {
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
    "protection",
    "protections",
}
