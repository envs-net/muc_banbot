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
* `RTBL_ENABLED`
* `RTBL_PUBLISH_ENABLED`
* `RTBL_PUBLISH_SERVICE`
* `RTBL_PUBLISH_JID_NODE`
* `RTBL_PUBLISH_DOMAIN_NODE`
* `OMEMO_ENABLED`
* `OMEMO_STORAGE_FILE`
* `OMEMO_AUTO_ENCRYPT_ADMIN_ROOM`
* `OMEMO_PLAINTEXT_FALLBACK`

## Runtime-Reloadable Settings

Most operational settings can be reloaded with:

```text
!reloadconfig
```

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
| `PUBLIC_COMMAND_RATE_LIMIT_WINDOW` | `30` | Rate-limit window in seconds |
| `PUBLIC_COMMAND_RATE_LIMIT_MAX` | `3` | Max public command uses per nick/room/command/window |
| `STRUCTURED_EVENT_LOGS` | `True` | Emit JSON logs for important events |
| `AUDIT_LOG_ENABLED` | `True` | Store audit events in SQLite |
| `AUDIT_LOG_RETENTION_DAYS` | `365` | Audit retention; validation caps this at 365 |
| `HEALTH_CHECK_INTERVAL` | `300` | Health check interval; minimum 60 seconds |
| `UNBAN_CHECK_INTERVAL` | `60` | Expired tempban check interval |
| `MAX_TEMPBAN_DAYS` | `30` | Max temporary ban duration; 1-365 |
| `MUC_WRITE_SEMAPHORE` | `5` | Concurrent XMPP IQ operation limit |
| `VERSION_CHECK_ENABLED` | `False` | Enable GitHub release checks |
| `VERSION_CHECK_INTERVAL` | `3600` | Release check interval; minimum 300 seconds |
| `VERSION_CHECK_URL` | GitHub latest release URL | URL used to discover latest release |

Boolean aliases such as `true`/`false` are supported by the config loader for convenience.

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

## OMEMO Settings

OMEMO settings are startup-only and require a bot restart when changed. See [OMEMO](omemo.md) for behavior details.

OMEMO is optional. The bot can run without `slixmpp-omemo`; if `OMEMO_ENABLED=True` but optional dependencies are missing, startup continues with OMEMO disabled and a warning in the log. Install `requirements-omemo.txt` after installing the required system libraries when encrypted command/reply support is needed.

```python
OMEMO_ENABLED = False
OMEMO_STORAGE_FILE = "data/omemo.json"
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = True
OMEMO_PLAINTEXT_FALLBACK = False
```

| Setting | Description |
| --- | --- |
| `OMEMO_ENABLED` | Enable OMEMO plugin integration |
| `OMEMO_STORAGE_FILE` | JSON storage for identity/session/trust state |
| `OMEMO_AUTO_ENCRYPT_ADMIN_ROOM` | Encrypt proactive admin-room messages when possible |
| `OMEMO_PLAINTEXT_FALLBACK` | Allow plaintext fallback when encrypted reply fails |

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

Successful RTBL refreshes reconcile the local cache with the current PubSub node snapshot. RTBL matches that are actually applied are stored in the main ban table as `issuer=rtbl`; stale `issuer=rtbl` bans are automatically unbanned when their RTBL source disappears.

Avatar/vCard data is updated on startup and after `!reloadconfig`.

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

Some installations may prefer classic log files under `/var/log`. In that case, add append-based stdout/stderr logging to the `[Service]` section of `/etc/systemd/system/muc_banbot.service`:

```ini
StandardOutput=append:/var/log/muc_banbot.log
StandardError=append:/var/log/muc_banbot_error.log
```

After changing the service file, reload systemd and restart the bot:

```bash
sudo systemctl daemon-reload
sudo systemctl restart muc_banbot
```

When using `StandardOutput=append:` / `StandardError=append:`, the running process keeps the log files open. Use `copytruncate` in logrotate so rotation does not require restarting the bot.

Example `/etc/logrotate.d/muc_banbot`:

```conf
/var/log/muc_banbot.log /var/log/muc_banbot_error.log {
    daily
    rotate 7
    copytruncate
    compress
    delaycompress
    missingok
    notifempty
}
```

If these `StandardOutput` / `StandardError` options are omitted, BanBot continues to log through `journald` only.
