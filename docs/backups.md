# Backups and Restore

BanBot supports managed full backups and restore from the admin room.

Managed backups are intended for operational recovery: bad imports, accidental changes, bot upgrades, database corruption, or moving the bot to another host.

## Commands

```text
!backup
!backup list [all|page|last]
!backup show <filename|latest>
!backup verify <filename|latest>
!backup delete/remove/del/rm <filename|latest>
!restore <filename|latest> confirm
```

`latest` resolves to the newest managed backup archive.

## Configuration

```python
DB_BACKUP_ON_START = True
DB_BACKUP_DIR = "data/backups"
DB_BACKUP_KEEP = 15
DB_BACKUP_INCLUDE_OMEMO = True
LIST_PAGE_SIZE = 10
```

* `DB_BACKUP_ON_START` creates a managed backup when the bot starts.
* `DB_BACKUP_DIR` stores managed backup archives.
* `DB_BACKUP_KEEP` controls retention for managed backup archives.
* `DB_BACKUP_INCLUDE_OMEMO` includes OMEMO storage when enabled and available.
* `LIST_PAGE_SIZE` controls `!backup list` pagination.

## Backup Archive Format

New managed backups are self-contained ZIP archives.

Typical archive contents:

```text
manifest.json
database.sqlite3
config.py       optional
omemo.json      optional
```

The manifest describes the backup format and which files are part of the archive. It allows BanBot to verify that the file is really a BanBot backup before restore and makes future format upgrades possible.

Conceptual manifest structure:

```json
{
  "format": "banbot-backup-v1",
  "created_at": 1760000000,
  "bot_version": "2.4.0",
  "contains": {
    "database": true,
    "config": true,
    "omemo": true
  },
  "files": {
    "database": "database.sqlite3",
    "config": "config.py",
    "omemo": "omemo.json"
  }
}
```

For new ZIP backups, `manifest.json` is required. ZIP files without a valid manifest are rejected as managed backup archives.

## What Is Backed Up

### Database

The SQLite database is always included as `database.sqlite3`.

### Config Companion

When a matching `config.py` companion is available, it is included as `config.py`. This makes an archive useful for full operational recovery, but it also means backup archives may contain secrets such as the bot password.

### OMEMO Companion

When OMEMO is enabled and `DB_BACKUP_INCLUDE_OMEMO=True`, the configured OMEMO storage file is included as `omemo.json` if it exists and is readable.

OMEMO storage contains identity/session material. Treat backup archives as private secrets.

## Verify

```text
!backup verify latest
```

Verification checks:

* the ZIP archive can be opened
* `manifest.json` exists and is valid JSON
* the archive has the expected files described by the manifest
* the SQLite database passes `PRAGMA integrity_check`
* `config.py` compiles as valid Python when present
* `omemo.json` parses as valid JSON when present

Verification never replaces the active database.

## Show

```text
!backup show latest
```

Show displays metadata such as archive format, size, creation time, and contents. Use it before restore when you are not sure which backup is selected by `latest`.

## Restore

```text
!restore latest confirm
```

The `confirm` argument is required intentionally.

Restore behavior:

1. Resolve and verify the selected backup.
2. Create a safety backup before replacing active files.
3. Extract the archive into a temporary location.
4. Run SQLite integrity checks.
5. Replace the active database.
6. Restore optional companion files when present.
7. Record audit events.

If verification fails, restore is aborted before active files are replaced.

## Safety Backups

Before restore and write imports, BanBot creates a managed safety backup using the same ZIP archive format. These safety backups appear in `!backup list` and follow the same retention rules.

Typical reasons include:

* `before-restore`
* `before-import`
* startup backup

## Delete / Remove

```text
!backup delete latest
!backup remove banbot-backup-20260602_231500.zip
!backup del latest
!backup rm latest
```

Delete removes the selected managed backup archive. For legacy backups, BanBot also cleans up known companion files when applicable.

## Legacy Backup Compatibility

BanBot can still list, verify, and restore older managed backups that used loose companion files instead of a ZIP archive. This compatibility is kept so pre-archive snapshots remain usable.

New backups should use the ZIP archive format.

## Security Notes

Backup archives may contain:

* the SQLite database
* `config.py` with bot credentials
* OMEMO identity/session material

Recommended practice:

* keep `DB_BACKUP_DIR` readable only by the bot/operator account
* do not publish backup archives
* do not attach backup archives to public bug reports
* use filesystem permissions and off-host backup storage appropriate for secrets

## Recommended Smoke Test After Upgrade

After upgrading or changing backup settings:

```text
!backup
!backup list
!backup show latest
!backup verify latest
```

Only test `!restore` on a staging bot or after creating an external copy of the current deployment.
