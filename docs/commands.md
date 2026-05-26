# Command Reference

Examples assume the default command prefix `!`. If `COMMAND_PREFIX` is changed, replace `!` accordingly.

## Paging

Several commands support pagination. Use a page number, `last`, or `all`:

```text
!audit
!audit 2
!audit last
!audit all

!banlist all
!blacklist all
!banlist rtbl all
!blacklist rtbl all
!bansearch all spam
!ignore list all
!whitelist all
!room list all
!room invite list all
```

`all` disables paging and prints the complete result set. Existing page and `last` syntax remain unchanged.

## Admin Room Commands

| Command | Description | Example |
| --- | --- | --- |
| `!help` | Shows admin help | `!help` |
| `!config` | Shows current bot configuration | `!config` |
| `!reload` / `!reloadconfig` | Reloads runtime config safely | `!reload` |
| `!restart` / `!restart confirm` | Shows restart confirmation / exits cleanly so a supervisor can restart the bot | `!restart confirm` |
| `!status` | Shows health, rooms, uptime, bans, DB, RTBL, and workers | `!status` |
| `!checkupdate` / `!updatecheck` | Checks whether a newer GitHub release is available | `!updatecheck` |
| `!whoami` | Shows affiliation, role, and permissions | `!whoami` |

### Reload and Restart

`!reload` is a short alias for `!reloadconfig` and reloads runtime-reloadable settings from `config.py`. Startup-only settings still require a process restart.

`!restart` is guarded and requires explicit confirmation:

```text
!restart
!restart confirm
```

When confirmed, BanBot sends a final admin-room message, flushes pending redaction-index writes, stops background tasks, disconnects, and exits with status code `0`. When the service is managed by systemd or another supervisor with restart enabled, the supervisor starts the bot again.

### Admin Direct Messages

BanBot accepts a small read-only admin command subset in direct messages and MUC PMs. Mutating commands still require the admin room for auditability and safety.

Allowed DM commands for admins:

```text
!config
!status
!banlist / !blacklist [all|page|last]
!banlist / !blacklist rtbl [all|page|last]
!room list [all|page|last]
!room invite list [all|page|last]
!ignore / !ignore list [all|page|last]
!whitelist / !whitelist list [all|page|last]
!rtbl list
!audit [all|page|last|query]
```

All other admin commands, especially ban, unban, room changes, RTBL changes, reload/restart, policy changes, and redaction, must be run in the admin room.

## Rooms and Sync

| Command | Description | Example |
| --- | --- | --- |
| `!room add <room>` | Adds a protected room and stores it in the DB | `!room add secret@conference.example.org` |
| `!room remove <room>` | Removes a protected room and makes the bot leave | `!room remove secret@conference.example.org` |
| `!room list [all/page]` | Lists protected rooms | `!room list all` |
| `!room invite list [all/page/last]` | Lists pending protected-room invites | `!room invite list all` |
| `!room invite accept <id>` | Accepts a pending invite and adds the room | `!room invite accept 3` |
| `!room invite decline <id>` | Declines a pending invite | `!room invite decline 3` |
| `!room invite cleanup` | Deletes all pending protected-room invites | `!room invite cleanup` |
| `!sync` | Rejoins rooms, verifies rights, applies missing active bans | `!sync` |
| `!syncadmins` | Updates admins from the admin room | `!syncadmins` |
| `!syncbans` | Reads room outcasts into the DB and reapplies active bans | `!syncbans` |

### Sync differences

* `!sync` is faster and applies only bans that are missing in rooms.
* `!syncbans` is comprehensive: it also adopts orphan room outcasts into the DB and removes expired tempban outcasts.
* `sync_bans_startup()` runs internally on startup.

### Protected-Room Invite Workflow

When `ROOM_INVITES_ENABLED=True`, BanBot can be invited to potential protected rooms. Invites are announced in the admin room and stay pending until an admin accepts or declines them.

The admin-room invite message includes the room JID, inviter JID, optional invite reason, and the accept/decline commands. Duplicate invites from the same inviter for the same room are ignored.

Pending invites are stored in SQLite and survive bot restarts. They are removed only when accepted, declined/rejected, or cleared with `!room invite cleanup`.

Accepting an invite uses the normal `!room add` flow, including room JID validation, DB persistence, joining the room, ban sync, and RTBL occupant checks.

## Ban Management

| Command | Description | Example |
| --- | --- | --- |
| `!ban <jid/nick/domain> [comment]` | Bans a JID, nick, or wildcard domain | `!ban alice@example.org spam` / `!ban *.evil.org` |
| `!tempban <jid/nick> <duration> [comment]` | Adds a temporary ban | `!tempban bob 10m rude behavior` |
| `!unban <jid/nick/domain>` | Removes a ban | `!unban bob` / `!unban *.evil.org` |
| `!banlist` / `!blacklist [all/page/last]` | Shows active bans | `!blacklist all` |
| `!banlist` / `!blacklist rtbl [all/page/last]` | Shows raw RTBL hashes/domains | `!blacklist rtbl all` |
| `!bansearch [all] <query>` | Searches target, JID, nick, domain, issuer, comment, and RTBL reason | `!bansearch all reason:abuse` |
| `!why <nick/jid>` | Shows reason and remaining time; admin output includes recent audit history | `!why alice` |
| `!audit [all/page/last/query]` | Shows audit events, optionally filtered | `!audit all alice` |
| `!redact <jid> [reason]` | Redacts all indexed messages from a bare JID in protected rooms | `!redact spammer@example.org spam` |
| `!redact id <room_jid> <stanza_id> [reason]` | Redacts one specific stanza ID in a protected room | `!redact id room@conference.example.org abc123 spam` |
| `!redact cleanup` | Deletes old redaction index entries according to retention settings | `!redact cleanup` |

### Redaction notes

Redaction only works for messages whose room-assigned stanza IDs are known to BanBot. BanBot indexes live messages and any join history it receives after `REDACTION_ENABLED=True`. It stores metadata only, not message bodies.

### Durations

Temporary bans support duration suffixes:

```text
10s
10m
2h
1d
```

`MAX_TEMPBAN_DAYS` limits the maximum allowed duration.

## Ignorelist / Whitelist

`!whitelist` is an alias for `!ignore`. Without arguments, both commands show the current list.

| Command | Description | Example |
| --- | --- | --- |
| `!ignore` | Shows ignorelist entries | `!ignore` |
| `!ignore list [all/page/last]` | Shows ignorelist entries | `!ignore list all` |
| `!ignore all` | Alias for full list output | `!ignore all` |
| `!ignore add <jid/domain> [reason]` | Adds a protected exact JID or wildcard domain | `!ignore add alice@example.org trusted user` |
| `!ignore remove <jid/domain>` | Removes an entry | `!ignore remove alice@example.org` |
| `!whitelist` | Alias for list | `!whitelist` |
| `!whitelist list [all/page/last]` | Alias for list | `!whitelist list all` |
| `!whitelist all` | Alias for full list output | `!whitelist all` |
| `!whitelist add/remove ...` | Alias for add/remove | `!whitelist add *.example.org local domain` |

Exact JID entries protect that JID from all bans. Domain entries protect against domain-based bans and RTBL domain matches, but do not block explicit manual JID bans on that domain.

## RTBL Commands

| Command | Description | Example |
| --- | --- | --- |
| `!rtbl list` | Shows active subscriptions and publish-feed counts | `!rtbl list` |
| `!rtbl add <service> <node>` | Subscribes to an RTBL PubSub node | `!rtbl add xmppbl.org muc_bans_sha256` |
| `!rtbl delete <service> [node]` | Removes one or all subscriptions for a service | `!rtbl delete xmppbl.org muc_bans_sha256` |
| `!rtbl refresh [service] [node]` | Refreshes all, one service, or one node | `!rtbl refresh xmppbl.org muc_bans_sha256` |
| `!rtbl publish status` | Shows own publish-feed status and local publish counts | `!rtbl publish status` |
| `!rtbl publish sync` | Publishes current local non-RTBL bans | `!rtbl publish sync` |

See [RTBL](rtbl.md) for behavior details.

## Public Policy Commands

Admin-room management:

| Command | Description |
| --- | --- |
| `!policy show` / `!rules show` | Shows configured policy text and enable state |
| `!policy set <text>` / `!rules set <text>` | Sets and enables policy text; use literal `\n` for line breaks |
| `!policy clear` / `!rules clear` | Clears and disables policy text |
| `!policy enable` / `!rules enable` | Enables protected-room `!rules` / `!policy` output |
| `!policy disable` / `!rules disable` | Disables public policy output without deleting text |

Protected-room public commands:

```text
!rules
!policy
```

Placeholders supported in policy text:

* `{prefix}` - Current command prefix
* `{room}` - Current room JID
* `{room_count}` - Number of protected rooms
* `{admin_room}` - Admin room JID
* `{bot_name}` - Bot nickname/name

## Import / Export

| Command | Description |
| --- | --- |
| `!export` | Exports all bans to `bans_export_TIMESTAMP.csv` |
| `!import <file>` | Imports bans from CSV with validation and pre-import DB backup |

See [Import / Export](import-export.md).

## Public Protected-Room Commands

Public commands are restricted and rate-limited in protected rooms:

| Command | Description |
| --- | --- |
| `!help` | Shows restricted help |
| `!whoami` | Shows affiliation/role/permissions |
| `!banlist` / `!blacklist [all/page/last]` | Shows active temporary bans if enabled |
| `!why <jid/nick>` | Shows reason and remaining time for a ban |
| `!rules` / `!policy` | Shows public moderation policy if configured |

Visibility rules:

* Permanent bans are only shown in the admin room.
* Protected-room banlists show only temporary bans. `!blacklist` is an alias for `!banlist`.
* JIDs and admin issuers are anonymized in protected rooms.
* RTBL bans are shown as `by rtbl`.
* Admin-room use is not rate-limited.
