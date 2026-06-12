# Database

BanBot uses SQLite for persistent state.

The database path is configured with `DB_FILE` in `config.py`. Manual database checks and compaction are documented in [Maintenance](maintenance.md#sqlite-database-maintenance).

## Main Tables

### `bans`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Internal row id |
| `target_type` | TEXT | `jid`, `nick`, or `domain` |
| `target` | TEXT | Normalized unique target key |
| `jid` | TEXT | Bare JID or wildcard domain if known |
| `nick` | TEXT | Nickname if known |
| `until` | INTEGER | Unix expiration timestamp; `0` means permanent |
| `issuer` | TEXT | Admin JID/nick, `rtbl`, or `system` |
| `comment` | TEXT | Optional reason/comment |
| `created_at` | INTEGER | Creation timestamp |
| `updated_at` | INTEGER | Last update timestamp |

### `rooms`

| Column | Type | Description |
| --- | --- | --- |
| `room` | TEXT | Protected room JID |

### `audit_log`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Internal row id |
| `created_at` | INTEGER | Event timestamp |
| `event_type` | TEXT | Event name, e.g. `ban_applied`, `unban_applied` |
| `actor` | TEXT | Admin, `rtbl`, `ignorelist`, or `system` |
| `room` | TEXT | Related room |
| `target_type` | TEXT | `jid`, `nick`, or `domain` |
| `target` | TEXT | Normalized target |
| `jid` | TEXT | Related JID |
| `nick` | TEXT | Related nick |
| `until` | INTEGER | Expiration timestamp |
| `comment` | TEXT | Reason/comment |
| `details` | TEXT | JSON metadata |

### `ignorelist`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Internal row id |
| `target` | TEXT | Protected JID or domain |
| `target_type` | TEXT | `jid` or `domain` |
| `reason` | TEXT | Optional reason |
| `added_by` | TEXT | Admin who added the entry |
| `created_at` | INTEGER | Creation timestamp |

### `redaction_index`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Internal row id |
| `room_jid` | TEXT | Protected room where the message was seen |
| `sender_jid` | TEXT | Bare JID resolved from the MUC occupant |
| `sender_nick` | TEXT | Nickname seen in the room |
| `stanza_id` | TEXT | Room-assigned XEP-0359 stanza ID used for redaction |
| `message_id` | TEXT | Optional client message id, if present |
| `created_at` | INTEGER | Time the message ID was indexed |
| `redacted_at` | INTEGER | Time redaction was attempted successfully |
| `redacted_by` | TEXT | Admin/system actor that triggered redaction |
| `redact_reason` | TEXT | Reason sent with the moderation retraction |

Message bodies are not stored.

### `room_invites`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Pending invite id shown to admins |
| `room_jid` | TEXT | Invited room JID |
| `inviter` | TEXT | Bare JID or occupant JID that invited the bot |
| `reason` | TEXT | Optional invite reason |
| `created_at` | INTEGER | Creation timestamp |

Pending room invites are persisted so they survive bot restarts. Entries are removed when accepted, declined/rejected, expired via `ROOM_INVITE_MAX_AGE_DAYS`, or cleared with `!room invite cleanup`; `!room invite cleanup expired` removes only expired entries.

### `protections`

| Column | Type | Description |
| --- | --- | --- |
| `name` | TEXT | Canonical protection name |
| `enabled` | INTEGER | Whether the protection is enabled |
| `config_json` | TEXT | JSON object containing runtime config overrides |
| `updated_at` | INTEGER | Last update timestamp |

Protection defaults live in code. This table stores only the current enabled state and configured overrides changed through `!protections`.

### `public_policy`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Single-row id, always `1` |
| `enabled` | INTEGER | Whether public `!rules`/`!policy` is enabled |
| `text` | TEXT | Policy text |
| `updated_at` | INTEGER | Last update timestamp |

## RTBL Tables

### `rtbl_subscriptions`

| Column | Type | Description |
| --- | --- | --- |
| `id` | INTEGER | Internal row id |
| `service_jid` | TEXT | PubSub service |
| `node` | TEXT | PubSub node |
| `created_at` | INTEGER | Creation timestamp |

### `rtbl_hashes`

| Column | Type | Description |
| --- | --- | --- |
| `hash` | TEXT | SHA-256 bare-JID hash |
| `service_jid` | TEXT | Source PubSub service |
| `node` | TEXT | Source PubSub node |
| `reason` | TEXT | Optional RTBL reason |
| `created_at` | INTEGER | Creation timestamp |

### `rtbl_domains`

| Column | Type | Description |
| --- | --- | --- |
| `domain` | TEXT | Plain domain from RTBL feed |
| `service_jid` | TEXT | Source PubSub service |
| `node` | TEXT | Source PubSub node |
| `reason` | TEXT | Optional RTBL reason |
| `created_at` | INTEGER | Creation timestamp |

## Backups

Managed backups are self-contained ZIP archives documented in [Backups and Restore](backups.md).

CSV imports create a managed full backup before writing imported rows that actually change the database. These safety backups use the same archive format and retention settings as manual backups.

## Maintenance Notes

For manual SQLite maintenance, including `PRAGMA integrity_check`, `PRAGMA optimize`, and `VACUUM`, see [Maintenance](maintenance.md#sqlite-database-maintenance).

* Audit events are retained up to `AUDIT_LOG_RETENTION_DAYS`, capped at 365 days.
* Expired temporary bans are removed by the unban worker.
* RTBL subscription data is separate from locally applied bans.
* Applied RTBL bans in the main `bans` table use `issuer=rtbl`.
* Redaction index cleanup is controlled by `REDACTION_INDEX_RETENTION_DAYS`; `0` keeps indexed stanza IDs indefinitely.
