# Command Reference

Examples assume the default command prefix `!`. If `COMMAND_PREFIX` is changed, replace `!` accordingly.

Most operator workflows start with focused help in the admin room:

```text
!help
!help <command>
!help room invite
!help rtbl publish
```

`!help <command>` is intentionally available for all admin command families, so the runtime help can be used as the first source of truth.

## Paging and Long Output

Long list commands support page numbers, `last`, and often `all`:

```text
!audit all
!banlist last
!banlist rtbl all
!bansearch all spam
!ignore list 2
!room list all
!room invite list last
!backup list all
!export list 2
```

`LIST_PAGE_SIZE` controls the default page size for paginated list output.

`!help` and `!config` default to full output, but can be configured to paginate for clients with stricter message length handling:

```python
HELP_OUTPUT_MODE = "all"       # "all" or "paginate"
CONFIG_OUTPUT_MODE = "all"     # "all" or "paginate"
```

Explicit full output remains available regardless of mode:

```text
!help all
!config all
!config show all
```

## Admin Room Commands

### Core / Runtime

| Command | Description |
| --- | --- |
| `!help [all\|page\|last]` / `!help <command>` | Shows admin help or focused command help |
| `!status` | Shows health, uptime, rooms, bans, DB, RTBL, workers, alerts, reconnect state, and all protection states |
| `!tasks [all\|failed]` | Shows supervised background workers, restart counts, terminal failures, and runtime/systemd watchdog health |
| `!config [all\|page\|last]` | Shows active configuration; secrets are hidden |
| `!config show [all\|page\|last]` | Same as `!config`, explicit show form |
| `!config search/find <query>` | Searches config option names and displayed values |
| `!config diff [all\|page\|last]` | Shows current values that differ from `config_sample.py` defaults |
| `!config set <KEY> <value>` | Updates a runtime-writable config option |
| `!config unset <KEY>` | Resets a runtime-writable option to the `config_sample.py` default |
| `!reload` / `!reloadconfig` | Validates and reloads runtime configuration |
| `!restart confirm` | Exits cleanly so a supervisor can restart the bot |
| `!checkupdate` / `!updatecheck` | Checks whether a newer release is available |
| `!whoami` | Shows affiliation, role, and permissions |
| `!audit [all\|page\|last\|query]` | Shows audit events, optionally filtered |

### Backups / Restore

| Command | Description |
| --- | --- |
| `!backup` | Creates a managed full ZIP backup archive |
| `!backup list [all\|page\|last]` | Lists managed backup archives |
| `!backup show <filename\|latest>` | Shows archive metadata and contents |
| `!backup verify <filename\|latest>` | Verifies archive structure, manifest, companion files, and SQLite integrity |
| `!backup delete/remove/del/rm <filename\|latest>` | Deletes a managed backup archive |
| `!restore <filename\|latest> confirm` | Restores from a managed backup archive after creating a safety backup |

See [Backups and Restore](backups.md).

### Rooms and Invites

| Command | Description |
| --- | --- |
| `!room` | Shows focused room command usage |
| `!room list [all\|page]` | Lists protected rooms with joined/not-joined state and the bot affiliation |
| `!room rejoin <room_jid\|all>` | Retries joining one or all protected rooms |
| `!room add <room_jid>` | Adds a protected room and stores it in the database |
| `!room remove/delete/rm/del <room_jid>` | Removes a protected room and makes the bot leave |
| `!room invite list [all\|page\|last]` | Lists pending protected-room invites |
| `!room invite accept <id>` | Accepts a pending invite and adds the room |
| `!room invite decline/remove/delete/del/rm <id>` | Declines/removes a pending invite |
| `!room invite cleanup [expired]` | Cleans all pending invites or only expired invites |

See [Rooms and Invites](rooms.md).

### Public Policy / Rules

| Command | Description |
| --- | --- |
| `!policy show` / `!rules show` | Shows configured public policy text and enabled state in the admin room |
| `!policy set <text>` | Stores public policy/rules text |
| `!policy enable` | Enables public `!rules` / `!policy` in protected rooms |
| `!policy disable` | Disables public policy output without deleting the stored text |
| `!policy clear/delete/remove` | Deletes stored public policy text |
| `!policy help/usage` | Shows policy usage and placeholder help |

See [Public Policy / Rules](policy.md).

### Moderation

| Command | Description |
| --- | --- |
| `!ban <jid\|nick\|*.domain.tld> [comment]` | Adds a permanent ban or updates the reason of an existing permanent ban |
| `!tempban <jid\|nick> <10m\|2h\|1d> [comment]` | Adds or updates a temporary ban; without a comment, the existing reason is preserved |
| `!unban <jid\|nick\|*.domain.tld>` | Removes a ban |
| `!banedit <target> reason <text>` | Updates the reason of an existing ban |
| `!banedit <target> duration <duration>` | Resets a tempban duration from now |
| `!banedit <target> extend/reduce <duration>` | Extends or shortens an existing tempban |
| `!banedit <target> permanent` | Converts an existing tempban into a permanent ban |
| `!banedit <target> temp <duration>` | Converts or resets an existing ban as a tempban |
| `!banedit <nick> jid <user@domain.tld>` | Converts a nick-only ban into a JID ban |
| `!redact <jid> [reason]` | Redacts indexed messages from a bare JID in protected rooms |
| `!redact id <room_jid> <stanza_id> [reason]` | Redacts one specific stanza ID in a protected room |
| `!redact cleanup` | Deletes old redaction index entries according to retention settings |
| `!sync` | Rejoins rooms, verifies admin rights, and enforces active bans |
| `!syncadmins` | Updates admin state from the admin room |
| `!syncbans` | Syncs bans from rooms into the database and enforces them |

### Protections

| Command | Description |
| --- | --- |
| `!protections list [all\|page\|last]` | Lists protections with 🟢 enabled / 🔴 disabled / 👁️ observe state, aliases, and `[observe]` / `[notify-only]` capability labels |
| `!protection enable <name>` | Enables a protection |
| `!protection disable <name>` | Disables a protection |
| `!protections <name> config` | Shows one protection config |
| `!protections <name> set <key> <value>` | Updates one protection config value |
| `!protections <name> reset` | Resets one protection to built-in defaults |
| `!protections <name> observe <on\|off>` | Enables or disables consequence-free observe mode for action-capable protections |
| `!protections reporters add/remove/list` | Manages trusted reporter bare JIDs |
| `!report <nick\|jid> [reason]` | Trusted reporter command when `TrustedReporters` is enabled |

Aliases shown by `!protections list all` include `flood`, `similar`, `media`, `mentions`, `wordlist`, `joinwave`, `reporters`, and `policy`.

See [Protections](protections.md).

### Ban Queries

| Command | Description |
| --- | --- |
| `!banlist [all\|page\|last]` | Shows active local bans |
| `!blacklist ...` | Alias for `!banlist ...` |
| `!banlist rtbl [all\|page\|last]` / `!blacklist rtbl [all\|page\|last]` | Shows raw RTBL hash/domain cache entries |
| `!bansearch <query> [all\|page\|last]` | Searches bans by target, issuer, comment, or RTBL reason |
| `!why <nick\|jid>` | Explains why a user is banned |
| `!baninfo <jid\|nick\|*.domain.tld>` | Shows complete current ban metadata |
| `!history <jid\|nick\|*.domain.tld> [all\|page\|last]` | Shows paginated moderation history from the audit log |

### Ignorelist / Whitelist

| Command | Description |
| --- | --- |
| `!ignore [list\|all\|page\|last]` | Shows ignored/protected exact JIDs and domains |
| `!ignore add <jid\|domain> [reason]` | Adds a target to the global ignorelist |
| `!ignore remove/delete/del/rm <jid\|domain>` | Removes a target from the global ignorelist |
| `!whitelist ...` | Alias for `!ignore ...` |

### RTBL / PubSub

| Command | Description |
| --- | --- |
| `!rtbl list [all\|page\|last]` | Lists RTBL subscriptions |
| `!rtbl add <service_jid> <node>` | Adds and fetches an RTBL subscription |
| `!rtbl delete/remove/del/rm <service_jid> [node]` | Removes one RTBL subscription or all nodes for a service |
| `!rtbl refresh [service_jid] [node]` | Refreshes all or selected subscriptions |
| `!rtbl publish status` | Shows own RTBL publish state |
| `!rtbl publish sync` | Publishes local non-RTBL bans to own RTBL feed |

See [RTBL / PubSub](rtbl.md).

### OMEMO

| Command | Description |
| --- | --- |
| `!omemo status` | Shows OMEMO readiness, storage, identity, and fallback state |
| `!omemo devices` | Shows current admin-room recipients and conservative local storage hints |
| `!omemo reset [confirm]` | Rotates local OMEMO storage after confirmation |
| `!omemo help` | Shows OMEMO usage |

See [OMEMO](omemo.md).

### Import / Export

| Command | Description |
| --- | --- |
| `!export` | Creates a managed CSV ban export |
| `!export list [all\|page\|last]` | Lists managed CSV exports |
| `!export show <filename\|latest>` | Shows export file details |
| `!export delete/remove/del/rm <filename\|latest>` | Deletes a managed CSV export |
| `!import <filename> [dryrun]` | Imports a managed CSV export, optionally without changes |

See [Import / Export](import-export.md).

## Protected Room Public Commands

When `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS=True`, users in protected rooms can use selected read-only commands:

| Command | Description |
| --- | --- |
| `!help` | Shows public command help |
| `!whoami` | Shows the sender's visible room role/affiliation information |
| `!banlist` / `!blacklist` | Shows active local bans |
| `!why <nick\|jid>` | Shows why a target is banned |
| `!rules` / `!policy` | Shows public policy text when configured and enabled |
| `!report <nick\|jid> [reason]` | Reports abuse when `TrustedReporters` is enabled and the sender is trusted |

Public commands are rate-limited with `PUBLIC_COMMAND_RATE_LIMIT_WINDOW` and `PUBLIC_COMMAND_RATE_LIMIT_MAX`.

## Direct Messages / MUC PMs

When `ALLOW_ADMIN_COMMANDS_IN_DMS=True`, admins may use selected read-only admin commands via direct messages or MUC PMs. Mutating commands remain restricted to the admin room.

Allowed read-only DM/MUC-PM commands:

```text
!help [all|page|last]
!help <command>
!config [all|page|last]
!config show [all|page|last]
!status
!tasks [all|failed]
!omemo status
!omemo devices
!omemo help
!checkupdate
!updatecheck
!banlist [all|page|last]
!banlist rtbl [all|page|last]
!bansearch <query> [all|page|last]
!baninfo <jid|nick|*.domain.tld>
!history <jid|nick|*.domain.tld> [all|page|last]
!why <nick|jid>
!room list [all|page]
!room invite list [all|page|last]
!ignore [list] [all|page|last]
!whitelist [list] [all|page|last]
!rtbl list [all|page|last]
!audit [all|page|last|query]
```

`!config set`, `!config unset`, backup/restore, export/import, moderation, room mutation, RTBL mutation, and ignorelist mutation commands must be run in the admin room for auditability and safety.
