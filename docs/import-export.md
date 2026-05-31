# Import / Export

BanBot supports managed CSV exports for portable ban data and CSV imports for migration or batch updates. Full recovery should use the managed backup commands.

## Export

```text
!export
!export list
!export delete <filename|latest>
```

Exports are written to `EXPORT_DIR` as `bans_export_YYYYMMDD_HHMMSS.csv`. Old export files are pruned according to `EXPORT_KEEP`.

## Import

```text
!import bans_export_20240412_120000.csv
!import bans_export_20240412_120000.csv dryrun
```

Before writing staged rows, BanBot creates a managed full backup using the normal backup system. This means before-import snapshots are visible in `!backup list`, use the same retention settings and include companion files such as `config.py` and optional OMEMO storage.

Dry-runs validate and stage rows but do not create a backup and do not change the database.

## Full backups

For full SQLite/config/OMEMO recovery use:

```text
!backup
!backup list
!backup show latest
!restore latest confirm
```
