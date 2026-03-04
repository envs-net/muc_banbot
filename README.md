# BanBot - XMPP Multi-Room Ban Management Bot

BanBot is an XMPP bot for managing bans and temporary bans across multiple MUC rooms (Multi-User Chat).  
It provides central administration via an admin room and protects multiple chat rooms from unwanted users.

---

## Features

* 🛡️ Central admin room for all commands  
* ❌ Ban, temporary ban, unban, banlist, bansearch, why  
* 📝 Optional comment when banning (e.g., `!tempban user 10m spamming`)  
* 🔒 Dynamic addition/removal of protected rooms  
* 🔄 Automatic rejoin and reapplication of bans on restart  
* 📦 Sync existing room bans into the database at startup  
* ⏱️ Human-readable remaining time for temporary bans  
* ⏳ Automatic removal of expired temporary bans  
* 📣 Logs ban/unban actions in both admin and protected rooms  
* ⚠️ Admins/Owners are protected from accidental bans  
* 👀 Monitors bot’s admin/owner rights per room and reports loss to the admin room  
* ⛔ Prevents ban application if the bot does not have admin/owner rights  
* 🐞 Handles nick-only bans with best-effort enforcement  

---

## Commands (Admin Room)

| Command | Description | Example |
|---------|-------------|---------|
| `!help` | Shows this help message | `!help` |
| `!reloadconfig` | Reloads `config.py` at runtime without restarting | `!reloadconfig` |
| `!status` | Shows bot status, active rooms, and bot admin/owner rights | `!status` |
| `!whoami` | Shows your affiliation/role in the current room | `!whoami` |
| `!room add <room>` | Adds a room to the protected list and stores it in the DB | `!room add secretroom@muc.example.com` |
| `!room remove <room>` | Removes a room from the protected list and DB | `!room remove secretroom@muc.example.com` |
| `!room list` | Lists all protected rooms | `!room list` |
| `!ban <jid|nick> [comment]` | Bans a user across all protected rooms | `!ban alice@example.com spamming` |
| `!tempban <jid|nick> <10m|2h|1d> [comment]` | Temporary ban | `!tempban bob 10m rude behavior` |
| `!unban <jid|nick>` | Removes a ban | `!unban bob` |
| `!banlist` | Shows all active bans with remaining time and comments | `!banlist` |
| `!bansearch <query>` | Searches bans by nick, JID, or domain | `!bansearch example.com` |
| `!why <nick|jid>` | Shows the reason and remaining time of a ban | `!why bob` |
| `!sync` | Full room sync: rejoins all protected rooms, checks bot admin/owner rights, and applies all active bans | `!sync` |
| `!syncadmins` | Updates the internal admin list from the admin room | `!syncadmins` |
| `!syncbans` | Syncs bans from all rooms into the DB and applies them | `!syncbans` |

---

## Public Commands (Protected Rooms)

| Command       | Description                               | Example      |
| ------------- | ----------------------------------------- | ------------ |
| `!help`       | Shows a restricted help message           | `!help`      |
| `!banlist`    | Shows active temporary bans               | `!banlist`   |
| `!why <jid/nick>` | Shows reason and remaining time for a ban | `!why alice` |

> ⚠️ Permanent bans are **not shown** in protected rooms, only temporary bans.  

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

you can run `!reloadconfig` in the admin room to apply changes immediately.  
**Note:** The following changes **REQUIRE** a bot restart!  

- JID
- PASSWORD
- ADMIN_ROOM
- NICK
- DB_FILE

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

* The bot account **must be moderator/admin** in all protected rooms.  
* Admin room is the **single source of truth** for permissions.  
* Admins/Owners are automatically protected.  
* Bot automatically reports if admin/owner rights are lost.  

---

## Sync and Room/Ban Commands Overview

| Command                          | Effect                                                                                            | When Useful / Example Use Case                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `!room add <room>`               | Adds a new protected room to the list **and saves it in the database**                            | After creating a new room to protect; optionally run `!sync` to join immediately  |
| `!room remove <room>`            | Removes a room from the protected list and database                                               | Stop protecting a room; bot will no longer enforce bans there                     |
| `!sync`                          | Full sync: rejoin rooms, check admin rights, reapply all active bans                              | Needed if bot was disconnected or removed from rooms                              |
| `!syncadmins`                    | Updates internal admin list from admin room                                                       | Use after changing admins/owners, or at startup                                   |
| `!syncbans`                      | Full ban synchronization: database ↔ rooms; reads outcasts from rooms, updates DB, reapplies bans | Use after manual ban changes or DB recovery                                       |
| `sync_bans_startup()` (internal) | Runs automatically on bot startup; applies only active bans                                       | Not an admin command. Ensures bans are enforced at startup                        |

**Tips:**  

* Add new rooms: `!room add <room>` → optionally `!sync`  
* Update admin list: `!syncadmins`  
* Check ban consistency / adopt outcasts: `!syncbans`  
* After restart or room removal: `!sync` + `!syncbans`  
* Normal operation: `!sync` usually not needed; `!syncbans` only as needed  

---

## Database (SQLite)

**`banbot.db`** with two tables:

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

* Temporary bans expire automatically and are removed by the bot.  
* `!why` supports lookup by **JID** or **Nick**, including nick-to-JID mapping.  
* Messages in protected rooms are ephemeral; the admin room always receives full notifications.  
* Changes to `config.py` can **usually be applied via `!reloadconfig`** (except critical settings like JID, PASSWORD, NICK, ADMIN_ROOM, DB_FILE).  
