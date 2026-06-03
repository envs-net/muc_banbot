# BanBot - XMPP Multi-Room Ban Management Bot - [![calver](https://img.shields.io/github/v/release/envs-net/muc_banbot)](https://github.com/envs-net/muc_banbot/releases/latest) / [![Build Status](https://drone.envs.net/api/badges/envs/muc_banbot/status.svg)](https://drone.envs.net/envs/muc_banbot)

BanBot is an XMPP bot for centralized ban management across multiple MUC rooms (Multi-User Chat).

It provides admin-room based moderation, protects configured MUCs from unwanted users, and supports temporary bans, domain bans, ban synchronization, audit logging, ignorelists, health checks, RTBL/PubSub integration, and optional OMEMO support for encrypted commands and replies.

---

## Features

* 🛡️ Central admin room for all administrative commands
* 🧩 Dynamic addition/removal of protected rooms
* 🔒 Optional OMEMO support: encrypted commands receive encrypted replies
* ❌ Ban, temporary ban, unban, banlist, bansearch, why, and redaction commands
* 🌐 Domain-based bans (`*.domain.tld`) to ban all users from a domain
* ⏱️ Automatic temporary ban expiration with human-readable durations
* 📊 Smart duplicate ban handling with automatic conversion between permanent and temporary bans
* 🐞 Nick-only ban support with best-effort JID upgrade when the user rejoins
* ⚠️ Admin/owner protection for direct, nick-based, and domain-based bans
* 🚫 Global ignorelist/whitelist for exact JIDs and domain-based ban protection
* 📦 Startup and manual synchronization of room bans/admins
* 🏥 Health checks, reconnect awareness, admin-right monitoring, and dynamic `!status`
* 🛡️ RTBL subscriptions via PubSub for SHA-256 JID hashes and plaintext domain bans
* 🔄 Periodic RTBL refresh with quiet/no-change behavior and snapshot reconciliation
* ♻️ RTBL snapshot reconciliation with stale local ban cleanup
* 📡 Optional own RTBL publish feed for local bans
* 🧾 SQLite audit log and structured JSON event logs
* 💾 CSV import/export with managed safety backups
* 🗄️ Managed ZIP backup archives with manifest, restore command, verification, and automatic startup backups
* ✅ Startup/runtime config validation with safe `!reloadconfig`
* 🚦 Rate limiting for public protected-room commands
* 📜 Optional public room policy text via `!rules` / `!policy`
* ⬆️ Optional GitHub release checks
* 🖼️ Avatar/vCard support via XEP-0054, XEP-0084, and XEP-0153
* 🧪 Extensive pytest suite, coverage, property tests, mutation-testing support, and Drone CI

---

## Installation / Quickstart

Requires **Python 3.10+**. The project is developed and tested with Python 3.13.

```bash
sudo useradd -m -s /bin/bash adminbot -d /srv/adminbot
sudo su - adminbot

cd /srv/adminbot
git clone https://git.envs.net/envs/muc_banbot.git
cd muc_banbot

# Production installs should use the latest tagged release, not the main branch.
git fetch --tags
LATEST_TAG="$(git tag --sort=-v:refname | head -n1)"
git checkout "$LATEST_TAG"
echo "Using muc_banbot release $LATEST_TAG"

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: install OMEMO support after installing system libraries.
# Raspbian example: sudo apt install libsodium-dev libxeddsa-dev
# Then: pip install -r requirements-omemo.txt

cp config_sample.py config.py
$EDITOR config.py

python muc_banbot.py
```

For a systemd service example, see [docs/configuration.md](docs/configuration.md#systemd-service).

`main` is the development branch. For production deployments, use the latest tagged release shown on GitHub/Gitea.

## Updating to a New Release

For production deployments, update to the latest tagged release instead of running directly from `main`:

```bash
cd /srv/adminbot/muc_banbot

git fetch --tags
LATEST_TAG="$(git tag --sort=-v:refname | head -n1)"
git checkout "$LATEST_TAG"

source venv/bin/activate
pip install -r requirements.txt

# Optional, only when OMEMO support is used:
# pip install -r requirements-omemo.txt

systemctl --user restart muc_banbot
# or, for a system-wide service:
# sudo systemctl restart muc_banbot
```

If the release notes mention new configuration options, compare your local `config.py` with the updated `config_sample.py` and add any new settings you want to customize.

---

## Minimal Configuration

Copy `config_sample.py` to `config.py` and set at least:

```python
JID = "adminbot@example.org"
PASSWORD = "secret"
RESOURCE = "service"
ADMIN_ROOM = "admin@conference.example.org"
NICK = "BanBot"
DB_FILE = "banbot.db"
```

Most runtime settings can be reloaded with `!reloadconfig`. Startup-only settings such as the bot account/room identity, database path, RTBL enable/publish setup, and OMEMO setup require a restart.

See [docs/configuration.md](docs/configuration.md) for the full configuration reference.

---

## Important Commands

Examples assume the default command prefix `!`.

| Command | Description |
| --- | --- |
| `!help [all|page|last]` / `!help <command>` | Show available commands or focused help for every command topic, including subtopics such as `room invite` and `rtbl publish` |
| `!status` | Show bot health, uptime, rooms, bans, RTBL, and DB state |
| `!config [all|page|last]` / `!config show [all|page|last]` | Show active configuration grouped in `config_sample.py` section order; secrets are hidden |
| `!config set <KEY> <value>` | Change a runtime-writable configuration value |
| `!config unset <KEY>` | Reset a runtime-writable configuration value to `config_sample.py` default |
| `!reload` / `!reloadconfig` | Validate and reload runtime configuration |
| `!restart confirm` | Stop the bot so a supervisor can restart it |
| `!checkupdate` / `!updatecheck` | Check whether a newer release is available |
| `!whoami` | Show your affiliation, role, and permissions |
| `!audit [all/page/last/query]` | Show audit log entries |
| `!backup` | Create a managed full ZIP backup archive |
| `!backup list [all/page/last]` | List managed full backup archives |
| `!backup show <file/latest>` | Inspect one managed backup archive |
| `!backup verify <file/latest>` | Verify a managed backup archive |
| `!backup delete/remove <file/latest>` | Delete a managed full backup archive |
| `!restore <file/latest> confirm` | Restore a managed full backup archive |
| `!room add <room>` | Add a protected room |
| `!room remove/delete <room>` | Remove a protected room |
| `!room list [all/page]` | List protected rooms |
| `!room invite list [all/page/last]` | List pending room invites |
| `!room invite accept/decline/remove/delete <id>` | Accept or decline a pending room invite |
| `!policy` / `!rules show/set/clear/delete/remove/enable/disable` | Manage public room policy text |
| `!ban <jid/nick/domain> [comment]` | Ban a JID, nick, or wildcard domain |
| `!tempban <jid/nick> <10m/2h/1d> [comment]` | Add a temporary ban |
| `!unban <jid/nick/domain>` | Remove a ban |
| `!redact <jid> [reason]` / `!redact id ...` / `!redact cleanup` | Redact indexed messages or clean old redaction index entries |
| `!banlist` / `!blacklist [all/page/last]` | Show active bans |
| `!banlist` / `!blacklist rtbl [all/page/last]` | Show raw RTBL hash/domain entries |
| `!bansearch <query> [all/page/last]` | Search bans by target, issuer, comment, or RTBL reason |
| `!why <nick/jid>` | Explain why a user is banned |
| `!ignore [list/all/page]` | Show the global ignorelist |
| `!ignore add/remove <jid/domain>` | Manage protected exact JIDs and domains |
| `!whitelist [list/all/add/remove]` | Alias for `!ignore ...` |
| `!sync` | Rejoin rooms, verify admin rights, and enforce active bans |
| `!syncadmins` | Update admin list from the admin room |
| `!syncbans` | Sync bans from rooms into the database and enforce them |
| `!omemo status` | Inspect OMEMO readiness and storage state |
| `!omemo devices` | List visible admin-room recipients and local storage hints |
| `!omemo reset [confirm]` | Rotate local OMEMO storage after confirmation |
| `!rtbl list/add/delete/remove/refresh` | Manage RTBL subscriptions |
| `!rtbl publish status/sync` | Manage the bot's own RTBL publish feed |
| `!export [list/delete]` | Manage CSV ban exports |
| `!import <file> [dryrun]` | Import bans from CSV with validation and optional dry-run |

For paginated commands, the standalone `all` argument disables paging and prints the complete result set. Examples: `!audit all`, `!banlist all`, `!banlist rtbl all`, `!bansearch all spam`, `!ignore list all`, `!whitelist all`, and `!room list all`.

Full command reference: [docs/commands.md](docs/commands.md).


---

## Room Invite Service

When `ROOM_INVITES_ENABLED=True`, BanBot can receive MUC invites for potential protected rooms. Invites are announced in the admin room and must be accepted or declined with `!room invite` commands. Pending invites older than `ROOM_INVITE_MAX_AGE_DAYS` are expired automatically; set it to `0` to keep them indefinitely. BanBot does not auto-join invited rooms.

## Message Redaction

Optional redaction support indexes room-assigned stanza IDs for messages BanBot sees in protected rooms. Message bodies are not stored. Admins can redact all known messages from a bare JID with `!redact <jid> [reason]` or target a specific stanza ID with `!redact id <room_jid> <stanza_id> [reason]`.

See [docs/commands.md](docs/commands.md) and [docs/configuration.md](docs/configuration.md#redaction-settings).

## OMEMO

BanBot supports optional OMEMO replies. OMEMO dependencies are not required for normal plaintext operation. If `OMEMO_ENABLED=True` but the optional Python/system libraries are missing, BanBot starts with OMEMO disabled and logs a clear warning.

The behavior is dynamic:

```text
plaintext command  -> plaintext reply
OMEMO command      -> OMEMO reply
```

Encrypted MUC replies are sent to current occupants with visible real JIDs as far as possible. Occupants with unusable OMEMO devices are skipped; plaintext fallback is controlled by configuration. Admins can inspect local OMEMO state with `!omemo status`, show current visible admin-room OMEMO recipients with `!omemo devices`, and rotate the local OMEMO store with `!omemo reset confirm` when the bot identity changed or devices got stale. `!omemo devices` also shows conservative local storage hints, but those hints are diagnostic only and may be stale.

See [docs/omemo.md](docs/omemo.md).

---

## RTBL / PubSub

BanBot can subscribe to RTBL PubSub nodes containing SHA-256 bare-JID hashes and plaintext domain bans. Successful refreshes reconcile local RTBL cache state with the current node snapshot. Removed RTBL items are cleaned up locally, and stale `issuer=rtbl` bans are automatically unbanned.

```text
!rtbl add xmppbl.org muc_bans_sha256
!rtbl add xmppbl.org spam_source_domains
!rtbl refresh
!banlist rtbl all
```

BanBot can also publish local non-RTBL bans to its own RTBL feed.

See [docs/rtbl.md](docs/rtbl.md) and [docs/rtbl_pubsub-setup.md](docs/rtbl_pubsub-setup.md).

---

## Tests and CI

Install dev dependencies and run tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
pytest
```

Run with coverage:

```bash
pytest --cov=banbot --cov-report=term-missing
```

The test suite also includes Hypothesis-based property tests for pure helper logic such as duration parsing, human-readable time formatting, JID/domain normalization, RTBL utilities, paging helpers, and ban-target normalization.

Drone CI runs the offline pytest suite with coverage on pushes and tags to `main`. Live XMPP/Prosody and OMEMO integration tests are opt-in and skipped by default.

See [docs/testing.md](docs/testing.md) for the full testing workflow and [tests/README.md](tests/README.md) for the test-suite layout.

---

## Documentation

The full documentation is split into focused guides. Start with the [documentation index](docs/README.md), or jump directly to a topic:

* [Configuration](docs/configuration.md)
* [Commands](docs/commands.md)
* [Backups and Restore](docs/backups.md)
* [Rooms and Invites](docs/rooms.md)
* [Import / Export](docs/import-export.md)
* [OMEMO](docs/omemo.md)
* [RTBL / PubSub](docs/rtbl.md)
* [Prosody PubSub Setup](docs/rtbl_pubsub-setup.md)
* [Public Policy / Rules](docs/policy.md)
* [Admin Protection](docs/admin-protection.md)
* [Database](docs/database.md)
* [Testing and CI](docs/testing.md)
* [Release Checklist](docs/release-checklist.md)
* [Troubleshooting](docs/troubleshooting.md)

---

## Security Notes

* The bot account must have admin/owner rights in every protected room.
* The admin room is the single source of truth for command permissions.
* Admins/owners are protected from manual, nick-based, domain-based, and RTBL-applied bans.
* Domain bans such as `*.domain.tld` reject overly generic targets such as `*.com`.
* CSV imports create a managed full backup before writing data, but dry-runs do not create backups or change the database.
* The ignorelist protects exact JIDs from all bans and domains from domain-based/RTBL domain matches.
* If RTBL publishing is enabled, ensure the configured PubSub nodes are not writable by arbitrary users.
