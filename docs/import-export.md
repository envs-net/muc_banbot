# Import / Export

BanBot supports managed CSV exports for portable ban data and CSV imports for migration or batch updates.

Use managed ZIP backups for full recovery. CSV import/export is for ban data, not full bot state.

## Commands

```text
!export
!export list [all|page|last]
!export show <filename|latest>
!export delete/remove/del/rm <filename|latest>
!import <filename> [dryrun]
```

## Configuration

```python
EXPORT_DIR = "data/exports"
EXPORT_KEEP = 15
LIST_PAGE_SIZE = 10
```

* `EXPORT_DIR` stores managed CSV exports.
* `EXPORT_KEEP` controls export retention.
* `LIST_PAGE_SIZE` controls `!export list` paging.

## Export

```text
!export
```

Exports are written to `EXPORT_DIR` as managed CSV files with collision-safe filenames.

List and inspect exports:

```text
!export list
!export list last
!export list all
!export show latest
```

Delete exports:

```text
!export delete latest
!export remove bans_export_20260602_231500.csv
!export del latest
!export rm latest
```

Export paths are constrained to the configured export directory.

## Import

```text
!import bans_export_20260602_231500.csv
!import bans_export_20260602_231500.csv dryrun
```

Import files must be managed files in `EXPORT_DIR`. BanBot does not import arbitrary filesystem paths from chat commands.

Accepted dry-run aliases include:

```text
dryrun
dry-run
check
```

Dry-run mode validates and stages rows without writing bans to the database and without creating a safety backup.

## Import Safety Backup

Before applying a write import, BanBot creates a managed full ZIP backup through the normal backup system.

This backup appears in:

```text
!backup list
```

and can be inspected/restored with:

```text
!backup show latest
!backup verify latest
!restore latest confirm
```

See [Backups and Restore](backups.md).

## Deduplication Rules

Imports deduplicate staged rows before database writes.

When duplicate targets are found:

* permanent bans win over temporary bans
* temporary bans with later expiration timestamps win over earlier duplicates
* normalized target keys are used for comparison

In-memory ban caches are updated only after database commits succeed.

## Recommended Workflow

1. Copy the CSV file into `EXPORT_DIR` or create it with `!export`.
2. Run a dry-run:

   ```text
   !import bans_export_20260602_231500.csv dryrun
   ```

3. Review the result.
4. Apply the import:

   ```text
   !import bans_export_20260602_231500.csv
   ```

5. Check status and audit:

   ```text
   !status
   !audit last
   ```
