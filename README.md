# BanBot - XMPP Multi-Room Ban Management Bot

BanBot is an XMPP bot for managing bans and temporary bans across multiple MUC rooms (Multi-User Chat).  
It provides central administration via an admin room and protects multiple chat rooms from unwanted users.

---

## Features

* 🛡️ Central admin room for all commands  
* ❌ Ban, temporary ban, unban, banlist, bansearch, why  
* 🌐 Domain-based bans (`*.domain.tld`) to ban all users from a domain  
* 📝 Optional comment when banning (e.g., `!tempban user 10m spamming`)  
* 🔒 Dynamic addition/removal of protected rooms  
* 🔄 Automatic rejoin and reapplication of bans on restart  
* 📦 Sync existing room bans into the database at startup  
* ⏱️ Human-readable remaining time for temporary bans  
* ⏳ Automatic removal of expired temporary bans  
* 📣 Logs ban/unban actions in both admin and protected rooms  
* ⚠️ Admins/Owners are protected from accidental bans  
* 👀 Monitors bot's admin/owner rights per room and reports loss to the admin room  
* ⛔ Prevents ban application if the bot does not have admin/owner rights  
* 🐞 Handles nick-only bans with best-effort enforcement  
* 🔄 Auto-updates nick-only bans to JID when user rejoins  
* 🖼️ Avatar support (XEP-0054, XEP-0084, XEP-0153) with vCard customization  
* ✅ Input validation for JID format and domain bans  
* 📊 Smart duplicate ban handling with automatic conversion (Permanent ↔ Tempban)  
* ⏰ Configurable tempban duration limits (1-365 days, default: 30)  
* 🏥 Periodic health checks for room connectivity and admin rights

---

## Commands (Admin Room)

| Command | Description | Example |
|---------|-------------|---------|
| `!help` | Shows this help message | `!help` |
| `!config` | Shows current bot configuration | `!config` |
| `!reloadconfig` | Reloads `config.py` at runtime without restarting | `!reloadconfig` |
| `!status` | Shows bot status, active rooms, uptime, and ban statistics | `!status` |
| `!whoami` | Shows your affiliation/role in the current room | `!whoami` |
| `!room add <room>` | Adds a room to the protected list and stores it in the DB | `!room add secretroom@muc.example.com` |
| `!room remove <room>` | Removes a room from the protected list and DB | `!room remove secretroom@muc.example.com` |
| `!room list` | Lists all protected rooms | `!room list` |
| `!ban <jid/nick/domain> [comment]` | Bans a user or domain across all protected rooms | `!ban alice@example.com spamming` or `!ban *.evil.com` |
| `!tempban <jid/nick> <10m/2h/1d> [comment]` | Temporary ban (limited to MAX_TEMPBAN_DAYS) | `!tempban bob 10m rude behavior` |
| `!unban <jid/nick/domain>` | Removes a ban | `!unban bob` or `!unban *.evil.com` |
| `!banlist` | Shows all active bans with remaining time and comments | `!banlist` |
| `!bansearch <query>` | Searches bans by nick, JID, domain, or issuer | `!bansearch example.com` |
| `!why <nick/jid>` | Shows the reason and remaining time of a ban | `!why bob` |
| `!sync` | Full room sync: rejoin rooms, verify admin rights, apply only missing active bans | `!sync` |
| `!syncadmins` | Updates the internal admin list from the admin room | `!syncadmins` |
| `!syncbans` | Full ban synchronization: syncs outcasts from rooms into DB and applies all active bans | `!syncbans` |

---

## Public Commands (Protected Rooms)

| Command       | Description                               | Example      |
| ------------- | ----------------------------------------- | ------------ |
| `!help`       | Shows a restricted help message           | `!help`      |
| `!banlist`    | Shows active temporary bans (if enabled)  | `!banlist`   |
| `!why <jid/nick>` | Shows reason and remaining time for a ban | `!why alice` |

> ⚠️ **Visibility Rules:**
> - Permanent bans are **only shown in admin room**.
> - In protected rooms: only temporary bans are visible (if `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS=True`).
> - JID information is anonymized in protected rooms (only nick shown).

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

You can run `!reloadconfig` in the admin room to apply most changes immediately.  
**Note:** The following settings **REQUIRE** a bot restart!

- `JID` - Bot's XMPP account
- `PASSWORD` - Bot's password
- `ADMIN_ROOM` - JID of the admin control room
- `NICK` - Bot's nickname in rooms
- `DB_FILE` - Path to SQLite database

#### Configuration Options

**Required Settings:**
- `JID` - Bot's full JID (e.g., `bot@example.com`)
- `PASSWORD` - Bot's XMPP password
- `ADMIN_ROOM` - Control room JID (e.g., `admin@muc.example.com`)
- `NICK` - Bot's nickname in rooms (default: `BanBot`)
- `DB_FILE` - SQLite database path (default: `banbot.db`)

**Optional Settings (can be reloaded with `!reloadconfig`):**
- `ANNOUNCE_STARTUP` (bool, default: `True`) - Send status messages when bot starts
- `SHOW_BAN_IN_MUC` (bool, default: `True`) - Announce bans in protected rooms
- `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS` (bool, default: `True`) - Allow users to run `!help`, `!banlist`, `!why`
- `HEALTH_CHECK_INTERVAL` (int, default: `300`) - Seconds between health checks of room connectivity (minimum: 60)
- `UNBAN_CHECK_INTERVAL` (int, default: `60`) - Seconds between checking for expired tempbans
- `MAX_TEMPBAN_DAYS` (int, default: `30`) - Maximum temporary ban duration in days (1-365)
- `AVATAR_PATH` (str) - Path to bot avatar image (PNG, JPG, etc.)
- `VCARD_NICKNAME` (str) - Bot's nickname in vCard
- `VCARD_FN` (str) - Bot's full name in vCard (e.g., "Ban Management Bot")
- `VCARD_ORG` (str) - Organization in vCard
- `VCARD_ROLE` (str) - Role in vCard (e.g., "Security")
- `VCARD_URL` (str) - Website or contact URL
- `VCARD_NOTE` (str) - Additional notes in vCard

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
ExecStart=/srv/adminbot/venv/bin/python /srv/adminbot/muc_banbot/muc_banbot.py
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
* Bot automatically reports if admin/owner rights are lost or regained.
* Domain bans (`*.domain.tld`) will refuse to ban admins/owners on that domain.
* **JID validation** prevents malformed JIDs from being banned.
* **Domain ban validation** blocks overly generic bans (e.g., `*.com`).

---

## Advanced Features

### Input Validation & Duration Limits

The bot validates all ban inputs to prevent errors:
- **JID Format Validation**: Checks for valid `user@domain.tld` format
- **Domain Ban Validation**: Requires specific domains (e.g., `*.spam-domain.com`), blocks generic TLDs like `*.com`
- **Tempban Duration Limits**: Enforces configurable limits (default max: 30 days, configurable up to 365 days)
- **Negative Duration Rejection**: Prevents banning into the past

Example validations:
```
❌ !ban invalid  → Invalid JID format
❌ !ban user@  → Invalid JID format
❌ !ban *.com  → Domain too generic
✅ !ban user@example.com  → Valid
✅ !ban *.spam-domain.com  → Valid
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

Protections:
- Will refuse to ban admins/owners on the domain
- Automatically kicks all current users from that domain
- Prevents future logins from that domain
- Must be specific (e.g., `*.spam-domain.com`, not `*.com`)

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

### Configuration Display

The `!config` command displays all current bot configuration settings in the admin room:
- Bot JID and nickname
- Database path
- Check intervals (health check, unban check)
- Feature flags (announcements, ban visibility, user commands)
- Tempban limits (MAX_TEMPBAN_DAYS)

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

**`banbot.db`** with two tables:

### `bans`

| Column    | Type    | Description                                         |
| --------- | ------- | --------------------------------------------------- |
| `jid`     | TEXT    | User JID (optional if nick exists; can be `*.domain.tld` for domain bans) |
| `nick`    | TEXT    | User nickname (optional if JID exists)              |
| `until`   | INTEGER | Expiration time as Unix timestamp (`0` = permanent) |
| `issuer`  | TEXT    | Who issued the ban (username or "system" for auto-unban) |
| `comment` | TEXT    | Optional reason/comment                             |

### `rooms`

| Column | Type | Description         |
| ------ | ---- | ------------------- |
| `room` | TEXT | Protected room JID  |

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

---

## Notes

* Temporary bans expire automatically; the bot removes them periodically (configurable interval).  
* `!why` supports lookup by **JID**, **Nick**, or domain prefix from JID.
* Messages in protected rooms are sent as **ephemeral** (not stored); admin room always receives full notifications.
* Changes to `config.py` can **usually be applied via `!reloadconfig`** (except critical settings).
* The bot uses **exponential backoff** (max 5 minutes) if disconnected from the XMPP server.
* Domain bans are stored as-is (e.g., `*.domain.tld`) an
