# BanBot - XMPP Multi-Room Ban Management Bot

BanBot is an XMPP bot for managing bans and temporary bans in multiple MUC (Multi-User Chat) rooms.  
It allows centralized administration from a designated admin room and protects multiple chat rooms from unwanted users.

---

## Features

* 🛡️ Central admin room for issuing commands
* ❌ Ban, temporary ban, unban, and banlist commands
* 📝 Optional comments for bans (e.g., `!tempban user 10m calm down!`)
* 🔒 Add/remove protected rooms dynamically
* 🔄 Auto-rejoin and auto-apply bans on restart
* 📦 Auto-sync existing room bans into the database at startup
* ⏱️ Human-readable remaining time for temporary bans
* ⏳ Automatic unbanning of expired bans
* 📣 Logs ban/unban actions in both protected rooms and admin room
* ⚠️ Admins/owners are protected from accidental banning
* 🐞 Safe handling of nick-only bans with best-effort enforcement

---

## Commands (Admin Room)

| Command | Description | Example |
|---------|-------------|---------|
| `!help` | Show available commands | `!help` |
| `!ban <jid/nick> [comment]` | Ban a user from all protected rooms | `!ban alice@example.com spamming` |
| `!tempban <jid/nick> <duration> [comment]` | Temporarily ban a user | `!tempban bob 10m rude behavior` |
| `!unban <jid/nick>` | Unban a user from all protected rooms | `!unban bob` |
| `!banlist` | Show all active bans with remaining time and comments | `!banlist` |
| `!bansearch <query>` | Search bans by nick, JID, or domain | `!bansearch example.com` |
| `!room add <room>` | Add a room to the protected list and DB | `!room add secretroom@muc.example.com` |
| `!room remove <room>` | Remove a room from the protected list and DB | `!room remove secretroom@muc.example.com` |
| `!room list` | List all protected rooms | `!room list` |
| `!sync` | Rejoin all protected rooms | `!sync` |
| `!syncadmins` | Update admin list from the admin room | `!syncadmins` |
| `!syncbans` | Sync existing bans from rooms into the DB and enforce them | `!syncbans` |
| `!reloadconfig` | Reload `config.py` at runtime | `!reloadconfig` |
| `!status` | Show bot health and active rooms/admins | `!status` |
| `!whoami` | Show your role/affiliation | `!whoami` |
| `!why <jid/nick>` | Show reason and remaining time for a ban | `!why bob` |

---

## Public Commands (Protected Rooms)

| Command       | Description                              | Example      |
| ------------- | ---------------------------------------- | ------------ |
| `!help`       | Show limited help                        | `!help`      |
| `!banlist`    | Show active temporary bans               | `!banlist`   |
| `!why <jid/nick>` | Show reason and remaining time for a ban | `!why alice` |

> ⚠️ Permanent bans are not shown in protected rooms for privacy; only temporary bans are displayed.

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
python3 -m venv venv
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configuration

Copy `config_sample.py` to `config.py` and configure as needed.  

**The following changes do not require a bot restart:**
(run `!reloadconfig` in the admin room to apply changes immediately.)

- ANNOUNCE_STARTUP
- SHOW_BAN_IN_MUC
- ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS

### 6. Test the bot manually

```bash
python muc_banbot.py
```

---

## Systemd Service

Create `/etc/systemd/system/muc_banbot.service`:

```bash
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

* Bot account **must have moderator or admin privileges** in all protected rooms.
* Admin room is the **single source of truth** for authorization; only users with owner/admin affiliation can issue admin commands.
* Admins/owners are immune to bans by design.

---

## Sync and Room/Ban Commands Overview

| Command                          | Effect                                                                                            | When Useful / Example Use Case                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `!room add <room>`               | Adds a new protected room to the list **and saves it in the database**                            | After creating a new room to protect; optionally run `!sync` to join immediately  |
| `!room remove <room>`            | Removes a room from the protected list and database                                               | Stop protecting a room; bot will no longer enforce bans there                     |
| `!sync`                          | Bot rejoins all protected rooms                                                                   | Needed if bot was disconnected or removed from rooms                              |
| `!syncadmins`                    | Updates internal admin list from admin room                                                       | Use after changing admins/owners, or at startup                                   |
| `!syncbans`                      | Full ban synchronization: database ↔ rooms; reads outcasts from rooms, updates DB, reapplies bans | Use after manual ban changes or DB recovery                                       |
| `sync_bans_startup()` (internal) | Runs automatically on bot startup; applies only active bans                                       | Not an admin command. Ensures bans are enforced at startup                        |

**Key Takeaways**  

* **Adding new rooms:** `!room add <room>` → optionally `!sync`
* **Updating admin list:** `!syncadmins`
* **Fixing ban inconsistencies / syncing outcasts:** `!syncbans`
* **Bot restarted or kicked from rooms:** `!sync` + `!syncbans`
* *Normal operation:* `!sync` usually not needed; `!syncbans` only when necessary

---

## Database (SQLite)

**`bans.db`** with two tables:

### `bans`

| Column    | Type    | Description                                         |
| --------- | ------- | --------------------------------------------------- |
| `jid`     | TEXT    | User JID (optional if nick exists)                  |
| `nick`    | TEXT    | User nickname (optional if JID exists)              |
| `until`   | INTEGER | Expiration time as Unix timestamp (`0` = permanent) |
| `issuer`  | TEXT    | Who issued the ban                                  |
| `comment` | TEXT    | Optional reason/comment                             |

### `rooms`

| Column | Type | Description         |
| ------ | ---- | ------------------- |
| `room` | TEXT | Protected room name |

---

## Notes

* Temporary bans expire automatically and are lifted via the unban worker.
* `!why` supports lookup by **JID** or **nick**, including nick-to-JID mapping.
* Ephemeral messages in protected rooms do not persist.
* Admin room always receives full notifications about ban/unban actions.
* Changing `SHOW_BAN_IN_MUC` or `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS` can be applied **without bot restart** via `!reloadconfig`.
