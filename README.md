# BanBot - XMPP Multi-Room Ban Management Bot

BanBot is an XMPP bot for managing bans and temporary bans across multiple MUC rooms (Multi-User Chat).  
It provides central administration via an admin room and protects multiple chat rooms from unwanted users.

---

## Features

* 🛡️ Central admin room for all administrative commands  
* 🔒 Dynamic addition/removal of protected rooms  
* ❌ Ban, temporary ban, unban, banlist, bansearch, and why commands  
* 🌐 Domain-based bans (`*.domain.tld`) to ban all users from a domain  
* 📝 Optional comment when banning (e.g., `!tempban user 10m spamming`)  
* ⏱️ Human-readable remaining time for temporary bans  
* ⏳ Automatic removal of expired temporary bans  
* 📊 Smart duplicate ban handling with automatic conversion (Permanent ↔ Tempban)  
* 🐞 Handles nick-only bans with best-effort enforcement  
* 🔄 Auto-updates nick-only bans to JID when user rejoins  
* ⚠️ Admins/Owners are protected from accidental bans  
* ⛔ Prevents ban application if the bot does not have admin/owner rights  
* 🚫 Global ignorelist/whitelist (`!ignore` / `!whitelist`) for exact JIDs and domain-based ban protection  
* 📦 Sync existing room bans into the database at startup  
* 🔄 Automatic rejoin and reapplication of bans on restart  
* 👀 Monitors bot's admin/owner rights per room and reports loss to the admin room  
* 🏥 Periodic health checks for room connectivity and admin rights  
* 🩺 Dynamic `!status` health headline with reconnect, worker, DB, room-rights, and RTBL status  
* 🛡️ RTBL subscriptions via PubSub for SHA-256 JID hashes and plaintext domain bans  
* 🧾 Applied RTBL bans are persisted in the main banlist with a shield marker; domain matches are stored as concrete JID bans  
* 🔎 Current occupants are scanned immediately after startup/new RTBL subscription fetches  
* 🔄 Periodic RTBL refresh with quiet/no-change behavior  
* 📡 Optional own RTBL publish feed for local bans  
* 📣 Logs ban/unban actions in both admin and protected rooms  
* 🧾 SQLite audit log for moderation actions with automatic 365-day retention  
* 🧱 Structured JSON event logs for important moderation and operational events  
* 💾 Ban import/export to CSV format for backup and migration  
* 🧯 Automatic SQLite database backup before CSV imports  
* ✅ Input validation for JID format and domain bans  
* ✅ Startup and runtime config validation with safe reload handling  
* ⌨️ Configurable command prefix for all chat commands  
* 🚦 Rate limiting for all public protected-room commands  
* ⬆️ Periodic GitHub release checks with admin notifications and manual update checks  
* 🖼️ Avatar support (XEP-0054, XEP-0084, XEP-0153) with vCard customization  

---

## Commands (Admin Room)

> Examples below assume the default command prefix `!`. If you change `COMMAND_PREFIX`, replace `!` accordingly.

| Command | Description | Example |
|---------|-------------|---------|
| `!help` | Shows this help message | `!help` |
| `!config` | Shows current bot configuration | `!config` |
| `!reloadconfig` | Reloads `config.py` at runtime without restarting | `!reloadconfig` |
| `!status` | Shows bot status, active rooms, uptime, and ban statistics | `!status` |
| `!checkupdate` | Checks whether a newer GitHub release is available | `!checkupdate` |
| `!whoami` | Shows your affiliation/role and permissions in the current room | `!whoami` |
| `!room add <room>` | Adds a room to the protected list and stores it in the DB | `!room add secretroom@muc.example.com` |
| `!room remove <room>` | Removes a room from the protected list and DB | `!room remove secretroom@muc.example.com` |
| `!room list [page]` | Lists all protected rooms with pagination | `!room list` |
| `!ban <jid/nick/domain> [comment]` | Bans a user or domain across all protected rooms | `!ban alice@example.com spamming` or `!ban *.evil.com` |
| `!tempban <jid/nick> <10m/2h/1d> [comment]` | Temporary ban (limited to MAX_TEMPBAN_DAYS) | `!tempban bob 10m rude behavior` |
| `!unban <jid/nick/domain>` | Removes a ban | `!unban bob` or `!unban *.evil.com` |
| `!banlist [page/last]` | Shows all active bans with remaining time and comments | `!banlist`, `!banlist last` |
| `!bansearch <query> [page/last]` | Searches bans by nick, JID, domain, issuer, comment, or RTBL reason | `!bansearch spam`, `!bansearch reason:abuse 2` |
| `!why <nick/jid>` | Shows the reason and remaining time of a ban; admin-room output also includes recent audit history | `!why bob` |
| `!audit [page/last/query]` | Shows recent audit events, optionally filtered by text | `!audit`, `!audit last`, `!audit skx` |
| `!sync` | Full room sync: rejoin rooms, verify admin rights, apply only missing active bans | `!sync` |
| `!syncadmins` | Updates the internal admin list from the admin room | `!syncadmins` |
| `!syncbans` | Full ban synchronization: syncs outcasts from rooms into DB and applies all active bans | `!syncbans` |
| `!ignore list [page]` / `!whitelist list [page]` | Shows the global ignorelist | `!ignore list`, `!whitelist list last` |
| `!ignore add <jid/domain> [reason]` / `!whitelist add <jid/domain> [reason]` | Protects an exact JID from all bans, or a domain from domain-based bans/RTBL domain matches | `!whitelist add alice@example.com trusted admin` |
| `!ignore remove <jid/domain>` / `!whitelist remove <jid/domain>` | Removes an entry from the global ignorelist | `!whitelist remove alice@example.com` |
| `!banlist rtbl [page/last]` | Shows RTBL hash and domain entries | `!banlist rtbl`, `!banlist rtbl last` |
| `!rtbl list` | Shows active RTBL subscriptions and own publish feed counts | `!rtbl list` |
| `!rtbl add <service> <node>` | Subscribes to an RTBL PubSub node after validation | `!rtbl add xmppbl.org muc_bans_sha256` |
| `!rtbl delete <service> [node]` | Removes one or all RTBL subscriptions for a service | `!rtbl delete xmppbl.org muc_bans_sha256` |
| `!rtbl publish status` | Shows own RTBL publish feed status and local publish counts | `!rtbl publish status` |
| `!rtbl publish sync` | Publishes all current local bans to the own RTBL feed | `!rtbl publish sync` |
| `!export` | Exports all bans to a CSV file (bans_export_TIMESTAMP.csv) | `!export` |
| `!import <file>` | Imports bans from a CSV file with validation | `!import bans_export_20240412_120000.csv` |

---

## Public Commands (Protected Rooms)

> Examples below assume the default command prefix `!`. If you change `COMMAND_PREFIX`, replace `!` accordingly.

| Command       | Description                               | Example      |
| ------------- | ----------------------------------------- | ------------ |
| `!help`       | Shows a restricted help message           | `!help`      |
| `!whoami`     | Shows your affiliation/role and permissions | `!whoami`  |
| `!banlist [page/last]` | Shows active temporary bans (if enabled)  | `!banlist`, `!banlist last`   |
| `!why <jid/nick>` | Shows reason and remaining time for a ban | `!why alice` |

> ⚠️ **Visibility Rules:**
> - Permanent bans are **only shown in admin room**.
> - In protected rooms: only temporary bans are visible (if `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS=True`).
> - JID information is anonymized in protected rooms (only nick shown).
> - Admin issuers are anonymized in protected rooms; full admin JIDs are only shown in the admin room. RTBL bans are shown as `by rtbl`.
> - Public `!help`, `!whoami`, `!why` and `!banlist` are rate-limited per room, nick, and command. Admin-room use is not rate-limited.

---

## Installation

***Requires Python 3.10+***

### 1. Create a system user for the bot

```bash
sudo useradd -m -s /bin/bash -p "yourpassword" adminbot -d /srv/adminbot
sudo su - adminbot
```

### 2. Clone the repository

```bash
cd /srv/adminbot
git clone https://git.envs.net/envs/muc_banbot.git
cd muc_banbot
```

### 3. Setup Python virtual environment

```bash
sudo apt install python3-venv python3-pip

python3 -m venv venv
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configuration

Copy `config_sample.py` to `config.py` and configure as needed.

You can run `<prefix>reloadconfig` in the admin room to apply most changes immediately. Examples in this README use the default prefix `!`.  
`reloadconfig` validates `config.py`, keeps the last known good configuration active if reload fails, and reports warnings/errors in the admin room.

**Note:** The following settings **REQUIRE** a bot restart! `reloadconfig` will warn if any of these changed and keep the old running values active.

- `JID` - Bot's XMPP account
- `PASSWORD` - Bot's password
- `RESOURCE` / `RESSOURCE` - Bot's XMPP resource (`RESSOURCE` is legacy spelling)
- `ADMIN_ROOM` - JID of the admin control room
- `NICK` - Bot's nickname in rooms
- `DB_FILE` - Path to SQLite database
- `RTBL_ENABLED` - Enables/disables RTBL support
- `RTBL_PUBLISH_ENABLED` - Enables/disables the own RTBL publish feed
- `RTBL_PUBLISH_SERVICE` - PubSub service used for the own publish feed
- `RTBL_PUBLISH_JID_NODE` - Own publish node for SHA-256 JID hashes
- `RTBL_PUBLISH_DOMAIN_NODE` - Own publish node for domain bans

#### Configuration Options

**Required Settings:**
- `JID` - Bot's full JID (e.g., `bot@example.com`)
- `PASSWORD` - Bot's XMPP password
- `RESOURCE` - Bot's XMPP resource
- `ADMIN_ROOM` - Control room JID (e.g., `admin@muc.example.com`)
- `NICK` - Bot's nickname in rooms (default: `BanBot`)
- `DB_FILE` - SQLite database path (default: `banbot.db`)

**Optional Startup-Only Settings (require restart):**
- `RTBL_ENABLED` (bool, default: `False`) - Enable RTBL subscriptions and checks
- `RTBL_PUBLISH_ENABLED` (bool, default: `False`) - Enable the bot's own RTBL publish feed
- `RTBL_PUBLISH_SERVICE` (str) - PubSub service used for publishing local bans (e.g., `pubsub.example.org`)
- `RTBL_PUBLISH_JID_NODE` (str, default: `muc_bans_sha256`) - PubSub node for SHA-256 bare-JID hashes
- `RTBL_PUBLISH_DOMAIN_NODE` (str, default: `muc_bans_domains`) - PubSub node for plaintext domain bans

**Optional Settings (can be reloaded with `!reloadconfig`):**
- `AVATAR_PATH` (str) - Path to bot avatar image (PNG, JPG, etc.)
- `VCARD_NICKNAME` (str) - Bot's nickname in vCard
- `VCARD_FN` (str) - Bot's full name in vCard (e.g., "Ban Management Bot")
- `VCARD_ORG` (str) - Organization in vCard
- `VCARD_ROLE` (str) - Role in vCard (e.g., "Security")
- `VCARD_URL` (str) - Website or contact URL
- `VCARD_NOTE` (str) - Additional notes in vCard
- `COMMAND_PREFIX` (str, default: `!`) - Prefix used to trigger commands in rooms (for example `!help`, `.help`, `/help`)
- `ANNOUNCE_STARTUP` (bool, default: `True`) - Send status messages when bot starts
- `ANNOUNCE_SYNC_DETAILS` (bool, default: `True`) - Show detailed sync progress messages at startup
- `SHOW_BAN_IN_MUC` (bool, default: `False`) - Announce bans in protected rooms
- `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS` (bool, default: `True`) - Allow users to run `!help`, `!banlist`, `!why`
- `PUBLIC_COMMAND_RATE_LIMIT_WINDOW` (int, default: `30`) - Sliding window in seconds for public protected-room command rate limits
- `PUBLIC_COMMAND_RATE_LIMIT_MAX` (int, default: `3`) - Max public command uses per nick, room, and command within the rate-limit window
- `STRUCTURED_EVENT_LOGS` (bool, default: `True`) - Emit important bot events as JSON logs
- `AUDIT_LOG_ENABLED` (bool, default: `True`) - Store moderation and operational audit events in SQLite
- `AUDIT_LOG_RETENTION_DAYS` (int, default: `365`) - Delete audit events older than this many days; maximum 365
- `HEALTH_CHECK_INTERVAL` (int, default: `300`) - Seconds between health checks of room connectivity (minimum: 60)
- `UNBAN_CHECK_INTERVAL` (int, default: `60`) - Seconds between checking for expired tempbans
- `MAX_TEMPBAN_DAYS` (int, default: `30`) - Maximum temporary ban duration in days (1-365)
- `MUC_WRITE_SEMAPHORE` (int, default: `5`) - Concurrency limit for XMPP IQ operations
- `RTBL_ANNOUNCE` (bool, default: `True`) - Announce RTBL changes in the admin room; periodic refreshes stay quiet when nothing changed
- `RTBL_REFRESH_INTERVAL` (int, default: `3600`) - Seconds between periodic RTBL refreshes; set to `0` to disable periodic refresh
- `VERSION_CHECK_ENABLED` (bool, default: `False`) - Enable periodic checks for newer GitHub releases
- `VERSION_CHECK_INTERVAL` (int, default: `3600`) - Seconds between release checks (minimum: 300)
- `VERSION_CHECK_URL` (str, default: `https://github.com/envs-net/muc_banbot/releases/latest`) - URL used to detect the latest GitHub release

### 6. Test the bot manually

```bash
python muc_banbot.py
```

---

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

---

## Security Notes

* The bot account **must be moderator/admin** in all protected rooms.  
* Admin room is the **single source of truth** for permissions.  
* Admins/Owners are automatically protected from bans.  
* Admin/Owner protection checks both the local occupant cache and server-side room affiliations where available.  
* Bot automatically reports if admin/owner rights are lost or regained.
* Domain bans (`*.domain.tld`) will refuse to ban admins/owners on that domain.
* **JID validation** prevents malformed JIDs from being banned.
* **Domain ban validation** blocks overly generic bans (e.g., `*.com`).
* CSV imports create a timestamped database backup before any database write is attempted.
* Audit events are retained for up to 365 days and cleaned up automatically.
* The global ignorelist protects exact JIDs from all bans, and domains from domain-based bans/RTBL domain matches.
* `!rtbl add` refuses malformed PubSub services/nodes and refuses to subscribe to the bot's own publish nodes.
* If RTBL publish is enabled, ensure your PubSub service allows the bot to publish to the configured nodes.

---

## Advanced Features

### Ban Import/Export

The bot supports importing and exporting bans in CSV format for easy backup, migration, and batch operations.

#### Export Bans

```
!export
```

> Replace `!` with your configured `COMMAND_PREFIX` if you changed it.

Exports all current bans to a CSV file named `bans_export_YYYYMMDD_HHMMSS.csv` in the current working directory.

**CSV Format:**
```
jid,nick,until,issuer,comment
alice@example.com,Alice,0,admin@example.com,spamming
bob@example.com,Bob,1712923200,mod@example.com,rude behavior
```

#### Import Bans

```
!import <filename>
```

Imports bans from a CSV file with full validation:
- **JID Format**: Validates `user@domain.tld` format
- **Timestamps**: Supports `0` (permanent) or Unix timestamp for temporary bans
- **Smart Duplicates**: Automatically handles conflicts (converts permanent ↔ tempban)
- **Error Reporting**: Shows invalid rows with reasons (first 10 errors)
- **Pre-Import Backup**: Creates `banbot.db.backup-before-import-YYYYMMDD_HHMMSS` before writing imported rows
- **Atomic Operations**: All-or-nothing database updates

**Example Import:**
```
!import bans_backup.csv
```

Response:
```
📥 Import Results:
✅ Successful: 42
⚠️ Skipped: 3

❌ Errors (2):
Row 5: Invalid JID format: user@
Row 12: until must be a valid number
```

**Use Cases:**
- Backup before major operations
- Migrate bans to a new bot instance
- Batch import bans from external lists
- Restore from backup after database recovery


### Audit Log and Structured Event Logs

BanBot stores important moderation and operational events in the SQLite `audit_log` table when `AUDIT_LOG_ENABLED=True`.
Audit events are kept for `AUDIT_LOG_RETENTION_DAYS` days, with config validation capped at 365 days. Old audit events are cleaned up automatically during normal bot operation.

Examples of audited events:

- `ban_applied`
- `ban_updated`
- `ban_duplicate_ignored`
- `ban_refused_admin_protected`
- `unban_applied`
- `tempban_expired`
- `import_completed`
- `db_backup_created`

Use the admin command:

```
!audit
!audit 2
!audit skx
!audit ban_refused
```

Admin-room `!why <target>` also shows recent audit history for that target.

When `STRUCTURED_EVENT_LOGS=True`, important events are also logged as JSON objects, making them easier to process with tools like `journalctl`, `jq`, Loki, or ELK.

### Input Validation & Duration Limits

The bot validates all ban inputs to prevent errors:
- **JID Format Validation**: Checks for valid `user@domain.tld` format
- **Domain Ban Validation**: Requires specific domains (e.g., `*.spam-domain.com`), blocks generic TLDs like `*.com`
- **Tempban Duration Limits**: Enforces configurable limits (default max: 30 days, configurable up to 365 days)
- **Zero/Negative Duration Rejection**: Prevents empty, zero-length, or past temporary bans

Example validations:
```
❌ !ban invalid  → Invalid JID format
❌ !ban user@  → Invalid JID format
❌ !ban *.com  → Domain too generic
✅ !ban user@example.com  → Valid
✅ !ban *.spam-domain.com  → Valid
❌ !tempban user 0m  → Duration must be greater than zero
❌ !tempban user -1d  → Duration must be greater than zero
❌ !tempban user 400d  → Duration exceeds MAX_TEMPBAN_DAYS (30)
✅ !tempban user 30d  → Valid
```

### Smart Duplicate Ban Handling

When banning an existing user, the bot intelligently handles the action:

| Scenario | Action | Message |
|----------|--------|---------|
| Permanent ban exists, applying permanent | Returns info | `ℹ️ Ban already exists (permanent)` |
| Permanent ban exists, applying tempban | Converts | `🔄 Converting permanent ban to tempban (10m)` |
| Tempban exists, applying permanent | Converts | `🔄 Converting tempban to permanent ban` |
| Tempban exists, applying new tempban | Updates | `🔄 Ban updated: duration changed from 10m to 20m` |

### Auto-Update Nick-Only Bans

When a user is banned by nick only (not by JID), and they rejoin the room, the bot automatically:
1. Detects their full JID
2. Updates the ban in the database with their JID
3. Applies the corrected JID-based ban to all rooms

This improves ban reliability for users who may change nicks.

### Domain-Based Bans

Ban all users from a domain using the `*.domain.tld` format:

```
!ban *.evil.com
!tempban *.spam.org 2h
!unban *.evil.com
```

A wildcard domain ban matches both the base domain and its subdomains:

- `*.evil.com` matches `user@evil.com`
- `*.evil.com` also matches `user@chat.evil.com`
- Generic TLD bans such as `*.com` or `*.org` are blocked

Safety behavior:
- Will refuse to ban admins/owners on the domain
- Automatically kicks all current users from that domain
- Prevents future logins from that domain
- Must be specific (e.g., `*.spam-domain.com`, not `*.com`)

### Global Ignorelist

The global ignorelist/whitelist has two protection modes: exact JID entries protect that JID from **all** bans, while domain entries protect against domain-based bans and RTBL domain matches. A domain entry does **not** block an explicit manual ban of an individual JID on that domain. The `!whitelist` command is an alias for `!ignore`.

```
!ignore list
!ignore add alice@example.com trusted user
!ignore add *.example.org local domain
!ignore remove alice@example.com

# Alias:
!whitelist list
!whitelist add alice@example.com trusted user
!whitelist remove alice@example.com
```

Ignorelist behavior:
- Exact bare JIDs are protected from all bans, including manual bans and RTBL hash/domain matches
- Wildcard domains such as `*.example.org` protect against domain-based bans and RTBL domain matches
- Domain suffix matching is used, so `*.example.org` also protects subdomains for domain-based matches
- Domain entries do not block explicit manual bans of individual JIDs such as `user@example.org`
- Entries are loaded into memory and checked before matching bans are written/applied
- Existing legacy RTBL-only ignorelist entries are migrated into the global ignorelist table if present

### RTBL Subscriptions and Own Publish Feed

BanBot supports RTBL (Real-Time Block List) PubSub feeds:

- **Inbound subscriptions**: subscribe to external RTBL nodes and apply matching bans at join time or on live PubSub events
- **JID entries**: SHA-256 hashes of bare JIDs, compatible with `muc_bans_sha256`
- **Domain entries**: plaintext domains; matching occupants are locally persisted as concrete JID bans with the RTBL domain source in the comment
- **Applied RTBL persistence**: when an inbound RTBL entry actually matches and is applied, the resulting local ban is stored in the main `bans` table with `issuer=rtbl`
- **Current occupant scan**: startup fetches and newly added subscriptions immediately scan current occupants so matching users are banned without waiting for a rejoin; periodic refreshes stay quiet and do not rescan unchanged lists
- **Own publish feed**: publish local non-RTBL bans to your own PubSub service for other bots to consume

Common commands:

```
!rtbl list
!rtbl add xmppbl.org muc_bans_sha256
!rtbl add xmppbl.org spam_source_domains
!rtbl delete xmppbl.org muc_bans_sha256
!banlist rtbl
!rtbl publish status
!rtbl publish sync
```

Banlist behavior:
- `!banlist rtbl` shows the raw RTBL subscription entries from `rtbl_hashes` and `rtbl_domains`.
- `!banlist` shows applied bans from the main `bans` table. RTBL-applied entries use the 🛡️ icon and `by rtbl`.
- For RTBL domain matches, the main banlist stores the concrete matched JID, for example `picelboi@xmpp.earth`, with a comment such as `RTBL domain ban: *.xmpp.earth`.
- The RTBL domain rule itself remains in `rtbl_domains`; it is not stored as a local wildcard ban unless it came from an older version.
- For RTBL JID-hash matches, the main banlist stores the resolved bare JID if the bot can match the hash to a current occupant.

Safety and validation:
- `!rtbl add` validates that the service looks like a PubSub service/domain and that the node is non-empty
- The bot attempts to subscribe before writing the subscription into the database
- `!rtbl delete` refuses to report success for non-existing subscriptions
- The bot refuses to subscribe to its own configured publish nodes
- Periodic refreshes only announce when new or updated RTBL entries are found
- Admin/owner protection and the global ignorelist are checked before any RTBL ban is applied
- Exact JID ignorelist entries block RTBL hash and domain matches for that JID
- Domain ignorelist entries block RTBL domain matches, but do not suppress RTBL hash matches for a specifically listed JID hash
- Removing a subscription or receiving RTBL retractions removes stale persisted `issuer=rtbl` bans when they are no longer present in active subscriptions
- Inbound RTBL bans are not mirrored into the bot's own RTBL publish feed

RTBL publish nodes on Prosody can be created/configured manually if your server does not allow the bot to create and own nodes itself. Replace `pubsub.example.org`, the node names, and `adminbot@example.org` with your configured `RTBL_PUBLISH_SERVICE`, `RTBL_PUBLISH_JID_NODE`, `RTBL_PUBLISH_DOMAIN_NODE`, and bot bare JID.

```lua
-- Optional cleanup when recreating test nodes:
pubsub:delete_node("pubsub.example.org", "muc_bans_sha256")
pubsub:delete_node("pubsub.example.org", "muc_bans_domains")

-- Create the publish nodes:
pubsub:create_node("pubsub.example.org", "muc_bans_sha256")
pubsub:create_node("pubsub.example.org", "muc_bans_domains")

-- Make the bot owner/publisher for both nodes:
local service = hosts["pubsub.example.org"].modules.pubsub.service
service:set_affiliation("muc_bans_sha256", true, "adminbot@example.org", "owner")
service:set_affiliation("muc_bans_domains", true, "adminbot@example.org", "owner")

-- Keep publishing restricted to node publishers/owners:
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_sha256", "pubsub#publish_model", "publishers")
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_domains", "pubsub#publish_model", "publishers")

-- Allow enough retained RTBL items:
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_sha256", "pubsub#max_items", "1000")
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_domains", "pubsub#max_items", "1000")
```

`pubsub#publish_model` should be `publishers`, not `open`, so arbitrary users cannot publish items into your RTBL nodes.

### Avatar & vCard Support

The bot can display a custom avatar and vCard profile via:
- **XEP-0054** (vCard-temp) - Traditional vCard with photo
- **XEP-0084** (User Avatar) - Modern avatar format
- **XEP-0153** (vCard-temp Update) - Avatar hash in presence

Configure in `config.py`:
```python
AVATAR_PATH = "path/to/avatar.png"
VCARD_NICKNAME = "My Bot Nickname"
VCARD_FN = "Admin Bot"
VCARD_ORG = "Your Organization"
VCARD_ROLE = "Administrator"
VCARD_URL = "https://example.com"
VCARD_NOTE = "Bot Admin Assistant"
```

Avatar is updated:
- Automatically on bot startup
- When `!reloadconfig` is executed
- Supports PNG, JPG, GIF, and other standard image formats

### Admin Rights Monitoring

The bot continuously monitors its affiliation status in all rooms:
- ✅ If rights are regained → announces to admin room
- ⚠️ If rights are lost → notifies admin room and prevents bans
- Spam-safe: only triggers on real state changes (not spammed on every presence)

The `!status` command shows the current admin/owner status in each room.

### Periodic Health Checks

The bot runs a **health check worker** that:
- Periodically verifies bot is still connected to all rooms (configurable interval, default: 300s)
- Checks if bot maintains admin/owner rights in each room
- Notifies admin room if connectivity or rights issues are detected
- Configurable via `HEALTH_CHECK_INTERVAL` (minimum: 60 seconds)

### Automatic Temporary Ban Expiration

The bot runs an automatic **unban worker** that:
- Checks for expired temporary bans every 60 seconds (configurable)
- Automatically removes outcast affiliations for expired bans
- Restores `participant` role to users who are currently online
- Logs all auto-unbans to the admin room

### Release Update Checks

The bot can periodically check the latest GitHub release page and notify the admin room when a newer version is available.

- Automatic checks run in the background when `VERSION_CHECK_ENABLED=True`
- The release check interval is controlled by `VERSION_CHECK_INTERVAL`
- The release URL is configurable via `VERSION_CHECK_URL`
- Manual checks are available with `!checkupdate` (or your configured `COMMAND_PREFIX`)
- When a newer release is found, the bot logs the event and includes the release page URL in the admin-room notification

### Configuration Display

The `!config` command displays all current bot configuration settings in the admin room:
- Bot JID and nickname
- Database path
- Check intervals (health check, unban check, RTBL refresh, version check)
- Feature flags (announcements, ban visibility, user commands, RTBL, RTBL publish)
- Tempban limits (MAX_TEMPBAN_DAYS)
- Bot version displayed in `!config` and `!status`
- Examples in this README assume the default `!` prefix; if you set `COMMAND_PREFIX`, commands use that prefix instead

The `!status` command shows a dynamic health headline. It reports problems/warnings such as reconnects in progress, missing admin/owner rights, stopped background workers, missing protected rooms, DB stats failures, pending expired tempbans, and RTBL subscription/publish state.

---

## Sync and Room/Ban Commands Overview

| Command                          | Effect                                                                                            | When Useful / Example Use Case                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `!room add <room>`               | Adds a new protected room to the list **and saves it in the database**                            | After creating a new room to protect; optionally run `!syncbans`  |
| `!room remove <room>`            | Removes a room from the protected list and database; bot leaves immediately                       | Stop protecting a room; bot will no longer enforce bans   |
| `!sync`                          | Full sync: rejoin rooms, verify admin rights, apply only MISSING active bans (fast)               | After bot was disconnected or removed from rooms          |
| `!syncadmins`                    | Updates internal admin list from admin room (via server query)                                    | After adding/removing admins or owners; at startup        |
| `!syncbans`                      | Full ban synchronization: reads outcasts from all rooms, adds orphan bans to DB, reapplies all    | After manual ban changes in rooms or DB recovery          |
| `sync_bans_startup()` (internal) | Runs automatically on bot startup; applies only active (non-expired) bans                         | Not an admin command. Ensures bans enforced after restart |

**Key Differences:**
- **`!sync`**: Faster, applies only bans that aren't already set; useful for reconnects
- **`!syncbans`**: Slower, comprehensive; finds orphan outcasts and syncs them into DB

**Tips:**
- Add new rooms: `!room add <room>` → optionally `!syncbans` to import existing bans
- Update admin list: `!syncadmins`
- Check ban consistency / adopt orphan outcasts: `!syncbans`
- After restart or room removal: `!sync` + `!syncbans`
- Normal operation: `!sync` usually not needed; `!syncbans` only for maintenance

---

## Database (SQLite)

**`banbot.db`** contains the main moderation tables plus optional RTBL/ignorelist tables:

### `bans`

| Column        | Type    | Description |
| ------------- | ------- | ----------- |
| `id`          | INTEGER | Internal row id |
| `target_type` | TEXT    | `jid`, `nick`, or `domain` |
| `target`      | TEXT    | Normalized unique target key |
| `jid`         | TEXT    | Bare JID or wildcard domain (`*.domain.tld`) if known |
| `nick`        | TEXT    | User nickname if known |
| `until`       | INTEGER | Expiration time as Unix timestamp (`0` = permanent) |
| `issuer`      | TEXT    | Who issued the ban, or `system` for auto-unban |
| `comment`     | TEXT    | Optional reason/comment |
| `created_at`  | INTEGER | Creation timestamp |
| `updated_at`  | INTEGER | Last update timestamp |

### `audit_log`

| Column       | Type    | Description |
| ------------ | ------- | ----------- |
| `id`         | INTEGER | Internal row id |
| `created_at` | INTEGER | Event timestamp |
| `event_type` | TEXT    | Event name, e.g. `ban_applied`, `unban_applied` |
| `actor`      | TEXT    | Admin nick or `system` |
| `room`       | TEXT    | Related room if applicable |
| `target_type`| TEXT    | `jid`, `nick`, or `domain` if applicable |
| `target`     | TEXT    | Normalized target if applicable |
| `jid`        | TEXT    | Related JID if applicable |
| `nick`       | TEXT    | Related nick if applicable |
| `until`      | INTEGER | Expiration timestamp if applicable |
| `comment`    | TEXT    | Reason/comment if applicable |
| `details`    | TEXT    | JSON metadata for event-specific details |

### `rooms`

| Column | Type | Description         |
| ------ | ---- | ------------------- |
| `room` | TEXT | Protected room JID  |

### `ignorelist`

| Column        | Type    | Description |
| ------------- | ------- | ----------- |
| `id`          | INTEGER | Internal row id |
| `target`      | TEXT    | Protected JID or domain |
| `target_type` | TEXT    | `jid` or `domain` |
| `reason`      | TEXT    | Optional reason |
| `added_by`    | TEXT    | Admin who added the entry |
| `created_at`  | INTEGER | Creation timestamp |

### `rtbl_subscriptions`

| Column        | Type    | Description |
| ------------- | ------- | ----------- |
| `id`          | INTEGER | Internal row id |
| `service_jid` | TEXT    | PubSub service JID/domain |
| `node`        | TEXT    | PubSub node name |
| `created_at`  | INTEGER | Creation timestamp |

### `rtbl_hashes`

| Column        | Type    | Description |
| ------------- | ------- | ----------- |
| `hash`        | TEXT    | SHA-256 hash of a bare JID |
| `service_jid` | TEXT    | Source PubSub service |
| `node`        | TEXT    | Source PubSub node |
| `reason`      | TEXT    | Optional RTBL reason |
| `created_at`  | INTEGER | Creation timestamp |

### `rtbl_domains`

| Column        | Type    | Description |
| ------------- | ------- | ----------- |
| `domain`      | TEXT    | Plain domain from RTBL feed |
| `service_jid` | TEXT    | Source PubSub service |
| `node`        | TEXT    | Source PubSub node |
| `reason`      | TEXT    | Optional RTBL reason |
| `created_at`  | INTEGER | Creation timestamp |

---

## Troubleshooting

### Invalid JID format error

```
❌ Invalid JID format: user@. Expected: user@domain.tld
```

**Solution:** Use valid JID format with both local part and domain:
- ✅ `user@example.com`
- ✅ `alice@my-server.org`
- ❌ `user@` (missing domain)
- ❌ `@example.com` (missing local part)

### Domain ban too generic error

```
❌ Domain '*.com' is too generic. Specify more precise domain (e.g., *.domain.tld).
```

**Solution:** Use specific domain bans, not generic TLDs:
- ✅ `!ban *.spam-domain.com`
- ✅ `!ban *.evil-company.co.uk`
- ❌ `!ban *.com` (too generic)
- ❌ `!ban *.org` (too generic)

### Tempban duration exceeds limit

```
❌ Tempban duration exceeds MAX_TEMPBAN_DAYS (30 days). Max: 30 days.
```

**Solution:** Use duration within limits, or adjust `MAX_TEMPBAN_DAYS` in config (max: 365):
- ✅ `!tempban user 30d`
- ✅ `!tempban user 10m`
- ❌ `!tempban user 365d` (if MAX_TEMPBAN_DAYS is 30)

### Bot loses admin rights in a room

The bot will automatically notify the admin room. To fix:
1. Ensure the bot account has admin/owner affiliation in the room
2. Check room settings on your XMPP server
3. Run `!sync` to verify rights are restored

### Ban not being enforced

Run `!syncbans` to:
- Check if the ban exists in the database
- Verify the outcast affiliation is set in the room
- Reapply the ban if needed

### Tempbans not expiring

Verify `UNBAN_CHECK_INTERVAL` is set in config.py (default 60 seconds). The unban worker runs in the background automatically.

### Health check warnings

If you see health check warnings in the admin room, the bot has detected:
- Bot is not in room occupants list → likely a network issue, will attempt rejoin
- Bot lost admin/owner rights → check room configuration on server

Run `!sync` to re-establish connections and verify rights.

### RTBL subscription cannot be added

If `!rtbl add <service> <node>` fails:
- Verify the service looks like a PubSub service/domain, for example `xmppbl.org` or `pubsub.example.org`
- Verify the node name has no spaces and exists on the service
- Ensure the remote service is reachable from the bot account
- The bot refuses to subscribe to its own configured publish feed nodes

### RTBL publish node create/configure is forbidden

If the bot can publish only after you manually created nodes, create/configure the nodes with the Prosody console as shown in the RTBL section above. The bot treats `forbidden` during node configuration as informational if publishing still succeeds.

### RTBL bans are not being applied

Check:
- `RTBL_ENABLED=True`
- `!rtbl list` shows active subscriptions and non-zero hash/domain counts
- The user is not protected by an exact JID `!ignore` entry or admin/owner protection
- For RTBL domain matches, the user's domain is not protected by a domain `!ignore` entry
- Startup and newly added subscription fetches scan current occupants immediately; periodic refreshes do not rescan unchanged lists

---

## Notes

* Temporary bans expire automatically; the bot removes them periodically (configurable interval).  
* Changes to `config.py` can **usually be applied via your configured command prefix + `reloadconfig`** (examples here use `!reloadconfig`). Startup-only settings such as `JID`, `PASSWORD`, `RESOURCE` / `RESSOURCE`, `ADMIN_ROOM`, `NICK`, and `DB_FILE` require a restart.
* The bot schedules an automatic reconnect after disconnects and restores room/admin state during reconnect.
* Domain bans are stored as-is (e.g., `*.domain.tld`) and can be searched/unbanned using `!bansearch` and `!unban`
* Bot prevents banning of admins/owners, even via domain bans
* RTBL subscription data (`rtbl_hashes`, `rtbl_domains`) is stored separately from applied bans. When an RTBL entry actually matches, the applied ban is stored in the main `bans` table with `issuer=rtbl`. Domain RTBL matches are stored locally as concrete JID bans so they can be unbanned or ignored per user.
* The own RTBL publish feed mirrors active local non-RTBL bans; it should not be added back as an inbound RTBL subscription.
