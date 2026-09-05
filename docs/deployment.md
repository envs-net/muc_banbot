# Deployment

This page documents the recommended hardened systemd deployment and the
existing source-tree installation mode. Both remain supported.

## Recommended layout

Use a dedicated service account and separate application code from mutable
configuration and state:

```text
/srv/adminbot/muc_banbot/       Git checkout + virtualenv
/etc/muc_banbot/config.py       runtime-editable configuration
/var/lib/muc_banbot/            SQLite DB, backups, exports and OMEMO state
```

The checkout can stay read-only at runtime. New hardened configs should use
absolute paths, for example:

```python
DB_FILE = "/var/lib/muc_banbot/banbot.db"
DB_BACKUP_DIR = "/var/lib/muc_banbot/backups"
EXPORT_DIR = "/var/lib/muc_banbot/exports"
OMEMO_STORAGE_FILE = "/var/lib/muc_banbot/omemo.json"
# Optional custom avatar:
# AVATAR_PATH = "/var/lib/muc_banbot/avatar.png"
```

The packaged/default `avatar.png` may remain in the read-only checkout because
it only needs to be read.

## Hardened paths and permissions

A hardened deployment is intentionally stricter than the historical source-tree
layout. The following mutable settings must resolve below the configured data
directory (normally `/var/lib/muc_banbot`):

```python
DB_FILE = "/var/lib/muc_banbot/banbot.db"
DB_BACKUP_DIR = "/var/lib/muc_banbot/backups"
EXPORT_DIR = "/var/lib/muc_banbot/exports"
OMEMO_STORAGE_FILE = "/var/lib/muc_banbot/omemo.json"
```

Do **not** copy an old config to `/etc/muc_banbot/config.py` and leave values such
as `DB_FILE = "banbot.db"` or `DB_BACKUP_DIR = "data/backups"` unchanged. Relative
paths still resolve from the checkout working directory, while the hardened unit
keeps that checkout read-only with `ProtectSystem=strict`. `scripts/deploy.sh
check` and install/update validation reject this mismatch before the service is
started or an update proceeds. `AVATAR_PATH` is read-only state and may remain in
the application checkout.

The recommended ownership/mode baseline is:

```text
/etc/muc_banbot/             adminbot:adminbot  0750
/etc/muc_banbot/config.py    adminbot:adminbot  0600
/var/lib/muc_banbot/         adminbot:adminbot  0700
```

The config directory must be writable by the service user because `!config
set/unset` performs an atomic temporary-file + `os.replace()` update. BanBot
always forces a rewritten config file to `0600`, even in legacy/manual runs with
a permissive umask. It also warns when the active config is group/world readable
or is not owned by the running service user.

The running bot deliberately does **not** try to `chown` deployment files. It is
normally unprivileged, and silently taking ownership of operator-managed custom
layouts would be unsafe. Instead, `scripts/deploy.sh check` validates ownership
and modes and prints reviewable repair commands when they differ from the secure
baseline.

For a manual migration, stop the service first and prepare the directories with
explicit ownership/modes:

```bash
sudo systemctl stop muc_banbot.service
sudo install -d -o adminbot -g adminbot -m 0750 /etc/muc_banbot
sudo install -d -o adminbot -g adminbot -m 0700 /var/lib/muc_banbot
sudo install -o adminbot -g adminbot -m 0600 \
    /srv/adminbot/muc_banbot/config.py /etc/muc_banbot/config.py
```

Move/copy the existing database, backup/export state and OMEMO file into
`/var/lib/muc_banbot`. When files were copied as root, normalize the dedicated
private data tree before starting the service:

```bash
sudo chown -R adminbot:adminbot /var/lib/muc_banbot
sudo find /var/lib/muc_banbot -type d -exec chmod 0700 {} +
sudo find /var/lib/muc_banbot -type f -exec chmod 0600 {} +
```

Then set the absolute paths above and verify before starting:

```bash
sudo ./scripts/deploy.sh check
sudo systemctl start muc_banbot.service
```

The deploy check also validates existing database/OMEMO files and backup/export
directories so a `root:root` migration mistake is caught before runtime.

Keep the old source-tree data until the hardened service has been verified.

## Preservation-first deploy helper

`scripts/deploy.sh` wraps install/update checks without replacing the manual
workflow. Running it without a command only prints help:

```bash
./scripts/deploy.sh
./scripts/deploy.sh status
./scripts/deploy.sh check
./scripts/deploy.sh install --dry-run
./scripts/deploy.sh update --dry-run
```

Actual install/update operations require explicit confirmation. Stopping an
active service and starting it again are confirmed separately. If an operation
fails after the service was stopped, the helper deliberately leaves it stopped.

The helper preserves operator-managed state:

- existing config files are never replaced;
- existing databases/data directories are never replaced;
- existing systemd units are never overwritten automatically;
- tracked local Git changes block updates;
- legacy config/database/OMEMO/avatar files inside the checkout are protected
  across a release checkout; and
- a consistent SQLite backup is created before code changes during updates.

Custom layouts can be supplied with `--root`, `--venv`, `--config`,
`--data-dir`, `--service`, `--user`, `--group` and `--unit`. Useful environment
overrides include `MUC_BANBOT_CONFIG`, `MUC_BANBOT_DATA_DIR`,
`MUC_BANBOT_VENV`, `MUC_BANBOT_SERVICE`, `MUC_BANBOT_SERVICE_USER`,
`MUC_BANBOT_SERVICE_GROUP`, `MUC_BANBOT_SYSTEMD_UNIT`,
`MUC_BANBOT_DEPLOY_REMOTE`, `MUC_BANBOT_DEPLOY_BASE_PYTHON` and
`MUC_BANBOT_DEPLOY_PYTHON`.

## Fresh hardened install

The helper assumes the account and source checkout already exist; it does not
guess service-account policy or XMPP credentials:

```bash
sudo useradd -m -s /bin/bash adminbot -d /srv/adminbot
sudo -u adminbot git clone https://git.envs.net/envs/muc_banbot.git /srv/adminbot/muc_banbot
cd /srv/adminbot/muc_banbot

git fetch --tags
LATEST_TAG="$(git tag --sort=-v:refname | head -n1)"
git checkout "$LATEST_TAG"

./scripts/deploy.sh install --dry-run
sudo ./scripts/deploy.sh install
```

On the first run, when the config is missing, the helper creates
`/etc/muc_banbot/config.py` with the `/var/lib/muc_banbot` paths shown above
and then stops. Edit at least the account/room values:

```bash
sudoedit /etc/muc_banbot/config.py
sudo ./scripts/deploy.sh install
```

The second run validates the config, creates/reuses the virtualenv, and can
install a new hardened systemd unit after confirmation. An already installed
unit is kept for manual review.

Optional OMEMO dependencies are intentionally separate, just as in the manual
installation:

```bash
sudo -u adminbot /srv/adminbot/muc_banbot/venv/bin/pip install -e "/srv/adminbot/muc_banbot[omemo]"
```

## Hardened systemd service

[`contrib/muc_banbot.service`](../contrib/muc_banbot.service) is the recommended
static example. Important properties are:

```ini
Type=notify
NotifyAccess=main
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=MUC_BANBOT_CONFIG=/etc/muc_banbot/config.py
ExecStart=/srv/adminbot/muc_banbot/venv/bin/muc_banbot
Restart=on-failure
WatchdogSec=60
ProtectSystem=strict
ReadWritePaths=/etc/muc_banbot /var/lib/muc_banbot
```

Additional hardening includes `PrivateTmp`, `PrivateDevices`, kernel/control
group protection, `NoNewPrivileges`, empty capability sets and `UMask=0077`.
The unit's `EnvironmentFile=-/etc/default/muc_banbot` is optional: the leading
`-` tells systemd to continue normally when that file does not exist.
`PYTHONDONTWRITEBYTECODE=1` prevents imports/reloads of `/etc/muc_banbot/config.py`
from creating an `__pycache__` directory there. An old cache left by an earlier
unit can be removed once with `sudo rm -rf /etc/muc_banbot/__pycache__`.
`Restart=on-failure` still means a normal `systemctl stop` remains stopped,
while startup failures, unexpected process exits and `!restart confirm` can be
recovered automatically.

BanBot sends `READY=1` only after database/room startup has completed. While
the first XMPP session is still unavailable, BanBot periodically extends
systemd's startup deadline with `EXTEND_TIMEOUT_USEC`. This lets a `Type=notify`
service survive a deliberately offline XMPP server (for example during a long
backup window) without disabling `TimeoutStartSec` entirely. As soon as
`session_start` is received, the extension stops and the normal startup timeout
again protects the remaining initialization path from genuine hangs.

Its runtime watchdog feeds systemd after startup while the asyncio event loop
remains responsive. If lag exceeds `WATCHDOG_LAG_FAILURE_SECONDS`, heartbeats
are suppressed so the systemd watchdog can recover a genuinely stuck process.

Install the static unit manually when desired:

```bash
sudo install -m 0644 contrib/muc_banbot.service /etc/systemd/system/muc_banbot.service
sudo systemd-analyze verify /etc/systemd/system/muc_banbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now muc_banbot.service
```

For custom source/config/data paths, let `scripts/deploy.sh install` render a
matching new unit, or edit/review the unit manually. `scripts/deploy.sh check`
compares effective systemd properties with the selected deployment layout.

## Safe updates

For the newest stable release:

```bash
./scripts/deploy.sh update --dry-run
sudo ./scripts/deploy.sh update
```

The automatic path never deploys `main`. It refreshes normal remote refs with
`--no-tags`, queries release tags with `git ls-remote`, selects only stable
`vX.Y.Z` tags and fetches only the selected release. A conflicting local tag is
not overwritten. Git ancestry is checked so diverged/non-fast-forward
deployments are refused.

An intentional older code release must be explicit:

```bash
sudo ./scripts/deploy.sh update --to v2.6.3 --allow-downgrade
```

This only permits a code downgrade; it cannot roll a database schema backward.
A matching database backup may be required if an older release is incompatible.

## Legacy/source-tree deployment

The historical layout remains valid:

```text
/srv/adminbot/muc_banbot/config.py
/srv/adminbot/muc_banbot/banbot.db
/srv/adminbot/muc_banbot/data/backups/
/srv/adminbot/muc_banbot/data/exports/
```

Relative paths keep their existing meaning because the working directory remains
the checkout. Existing installations do **not** have to migrate merely to update
BanBot. Keep the current unit or use
[`contrib/muc_banbot-legacy.service`](../contrib/muc_banbot-legacy.service).
The deploy helper auto-detects `ROOT/config.py` for existing non-install
operations when no external config path is configured and reports the layout as
`legacy source-tree`.

A migration to the hardened layout should be deliberate. Follow the
[hardened paths and permissions](#hardened-paths-and-permissions) checklist:
stop the service, copy config/data with explicit ownership and modes, change all
mutable paths to absolutes below `/var/lib/muc_banbot`, install/review the
hardened unit, run `sudo ./scripts/deploy.sh check`, and only then start the
service. Do not remove the old data until the new service has been verified.
