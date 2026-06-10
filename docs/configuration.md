# Configuration

Copy `config_sample.py` to `config.py` and edit it before starting the bot.

```bash
cp config_sample.py config.py
$EDITOR config.py
```

`config.py` is private deployment state. Do not commit it.

## `config_sample.py` as Display Reference

BanBot uses `config_sample.py` as the reference for the section order shown by `!config`. Keep this file deployed together with the bot and update it when adding new configuration options.

Do not put local secrets or production values into `config_sample.py`; use `config.py` for local configuration. `config_sample.py` should remain a versioned sample file.

If `config_sample.py` is missing or incomplete, BanBot falls back to its built-in config ordering, but the displayed grouping may be less complete.

## Required Settings

```python
JID = "adminbot@example.org"
PASSWORD = "secret"
RESOURCE = "service"
ADMIN_ROOM = "admin@conference.example.org"
NICK = "BanBot"
DB_FILE = "banbot.db"
```

| Setting | Description |
| --- | --- |
| `JID` | Bot account JID |
| `PASSWORD` | Bot account password |
| `RESOURCE` | XMPP resource. Legacy `RESSOURCE` is accepted for backward compatibility |
| `ADMIN_ROOM` | Admin/control MUC JID |
| `NICK` | Bot nickname in rooms |
| `DB_FILE` | SQLite database path |

## Runtime Config Commands

```text
!config [all|page|last]
!config show [all|page|last]
!config set <KEY> <value>
!config unset <KEY>
!reloadconfig
```

`!config` hides secret values such as `PASSWORD`.

Runtime-writable values are marked with `✏️` in the output. Protected/startup-only values are marked as protected and cannot be changed through chat commands.

`!config unset <KEY>` resets a runtime-writable value to the default from `config_sample.py`.

## Output Modes and Paging

```python
LIST_PAGE_SIZE = 10
CONFIG_OUTPUT_MODE = "all"
HELP_OUTPUT_MODE = "all"
```

`LIST_PAGE_SIZE` controls paginated list commands such as `!banlist`, `!audit`, `!backup list`, `!export list`, `!room list`, and invite lists.

`CONFIG_OUTPUT_MODE` controls whether `!config` defaults to full output or paginated output.

`HELP_OUTPUT_MODE` controls whether `!help` defaults to full output or paginated output.

Accepted values:

```python
"all"
"paginate"
```

The default is `"all"` to preserve previous behavior. Explicit full output is always available:

```text
!help all
!config all
!config show all
```

## Startup-Only Settings

These require a bot restart. `!reloadconfig` warns if they changed and keeps the old running value active.

* `JID`
* `PASSWORD`
* `RESOURCE` / `RESSOURCE`
* `ADMIN_ROOM`
* `NICK`
* `DB_FILE`
* `CONNECT_HOST`
* `CONNECT_PORT`
* `CONNECT_DIRECT_TLS`
* `RTBL_ENABLED`
* `RTBL_PUBLISH_ENABLED`
* `RTBL_PUBLISH_SERVICE`
* `RTBL_PUBLISH_JID_NODE`
* `RTBL_PUBLISH_DOMAIN_NODE`
* `OMEMO_ENABLED`
* `OMEMO_STORAGE_FILE`
* `OMEMO_AUTO_ENCRYPT_ADMIN_ROOM`
* `OMEMO_PLAINTEXT_FALLBACK`
* `OMEMO_RESET_ON_IDENTITY_CHANGE`

## Common Runtime-Reloadable Settings

Common runtime-reloadable settings include:

* `LOG_LEVEL`
* `COMMAND_PREFIX`
* `ANNOUNCE_STARTUP`
* `ANNOUNCE_SYNC_DETAILS`
* `SHOW_BAN_IN_MUC`
* `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS`
* `ALLOW_ADMIN_COMMANDS_IN_DMS`
* `ROOM_INVITES_ENABLED`
* `ROOM_INVITE_MAX_AGE_DAYS`
* `HEALTH_CHECK_INTERVAL`
* `UNBAN_CHECK_INTERVAL`
* `MAX_TEMPBAN_DAYS`
* `PUBLIC_COMMAND_RATE_LIMIT_WINDOW`
* `PUBLIC_COMMAND_RATE_LIMIT_MAX`
* `LIST_PAGE_SIZE`
* `CONFIG_OUTPUT_MODE`
* `HELP_OUTPUT_MODE`
* `MUC_WRITE_SEMAPHORE`
* `SYNC_BATCH_SIZE`
* `STRUCTURED_EVENT_LOGS`
* `AUDIT_LOG_ENABLED`
* `AUDIT_LOG_RETENTION_DAYS`
* alert settings
* `RTBL_ANNOUNCE`
* `RTBL_REFRESH_INTERVAL`
* version-check settings
* redaction settings

Use `!config` in the admin room for the current authoritative runtime-writable list.

## Connection Settings

```python
CONNECT_HOST = None
CONNECT_PORT = 5222
CONNECT_DIRECT_TLS = False
```

`CONNECT_HOST=None` uses the domain from `JID`.

Use STARTTLS on 5222 for normal client-to-server connections. Use direct TLS only when your server expects it.

## Backup Settings

```python
DB_BACKUP_ON_START = True
DB_BACKUP_DIR = "data/backups"
DB_BACKUP_KEEP = 15
DB_BACKUP_INCLUDE_OMEMO = True
```

Managed backups are self-contained ZIP archives with `manifest.json`, `database.sqlite3`, and optional `config.py` / `omemo.json` entries.

See [Backups and Restore](backups.md).

## Managed CSV Export Settings

```python
EXPORT_DIR = "data/exports"
EXPORT_KEEP = 15
```

See [Import / Export](import-export.md).

## Bot Settings

```python
LOG_LEVEL = "INFO"
COMMAND_PREFIX = "!"
ANNOUNCE_STARTUP = True
ANNOUNCE_SYNC_DETAILS = True
SHOW_BAN_IN_MUC = False
ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS = True
ALLOW_ADMIN_COMMANDS_IN_DMS = True
ROOM_INVITES_ENABLED = False
ROOM_INVITE_MAX_AGE_DAYS = 30
HEALTH_CHECK_INTERVAL = 300
UNBAN_CHECK_INTERVAL = 60
MAX_TEMPBAN_DAYS = 30
PUBLIC_COMMAND_RATE_LIMIT_WINDOW = 10
PUBLIC_COMMAND_RATE_LIMIT_MAX = 3
```

Room invite details are documented in [Rooms and Invites](rooms.md).

## Performance Settings

```python
MUC_WRITE_SEMAPHORE = 5
SYNC_BATCH_SIZE = 10
```

`MUC_WRITE_SEMAPHORE` limits concurrent MUC write operations.

`SYNC_BATCH_SIZE` controls how many rooms are processed concurrently during full sync operations.

## Logging / Audit Settings

```python
STRUCTURED_EVENT_LOGS = True
AUDIT_LOG_ENABLED = True
AUDIT_LOG_RETENTION_DAYS = 365
```

Audit events are stored in SQLite. Structured events are emitted as JSON logs for external log processing.

## Operational Alert Settings

```python
ALERT_ON_RECONNECT = True
ALERT_ON_ADMIN_RIGHTS_LOST = True
ALERT_ON_HEALTH_CHECK_FAILURE = True
ALERT_ON_DB_STATS_FAILURE = True
ALERT_ON_REDACTION_FAILURE = True
ALERT_ON_DB_SIZE_MB = 0
ALERT_ON_RTBL_REFRESH_FAILURES = 3
ALERT_DEDUP_WINDOW = 300
```

Alerts are sent to the admin room and deduplicated by key/window.

`ALERT_ON_DB_SIZE_MB = 0` disables database-size alerts.

`ALERT_ON_RTBL_REFRESH_FAILURES = 0` disables RTBL refresh failure alerts.

## OMEMO Settings

```python
OMEMO_ENABLED = False
OMEMO_STORAGE_FILE = "data/omemo.json"
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = True
OMEMO_PLAINTEXT_FALLBACK = False
OMEMO_RESET_ON_IDENTITY_CHANGE = True
```

See [OMEMO](omemo.md).

## RTBL Settings

```python
RTBL_ENABLED = False
RTBL_ANNOUNCE = True
RTBL_REFRESH_INTERVAL = 3600
RTBL_PUBLISH_ENABLED = False
RTBL_PUBLISH_SERVICE = "pubsub.domain.tld"
RTBL_PUBLISH_JID_NODE = "muc_bans_sha256"
RTBL_PUBLISH_DOMAIN_NODE = "muc_bans_domains"
```

See [RTBL / PubSub](rtbl.md).

## Version Check Settings

```python
VERSION_CHECK_ENABLED = False
VERSION_CHECK_INTERVAL = 3600
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"
```

Use `!checkupdate` / `!updatecheck` for manual checks.

## Redaction Settings

```python
REDACTION_ENABLED = False
REDACTION_INDEX_RETENTION_DAYS = 30
AUTO_REDACT_ON_IMPORTED_BAN_REASON = False
AUTO_REDACT_ON_MANUAL_MUC_BAN = True
REDACTION_AUTO_REASONS = [
    "spam",
    "advertising",
]
```

Redaction indexes room-assigned stanza IDs, not message bodies.

`REDACTION_AUTO_REASONS` is used for automatic redaction decisions.
Normal bot-command bans can trigger auto-redaction when their comment matches.
`AUTO_REDACT_ON_IMPORTED_BAN_REASON` extends this behavior to matching imported JID bans.
`AUTO_REDACT_ON_MANUAL_MUC_BAN` extends it to newly discovered manual/external MUC bans recovered during startup sync, `!syncbans`, room sync, or live MUC ban presence events. Existing known room outcasts are not auto-redacted again on every sync/startup. Imported bans remain disabled by default, while manual/external MUC ban auto-redaction is enabled by default for matching configured reasons.

See [Commands](commands.md#moderation).

## vCard Settings

```python
AVATAR_PATH = "avatar.png"
VCARD_NICKNAME = "My Bot Nickname"
VCARD_FN = "Admin Bot"
VCARD_ORG = "My Organization"
VCARD_ROLE = "Administrator"
VCARD_URL = "https://example.com"
VCARD_NOTE = "Bot Admin Assistant"
```

If you use a custom avatar, it is recommended to store it in a local, non-tracked path such as `data/avatar.png` or `custom/avatar.png` and then set `AVATAR_PATH` accordingly. This avoids accidentally replacing or committing the repository's default `avatar.png` during updates.

Example:

```python
AVATAR_PATH = "data/avatar.png"
```

## Systemd Service

Example system service:

```ini
[Unit]
Description=BanBot XMPP moderation bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=adminbot
WorkingDirectory=/srv/adminbot/muc_banbot
ExecStart=/srv/adminbot/muc_banbot/venv/bin/python /srv/adminbot/muc_banbot/muc_banbot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Reload after changes:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now muc_banbot
sudo systemctl status muc_banbot
```
