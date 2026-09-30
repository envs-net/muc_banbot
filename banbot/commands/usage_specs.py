"""Structured, permission-neutral BanBot command usage specifications.

The specs are documentation contracts only. Runtime command routing and
authorization remain in their existing BanBot modules.
"""

from envs_xmpp_core.commands import (
    CommandExample,
    CommandHelpDocument,
    CommandSpec,
    SubcommandSpec,
)

USAGE_DOCUMENTS: dict[str, CommandHelpDocument] = {
    'protection': CommandHelpDocument(
        command=CommandSpec(
            name='protection',
            subcommands=(
                SubcommandSpec('list', '{prefix}protections list [all|page|last]', ""),
                SubcommandSpec('enable', '{prefix}protection enable <name>', ""),
                SubcommandSpec('disable', '{prefix}protection disable <name>', ""),
                SubcommandSpec('<name>', '{prefix}protections <name> config/show', ""),
                SubcommandSpec('<name>', '{prefix}protections <name> set <key> <value>', ""),
                SubcommandSpec('<name>', '{prefix}protections <name> reset', ""),
                SubcommandSpec('<name>', '{prefix}protections <name> observe <on|off>', ""),
                SubcommandSpec('reporters', '{prefix}protections reporters add/remove/list <jid>', ""),
            ),
            examples=(
                CommandExample('{prefix}protection enable FloodSpamProtection'),
                CommandExample('{prefix}protections MentionLimitProtection set max_mentions 5'),
                CommandExample('{prefix}protections FloodSpamProtection set tempban_seconds 1h'),
                CommandExample('{prefix}protections reporters add alice@example.org'),
            ),
        ),
    ),
    'report': CommandHelpDocument(
        command=CommandSpec(
            name='report',
            subcommands=(
                SubcommandSpec('<nick|jid>', '{prefix}report <nick|jid> [reason]', ""),
            ),
        ),
        notes='Reports only count when TrustedReporters is enabled and the sender JID is configured as trusted.',
    ),
    'policy': CommandHelpDocument(
        command=CommandSpec(
            name='policy',
            subcommands=(
                SubcommandSpec('show', '{prefix}policy show', ""),
                SubcommandSpec('set', '{prefix}policy set <text>', ""),
                SubcommandSpec('enable', '{prefix}policy enable', ""),
                SubcommandSpec('disable', '{prefix}policy disable', ""),
                SubcommandSpec('clear', '{prefix}policy clear/delete/remove', ""),
                SubcommandSpec('help', '{prefix}policy help/usage', ""),
            ),
        ),
        notes='Supported placeholders:\n  {prefix}, {room}, {room_count}, {admin_room}, {bot_name}\nUse literal \\n for line breaks.',
    ),
    'room': CommandHelpDocument(
        command=CommandSpec(
            name='room',
            subcommands=(
                SubcommandSpec('list', '{prefix}room/rooms list [joined|offline|problems] [all|page|last]', ""),
                SubcommandSpec('add', '{prefix}room add <room_jid>', ""),
                SubcommandSpec('rejoin', '{prefix}room rejoin <room_jid|all>', ""),
                SubcommandSpec('remove', '{prefix}room remove/delete/rm/del <room_jid>', ""),
                SubcommandSpec('invite', '{prefix}room invite list [all|page|last]', ""),
                SubcommandSpec('invite', '{prefix}room invite accept <id>', ""),
                SubcommandSpec('invite', '{prefix}room invite decline/remove/delete/del/rm <id>', ""),
                SubcommandSpec('invite', '{prefix}room invite cleanup [expired]', ""),
            ),
        ),
    ),
    'room invite': CommandHelpDocument(
        command=CommandSpec(
            name='room invite',
            subcommands=(
                SubcommandSpec('invite', '{prefix}room invite list [all|page|last]', ""),
                SubcommandSpec('invite', '{prefix}room invite accept <id>', ""),
                SubcommandSpec('invite', '{prefix}room invite decline/remove/delete/del/rm <id>', ""),
                SubcommandSpec('invite', '{prefix}room invite cleanup [expired]', ""),
            ),
        ),
    ),
    'redact': CommandHelpDocument(
        command=CommandSpec(
            name='redact',
            subcommands=(
                SubcommandSpec('<jid>', '{prefix}redact <jid> [reason]', ""),
                SubcommandSpec('id', '{prefix}redact id <room_jid> <stanza_id> [reason]', ""),
                SubcommandSpec('cleanup', '{prefix}redact cleanup', ""),
            ),
        ),
    ),
    'help': CommandHelpDocument(
        command=CommandSpec(
            name='help',
            subcommands=(
                SubcommandSpec('[all|page|last]', '{prefix}help [all|page|last]', ""),
                SubcommandSpec('<command>', '{prefix}help <command>', ""),
            ),
            examples=(
                CommandExample('{prefix}help room'),
                CommandExample('{prefix}help redact'),
                CommandExample('{prefix}help backup'),
                CommandExample('{prefix}help room invite'),
                CommandExample('{prefix}help rtbl publish'),
            ),
        ),
        trailing_newlines=2,
    ),
    'backup': CommandHelpDocument(
        command=CommandSpec(
            name='backup',
            subcommands=(
                SubcommandSpec('backup', '{prefix}backup', ""),
                SubcommandSpec('list', '{prefix}backup list [all|page|last]', ""),
                SubcommandSpec('show', '{prefix}backup show <filename|latest>', ""),
                SubcommandSpec('verify', '{prefix}backup verify <filename|latest>', ""),
                SubcommandSpec('delete', '{prefix}backup delete/remove/del/rm <filename|latest>', ""),
                SubcommandSpec('<filename|latest>', '{prefix}restore <filename|latest> confirm', ""),
            ),
        ),
    ),
    'restore': CommandHelpDocument(
        command=CommandSpec(
            name='restore',
            subcommands=(
                SubcommandSpec('<filename|latest>', '{prefix}restore <filename|latest> confirm', ""),
            ),
        ),
        notes='Restores a full backup. The confirm argument is required intentionally.',
    ),
    'export': CommandHelpDocument(
        command=CommandSpec(
            name='export',
            subcommands=(
                SubcommandSpec('export', '{prefix}export', ""),
                SubcommandSpec('list', '{prefix}export list [all|page|last]', ""),
                SubcommandSpec('show', '{prefix}export show <filename|latest>', ""),
                SubcommandSpec('delete', '{prefix}export delete/remove/del/rm <filename|latest>', ""),
                SubcommandSpec('<filename>', '{prefix}import <filename> [dryrun]', ""),
            ),
        ),
    ),
    'import': CommandHelpDocument(
        command=CommandSpec(
            name='import',
            subcommands=(
                SubcommandSpec('<filename>', '{prefix}import <filename> [dryrun]', ""),
            ),
        ),
        notes='Use dryrun/dry-run/check to validate an import without changing the database.',
    ),
    'ignore': CommandHelpDocument(
        command=CommandSpec(
            name='ignore',
            subcommands=(
                SubcommandSpec('[list]', '{prefix}ignore [list] [all|page|last]', ""),
                SubcommandSpec('add', '{prefix}ignore add <jid|domain> [reason]', ""),
                SubcommandSpec('remove', '{prefix}ignore remove/delete/del/rm <jid|domain>', ""),
                SubcommandSpec('...', '{prefix}whitelist ... - alias for {prefix}ignore', ""),
            ),
        ),
    ),
    'rtbl': CommandHelpDocument(
        command=CommandSpec(
            name='rtbl',
            subcommands=(
                SubcommandSpec('list', '{prefix}rtbl list [all|page|last]', ""),
                SubcommandSpec('add', '{prefix}rtbl add <service_jid> <node>', ""),
                SubcommandSpec('delete', '{prefix}rtbl delete/remove/del/rm <service_jid> [node]', ""),
                SubcommandSpec('refresh', '{prefix}rtbl refresh [service_jid] [node]', ""),
                SubcommandSpec('publish', '{prefix}rtbl publish status', ""),
                SubcommandSpec('publish', '{prefix}rtbl publish sync', ""),
            ),
        ),
    ),
    'rtbl publish': CommandHelpDocument(
        command=CommandSpec(
            name='rtbl publish',
            subcommands=(
                SubcommandSpec('publish', '{prefix}rtbl publish status', ""),
                SubcommandSpec('publish', '{prefix}rtbl publish sync', ""),
            ),
        ),
    ),
    'config': CommandHelpDocument(
        command=CommandSpec(
            name='config',
            subcommands=(
                SubcommandSpec('[all|page|last]', '{prefix}config [all|page|last]', ""),
                SubcommandSpec('show', '{prefix}config show [all|page|last]', ""),
                SubcommandSpec('search', '{prefix}config search/find <query>', ""),
                SubcommandSpec('diff', '{prefix}config diff [all|page|last]', ""),
                SubcommandSpec('set', '{prefix}config set <KEY> <value>', ""),
                SubcommandSpec('unset', '{prefix}config unset <KEY>', ""),
            ),
        ),
    ),
    'audit': CommandHelpDocument(
        command=CommandSpec(
            name='audit',
            usage='{prefix}audit [all|page|last|query]',
        ),
        inline=True,
    ),
    'ban': CommandHelpDocument(
        command=CommandSpec(
            name='ban',
            usage='{prefix}ban <jid|nick|*.domain.tld> [comment]',
        ),
        inline=True,
    ),
    'baninfo': CommandHelpDocument(
        command=CommandSpec(
            name='baninfo',
            usage='{prefix}baninfo <jid|nick|*.domain.tld>',
        ),
        inline=True,
    ),
    'history': CommandHelpDocument(
        command=CommandSpec(
            name='history',
            usage='{prefix}history <jid|nick|*.domain.tld> [all|page|last]',
        ),
        inline=True,
    ),
    'banedit': CommandHelpDocument(
        command=CommandSpec(
            name='banedit',
            subcommands=(
                SubcommandSpec('<target>', '{prefix}banedit <target> reason <text>', ""),
                SubcommandSpec('<target>', '{prefix}banedit <target> duration <10m|2h|1d>', ""),
                SubcommandSpec('<target>', '{prefix}banedit <target> extend <duration>', ""),
                SubcommandSpec('<target>', '{prefix}banedit <target> reduce <duration>', ""),
                SubcommandSpec('<target>', '{prefix}banedit <target> permanent', ""),
                SubcommandSpec('<target>', '{prefix}banedit <target> temp <duration>', ""),
                SubcommandSpec('<nick>', '{prefix}banedit <nick> jid <user@domain.tld>', ""),
            ),
        ),
    ),
    'tempban': CommandHelpDocument(
        command=CommandSpec(
            name='tempban',
            usage='{prefix}tempban <jid|nick> <10m|2h|1d> [comment]',
        ),
        inline=True,
    ),
    'unban': CommandHelpDocument(
        command=CommandSpec(
            name='unban',
            usage='{prefix}unban <jid|nick|domain.tld|*.domain.tld>',
        ),
        inline=True,
    ),
    'banlist': CommandHelpDocument(
        command=CommandSpec(
            name='banlist',
            subcommands=(
                SubcommandSpec('[all|page|last]', '{prefix}banlist [all|page|last]', ""),
                SubcommandSpec('rtbl', '{prefix}banlist rtbl [all|page|last]', ""),
                SubcommandSpec('...', '{prefix}blacklist ... - alias for {prefix}banlist', ""),
            ),
        ),
    ),
    'bansearch': CommandHelpDocument(
        command=CommandSpec(
            name='bansearch',
            usage='{prefix}bansearch <query> [all|page|last]',
        ),
        inline=True,
    ),
    'why': CommandHelpDocument(
        command=CommandSpec(
            name='why',
            usage='{prefix}why <nick|jid>',
        ),
        inline=True,
    ),
    'restart': CommandHelpDocument(
        command=CommandSpec(
            name='restart',
            usage='{prefix}restart confirm',
        ),
        inline=True,
    ),
    'reload': CommandHelpDocument(
        command=CommandSpec(
            name='reload',
            usage='{prefix}reload / {prefix}reloadconfig',
        ),
        inline=True,
    ),
    'checkupdate': CommandHelpDocument(
        command=CommandSpec(
            name='checkupdate',
            usage='{prefix}checkupdate / {prefix}updatecheck',
        ),
        inline=True,
    ),
    'status': CommandHelpDocument(
        command=CommandSpec(
            name='status',
            usage='{prefix}status [full]',
        ),
        inline=True,
    ),
    'tasks': CommandHelpDocument(
        command=CommandSpec(
            name='tasks',
            subcommands=(
                SubcommandSpec('tasks', '{prefix}tasks', ""),
                SubcommandSpec('[all|full|failed|stale|restarting|restarted|problems|page|last]', '{prefix}tasks [all|full|failed|stale|restarting|restarted|problems|page|last]', ""),
                SubcommandSpec('scope', '{prefix}tasks scope <name> [all|page|last]', ""),
                SubcommandSpec('show', '{prefix}tasks show <scope>/<task>', ""),
            ),
        ),
    ),
    'whoami': CommandHelpDocument(
        command=CommandSpec(
            name='whoami',
            usage='{prefix}whoami',
        ),
        inline=True,
    ),
    'sync': CommandHelpDocument(
        command=CommandSpec(
            name='sync',
            usage='{prefix}sync',
        ),
        inline=True,
    ),
    'syncadmins': CommandHelpDocument(
        command=CommandSpec(
            name='syncadmins',
            usage='{prefix}syncadmins',
        ),
        inline=True,
    ),
    'syncbans': CommandHelpDocument(
        command=CommandSpec(
            name='syncbans',
            usage='{prefix}syncbans',
        ),
        inline=True,
    ),
    'omemo': CommandHelpDocument(
        command=CommandSpec(
            name='omemo',
            subcommands=(
                SubcommandSpec('status', '{prefix}omemo status', ""),
                SubcommandSpec('devices', '{prefix}omemo devices', ""),
                SubcommandSpec('reset', '{prefix}omemo reset [confirm]', ""),
                SubcommandSpec('help', '{prefix}omemo help', ""),
            ),
        ),
    ),
}

