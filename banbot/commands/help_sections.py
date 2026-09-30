"""Declarative operator help sections; command policy stays in BanBot."""

from envs_xmpp_core.commands import CommandHelpSection

ADMIN_HELP_SECTIONS: tuple[CommandHelpSection, ...] = (
    CommandHelpSection(
        title='🛠️ Core / Runtime',
        entries=(
            '{prefix}help - show this help',
            '{prefix}status - show bot health, active rooms, and ban statistics',
            '{prefix}tasks [all|full|failed|stale|restarting|restarted|problems] - show supervised background tasks and watchdog state',
            '{prefix}config [all|page|last] / show/search/find/diff/set/unset - show/edit runtime config',
            '{prefix}reload / {prefix}reloadconfig - reload config.py at runtime',
            '{prefix}restart confirm - stop the bot so a supervisor can restart it',
            '{prefix}checkupdate / {prefix}updatecheck - check if a newer bot release is available',
            '{prefix}whoami - show your affiliation/role',
            '{prefix}audit [all|page|last|query] - show recent audit events',
        ),
    ),
    CommandHelpSection(
        title='💾 Backup / Restore',
        entries=(
            '{prefix}backup - create a full backup',
            '{prefix}backup list [all|page|last] - list full backups',
            '{prefix}backup show <filename|latest> - show backup details',
            '{prefix}backup verify <filename|latest> - verify a backup',
            '{prefix}backup delete/remove/del/rm <filename|latest> - delete a backup',
            '{prefix}restore <filename|latest> confirm - restore a full backup',
        ),
    ),
    CommandHelpSection(
        title='🏠 Rooms / Policy',
        entries=(
            '{prefix}room add/remove/delete/del/rm - manage protected rooms',
            '{prefix}room/rooms list [joined|offline|problems] [all|page|last] - list protected rooms',
            '{prefix}room rejoin <room|all> - retry selected protected-room joins',
            '{prefix}room invite list [all|page|last] - list pending room invites',
            '{prefix}room invite accept/decline/remove/delete/del/rm <id> - accept or remove a room invite',
            '{prefix}room invite cleanup [expired] - cleanup pending or expired room invites',
            '{prefix}policy / {prefix}rules show/set/clear/delete/remove/enable/disable/help/usage - manage public rules/policy text',
        ),
    ),
    CommandHelpSection(
        title='🛡️ Moderation',
        entries=(
            '{prefix}ban <jid|nick> [comment] - ban or update an existing permanent ban reason',
            '{prefix}tempban <jid|nick> <10m|2h|1d> [comment] - add/update tempban; omitted reason is preserved',
            '{prefix}unban <jid|nick> - remove ban',
            '{prefix}banedit <target> <operation> ... - edit reason, duration or ban type',
            '{prefix}redact <jid> [reason] - redact indexed messages from a JID in protected rooms',
            '{prefix}redact id <room_jid> <stanza_id> [reason] - redact one known stanza ID',
            '{prefix}redact cleanup - cleanup old redaction index entries',
        ),
    ),
    CommandHelpSection(
        title='🔎 Ban Queries',
        entries=(
            '{prefix}banlist / {prefix}blacklist [all|page|last] - show all active bans with remaining time and comments',
            '{prefix}banlist rtbl / {prefix}blacklist rtbl [all|page|last] - show RTBL hash and domain entries',
            '{prefix}bansearch <query> [all|page|last] - search bans by nick, domain, jid or RTBL reason',
            '{prefix}why <nick|jid> - show the reason and remaining time for a ban',
            '{prefix}baninfo <target> - show complete current ban metadata',
            '{prefix}history <target> [all|page|last] - show moderation history',
        ),
    ),
    CommandHelpSection(
        title='✅ Ignorelist / Whitelist',
        entries=(
            '{prefix}ignore [list] [all|page|last] - show global ignorelist (alias: {prefix}whitelist)',
            '{prefix}ignore add <jid|domain> [reason] - protect from all bans',
            '{prefix}ignore remove/delete/del/rm <jid|domain> - remove from ignorelist',
            '{prefix}whitelist [list|all|page|last|add|remove|delete|del|rm] - alias for {prefix}ignore',
        ),
    ),
    CommandHelpSection(
        title='🔄 Sync',
        entries=(
            '{prefix}sync - rejoin rooms, verify admin rights, and enforce all active bans',
            '{prefix}syncadmins - update admin list from the admin room',
            '{prefix}syncbans - sync bans from all rooms into the database and enforce them',
        ),
    ),
    CommandHelpSection(
        title='🛡️ Protections',
        entries=(
            '{prefix}protections list [all|page|last] - list available protections with enabled/disabled state',
            '{prefix}protection enable/disable <name> - enable or disable a protection',
            '{prefix}protections <name> show/config - show protection configuration',
            '{prefix}protections <name> set <key> <value> - update protection configuration',
            '{prefix}protections <name> reset - reset a protection to defaults',
            '{prefix}protections <name> observe <on|off> - toggle consequence-free observation',
            '{prefix}protections reporters add/remove/list <jid> - manage trusted reporters',
            '{prefix}report <nick|jid> [reason] - report abuse as trusted reporter',
        ),
    ),
    CommandHelpSection(
        title='🔐 OMEMO',
        entries=(
            '{prefix}omemo status - show OMEMO state',
            '{prefix}omemo devices - show admin-room recipients and storage hints',
            '{prefix}omemo reset [confirm] - rotate OMEMO storage after confirmation',
        ),
    ),
    CommandHelpSection(
        title='🛡️ RTBL',
        entries=(
            '{prefix}rtbl list [all|page|last] - show active RTBL subscriptions',
            '{prefix}rtbl add <service> <node> - subscribe to a RTBL node',
            '{prefix}rtbl delete/remove/del/rm <service> [node] - remove a RTBL subscription',
            '{prefix}rtbl refresh [service_jid] [node] - refresh RTBL subscriptions now',
            '{prefix}rtbl publish status - status of your own RTBL feed',
            '{prefix}rtbl publish sync - publish all current bans to your own feed',
        ),
    ),
    CommandHelpSection(
        title='📦 Import / Export',
        entries=(
            '{prefix}export [list|show|delete|remove|del|rm] [all|page|last] - export/list/show/delete managed CSV ban exports',
            '{prefix}import <filename> [dryrun] - import bans from a CSV file',
        ),
    ),
)
