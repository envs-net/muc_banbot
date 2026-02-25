# BanBot - XMPP Multi-Room Ban Management Bot

BanBot is an XMPP bot for managing bans and temporary bans in multiple MUC (Multi-User Chat) rooms.  
It allows centralized administration from a designated admin room and protects multiple chat rooms from unwanted users.  

---

## Features

- Central admin room for issuing commands.  
- Ban, temporary ban, unban, and banlist commands.  
- Add/remove protected rooms dynamically.  
- Auto-rejoin and auto-apply bans on restart.  
- Human-readable remaining time for temporary bans.  
- Automatic unbanning of expired bans.  
- Logs ban/unban actions in both protected rooms and admin room.  

### Commands (admin only)

- `!help` – Show available commands  
- `!ban <jid|nick>` – Ban a user from all protected rooms  
- `!tempban <jid|nick> <duration>` – Temporarily ban a user (e.g., `10m`, `2h`, `1d`)  
- `!unban <jid>` – Unban a user from all protected rooms  
- `!banlist` – Show all active bans with remaining time  
- `!room add <room>` – Add a room to the protected list  
- `!room remove <room>` – Remove a room from the protected list  
- `!room list` – List all protected rooms  
- `!sync` – Rejoin all protected rooms and reapply active bans  
- `!status` – Show bot health  
- `!whoami` – Show your role/affiliation in the admin room  

---

## Configuration

Edit the top of `muc_banbot.py`:

```python
JID = "adminbot@domain.tld"
PASSWORD = "yourpassword"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"
DB_FILE = "bans.db"
```

## Installation

### 1. Create a system user for the bot

```bash
sudo useradd -m -s /bin/bash -p "yourpassword" adminbot -d /srv/adminbot
sudo su - adminbot
```

This creates a dedicated user adminbot with home /srv/adminbot.

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

### 5. Test the bot manually

```bash
python muc_banbot.py
```

## Systemd Service

Create a systemd service file `/etc/systemd/system/muc_banbot.service`:

```
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

### Enable and start the bot

```bash
sudo systemctl daemon-reload
sudo systemctl start muc_banbot
sudo systemctl enable muc_banbot
sudo journalctl -u muc_banbot -f  # follow logs
```
