# BanBot Architecture

This document describes the runtime structure of muc_banbot. It is intended for maintainers who need to debug startup, MUC joins, command routing, moderation, protections, RTBL synchronization, persistence, backups, or shutdown behavior.

## Design Overview

BanBot is one Slixmpp `ClientXMPP` application composed from focused mixins. The public runtime class remains `banbot.bot.BanBot`; most behavior is implemented in smaller modules and packages below it.

```text
muc_banbot console command
        │
        ▼
banbot.config_loader
        │  locate and import config.py
        ▼
banbot.bot.BanBot
        │  register Slixmpp plugins and event handlers
        ├── commands/*
        ├── muc.py / admin.py / occupants.py
        ├── moderation.py / sync.py / ban_queries.py
        ├── protections/*
        ├── rtbl/*
        ├── backups/*
        ├── omemo/*
        └── db.py / audit.py / redaction.py / updates.py
```

`banbot/bot.py` should stay focused on application wiring, shared runtime state, startup sequencing, and the process entrypoint. New feature logic should normally live in the module that owns that responsibility.

## Entrypoints and Configuration

### Console entrypoint

`pyproject.toml` exposes the installed command:

```text
muc_banbot -> banbot.bot:main
```

The legacy `python muc_banbot.py` startup path remains a compatibility wrapper around the same `main()` function.

The bundled systemd service sets the repository as `WorkingDirectory` and starts the console command from the virtual environment. `Restart=on-failure` restarts unexpected failures, while an administrator-requested restart exits with a dedicated non-zero code so systemd restarts the process intentionally.

### Configuration loading

`banbot.config_loader` is the single entrypoint for loading the external `config.py` module. It resolves the configured override when present and otherwise finds the local configuration from the working/source directory.

The `banbot.config` package owns:

- validation and startup-only checks
- runtime-writable settings
- secret/redacted display handling
- configuration snapshots and rollback
- `!config` presentation and mutation helpers

Runtime settings are copied onto the `BanBot` instance. Code that supports live `!config set` updates should read the instance attribute rather than re-importing a constant directly.

## Startup and Reconnect Flow

The `session_start` event calls `BanBot.start()`.

```text
XMPP session established
        │
        ▼
stop old background tasks
        │
        ▼
open/setup SQLite and load persisted state
        │
        ├── bans and indexes
        ├── protected rooms and pending invites
        ├── ignorelist and protections
        ├── policy, audit and redaction state
        └── update-notification metadata
        ▼
send presence and fetch roster
        │
        ▼
register room handlers
        │
        ▼
join admin room and protected rooms in parallel
        │
        ▼
wait for self/occupant presence
        │
        ▼
check bot affiliation and synchronize admins/bans
        │
        ▼
initialize RTBL subscriptions and publish nodes
        │
        ▼
start background workers
        │
        ▼
set vCard and send startup/update notifications
```

Reconnects reuse the same startup sequence, but preserve the process uptime and emit reconnect-specific alerts. Old background workers are cancelled before replacements are created.

## MUC Join and Presence Model

MUC reliability is split across three modules:

- `banbot.muc_join` normalizes Slixmpp's `join_muc_wait()` API and the legacy join fallback.
- `banbot.muc` owns tracked joins, self-presence events, reconnect handling, and occupant-cache updates.
- `banbot.occupants` provides the authoritative lookup for the bot's own room occupant and the shared room-status formatter.

### Tracked join flow

```text
ensure_muc_joined(room)
        │
        ├── start join_muc_wait task
        ├── wait for BanBot self-presence
        ├── learn effective server-assigned nick
        ├── settle/cancel the remaining Slixmpp waiter
        └── retry according to MUC_JOIN_RETRIES
```

Self-presence is the source of truth. The configured nick alone is not sufficient because a server can assign a different nick or retain a stale occupant after an interrupted connection.

The bot identifies its active room session using, in order:

1. the nick learned from actual self-presence
2. the authenticated bare JID in the occupant cache
3. a configured-nick fallback only for lightweight standalone mixin users

`!room rejoin <room|all>`, startup joins, `!sync`, and health-check recovery all use the same tracked join path and the configured `MUC_JOIN_TIMEOUT_SECONDS` / `MUC_JOIN_RETRIES` values. The health worker runs an immediate first cycle after startup/reconnect. Missing rooms use a bounded 60/120/240/300-second retry schedule, remaining at 300 seconds until recovery; afterward the worker returns to `HEALTH_CHECK_INTERVAL`. Successful administrative recovery also reapplies active bans to the room.

## Message and Command Flow

### Public MUC messages

```text
groupchat_message
        │
        ▼
commands.entrypoint.on_message
        │
        ├── ignore own messages
        ├── decrypt OMEMO when applicable
        ├── index message for possible redaction
        ├── run protection checks
        └── parse command prefix
        ▼
commands.router
        │
        ├── public command gate and rate limit
        ├── admin-room authorization
        └── handler dispatch
        ▼
focused command mixin / subsystem
        ▼
messaging.bot_send_message
```

Protections run before command dispatch. A triggered protection can consume the message after applying its configured action.

### Direct messages and MUC private messages

`banbot.direct_messages` handles direct-chat and MUC-PM command access. It resolves the real sender, enforces the supported read-only/admin behavior, and can redirect command output to a private response target.

### Command package

`banbot.commands.CommandMixin` composes focused command modules:

- `entrypoint.py` — groupchat event entrypoint
- `router.py` — public/admin routing, authorization, and rate limiting
- `registry.py` — admin command-to-handler mapping
- `runtime.py` — status/reload/restart runtime commands
- `moderation.py` — ban/tempban/unban command adapters
- `rooms.py` — protected-room command adapters
- `config_display.py` — `!config` commands
- `rtbl.py` / `rtbl_admin.py` — RTBL administration
- `backups.py`, `import_export.py`, `ignore.py`, `policy.py`, `omemo.py` — subsystem adapters
- `help.py` / `usage.py` — generated help and usage text

New commands should be added to the focused command module and the command constants/registry rather than expanding the top-level message handler.

## Moderation and Ban-State Flow

Manual commands, protections, RTBL matches, and synchronization eventually converge on the same moderation state.

```text
ban request
   │
   ├── normalize target and duration
   ├── ignorelist check
   ├── admin/owner protection
   ▼
ban_state_lock
   │
   ├── persist/update bans table and in-memory indexes
   ├── write audit event
   ├── acknowledge the committed ban in the admin room
   ├── apply room affiliations through XEP-0045 in parallel
   ├── announce matching auto-redaction background work
   ├── start matching auto-redaction as a tracked background task
   └── publish/retract own RTBL items when enabled
```

The administrator acknowledgement is sent as soon as the local ban is safely committed. Potentially slow room, PubSub, and bulk-redaction network operations are not allowed to delay that acknowledgement. Room enforcement has priority over auto-redaction traffic, and per-room writes remain bounded by the shared MUC write semaphore.

`banbot.moderation` owns applying and removing bans in rooms and the tempban expiry worker. `banbot.sync` reconciles persisted bans with room affiliation state after startup, reconnects, manual syncs, and room recovery. `banbot.ban_queries` serves list, search, history, details, and edit operations.

The main in-memory indexes are:

- `ban_cache`
- `ban_index_by_jid`
- `ban_index_by_nick`
- `ban_index_by_domain`
- `bot_admin_state`
- `occupants`
- `protected_rooms`

Database-backed changes and these indexes must remain consistent. Use the shared ban-state lock for mutations rather than introducing subsystem-specific locks.

## Admin and Owner Protection

`banbot.admin` protects room owners and admins from manual, nick-based, domain-based, and RTBL-triggered bans.

It uses two sources:

- live occupant affiliations from the MUC cache
- server-side owner/admin affiliation queries when permitted

Some MUC services reject affiliation-list queries for non-owners. Such rooms are remembered and fall back to the live occupant cache instead of repeatedly issuing a failing query.

## Protection Subsystem

`banbot.protections.ProtectionMixin` is composed from focused modules:

- `definitions.py` — names, defaults, aliases, allowed actions, and ordering
- `detection.py` — pure message normalization and detection helpers
- `checks.py` — join and message protection decisions
- `actions.py` — kick, ban, tempban, redact, notify, and lockdown actions
- `storage.py` — SQLite persistence of per-protection overrides
- `commands.py` — administration commands
- `notifications.py` — policy-change notifications
- `presentation.py` — shared status formatting
- `manager.py` — runtime state and subsystem composition

Protection configuration is loaded from defaults plus persisted overrides. Observe mode runs detection and reporting without executing the configured enforcement action.

Protection actions should use existing moderation, redaction, audit, and messaging helpers. They should not implement a separate ban persistence path.

## RTBL Subsystem

`banbot.rtbl.RtblMixin` composes:

- `db.py` — schema, subscriptions, and in-memory caches
- `pubsub.py` — inbound subscription, snapshot, event, and refresh handling
- `apply.py` — JID/domain matching, occupant scans, and local ban application
- `publish.py` — optional outbound JID/domain block-list nodes
- `commands.rtbl_admin` — administration commands
- `utils.py` — pure hashing, item classification, payload, and validation helpers

Inbound items are stored separately as hashed JIDs or plaintext domains. A match that is actually enforced is also persisted as a normal local ban so regular `!banlist`, `!why`, audit, and sync behavior remain consistent.

Ignorelist and admin/owner protection checks run before any RTBL action. The shared ban-state lock serializes RTBL actions with manual moderation and synchronization.

## Persistence

SQLite is the authoritative persistent store. `banbot.db.DatabaseMixin` owns connection setup and core tables; subsystems create their own tables during setup.

Important state groups include:

- bans and protected rooms
- audit records
- ignorelist entries
- pending room invites
- protection overrides
- public policy text
- redaction index
- RTBL subscriptions, JID hashes, and domains
- bot metadata used for completed-update notifications

See [database.md](database.md) for the table-level reference.

The database is opened during every startup/reconnect sequence. High-impact import, restore, backup, and state replacement operations must use the shared database/file mutation helpers.

## Concurrency and Maintenance Locks

`banbot.locks` defines the canonical lock order:

```text
maintenance_operation
        ▼
database_file_lock
        ▼
ban_state_lock
```

- `database_file_lock` protects database/config/export/backup file operations.
- `ban_state_lock` protects DB-backed ban state and in-memory indexes.
- `database_mutation_locks` acquires both in the required order and marks maintenance mode.
- Both locks support same-task reentrancy so high-level operations can call protected helpers safely.

Background RTBL work checks maintenance mode and should not race destructive restore/import operations.

MUC affiliation writes are additionally limited by `muc_write_semaphore`, and large synchronization runs use the configured batch size to avoid flooding the XMPP service.

## Background Workers

The startup sequence supervises these long-running tasks when enabled:

- tempban expiry / unban worker
- room health-check and automatic rejoin worker
- RTBL periodic refresh worker
- redaction-index cleanup worker
- remote version-check worker
- delayed reconnect task after connection loss

`stop_background_tasks()` cancels the main workers before a new startup sequence. Feature-specific tasks should be stored on the bot instance and cancelled or replaced idempotently.

## Backups, Import, and Restore

`banbot.backups.BackupMixin` is split into:

- `base.py` — paths, metadata, listing, and common helpers
- `archive.py` — ZIP creation/extraction
- `create.py` — SQLite backup creation and retention
- `verify.py` — manifest and SQLite integrity checks
- `restore.py` — guarded restore and safety backup flow
- `commands.py` — command presentation

Backup and restore operations use the database/file lock. Restore/import paths must preserve consistency between the on-disk database, the active SQLite connection, and in-memory caches.

`banbot.import_export` owns managed CSV import/export and creates safety backups before mutating imports.

## OMEMO and Messaging

OMEMO is optional. `banbot.omemo` exposes one combined mixin while keeping storage, device handling, reset, status, and core encryption behavior in separate modules.

Incoming encrypted commands are decrypted before protection/command processing. A context token records whether the reply should be encrypted. `banbot.messaging` remains the common outbound message helper so groupchat, direct-message, OMEMO, and test behavior stay consistent.

## Logging, Audit, and Redaction

- `banbot.audit` writes operational audit records on a best-effort basis.
- `banbot.redaction` indexes eligible stanza IDs, performs explicit or automatic message redaction, and verifies otherwise unconfirmed moderation requests against room MAM tombstones.
- `banbot.alerts` sends deduplicated operational alerts for selected failures and recoveries.
- `banbot.updates` checks remote versions and stores the last successfully started version for update announcements.

User-controlled JIDs, reasons, URLs, and command details should pass through the existing safety/redaction helpers before appearing in logs or audit records.

## Testing Structure

The normal pytest suite is offline and uses focused mixin/test doubles for XMPP-heavy behavior. Tests are grouped by subsystem rather than mirroring every implementation file exactly.

Testing layers include:

- unit and regression tests for mixins and command flows
- Hypothesis tests for parsing, normalization, matching, paging, and RTBL helpers
- mutation tests focused on `banbot.utils` and `banbot.rtbl.apply`
- opt-in live XMPP/Prosody and OMEMO integration contracts
- a destructive opt-in live protection smoke tool for dedicated test rooms

See [testing.md](testing.md) and [../tests/README.md](../tests/README.md).

## Where to Add New Code

Use the module that already owns the responsibility:

- process startup, shared state, event registration: `banbot.bot`
- config loading: `banbot.config_loader`
- config validation/runtime mutation: `banbot.config/*`
- MUC joins, reconnects, and presence: `banbot.muc` / `banbot.muc_join`
- bot occupant identity and room status: `banbot.occupants`
- authorization and admin protection: `banbot.admin`
- command parsing/routing: `banbot.commands/*`
- room persistence and invites: `banbot.rooms/*`
- manual moderation and tempban expiry: `banbot.moderation`
- room/ban reconciliation: `banbot.sync`
- protection definitions/checks/actions: `banbot.protections/*`
- RTBL subscribe/apply/publish behavior: `banbot.rtbl/*`
- backup and restore: `banbot.backups/*`
- DB schema/core persistence: `banbot.db`
- cross-subsystem mutation locking: `banbot.locks`
- shared outbound messages: `banbot.messaging`
- audit, alerts, redaction, or update checks: the matching focused module

Avoid adding a second persistence path, occupant-identity implementation, command router, or subsystem-specific ban lock when a shared implementation already exists.
