#!/usr/bin/env python3
"""Interactive, preservation-first deployment helper for muc_banbot."""

from __future__ import annotations

import argparse
import filecmp
import grp
import json
import os
import pwd
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _envs_xmpp_bootstrap import ensure_envs_xmpp  # noqa: E402

_CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
_STABLE_RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$")


class DeployError(RuntimeError):
    """Expected deployment failure with an operator-readable message."""


class UserCancelled(DeployError):
    """Raised when an interactive action is declined."""


@dataclass(frozen=True)
class Deployment:
    root: Path
    venv: Path
    config: Path
    data_dir: Path
    service: str
    service_user: str
    service_group: str
    unit: Path
    python: str
    dry_run: bool = False

    @property
    def executable(self) -> Path:
        return self.venv / "bin" / "muc_banbot"

    @property
    def pip(self) -> Path:
        return self.venv / "bin" / "pip"

    @property
    def venv_python(self) -> Path:
        return self.venv / "bin" / "python"

    @property
    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["MUC_BANBOT_CONFIG"] = str(self.config)
        # Keep operator config directories clean when deploy/check imports config.py.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    @property
    def legacy_layout(self) -> bool:
        return self.config == (self.root / "config.py").resolve()


def _project_root() -> Path:
    return _CHECKOUT_ROOT


def _systemd_property(service: str, prop: str) -> str:
    from envs_xmpp_ops.systemd import systemd_property

    return systemd_property(service, prop, run_process=subprocess.run)


def _systemd_environment(service: str, key: str) -> str | None:
    value = _systemd_property(service, "Environment")
    if not value:
        return None
    try:
        assignments = shlex.split(value)
    except ValueError:
        assignments = value.split()
    prefix = f"{key}="
    for assignment in assignments:
        if assignment.startswith(prefix):
            return assignment[len(prefix) :]
    return None


def _systemd_exec_path_from_value(exec_start: str) -> Path | None:
    """Extract the executable path from systemctl show ExecStart output."""
    marker = "path="
    if marker not in exec_start:
        return None
    executable = exec_start.split(marker, 1)[1].split(";", 1)[0].strip()
    if not executable:
        return None
    return Path(executable).expanduser()


def _systemd_venv(service: str) -> Path | None:
    path = _systemd_exec_path_from_value(_systemd_property(service, "ExecStart"))
    if path is not None and path.name == "muc_banbot" and path.parent.name == "bin":
        return path.parent.parent.resolve()
    return None


def _systemd_paths(value: str) -> set[str]:
    """Normalize a systemd path-list property for exact comparisons."""
    if not value:
        return set()
    try:
        items = shlex.split(value)
    except ValueError:
        items = value.split()
    return set(items)


def _default_config(root: Path, service: str) -> Path:
    configured = os.environ.get("MUC_BANBOT_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    systemd_value = _systemd_environment(service, "MUC_BANBOT_CONFIG")
    if systemd_value:
        return Path(systemd_value).expanduser().resolve()
    legacy = root / "config.py"
    if legacy.exists():
        # When the installed unit does not explicitly select another config,
        # preserve a source-tree installation even if an unrelated/stale
        # /etc/muc_banbot/config.py also exists on the host. Hardened units
        # advertise their config through MUC_BANBOT_CONFIG and are handled
        # above.
        return legacy.resolve()
    return Path("/etc/muc_banbot/config.py")


def _default_account(service: str, prop: str, fallback: str) -> str:
    env_name = f"MUC_BANBOT_SERVICE_{prop.upper()}"
    configured = os.environ.get(env_name)
    if configured:
        return configured
    discovered = _systemd_property(service, prop)
    return discovered or fallback


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/deploy.sh",
        description=(
            "Interactive, preservation-first muc_banbot deployment helper. "
            "Running it without a command only shows this help."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  ./scripts/deploy.sh status
  ./scripts/deploy.sh check
  ./scripts/deploy.sh install --dry-run
  sudo ./scripts/deploy.sh install
  sudo ./scripts/deploy.sh update
  sudo ./scripts/deploy.sh update --to v2.6.3

New installations default to:
  config: /etc/muc_banbot/config.py
  data:   /var/lib/muc_banbot

Existing source-tree installations remain supported and are auto-detected when
possible. Override custom layouts with --root, --venv, --config, --data-dir,
--service, --user, --group and --unit.

Safety rules:
  * install/update require explicit confirmation;
  * stopping and starting systemd are confirmed separately;
  * existing config, database/data directory and systemd unit are preserved;
  * an existing systemd unit is never replaced automatically;
  * update refuses a dirty tracked Git worktree;
  * automatic updates select stable vX.Y.Z release tags only and never main;
  * explicit downgrades require --allow-downgrade and extra confirmation;
  * a failed update after stopping the service leaves it stopped.
""",
    )
    parser.add_argument("command", nargs="?", choices=("status", "check", "install", "update"))
    parser.add_argument("--root", type=Path, help="application checkout")
    parser.add_argument("--venv", type=Path, help="virtualenv path (default: ROOT/venv)")
    parser.add_argument("--config", type=Path, help="runtime config path")
    parser.add_argument("--data-dir", type=Path, help="mutable data directory")
    parser.add_argument(
        "--service",
        default=os.environ.get("MUC_BANBOT_SERVICE", "muc_banbot.service"),
        help="systemd service name (default: muc_banbot.service)",
    )
    parser.add_argument("--user", help="systemd service user")
    parser.add_argument("--group", help="systemd service group")
    parser.add_argument("--unit", type=Path, help="systemd unit path")
    parser.add_argument(
        "--python",
        default=os.environ.get("MUC_BANBOT_DEPLOY_BASE_PYTHON", "python3"),
        help="base interpreter used to create a missing virtualenv",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    parser.add_argument("--to", metavar="TAG", help="explicit release tag for update")
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="allow an explicit older --to TAG after an extra confirmation",
    )
    return parser


def _deployment(options: argparse.Namespace) -> Deployment:
    from deploy_profile import PROFILE

    root = (options.root or _project_root()).expanduser().resolve()
    service = options.service
    discovered_venv = _systemd_venv(service)
    venv = Path(
        options.venv
        or os.environ.get("MUC_BANBOT_VENV")
        or discovered_venv
        or root / PROFILE.venv_name
    )
    venv = venv.expanduser().resolve()
    config = Path(options.config or _default_config(root, service)).expanduser().resolve()
    explicit_data_dir = options.data_dir or os.environ.get("MUC_BANBOT_DATA_DIR")
    if explicit_data_dir is not None:
        data_dir = Path(explicit_data_dir).expanduser().resolve()
    elif config == (root / "config.py").resolve():
        # Historical source-tree layout: relative DB/data paths resolve below
        # WorkingDirectory. Keep reporting/checking that layout without forcing
        # an implicit migration to /var/lib.
        data_dir = root
    else:
        data_dir = Path(PROFILE.default_data)
    user = options.user or _default_account(service, "User", PROFILE.service_user)
    group = options.group or _default_account(service, "Group", user)
    unit_value = options.unit or os.environ.get("MUC_BANBOT_SYSTEMD_UNIT")
    if unit_value is None:
        fragment = _systemd_property(service, "FragmentPath")
        unit_name = service if service.endswith(".service") else f"{service}.service"
        unit_value = fragment or f"/etc/systemd/system/{unit_name}"
    return Deployment(
        root=root,
        venv=venv,
        config=config,
        data_dir=data_dir,
        service=service,
        service_user=user,
        service_group=group,
        unit=Path(unit_value).expanduser().resolve(),
        python=options.python,
        dry_run=options.dry_run,
    )


def _quote(command: Sequence[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _account_exists(user: str) -> bool:
    from envs_xmpp_ops.accounts import account_exists

    return account_exists(user, getpwnam=pwd.getpwnam)


def _run(
    command: Sequence[object],
    *,
    deployment: Deployment | None = None,
    as_service_user: bool = False,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    announce: bool = True,
) -> subprocess.CompletedProcess[str]:
    argv = [str(part) for part in command]
    env = deployment.environment if deployment is not None else os.environ.copy()
    if as_service_user and deployment is not None and os.geteuid() == 0:
        if not _account_exists(deployment.service_user):
            raise DeployError(f"service user does not exist: {deployment.service_user}")
        prefix: list[str]
        if shutil.which("runuser"):
            prefix = ["runuser", "-u", deployment.service_user, "--"]
        elif shutil.which("sudo"):
            prefix = ["sudo", "-u", deployment.service_user, "--"]
        else:
            raise DeployError("running as root requires runuser or sudo for service-user commands")
        argv = [*prefix, *argv]
    if announce:
        print(f"$ {_quote(argv)}")
    result = subprocess.run(
        argv,
        cwd=str(cwd or (deployment.root if deployment else Path.cwd())),
        env=env,
        check=False,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if capture:
            if result.stdout:
                print(result.stdout.rstrip(), file=sys.stderr)
            if result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
        raise DeployError(f"command failed with exit code {result.returncode}: {_quote(argv)}")
    return result


def _confirm(prompt: str) -> bool:
    from envs_xmpp_ops.interaction import confirm

    return confirm(prompt, input_func=input)


def _require_confirmation(prompt: str) -> None:
    if not _confirm(prompt):
        raise UserCancelled("cancelled by operator")


def _require_source_tree(deployment: Deployment) -> None:
    required = ("pyproject.toml", "config_sample.py", "scripts/deploy.sh")
    missing = [name for name in required if not (deployment.root / name).is_file()]
    if missing:
        raise DeployError(
            f"not a muc_banbot source checkout: {deployment.root} (missing: {', '.join(missing)})"
        )


def _ensure_dir(path: Path, deployment: Deployment, *, mode: int = 0o750) -> None:
    if path.exists():
        if not path.is_dir():
            raise DeployError(f"expected directory but found another file type: {path}")
        return
    path.mkdir(parents=True, mode=mode)
    path.chmod(mode)
    if os.geteuid() == 0 and _account_exists(deployment.service_user):
        user = pwd.getpwnam(deployment.service_user)
        try:
            gid = grp.getgrnam(deployment.service_group).gr_gid
        except KeyError:
            gid = user.pw_gid
        os.chown(path, user.pw_uid, gid)
    print(f"CREATE {path}")


def _write_config_from_sample(deployment: Deployment) -> bool:
    if deployment.config.exists():
        print(f"KEEP existing {deployment.config}")
        return False
    _ensure_dir(deployment.config.parent, deployment, mode=0o750)
    _ensure_dir(deployment.data_dir, deployment, mode=0o700)
    text = (deployment.root / "config_sample.py").read_text(encoding="utf-8")
    replacements = {
        "DB_FILE": str(deployment.data_dir / "banbot.db"),
        "DB_BACKUP_DIR": str(deployment.data_dir / "backups"),
        "EXPORT_DIR": str(deployment.data_dir / "exports"),
        "OMEMO_STORAGE_FILE": str(deployment.data_dir / "omemo.json"),
    }
    for key, value in replacements.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        text, count = pattern.subn(f"{key} = {value!r}", text, count=1)
        if count != 1:
            raise DeployError(f"could not prepare hardened config: missing {key}")
    try:
        with deployment.config.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        print(f"KEEP existing {deployment.config}")
        return False
    deployment.config.chmod(0o600)
    if os.geteuid() == 0 and _account_exists(deployment.service_user):
        user = pwd.getpwnam(deployment.service_user)
        try:
            gid = grp.getgrnam(deployment.service_group).gr_gid
        except KeyError:
            gid = user.pw_gid
        os.chown(deployment.config, user.pw_uid, gid)
    print(f"CREATE {deployment.config}")
    return True


def _create_venv_if_missing(deployment: Deployment) -> None:
    from envs_xmpp_ops.venv import create_venv_if_missing

    create_venv_if_missing(
        venv=deployment.venv,
        venv_python=deployment.venv_python,
        python=deployment.python,
        run_command=_run,
        deployment=deployment,
    )


def _install_dependencies(deployment: Deployment) -> None:
    _run(
        [deployment.pip, "install", "-e", deployment.root],
        deployment=deployment,
        as_service_user=True,
    )


def _runtime_paths(deployment: Deployment) -> dict[str, Path | None]:
    if not deployment.venv_python.is_file():
        raise DeployError(f"virtualenv Python not found: {deployment.venv_python}")
    code = r'''
import json
from pathlib import Path
from banbot.config_loader import load_config_module

config = load_config_module()

def path(name, default=None):
    value = getattr(config, name, default)
    if value in (None, ""):
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return str(candidate.resolve())

print(json.dumps({
    "database": path("DB_FILE", "banbot.db"),
    "backup_directory": path("DB_BACKUP_DIR", "data/backups"),
    "export_directory": path("EXPORT_DIR", "data/exports"),
    "omemo_storage": path("OMEMO_STORAGE_FILE", "data/omemo.json"),
    "avatar": path("AVATAR_PATH", "avatar.png"),
}))
'''
    result = _run(
        [deployment.venv_python, "-c", code],
        deployment=deployment,
        as_service_user=os.geteuid() == 0 and _account_exists(deployment.service_user),
        cwd=deployment.root,
        capture=True,
        announce=False,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeployError("could not resolve runtime paths from config.py") from exc
    return {
        name: (Path(value).resolve() if value else None)
        for name, value in data.items()
    }



def _path_is_within(path: Path, parent: Path) -> bool:
    """Return whether *path* resolves inside *parent* (or equals it)."""
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_hardened_runtime_paths(
    deployment: Deployment,
    runtime: dict[str, Path | None],
) -> None:
    """Reject mutable runtime paths that escape the hardened data directory."""
    if deployment.legacy_layout:
        return

    mutable = {
        "DB_FILE": runtime.get("database"),
        "DB_BACKUP_DIR": runtime.get("backup_directory"),
        "EXPORT_DIR": runtime.get("export_directory"),
        "OMEMO_STORAGE_FILE": runtime.get("omemo_storage"),
    }
    escaped = [
        f"{name}={path}"
        for name, path in mutable.items()
        if path is not None and not _path_is_within(path, deployment.data_dir)
    ]
    if escaped:
        details = "\n  - ".join(escaped)
        raise DeployError(
            "hardened deployment has mutable paths outside the configured data directory "
            f"{deployment.data_dir}:\n  - {details}\n"
            "Use absolute paths below the data directory (for example "
            f"{deployment.data_dir}/banbot.db) or select the legacy source-tree layout explicitly."
        )
    print(f"OK  mutable runtime paths stay below {deployment.data_dir}")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _ownership(path: Path) -> tuple[int, int]:
    stat_result = path.stat()
    return stat_result.st_uid, stat_result.st_gid


def _expected_ids(deployment: Deployment) -> tuple[int, int] | None:
    try:
        user = pwd.getpwnam(deployment.service_user)
    except KeyError:
        return None
    try:
        gid = grp.getgrnam(deployment.service_group).gr_gid
    except KeyError:
        gid = user.pw_gid
    return user.pw_uid, gid


def _permission_hint(deployment: Deployment) -> str:
    return (
        "Suggested repair (review before running):\n"
        f"  sudo chown {shlex.quote(deployment.service_user)}:{shlex.quote(deployment.service_group)} "
        f"{shlex.quote(str(deployment.config.parent))} {shlex.quote(str(deployment.config))} "
        f"{shlex.quote(str(deployment.data_dir))}\n"
        f"  sudo chmod 0750 {shlex.quote(str(deployment.config.parent))}\n"
        f"  sudo chmod 0600 {shlex.quote(str(deployment.config))}\n"
        f"  sudo chmod 0700 {shlex.quote(str(deployment.data_dir))}"
    )


def _check_hardened_permissions(deployment: Deployment) -> None:
    """Validate the secure/usable permission baseline for hardened deployments."""
    if deployment.legacy_layout:
        return

    for path, label in (
        (deployment.config.parent, "config directory"),
        (deployment.config, "config file"),
        (deployment.data_dir, "data directory"),
    ):
        if not path.exists():
            raise DeployError(f"{label} does not exist: {path}")

    expected = _expected_ids(deployment)
    if expected is None:
        raise DeployError(
            f"cannot verify hardened file ownership: service user {deployment.service_user!r} does not exist"
        )

    expected_uid, expected_gid = expected
    problems: list[str] = []

    for path, label in (
        (deployment.config.parent, "config directory"),
        (deployment.config, "config file"),
        (deployment.data_dir, "data directory"),
    ):
        uid, gid = _ownership(path)
        if (uid, gid) != (expected_uid, expected_gid):
            problems.append(
                f"{label} ownership is uid={uid},gid={gid}; expected "
                f"{deployment.service_user}:{deployment.service_group}"
            )

    config_dir_mode = _mode(deployment.config.parent)
    config_mode = _mode(deployment.config)
    data_mode = _mode(deployment.data_dir)

    if config_dir_mode != 0o750:
        problems.append(
            f"config directory mode is {config_dir_mode:04o}; expected 0750 so runtime config edits remain possible"
        )
    if config_mode != 0o600:
        problems.append(
            f"config file mode is {config_mode:04o}; expected 0600 because it contains credentials"
        )
    if data_mode != 0o700:
        problems.append(
            f"data directory mode is {data_mode:04o}; expected 0700 because it contains private runtime state/backups"
        )

    if problems:
        details = "\n  - ".join(problems)
        raise DeployError(
            f"hardened deployment permissions are unsafe or unusable:\n  - {details}\n"
            + _permission_hint(deployment)
        )

    print(
        "OK  hardened permissions: config dir 0750, config 0600, data dir 0700 "
        f"owned by {deployment.service_user}:{deployment.service_group}"
    )


def _check_hardened_runtime_permissions(
    deployment: Deployment,
    runtime: dict[str, Path | None],
) -> None:
    """Check existing mutable runtime targets for service-user ownership/access."""
    if deployment.legacy_layout:
        return

    expected = _expected_ids(deployment)
    if expected is None:
        return
    expected_uid, expected_gid = expected
    problems: list[str] = []

    def check_owner(path: Path, label: str) -> None:
        uid, gid = _ownership(path)
        if (uid, gid) != (expected_uid, expected_gid):
            problems.append(
                f"{label} ownership is uid={uid},gid={gid}; expected "
                f"{deployment.service_user}:{deployment.service_group}"
            )

    for key, label in (("database", "database"), ("omemo_storage", "OMEMO storage")):
        path = runtime.get(key)
        if path is None or not path.exists():
            continue
        if not path.is_file():
            problems.append(f"{label} is not a regular file: {path}")

    for key, label in (
        ("backup_directory", "backup directory"),
        ("export_directory", "export directory"),
    ):
        path = runtime.get(key)
        if path is None or not path.exists():
            continue
        if not path.is_dir():
            problems.append(f"{label} is not a directory: {path}")

    known_labels: dict[Path, str] = {}
    for key, label in (
        ("database", "database"),
        ("backup_directory", "backup directory"),
        ("export_directory", "export directory"),
        ("omemo_storage", "OMEMO storage"),
    ):
        path = runtime.get(key)
        if path is not None and path.exists():
            known_labels[path.resolve()] = label

    # New files are protected by UMask=0077, but migrated data may predate the
    # hardened unit. Verify the complete private data tree so old backups,
    # SQLite sidecar files or OMEMO state cannot remain group/world-readable.
    try:
        data_entries = list(deployment.data_dir.rglob("*"))
    except OSError as exc:
        problems.append(f"could not inspect data directory recursively: {exc}")
        data_entries = []
    for path in data_entries:
        try:
            if path.is_symlink():
                problems.append(f"symbolic link is not allowed in hardened data directory: {path}")
                continue
            if not path.exists():
                continue
            label = known_labels.get(path.resolve(), "data entry")
            check_owner(path, label)
            mode = _mode(path)
            if path.is_dir():
                if mode != 0o700:
                    problems.append(f"data directory mode is {mode:04o}; expected 0700: {path}")
            elif path.is_file():
                if mode != 0o600:
                    problems.append(f"data file mode is {mode:04o}; expected 0600: {path}")
            else:
                problems.append(f"unsupported filesystem entry in hardened data directory: {path}")
        except FileNotFoundError:
            # Runtime files such as SQLite sidecars can disappear while the
            # active service is being inspected; a vanished entry is harmless.
            continue

    if problems:
        details = "\n  - ".join(problems)
        raise DeployError(
            f"hardened runtime files/directories are not usable by the service account:\n  - {details}\n"
            "Review ownership after migration. For the dedicated data tree, a typical repair is:\n"
            f"  sudo chown -R {shlex.quote(deployment.service_user)}:{shlex.quote(deployment.service_group)} "
            f"{shlex.quote(str(deployment.data_dir))}\n"
            "For a dedicated hardened data tree, private modes can be restored with:\n"
            f"  sudo find {shlex.quote(str(deployment.data_dir))} -type d -exec chmod 0700 {{}} +\n"
            f"  sudo find {shlex.quote(str(deployment.data_dir))} -type f -exec chmod 0600 {{}} +"
        )

    print(
        "OK  existing mutable runtime files/directories are private and usable "
        "by the service account"
    )


def _validate_config(deployment: Deployment) -> None:
    if not deployment.config.is_file():
        raise DeployError(f"runtime config not found: {deployment.config}")
    code = r'''
from banbot.config_loader import load_config_module
config = load_config_module()
required = ("JID", "PASSWORD", "ADMIN_ROOM", "NICK", "DB_FILE")
missing = [name for name in required if not str(getattr(config, name, "")).strip()]
placeholders = []
if str(getattr(config, "PASSWORD", "")).strip().lower() in {"yourpassword", "password", "changeme", "change-me"}:
    placeholders.append("PASSWORD")
if str(getattr(config, "JID", "")).strip().lower() == "adminbot@domain.tld":
    placeholders.append("JID")
if str(getattr(config, "ADMIN_ROOM", "")).strip().lower() == "admin@muc.domain.tld":
    placeholders.append("ADMIN_ROOM")
if missing:
    raise SystemExit("missing required settings: " + ", ".join(missing))
if placeholders:
    raise SystemExit("sample placeholders still configured: " + ", ".join(placeholders))
print("config import and required settings: OK")
'''
    result = _run(
        [deployment.venv_python, "-c", code],
        deployment=deployment,
        as_service_user=True,
        cwd=deployment.root,
        capture=True,
        announce=False,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DeployError(f"config validation failed: {detail or 'unknown error'}")
    print("OK  config import and required settings")


def _render_systemd_unit(deployment: Deployment) -> str:
    writable = f"{deployment.config.parent} {deployment.data_dir}"
    return f"""[Unit]
Description=BanBot XMPP moderation bot
Documentation=https://github.com/envs-net/muc_banbot
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
User={deployment.service_user}
Group={deployment.service_group}
WorkingDirectory={deployment.root}
EnvironmentFile=-/etc/default/muc_banbot
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=MUC_BANBOT_CONFIG={deployment.config}
ExecStart={deployment.executable}
Restart=on-failure
RestartSec=5
WatchdogSec=60
TimeoutStartSec=300
TimeoutStopSec=120
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
ReadWritePaths={writable}

[Install]
WantedBy=multi-user.target
"""


def _systemctl_exists(deployment: Deployment) -> bool:
    from envs_xmpp_ops.systemd import systemctl_exists

    return systemctl_exists(
        deployment.service,
        run_command=_run,
        which=shutil.which,
    )


def _service_active(deployment: Deployment) -> bool:
    from envs_xmpp_ops.systemd import service_active

    return service_active(
        deployment.service,
        run_command=_run,
        which=shutil.which,
        capture=True,
    )


def _stop_active_service(deployment: Deployment, *, reason: str) -> bool:
    from envs_xmpp_ops.service import stop_active_service

    return stop_active_service(
        deployment.service,
        reason=reason,
        is_active=partial(_service_active, deployment),
        require_confirmation=_require_confirmation,
        run_systemctl=lambda action, service: _run(["systemctl", action, service]),
    )


def _ask_start(deployment: Deployment) -> None:
    from envs_xmpp_ops.service import ask_start

    ask_start(
        deployment.service,
        exists=partial(_systemctl_exists, deployment),
        is_active=partial(_service_active, deployment),
        confirm=_confirm,
        run_systemctl=lambda action, service: _run(["systemctl", action, service]),
    )


def _install_unit_if_missing(deployment: Deployment) -> None:
    if deployment.unit.exists() or _systemctl_exists(deployment):
        print(f"KEEP existing systemd service for {deployment.service}; it will not be replaced.")
        return
    if not _confirm(f"Install a new hardened systemd unit at {deployment.unit}?"):
        print("SKIP systemd unit installation (operator choice)")
        return
    deployment.unit.parent.mkdir(parents=True, exist_ok=True)
    try:
        with deployment.unit.open("x", encoding="utf-8") as handle:
            handle.write(_render_systemd_unit(deployment))
    except FileExistsError:
        print(f"KEEP existing {deployment.unit}")
        return
    deployment.unit.chmod(0o644)
    print(f"CREATE {deployment.unit}")
    if shutil.which("systemd-analyze"):
        try:
            _run(["systemd-analyze", "verify", deployment.unit])
        except DeployError:
            deployment.unit.unlink(missing_ok=True)
            raise
    _run(["systemctl", "daemon-reload"])


def _print_paths(
    deployment: Deployment,
    *,
    runtime: dict[str, Path | None] | None = None,
) -> None:
    rows: list[tuple[str, object]] = [
        ("application", deployment.root),
        ("virtualenv", deployment.venv),
        ("config", deployment.config),
        ("data dir", deployment.data_dir),
        ("layout", "legacy source-tree" if deployment.legacy_layout else "hardened/external"),
        ("service", deployment.service),
        ("service user", deployment.service_user),
        ("service group", deployment.service_group),
        ("unit", deployment.unit),
    ]
    if runtime:
        rows.extend(
            (name.replace("_", " "), runtime.get(name) or "-")
            for name in ("database", "backup_directory", "export_directory", "omemo_storage", "avatar")
        )
    width = max(len(label) for label, _ in rows)
    print("Deployment paths:")
    for label, value in rows:
        print(f"  {label + ':':<{width + 1}}  {value}")


def _install_plan(deployment: Deployment) -> None:
    _print_paths(deployment)
    print("\nInstall plan:")
    print("  - preserve every existing config/database/data/systemd unit file")
    print("  - create/reuse the configured virtualenv and install the checkout editable")
    print("  - create config.py only when missing; new configs use absolute /var/lib paths")
    print("  - keep the application checkout read-only under the hardened systemd unit")
    print("  - install a new systemd unit only when none exists and you confirm it")
    print("  - ask separately before starting the service")


def install(deployment: Deployment) -> int:
    _require_source_tree(deployment)
    _install_plan(deployment)
    if deployment.dry_run:
        print("\nDRY RUN: no files, packages or services were changed.")
        return 0
    _require_confirmation("Proceed with the muc_banbot installation shown above?")
    if not _account_exists(deployment.service_user):
        raise DeployError(
            f"service user {deployment.service_user!r} does not exist; create it manually or use --user"
        )
    stopped = _stop_active_service(
        deployment, reason="before installing dependencies and deployment files"
    )
    try:
        if not deployment.legacy_layout:
            _ensure_dir(deployment.data_dir, deployment, mode=0o700)
        _create_venv_if_missing(deployment)
        _install_dependencies(deployment)
        created_config = _write_config_from_sample(deployment)
        if created_config:
            print(
                "\nA hardened config was created with runtime paths under "
                f"{deployment.data_dir}, but credentials were not guessed.\n"
                f"Edit {deployment.config} and rerun './scripts/deploy.sh install'."
            )
            if stopped:
                print(f"LEAVE {deployment.service} stopped until the config has been reviewed.")
            return 0
        _validate_config(deployment)
        runtime = _runtime_paths(deployment)
        _validate_hardened_runtime_paths(deployment, runtime)
        _check_hardened_permissions(deployment)
        _check_hardened_runtime_permissions(deployment, runtime)
        _print_paths(deployment, runtime=runtime)
        _install_unit_if_missing(deployment)
    except Exception:
        if stopped:
            print(
                f"\nINSTALL FAILED: {deployment.service} was stopped and will remain stopped.",
                file=sys.stderr,
            )
        raise
    _ask_start(deployment)
    return 0


def _git(
    deployment: Deployment,
    *args: str,
    capture: bool = False,
    check: bool = True,
    announce: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["git", *args],
        deployment=deployment,
        as_service_user=os.geteuid() == 0 and _account_exists(deployment.service_user),
        cwd=deployment.root,
        capture=capture,
        check=check,
        announce=announce,
    )


def _require_clean_tracked_tree(deployment: Deployment) -> None:
    from envs_xmpp_ops.git import require_clean_tracked_tree

    require_clean_tracked_tree(partial(_git, deployment), error_factory=DeployError)


def _current_revision(deployment: Deployment) -> str:
    from envs_xmpp_ops.git import describe_revision

    return describe_revision(partial(_git, deployment))


def _is_stable_release_tag(tag: str) -> bool:
    from envs_xmpp_ops.git import is_stable_release_tag

    return is_stable_release_tag(tag)



def _git_remote(deployment: Deployment) -> str:
    configured = os.environ.get("MUC_BANBOT_DEPLOY_REMOTE")
    result = _git(deployment, "remote", capture=True, announce=False)
    remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if configured:
        if configured not in remotes:
            raise DeployError(f"configured Git remote does not exist: {configured}")
        return configured
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    raise DeployError("could not choose Git remote; set MUC_BANBOT_DEPLOY_REMOTE")


def _stable_tags(deployment: Deployment) -> list[str]:
    result = _git(deployment, "tag", "--sort=-v:refname", capture=True, announce=False)
    return [
        tag
        for tag in (line.strip() for line in result.stdout.splitlines())
        if _is_stable_release_tag(tag)
    ]


def _remote_tags(deployment: Deployment, remote: str) -> list[str]:
    from envs_xmpp_ops.git import remote_tags

    return remote_tags(partial(_git, deployment), remote)


def _latest_remote_tag(deployment: Deployment, remote: str) -> str:
    tags = [tag for tag in _remote_tags(deployment, remote) if _is_stable_release_tag(tag)]
    if not tags:
        raise DeployError(f"no stable vX.Y.Z release tags found on remote {remote!r}")
    return tags[0]


def _remote_tag_object(deployment: Deployment, remote: str, tag: str) -> str:
    from envs_xmpp_ops.git import remote_tag_object

    return remote_tag_object(
        partial(_git, deployment),
        remote,
        tag,
        error_factory=DeployError,
    )


def _local_tag_object(deployment: Deployment, tag: str) -> str | None:
    from envs_xmpp_ops.git import local_tag_object

    return local_tag_object(partial(_git, deployment), tag, error_factory=DeployError)


def _sync_release_tag(deployment: Deployment, remote: str, tag: str) -> None:
    """Fetch only the selected release tag without overwriting conflicts."""
    remote_object = _remote_tag_object(deployment, remote, tag)
    local_object = _local_tag_object(deployment, tag)
    if local_object is not None:
        if local_object != remote_object:
            raise DeployError(
                f"local release tag {tag!r} conflicts with remote {remote!r}; "
                "refusing to overwrite it. Verify the tag manually first."
            )
        return
    _git(
        deployment,
        "fetch",
        "--no-tags",
        remote,
        f"refs/tags/{tag}:refs/tags/{tag}",
    )


def _validate_tag(deployment: Deployment, tag: str) -> None:
    from envs_xmpp_ops.git import validate_tag

    validate_tag(partial(_git, deployment), tag, error_factory=DeployError)


def _prepare_release_target(deployment: Deployment, requested: str | None) -> tuple[str, str]:
    """Refresh branches without importing every tag, then sync one release."""
    remote = _git_remote(deployment)
    _git(deployment, "fetch", "--prune", "--no-tags", remote)
    if requested is not None and not _is_stable_release_tag(requested):
        raise DeployError("--to must name a stable vX.Y.Z release tag")
    target = requested or _latest_remote_tag(deployment, remote)
    _sync_release_tag(deployment, remote, target)
    _validate_tag(deployment, target)
    return remote, target


def _git_is_ancestor(deployment: Deployment, older: str, newer: str) -> bool:
    from envs_xmpp_ops.git import git_is_ancestor

    return git_is_ancestor(
        partial(_git, deployment),
        older,
        newer,
        error_factory=DeployError,
    )


def _target_relation(deployment: Deployment, target: str) -> str:
    head_before_target = _git_is_ancestor(deployment, "HEAD", target)
    target_before_head = _git_is_ancestor(deployment, target, "HEAD")
    if head_before_target and target_before_head:
        return "same"
    if head_before_target:
        return "upgrade"
    if target_before_head:
        return "downgrade"
    return "diverged"


def _head_is_detached(deployment: Deployment) -> bool:
    from envs_xmpp_ops.git import head_is_detached

    return head_is_detached(partial(_git, deployment), error_factory=DeployError)


def _approve_target(
    deployment: Deployment,
    target: str,
    *,
    requested: str | None,
    allow_downgrade: bool,
) -> bool:
    current = _current_revision(deployment)
    relation = _target_relation(deployment, target)
    if relation == "upgrade":
        _require_confirmation(f"Update {current} to {target}?")
        return True
    if relation == "same":
        if _head_is_detached(deployment):
            print(f"Already at release {target}; nothing to update.")
            return False
        _require_confirmation(
            f"Current HEAD already matches {target}. Pin this checkout to the release tag?"
        )
        return True
    if relation == "downgrade":
        if requested is None:
            print(f"No newer release is available (latest release: {target}).")
            print(f"The current checkout {current} contains commits newer than {target}.")
            print("Nothing to update; the development branch is never deployed automatically.")
            return False
        if not allow_downgrade:
            raise DeployError(
                f"requested release {target} is older than the current checkout {current}; "
                "refusing downgrade (use --allow-downgrade only for an intentional rollback)"
            )
        print(
            "WARNING: this is an explicit code downgrade. The helper does not downgrade the "
            "database schema; a matching database backup may be required."
        )
        _require_confirmation(f"Downgrade {current} to {target}?")
        return True
    raise DeployError(
        f"release {target} is not on the current HEAD history; refusing a non-fast-forward deployment"
    )


def _relative_to_root(path: Path | None, root: Path) -> bool:
    from envs_xmpp_ops.paths import relative_to_root

    return relative_to_root(path, root)


def _git_path_is_tracked(deployment: Deployment, path: Path) -> bool:
    """Return whether *path* is tracked by the deployment Git checkout."""
    if not _relative_to_root(path, deployment.root):
        return False
    relative = path.resolve().relative_to(deployment.root.resolve())
    result = _run(
        ["git", "ls-files", "--error-unmatch", "--", str(relative)],
        deployment=deployment,
        cwd=deployment.root,
        capture=True,
        check=False,
        announce=False,
    )
    return result.returncode == 0


def _protected_paths(deployment: Deployment) -> dict[str, Path]:
    runtime = _runtime_paths(deployment)
    protected: dict[str, Path] = {"config": deployment.config}
    for key in ("database", "omemo_storage"):
        path = runtime.get(key)
        if path is not None:
            protected[key.replace("_", " ")] = path

    # The repository's default avatar.png is tracked release content and must
    # be allowed to change during an update. Preserve only an operator-supplied
    # avatar that is not tracked by Git.
    avatar = runtime.get("avatar")
    if avatar is not None and not _git_path_is_tracked(deployment, avatar):
        protected["avatar"] = avatar
    return protected


def _backup_project_protected_paths(
    deployment: Deployment,
    protected: dict[str, Path],
    backup_dir: Path,
) -> dict[str, tuple[Path, Path]]:
    backups: dict[str, tuple[Path, Path]] = {}
    for label, path in protected.items():
        if not path.is_file() or not _relative_to_root(path, deployment.root):
            continue
        target = backup_dir / f"{len(backups):02d}-{path.name}"
        shutil.copy2(path, target)
        backups[label] = (path, target)
        print(f"PROTECT {label}: {path}")
    return backups


def _restore_project_protected_paths(backups: dict[str, tuple[Path, Path]]) -> None:
    for label, (path, backup) in backups.items():
        if path.is_file() and filecmp.cmp(path, backup, shallow=False):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, path)
        print(f"RESTORE protected {label}: {path}")


def _backup_database_before_update(deployment: Deployment) -> Path | None:
    runtime = _runtime_paths(deployment)
    database = runtime.get("database")
    backup_dir = runtime.get("backup_directory")
    if database is None or not database.exists():
        print("No existing database found; pre-update backup not required.")
        return None
    if backup_dir is None:
        backup_dir = deployment.data_dir / "backups"
    _ensure_dir(backup_dir, deployment, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"pre-update-{stamp}.sqlite3"
    source_conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()
    target.chmod(0o600)
    if os.geteuid() == 0 and _account_exists(deployment.service_user):
        user = pwd.getpwnam(deployment.service_user)
        try:
            gid = grp.getgrnam(deployment.service_group).gr_gid
        except KeyError:
            gid = user.pw_gid
        os.chown(target, user.pw_uid, gid)
    print(f"CREATE verified SQLite pre-update backup: {target}")
    return target


def _update_plan(deployment: Deployment, requested_tag: str | None) -> None:
    runtime = None
    if deployment.venv_python.is_file() and deployment.config.is_file():
        try:
            runtime = _runtime_paths(deployment)
        except DeployError:
            # Runtime-path discovery is optional when rendering the update plan.
            runtime = None
    _print_paths(deployment, runtime=runtime)
    print("\nUpdate plan:")
    print(f"  current: {_current_revision(deployment)}")
    print(f"  target:  {requested_tag or 'newest stable vX.Y.Z release'}")
    print("  - require a clean tracked Git worktree")
    print("  - preserve config, data directory and existing systemd unit")
    print("  - ask before stopping an active service")
    print("  - fetch and checkout only the selected stable release tag, never main")
    print("  - create a consistent SQLite pre-update backup while the service is stopped")
    print("  - reinstall the checkout into the existing virtualenv and validate config")
    print("  - ask separately before starting the service")


def update(
    deployment: Deployment,
    requested_tag: str | None,
    *,
    allow_downgrade: bool = False,
) -> int:
    _require_source_tree(deployment)
    if not (deployment.root / ".git").exists():
        raise DeployError(f"update requires a Git checkout: {deployment.root}")
    if not deployment.venv_python.is_file() or not deployment.config.is_file():
        raise DeployError("existing virtualenv and runtime config are required for update")
    _require_clean_tracked_tree(deployment)
    _validate_config(deployment)
    runtime = _runtime_paths(deployment)
    _validate_hardened_runtime_paths(deployment, runtime)
    _check_hardened_permissions(deployment)
    _check_hardened_runtime_permissions(deployment, runtime)
    _update_plan(deployment, requested_tag)
    if deployment.dry_run:
        print("\nDRY RUN: no Git refs, files, packages, database or services were changed.")
        return 0
    _require_confirmation("Proceed with the muc_banbot update plan shown above?")
    remote, target = _prepare_release_target(deployment, requested_tag)
    print(f"Selected release: {target} (remote: {remote})")
    if not _approve_target(
        deployment,
        target,
        requested=requested_tag,
        allow_downgrade=allow_downgrade,
    ):
        return 0
    stopped = _stop_active_service(deployment, reason="before changing code and dependencies")
    try:
        _backup_database_before_update(deployment)
        with tempfile.TemporaryDirectory(prefix="muc-banbot-deploy-") as tmp:
            protected = _backup_project_protected_paths(
                deployment,
                _protected_paths(deployment),
                Path(tmp),
            )
            try:
                _git(deployment, "checkout", target)
            finally:
                _restore_project_protected_paths(protected)
        _install_dependencies(deployment)
        _validate_config(deployment)
        runtime = _runtime_paths(deployment)
        _validate_hardened_runtime_paths(deployment, runtime)
        _check_hardened_permissions(deployment)
        _check_hardened_runtime_permissions(deployment, runtime)
    except Exception:
        if stopped:
            print(
                f"\nUPDATE FAILED: {deployment.service} was stopped and will remain stopped.",
                file=sys.stderr,
            )
        raise
    _ask_start(deployment)
    print(f"Update to {target} completed.")
    return 0


def _expected_systemd_values(deployment: Deployment) -> dict[str, str]:
    return {
        "Type": "notify",
        "NotifyAccess": "main",
        "User": deployment.service_user,
        "Group": deployment.service_group,
        "WorkingDirectory": str(deployment.root),
        "Restart": "on-failure",
        "UMask": "0077",
        "NoNewPrivileges": "yes",
        "PrivateDevices": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
    }


def _check_installed_systemd(deployment: Deployment) -> bool:
    if not _systemctl_exists(deployment):
        print(f"SKIP installed systemd check: {deployment.service} not found")
        return True
    if deployment.legacy_layout:
        print(
            "OK  legacy source-tree deployment detected; hardened Type=notify/path checks "
            "are not required for this supported installation mode"
        )
        return True
    ok = True
    print("Installed systemd service:")
    for prop, expected in _expected_systemd_values(deployment).items():
        actual = _systemd_property(deployment.service, prop)
        matches = actual == expected
        ok = ok and matches
        print(f"  {'OK' if matches else 'FAIL':<4}  {prop}: {actual or '-'}")
        if not matches:
            print(f"        expected: {expected}")

    config_value = _systemd_environment(deployment.service, "MUC_BANBOT_CONFIG")
    config_ok = config_value is not None and Path(config_value).resolve() == deployment.config
    print(f"  {'OK' if config_ok else 'FAIL':<4}  MUC_BANBOT_CONFIG: {config_value or '-'}")
    ok = ok and config_ok

    bytecode_value = _systemd_environment(deployment.service, "PYTHONDONTWRITEBYTECODE")
    bytecode_ok = bytecode_value == "1"
    print(
        f"  {'OK' if bytecode_ok else 'FAIL':<4}  "
        f"PYTHONDONTWRITEBYTECODE: {bytecode_value or '-'}"
    )
    ok = ok and bytecode_ok

    exec_start = _systemd_property(deployment.service, "ExecStart")
    exec_path = _systemd_exec_path_from_value(exec_start)
    exec_ok = exec_path is not None and exec_path.resolve() == deployment.executable.resolve()
    exec_display = str(exec_path) if exec_path is not None else (exec_start or "-")
    print(f"  {'OK' if exec_ok else 'FAIL':<4}  ExecStart: {exec_display}")
    if not exec_ok:
        print(f"        expected: {deployment.executable}")
    ok = ok and exec_ok

    writable = _systemd_property(deployment.service, "ReadWritePaths")
    expected_writable = {str(deployment.config.parent), str(deployment.data_dir)}
    writable_ok = _systemd_paths(writable) == expected_writable
    print(f"  {'OK' if writable_ok else 'FAIL':<4}  ReadWritePaths: {writable or '-'}")
    if not writable_ok:
        print(f"        expected exactly: {' '.join(sorted(expected_writable))}")
    ok = ok and writable_ok

    watchdog = _systemd_property(deployment.service, "WatchdogUSec")
    watchdog_ok = watchdog in {"1min", "60s", "60000000us"}
    print(f"  {'OK' if watchdog_ok else 'FAIL':<4}  WatchdogUSec: {watchdog or '-'}")
    if not watchdog_ok:
        print("        expected: 1min (60s)")
    ok = ok and watchdog_ok
    return ok


def status(deployment: Deployment) -> int:
    _require_source_tree(deployment)
    runtime = None
    if deployment.venv_python.is_file() and deployment.config.is_file():
        try:
            runtime = _runtime_paths(deployment)
        except DeployError as exc:
            print(f"Runtime paths: unavailable ({exc})")
    _print_paths(deployment, runtime=runtime)
    print("\nDeployment status:")
    rows: list[tuple[str, str]] = []
    if (deployment.root / ".git").exists():
        try:
            rows.append(("revision", _current_revision(deployment)))
            tags = _stable_tags(deployment)
            rows.append(("latest local stable tag", tags[0] if tags else "none"))
        except DeployError as exc:
            rows.append(("Git status", f"unavailable ({exc})"))
    if shutil.which("systemctl"):
        rows.append(("service state", "active" if _service_active(deployment) else "inactive/not found"))
    width = max((len(label) for label, _ in rows), default=1)
    for label, value in rows:
        print(f"  {label + ':':<{width + 1}}  {value}")
    return 0


def check(deployment: Deployment) -> int:
    _require_source_tree(deployment)
    if not deployment.executable.is_file():
        raise DeployError(f"muc_banbot executable not found: {deployment.executable}")
    _validate_config(deployment)
    runtime = _runtime_paths(deployment)
    _validate_hardened_runtime_paths(deployment, runtime)
    _check_hardened_permissions(deployment)
    _check_hardened_runtime_permissions(deployment, runtime)
    database = runtime.get("database")
    if database is not None and not database.parent.exists():
        raise DeployError(f"database parent does not exist: {database.parent}")
    print(f"OK  database parent: {database.parent if database else '-'}")
    for key in ("backup_directory", "export_directory"):
        path = runtime.get(key)
        if path is not None and not path.parent.exists() and not path.exists():
            raise DeployError(f"{key.replace('_', ' ')} parent does not exist: {path.parent}")
        print(f"OK  {key.replace('_', ' ')}: {path or '-'}")
    if not _check_installed_systemd(deployment):
        raise DeployError(
            "installed systemd service differs from the hardened deployment; review FAIL entries above. "
            "Existing units are intentionally not replaced automatically."
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    options = parser.parse_args(argv)
    if options.command is None:
        parser.print_help()
        return 0
    if options.to and options.command != "update":
        parser.error("--to is only valid with update")
    if options.allow_downgrade and options.command != "update":
        parser.error("--allow-downgrade is only valid with update")
    if options.allow_downgrade and not options.to:
        parser.error("--allow-downgrade requires --to TAG")
    ensure_envs_xmpp()
    deployment = _deployment(options)
    try:
        if options.command == "status":
            return status(deployment)
        if options.command == "check":
            return check(deployment)
        if options.command == "install":
            return install(deployment)
        return update(
            deployment,
            options.to,
            allow_downgrade=options.allow_downgrade,
        )
    except UserCancelled as exc:
        print(f"deploy: {exc}")
        return 2
    except DeployError as exc:
        print(f"deploy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
