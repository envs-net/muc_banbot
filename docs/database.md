# Database

BanBot uses SQLite for persistent state.

The database path is configured with `DB_FILE` in `config.py`.

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

CSV imports create a timestamped database backup before writing imported rows:

```text
banbot.db.backup-before-import-YYYYMMDD_HHMMSS
```

This allows recovery from bad import files or operator mistakes.

## Maintenance Notes

* Audit events are retained up to `AUDIT_LOG_RETENTION_DAYS`, capped at 365 days.
* Expired temporary bans are removed by the unban worker.
* RTBL subscription data is separate from locally applied bans.
* Applied RTBL bans in the main `bans` table use `issuer=rtbl`.
