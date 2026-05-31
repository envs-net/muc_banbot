# Import / Export

BanBot supports CSV import/export for backup, migration, and batch operations.

## Export

```text
!export
```

Exports all current bans to:

```text
bans_export_YYYYMMDD_HHMMSS.csv
```

## CSV Format

```csv
jid,nick,until,issuer,comment
alice@example.org,Alice,0,admin@example.org,spamming
bob@example.org,Bob,1712923200,mod@example.org,rude behavior
```

`until=0` means a permanent ban. Non-zero values are Unix timestamps.

## Import

```text
!import bans_export_20240412_120000.csv
```

Import behavior:

* Validates CSV headers and rows
* Validates JID format
* Validates timestamps
* Creates a database backup before writing
* Handles duplicates intelligently
* Reports invalid rows with reasons
* Uses all-or-nothing database updates where possible

Example response:

```text
📥 Import Results:
✅ Successful: 42
⚠️ Skipped: 3

❌ Errors (2):
Row 5: Invalid JID format: user@
Row 12: until must be a valid number
```

## Pre-Import Backup

Before writing imported rows, BanBot creates a timestamped backup:

```text
data/backups/banbot.db.snapshot-before-import-YYYYMMDD_HHMMSS
```

## Use Cases

* Backup before major moderation maintenance
* Migrate bans to a new bot instance
* Restore from a previous export
* Batch-import bans from an external moderation process

## Managed SQLite Backups

CSV export is useful for portable ban data. For full SQLite snapshots, use the managed database backup commands:

```text
!backup
!backup list
!restore latest confirm
```

Managed snapshots include the whole SQLite database, including rooms, audit log, ignorelist, pending invites, RTBL subscriptions and redaction index. They also include a companion copy of `config.py` when the active config file can be resolved. Automatic startup snapshots are controlled by `DB_BACKUP_ON_START`, `DB_BACKUP_DIR` and `DB_BACKUP_KEEP`.
