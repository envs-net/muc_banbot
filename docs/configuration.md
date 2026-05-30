# Configuration

Copy `config_sample.py` to `config.py` and edit it before starting the bot.

```bash
cp config_sample.py config.py
$EDITOR config.py
```

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

## Runtime-Reloadable Settings

Most operational settings can be shown, edited, reset to the sample default, or reloaded from disk with:

```text
!config show
!config set <KEY> <value>
!config unset <KEY>
!reloadconfig
```

`!config show` follows the active `config.py` order and appends missing supported keys from `config_sample.py`, marks runtime-writable values with `✏️`, marks protected/restart-only values with `🔒`, and hides secrets such as `PASSWORD` as `****`.

`!config set` and `!config unset` only allow runtime-writable settings. Identity, password, database path, admin room, RTBL setup, and OMEMO startup settings remain protected and require manual edit + restart.

`!reloadconfig` validates `config.py`, keeps the last known good runtime config if validation fails, and reports warnings/errors in the admin room.

Common runtime settings:

| Setting | Default | Description |
| --- | --- | --- |
| `COMMAND_PREFIX` | `!` | Prefix used for commands |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `ANNOUNCE_STARTUP` | `True` | Send startup announcements |
| `ANNOUNCE_SYNC_DETAILS` | `True` | Show detailed startup sync output |
| `SHOW_BAN_IN_MUC` | `False` | Announce bans in protected rooms |
| `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS` | `True` | Enable public protected-room commands |
| `ALLOW_ADMIN_COMMANDS_IN_DMS` | `True` | Allow admins to use selected read-only commands via direct messages / MUC PMs |
| `ROOM_INVITES_ENABLED` | `False` | Enable admin-reviewed protected-room invite workflow |
| `ALERT_ON_RECONNECT` | `True` | Alert after a successful reconnect |
| `ALERT_ON_ADMIN_RIGHTS_LOST` | `True` | Alert when the bot loses admin/owner rights |
| `ALERT_ON_HEALTH_CHECK_FAILURE` | `True` | Alert on room health-check failures |
| `ALERT_ON_DB_STATS_FAILURE` | `True` | Alert when DB stats cannot be read |
| `ALERT_ON_REDACTION_FAILURE` | `True` | Alert on failed message redactions |
| `ALERT_ON_DB_SIZE_MB` | `0` | Alert when DB size reaches this MiB value; `0` disables |
| `ALERT_ON_RTBL_REFRESH_FAILURES` | `3` | Alert after this many consecutive RTBL refresh failures per subscription; `0` disables |
| `ALERT_DEDUP_WINDOW` | `300` | Suppress duplicate alerts with the same key for this many seconds |
| `PUBLIC_COMMAND_RATE_LIMIT_WINDOW` | `30` | Rate-limit window in seconds |
| `PUBLIC_COMMAND_RATE_LIMIT_MAX` | `3` | Max public command uses per nick/room/command/window |
| `STRUCTURED_EVENT_LOGS` | `True` | Emit JSON logs for important events |
| `AUDIT_LOG_ENABLED` | `True` | Store audit events in SQLite |
| `AUDIT_LOG_RETENTION_DAYS` | `365` | Audit retention; validation caps this at 365 |
| `HEALTH_CHECK_INTERVAL` | `300` | Health check interval; minimum 60 seconds |
| `UNBAN_CHECK_INTERVAL` | `60` | Expired tempban check interval |
| `MAX_TEMPBAN_DAYS` | `30` | Max temporary ban duration; 1-365 |
| `MUC_WRITE_SEMAPHORE` | `5` | Concurrent XMPP IQ operation limit |
| `RTBL_ANNOUNCE` | `True` | Announce RTBL bans and skipped admin-protected entries in the admin room |
| `RTBL_REFRESH_INTERVAL` | `3600` | RTBL subscription refresh interval in seconds; `0` disables periodic refresh |
| `REDACTION_ENABLED` | `False` | Enable protected-room message redaction indexing and commands |
| `REDACTION_INDEX_RETENTION_DAYS` | `30` | Days to retain indexed stanza IDs; `0` keeps them indefinitely |
| `REDACTION_AUTO_REASONS` | `[]` | Ban-comment reason strings that trigger automatic redaction |
| `VERSION_CHECK_ENABLED` | `False` | Enable GitHub release checks |
| `VERSION_CHECK_INTERVAL` | `3600` | Release check interval; minimum 300 seconds |
| `VERSION_CHECK_URL` | GitHub latest release URL | URL used to discover latest release |

Boolean aliases such as `true`/`false` are supported by the config loader for convenience.

## Connection Settings

Connection settings are startup-only and require a bot restart when changed.

| Setting | Default | Description |
| --- | --- | --- |
| `CONNECT_HOST` | `None` | Optional host override; `None` uses the JID domain |
| `CONNECT_PORT` | `5222` | TCP port for the XMPP connection |
| `CONNECT_DIRECT_TLS` | `False` | Use direct TLS instead of STARTTLS |

Examples:

```python
# Default STARTTLS C2S
CONNECT_HOST = None
CONNECT_PORT = 5222
CONNECT_DIRECT_TLS = False

# Direct TLS / legacy SSL
CONNECT_PORT = 5223
CONNECT_DIRECT_TLS = True

# Native XMPP over direct TLS on 443
CONNECT_PORT = 443
CONNECT_DIRECT_TLS = True
```

## vCard / Avatar Settings

```python
AVATAR_PATH = "avatar.png"
VCARD_NICKNAME = "BanBot"
VCARD_FN = "Ban Management Bot"
VCARD_ORG = "Example"
VCARD_ROLE = "Moderation Bot"
VCARD_URL = "https://example.org"
VCARD_NOTE = "XMPP MUC ban management bot"
```

Avatar/vCard data is updated on startup and after `!reloadconfig`.

## Admin Direct Messages

When `ALLOW_ADMIN_COMMANDS_IN_DMS=True`, admins may use selected read-only commands via direct messages and MUC PMs. Mutating commands remain restricted to `ADMIN_ROOM` for auditability and safety.

When set to `False`, admin commands are rejected outside `ADMIN_ROOM`, matching the previous behavior.

## Room Invite Service

When `ROOM_INVITES_ENABLED=True`, BanBot can receive MUC invites and offer them in the admin room as pending protected-room requests. The bot does not auto-join invited rooms. Admins must explicitly accept or decline each invite:

```text
!room invite list [all|page|last]
!room invite accept <id>
!room invite decline <id>
!room invite cleanup
```

Incoming invite room JIDs are validated before they are shown as pending protected-room requests. Repeated invites from the same inviter for the same room are deduplicated.

Pending invites are persisted in SQLite. BanBot does not automatically expire or delete them. They are removed only when accepted, declined/rejected, or when an admin runs `!room invite cleanup`.

## OMEMO Settings

OMEMO settings are startup-only and require a bot restart when changed. See [OMEMO](omemo.md) for behavior details.

OMEMO is optional. The bot can run without `slixmpp-omemo`; if `OMEMO_ENABLED=True` but optional dependencies are missing, startup continues with OMEMO disabled and a warning in the log. Install `requirements-omemo.txt` after installing the required system libraries when encrypted command/reply support is needed.

```python
OMEMO_ENABLED = False
OMEMO_STORAGE_FILE = "data/omemo.json"
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = True
OMEMO_PLAINTEXT_FALLBACK = False
OMEMO_RESET_ON_IDENTITY_CHANGE = True
```

| Setting | Description |
| --- | --- |
| `OMEMO_ENABLED` | Enable OMEMO plugin integration |
| `OMEMO_STORAGE_FILE` | JSON storage for identity/session/trust state |
| `OMEMO_AUTO_ENCRYPT_ADMIN_ROOM` | Encrypt proactive admin-room messages when possible |
| `OMEMO_PLAINTEXT_FALLBACK` | Allow plaintext fallback when encrypted reply fails |
| `OMEMO_RESET_ON_IDENTITY_CHANGE` | Rotate OMEMO storage when JID, RESOURCE, or NICK changes |

## RTBL Settings

See [RTBL](rtbl.md) for behavior details.

| Setting | Description |
| --- | --- |
| `RTBL_ENABLED` | Enable inbound RTBL subscriptions |
| `RTBL_ANNOUNCE` | Announce RTBL changes in admin room |
| `RTBL_REFRESH_INTERVAL` | Periodic refresh interval; `0` disables refresh |
| `RTBL_PUBLISH_ENABLED` | Enable own local-ban publish feed |
| `RTBL_PUBLISH_SERVICE` | PubSub service used for local publish feed |
| `RTBL_PUBLISH_JID_NODE` | Node for SHA-256 bare-JID hashes |
| `RTBL_PUBLISH_DOMAIN_NODE` | Node for plaintext domain bans |

When own RTBL publishing is enabled, BanBot configures PubSub node `pubsub#max_items` dynamically. It keeps at least 1000 items per publish node and auto-grows in 1000-item steps when the number of active local published bans requires more retention.

Successful RTBL refreshes reconcile the local cache with the current PubSub node snapshot. RTBL matches that are actually applied are stored in the main ban table as `issuer=rtbl`; stale `issuer=rtbl` bans are automatically unbanned when their RTBL source disappears.

`RTBL_ANNOUNCE` and `RTBL_REFRESH_INTERVAL` are runtime-reloadable via `!reloadconfig`; enabling/disabling RTBL itself and changing own publish feed settings require a restart.

## Redaction Settings

```python
REDACTION_ENABLED = False
REDACTION_INDEX_RETENTION_DAYS = 30
REDACTION_AUTO_REASONS = ["spam", "harassment"]
```

When enabled, BanBot indexes room-assigned stanza IDs for messages it sees in protected rooms. Message bodies are not stored. `!redact <jid>` redacts all known, not-yet-redacted indexed messages for that bare JID. `REDACTION_INDEX_RETENTION_DAYS = 0` keeps the index indefinitely.

`REDACTION_AUTO_REASONS` is matched case-insensitively against ban comments. Matching JID bans trigger automatic redaction for the banned bare JID.

## Systemd Service

Create `/etc/systemd/system/muc_banbot.service`:

```ini
[Unit]
Description=BanBot XMPP MUC Bot
After=network.target

[Service]
Type=simple
User=adminbot
WorkingDirectory=/srv/adminbot/muc_banbot
ExecStart=/srv/adminbot/muc_banbot/venv/bin/python /srv/adminbot/muc_banbot/muc_banbot.py
Restart=always
RestartSec=5s
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl start muc_banbot
sudo systemctl enable muc_banbot
sudo journalctl -u muc_banbot -f
```

## Optional File Logging with systemd and logrotate

By default, the service example above writes logs to `journald`. You can inspect them with:

```bash
sudo journalctl -u muc_banbot -f
```

This is the recommended default.

Some installations may prefer classic log files under `/var/log`. BanBot uses Python logging, and Python logging writes to stderr by default. This means normal `INFO`, `WARNING`, and `ERROR` messages usually appear on stderr.

If you want systemd to write directly to a file, send both stdout and stderr to the same file. This avoids an empty main log file and a misleading separate “error” log.

Add this to the `[Service]` section of `/etc/systemd/system/muc_banbot.service`:

```ini
StandardOutput=append:/var/log/muc_banbot.log
StandardError=append:/var/log/muc_banbot.log
```

After changing the service file, reload systemd and restart the bot:

```bash
sudo systemctl daemon-reload
sudo systemctl restart muc_banbot
```

When using `append:` logging, logs are written to the file instead of the journal through stdout/stderr. Use `copytruncate` in logrotate so rotation does not require restarting the bot.

Example `/etc/logrotate.d/muc_banbot`:

```conf
/var/log/muc_banbot.log {
    daily
    rotate 7
    copytruncate
    compress
    delaycompress
    missingok
    notifempty
}
```

If your systemd version does not support `append:`, omit `StandardOutput` / `StandardError` and use the default `journald` logging instead.

If you need both `journald` and `/var/log/muc_banbot.log` at the same time, keep systemd logging to the journal and configure your system logger, such as rsyslog, to write selected `muc_banbot` journal/syslog entries to a file.
