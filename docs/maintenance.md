# Maintenance

This page collects manual maintenance tasks for BanBot operators.

## SQLite database maintenance

BanBot uses SQLite for persistent state. Normal operation does not require manual database maintenance, but after many deletes, imports, restores, or redaction cleanup runs it can be useful to check and compact the database.

Always use the database path configured as `DB_FILE` in the active config. Hardened deployments normally use `/var/lib/muc_banbot/banbot.db`; legacy source-tree deployments may keep `banbot.db`. The examples below use `banbot.db` as a placeholder.

Use your installed systemd unit name for BanBot. The examples below use `muc_banbot.service` as a placeholder (`BANBOT_SERVICE`), but your service name may differ.

### Safe manual maintenance flow

Stop BanBot before running manual SQLite maintenance:

```bash
BANBOT_SERVICE="muc_banbot.service"
systemctl stop "$BANBOT_SERVICE"
```

Set the database path and create a backup first:

```bash
DB_FILE="banbot.db"
BANBOT_SERVICE="muc_banbot.service"
cp "$DB_FILE" "$DB_FILE.backup-$(date +%Y%m%d-%H%M%S)"
```

Check database integrity:

```bash
sqlite3 "$DB_FILE" "PRAGMA integrity_check;"
```

Expected output:

```text
ok
```

Update SQLite query planner statistics:

```bash
sqlite3 "$DB_FILE" "PRAGMA optimize;"
```

Compact the database file:

```bash
sqlite3 "$DB_FILE" "VACUUM;"
```

Start BanBot again:

```bash
systemctl start "$BANBOT_SERVICE"
```

### Notes

* `VACUUM` rewrites the SQLite database file and can take some time on larger databases.
* Make sure there is enough free disk space before running `VACUUM`.
* Do not run manual `VACUUM` while BanBot is active.
* `PRAGMA optimize` is lightweight, but the stop-maintain-start flow keeps all manual database work predictable.
* Managed BanBot backups are documented in [Backups and Restore](backups.md). Manual SQLite file copies are only a maintenance safety net.

## Redaction index cleanup

The redaction index is cleaned automatically by BanBot on startup and then every 24 hours. Manual cleanup is still available with:

```text
!redact cleanup
```

Retention is controlled by `REDACTION_INDEX_RETENTION_DAYS` in `config.py`. A value of `0` keeps indexed stanza IDs indefinitely.
