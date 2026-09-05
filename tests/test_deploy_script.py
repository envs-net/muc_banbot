from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = ROOT / "scripts" / "deploy.py"


def _load_deploy_module():
    spec = importlib.util.spec_from_file_location("muc_banbot_deploy_script", DEPLOY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy_module()


def _deployment(tmp_path: Path, *, dry_run: bool = False):
    root = tmp_path / "muc_banbot"
    root.mkdir()
    return deploy.Deployment(
        root=root,
        venv=root / "venv",
        config=tmp_path / "etc" / "muc_banbot" / "config.py",
        data_dir=tmp_path / "var" / "lib" / "muc_banbot",
        service="muc_banbot-test.service",
        service_user="test-user",
        service_group="test-group",
        unit=tmp_path / "muc_banbot-test.service",
        python="python3",
        dry_run=dry_run,
    )


def _source_markers(deployment) -> None:
    (deployment.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (deployment.root / "config_sample.py").write_text(
        "DB_FILE = 'banbot.db'\n"
        "DB_BACKUP_DIR = 'data/backups'\n"
        "EXPORT_DIR = 'data/exports'\n"
        "OMEMO_STORAGE_FILE = 'data/omemo.json'\n",
        encoding="utf-8",
    )
    scripts = deployment.root / "scripts"
    scripts.mkdir()
    (scripts / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")


def test_bare_deploy_command_only_prints_help(capsys):
    assert deploy.main([]) == 0
    output = capsys.readouterr().out
    assert "Interactive, preservation-first muc_banbot deployment helper" in output
    assert "{status,check,install,update}" in output


def test_deploy_shell_wrapper_is_executable_and_defaults_to_help():
    wrapper = ROOT / "scripts" / "deploy.sh"
    result = subprocess.run([str(wrapper)], cwd=ROOT, check=False, capture_output=True, text=True)
    assert os.access(wrapper, os.X_OK)
    assert result.returncode == 0
    assert "{status,check,install,update}" in result.stdout


def test_hardened_unit_separates_code_config_and_data(tmp_path):
    deployment = _deployment(tmp_path)
    unit = deploy._render_systemd_unit(deployment)
    assert "Type=notify" in unit
    assert "ProtectSystem=strict" in unit
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit
    assert f"Environment=MUC_BANBOT_CONFIG={deployment.config}" in unit
    assert f"ReadWritePaths={deployment.config.parent} {deployment.data_dir}" in unit
    assert f"ReadWritePaths={deployment.root}" not in unit


def test_deploy_python_environment_disables_bytecode_writes(tmp_path):
    deployment = _deployment(tmp_path)
    assert deployment.environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert deployment.environment["MUC_BANBOT_CONFIG"] == str(deployment.config)


def test_systemd_exec_path_parser_returns_clean_executable_path():
    value = (
        "{ path=/srv/adminbot/muc_banbot/venv/bin/muc_banbot ; "
        "argv[]=/srv/adminbot/muc_banbot/venv/bin/muc_banbot ; ignore_errors=no ; }"
    )

    assert deploy._systemd_exec_path_from_value(value) == Path(
        "/srv/adminbot/muc_banbot/venv/bin/muc_banbot"
    )


def test_installed_systemd_check_prints_clean_execstart_and_rejects_extra_writable_path(
    tmp_path, monkeypatch, capsys
):
    deployment = _deployment(tmp_path)
    deployment.executable.parent.mkdir(parents=True)
    deployment.executable.write_text("#!/bin/sh\n", encoding="utf-8")
    deployment.config.parent.mkdir(parents=True)
    deployment.config.write_text("# config\n", encoding="utf-8")
    deployment.data_dir.mkdir(parents=True)

    properties = {
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
        "Environment": (
            f"MUC_BANBOT_CONFIG={deployment.config} "
            "PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
        ),
        "ExecStart": (
            f"{{ path={deployment.executable} ; argv[]={deployment.executable} ; "
            "ignore_errors=no ; }}"
        ),
        "ReadWritePaths": (
            f"{deployment.config.parent} {deployment.data_dir} /srv"
        ),
        "WatchdogUSec": "1min",
    }
    monkeypatch.setattr(deploy, "_systemctl_exists", lambda _deployment: True)
    monkeypatch.setattr(
        deploy,
        "_systemd_property",
        lambda _service, prop: properties.get(prop, ""),
    )

    assert deploy._check_installed_systemd(deployment) is False
    output = capsys.readouterr().out
    assert f"ExecStart: {deployment.executable}" in output
    assert "argv[]=" not in output
    assert "FAIL  ReadWritePaths:" in output
    assert "expected exactly:" in output


def test_installed_systemd_check_accepts_expected_hardened_unit(
    tmp_path, monkeypatch, capsys
):
    deployment = _deployment(tmp_path)
    deployment.executable.parent.mkdir(parents=True)
    deployment.executable.write_text("#!/bin/sh\n", encoding="utf-8")
    deployment.config.parent.mkdir(parents=True)
    deployment.config.write_text("# config\n", encoding="utf-8")
    deployment.data_dir.mkdir(parents=True)

    properties = {
        **deploy._expected_systemd_values(deployment),
        "Environment": (
            f"MUC_BANBOT_CONFIG={deployment.config} "
            "PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
        ),
        "ExecStart": f"{{ path={deployment.executable} ; }}",
        "ReadWritePaths": f"{deployment.config.parent} {deployment.data_dir}",
        "WatchdogUSec": "1min",
    }
    monkeypatch.setattr(deploy, "_systemctl_exists", lambda _deployment: True)
    monkeypatch.setattr(
        deploy,
        "_systemd_property",
        lambda _service, prop: properties.get(prop, ""),
    )

    assert deploy._check_installed_systemd(deployment) is True
    output = capsys.readouterr().out
    assert f"OK    ExecStart: {deployment.executable}" in output
    assert "FAIL" not in output


def test_installed_systemd_check_requires_bytecode_guard_and_expected_watchdog(
    tmp_path, monkeypatch, capsys
):
    deployment = _deployment(tmp_path)
    deployment.executable.parent.mkdir(parents=True)
    deployment.executable.write_text("#!/bin/sh\n", encoding="utf-8")
    deployment.config.parent.mkdir(parents=True)
    deployment.config.write_text("# config\n", encoding="utf-8")
    deployment.data_dir.mkdir(parents=True)

    properties = {
        **deploy._expected_systemd_values(deployment),
        "Environment": f"MUC_BANBOT_CONFIG={deployment.config}",
        "ExecStart": f"{{ path={deployment.executable} ; }}",
        "ReadWritePaths": f"{deployment.config.parent} {deployment.data_dir}",
        "WatchdogUSec": "5min",
    }
    monkeypatch.setattr(deploy, "_systemctl_exists", lambda _deployment: True)
    monkeypatch.setattr(
        deploy,
        "_systemd_property",
        lambda _service, prop: properties.get(prop, ""),
    )

    assert deploy._check_installed_systemd(deployment) is False
    output = capsys.readouterr().out
    assert "FAIL  PYTHONDONTWRITEBYTECODE: -" in output
    assert "FAIL  WatchdogUSec: 5min" in output
    assert "expected: 1min (60s)" in output


def test_new_hardened_config_uses_absolute_data_paths(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    _source_markers(deployment)
    monkeypatch.setattr(deploy, "_account_exists", lambda _user: False)

    assert deploy._write_config_from_sample(deployment) is True
    text = deployment.config.read_text(encoding="utf-8")
    assert f"DB_FILE = {str(deployment.data_dir / 'banbot.db')!r}" in text
    assert f"DB_BACKUP_DIR = {str(deployment.data_dir / 'backups')!r}" in text
    assert f"EXPORT_DIR = {str(deployment.data_dir / 'exports')!r}" in text
    assert f"OMEMO_STORAGE_FILE = {str(deployment.data_dir / 'omemo.json')!r}" in text

    deployment.config.write_text("operator config\n", encoding="utf-8")
    assert deploy._write_config_from_sample(deployment) is False
    assert deployment.config.read_text(encoding="utf-8") == "operator config\n"


def test_project_protected_files_are_restored_after_checkout_changes(tmp_path):
    deployment = _deployment(tmp_path)
    config = deployment.root / "config.py"
    database = deployment.root / "banbot.db"
    outside = tmp_path / "external.db"
    config.write_text("operator config\n", encoding="utf-8")
    database.write_bytes(b"operator db")
    outside.write_bytes(b"external")
    backup_dir = tmp_path / "protect"
    backup_dir.mkdir()

    backups = deploy._backup_project_protected_paths(
        deployment,
        {"config": config, "database": database, "external": outside},
        backup_dir,
    )
    assert set(backups) == {"config", "database"}

    config.unlink()
    database.write_bytes(b"replacement")
    deploy._restore_project_protected_paths(backups)
    assert config.read_text(encoding="utf-8") == "operator config\n"
    assert database.read_bytes() == b"operator db"
    assert outside.read_bytes() == b"external"


def test_install_dry_run_does_not_prompt_or_change_files(tmp_path, monkeypatch, capsys):
    deployment = _deployment(tmp_path, dry_run=True)
    _source_markers(deployment)
    monkeypatch.setattr(deploy, "_confirm", lambda _prompt: pytest.fail("dry-run must not prompt"))

    assert deploy.install(deployment) == 0
    assert not deployment.config.exists()
    assert not deployment.venv.exists()
    assert "DRY RUN" in capsys.readouterr().out


def test_explicit_legacy_install_keeps_source_tree_data_dir(tmp_path):
    root = tmp_path / "muc_banbot"
    root.mkdir()
    config = root / "config.py"
    config.write_text("# operator config\n", encoding="utf-8")
    parser = deploy._build_parser()
    options = parser.parse_args([
        "install",
        "--root", str(root),
        "--config", str(config),
        "--service", "missing-test.service",
    ])
    deployment = deploy._deployment(options)
    assert deployment.legacy_layout is True
    assert deployment.data_dir == root.resolve()


def test_ensure_dir_never_reowns_existing_operator_directory(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    existing = tmp_path / "operator-owned"
    existing.mkdir(mode=0o711)
    before_mode = existing.stat().st_mode & 0o777
    monkeypatch.setattr(deploy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(deploy, "_account_exists", lambda _user: True)
    monkeypatch.setattr(
        deploy.os,
        "chown",
        lambda *_args: pytest.fail("existing directory ownership must not change"),
    )

    deploy._ensure_dir(existing, deployment, mode=0o700)
    assert existing.stat().st_mode & 0o777 == before_mode


def test_default_config_detects_existing_legacy_layout_on_install(tmp_path, monkeypatch):
    root = tmp_path / "muc_banbot"
    root.mkdir()
    legacy = root / "config.py"
    legacy.write_text("# existing operator config\n", encoding="utf-8")
    monkeypatch.delenv("MUC_BANBOT_CONFIG", raising=False)
    monkeypatch.setattr(deploy, "_systemd_environment", lambda *_args: None)
    original_exists = deploy.Path.exists

    def controlled_exists(path):
        if path == deploy.Path("/etc/muc_banbot/config.py"):
            return False
        return original_exists(path)

    monkeypatch.setattr(deploy.Path, "exists", controlled_exists)
    assert deploy._default_config(root, "muc_banbot.service") == legacy.resolve()


def test_default_config_prefers_legacy_checkout_without_systemd_override(tmp_path, monkeypatch):
    root = tmp_path / "muc_banbot"
    root.mkdir()
    legacy = root / "config.py"
    legacy.write_text("# existing operator config\n", encoding="utf-8")
    monkeypatch.delenv("MUC_BANBOT_CONFIG", raising=False)
    monkeypatch.setattr(deploy, "_systemd_environment", lambda *_args: None)

    # Simulate a stale/unrelated hardened config existing on the same host.
    # A legacy checkout must not silently switch configs unless its unit says so.
    assert deploy._default_config(root, "muc_banbot.service") == legacy.resolve()


def test_hardened_runtime_paths_reject_mutable_state_outside_data_dir(tmp_path):
    deployment = _deployment(tmp_path)
    runtime = {
        "database": deployment.root / "banbot.db",
        "backup_directory": deployment.data_dir / "backups",
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }

    with pytest.raises(deploy.DeployError, match="mutable paths outside") as exc_info:
        deploy._validate_hardened_runtime_paths(deployment, runtime)

    assert "DB_FILE=" in str(exc_info.value)
    assert str(deployment.data_dir) in str(exc_info.value)


def test_hardened_runtime_paths_allow_mutable_state_below_data_dir_and_external_avatar(tmp_path, capsys):
    deployment = _deployment(tmp_path)
    runtime = {
        "database": deployment.data_dir / "banbot.db",
        "backup_directory": deployment.data_dir / "backups",
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }

    deploy._validate_hardened_runtime_paths(deployment, runtime)

    assert "mutable runtime paths stay below" in capsys.readouterr().out


def test_legacy_runtime_paths_remain_supported_outside_external_data_dir(tmp_path):
    deployment = _deployment(tmp_path)
    deployment = deploy.Deployment(
        root=deployment.root,
        venv=deployment.venv,
        config=deployment.root / "config.py",
        data_dir=deployment.root,
        service=deployment.service,
        service_user=deployment.service_user,
        service_group=deployment.service_group,
        unit=deployment.unit,
        python=deployment.python,
    )
    runtime = {
        "database": deployment.root / "banbot.db",
        "backup_directory": deployment.root / "data" / "backups",
        "export_directory": deployment.root / "data" / "exports",
        "omemo_storage": deployment.root / "data" / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }

    deploy._validate_hardened_runtime_paths(deployment, runtime)


def test_hardened_permission_check_accepts_secure_baseline(tmp_path, monkeypatch, capsys):
    deployment = _deployment(tmp_path)
    deployment.config.parent.mkdir(parents=True, mode=0o750)
    deployment.data_dir.mkdir(parents=True, mode=0o700)
    deployment.config.write_text("PASSWORD = 'secret'\n", encoding="utf-8")
    deployment.config.parent.chmod(0o750)
    deployment.data_dir.chmod(0o700)
    deployment.config.chmod(0o600)
    uid = os.getuid()
    gid = os.getgid()
    monkeypatch.setattr(deploy, "_expected_ids", lambda _deployment: (uid, gid))

    deploy._check_hardened_permissions(deployment)

    assert "hardened permissions" in capsys.readouterr().out


def test_hardened_permission_check_rejects_world_readable_config(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    deployment.config.parent.mkdir(parents=True, mode=0o750)
    deployment.data_dir.mkdir(parents=True, mode=0o700)
    deployment.config.write_text("PASSWORD = 'secret'\n", encoding="utf-8")
    deployment.config.parent.chmod(0o750)
    deployment.data_dir.chmod(0o700)
    deployment.config.chmod(0o644)
    uid = os.getuid()
    gid = os.getgid()
    monkeypatch.setattr(deploy, "_expected_ids", lambda _deployment: (uid, gid))

    with pytest.raises(deploy.DeployError, match="config file mode is 0644") as exc_info:
        deploy._check_hardened_permissions(deployment)

    message = str(exc_info.value)
    assert "sudo chmod 0600" in message
    assert "contains credentials" in message


def test_hardened_runtime_permission_check_rejects_root_owned_migrated_database(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    deployment.data_dir.mkdir(parents=True)
    database = deployment.data_dir / "banbot.db"
    database.write_bytes(b"sqlite")
    runtime = {
        "database": database,
        "backup_directory": deployment.data_dir / "backups",
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }
    uid = os.getuid()
    gid = os.getgid()
    monkeypatch.setattr(deploy, "_expected_ids", lambda _deployment: (uid + 1, gid + 1))

    with pytest.raises(deploy.DeployError, match="not usable by the service account") as exc_info:
        deploy._check_hardened_runtime_permissions(deployment, runtime)

    assert "database ownership" in str(exc_info.value)
    assert "sudo chown -R" in str(exc_info.value)


def test_hardened_runtime_permission_check_rejects_world_readable_nested_backup(
    tmp_path, monkeypatch
):
    deployment = _deployment(tmp_path)
    deployment.data_dir.mkdir(parents=True, mode=0o700)
    backup_dir = deployment.data_dir / "backups"
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "snapshot.zip"
    backup.write_bytes(b"backup")
    backup.chmod(0o644)
    runtime = {
        "database": deployment.data_dir / "banbot.db",
        "backup_directory": backup_dir,
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }
    monkeypatch.setattr(
        deploy, "_expected_ids", lambda _deployment: (os.getuid(), os.getgid())
    )

    with pytest.raises(deploy.DeployError, match="data file mode is 0644") as exc_info:
        deploy._check_hardened_runtime_permissions(deployment, runtime)

    message = str(exc_info.value)
    assert "expected 0600" in message
    assert "find" in message
    assert "chmod 0600" in message


def test_hardened_runtime_permission_check_accepts_private_nested_data(
    tmp_path, monkeypatch, capsys
):
    deployment = _deployment(tmp_path)
    deployment.data_dir.mkdir(parents=True, mode=0o700)
    backup_dir = deployment.data_dir / "backups"
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "snapshot.zip"
    backup.write_bytes(b"backup")
    backup.chmod(0o600)
    runtime = {
        "database": deployment.data_dir / "banbot.db",
        "backup_directory": backup_dir,
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }
    monkeypatch.setattr(
        deploy, "_expected_ids", lambda _deployment: (os.getuid(), os.getgid())
    )

    deploy._check_hardened_runtime_permissions(deployment, runtime)

    assert "private and usable" in capsys.readouterr().out


def test_hardened_runtime_permission_check_allows_missing_runtime_targets(tmp_path, monkeypatch, capsys):
    deployment = _deployment(tmp_path)
    deployment.data_dir.mkdir(parents=True)
    runtime = {
        "database": deployment.data_dir / "banbot.db",
        "backup_directory": deployment.data_dir / "backups",
        "export_directory": deployment.data_dir / "exports",
        "omemo_storage": deployment.data_dir / "omemo.json",
        "avatar": deployment.root / "avatar.png",
    }
    monkeypatch.setattr(deploy, "_expected_ids", lambda _deployment: (os.getuid(), os.getgid()))

    deploy._check_hardened_runtime_permissions(deployment, runtime)

    assert "existing mutable runtime files/directories are private and usable" in capsys.readouterr().out


def test_protected_paths_do_not_preserve_tracked_release_avatar(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    avatar = deployment.root / "avatar.png"
    runtime = {
        "database": deployment.root / "banbot.db",
        "backup_directory": deployment.root / "data" / "backups",
        "export_directory": deployment.root / "data" / "exports",
        "omemo_storage": deployment.root / "data" / "omemo.json",
        "avatar": avatar,
    }
    monkeypatch.setattr(deploy, "_runtime_paths", lambda _deployment: runtime)
    monkeypatch.setattr(
        deploy,
        "_git_path_is_tracked",
        lambda _deployment, path: path == avatar,
    )

    protected = deploy._protected_paths(deployment)

    assert "avatar" not in protected
    assert protected["database"] == runtime["database"]
    assert protected["omemo storage"] == runtime["omemo_storage"]


def test_protected_paths_preserve_untracked_custom_avatar(tmp_path, monkeypatch):
    deployment = _deployment(tmp_path)
    avatar = deployment.root / "custom" / "operator-avatar.png"
    runtime = {
        "database": deployment.root / "banbot.db",
        "backup_directory": deployment.root / "data" / "backups",
        "export_directory": deployment.root / "data" / "exports",
        "omemo_storage": deployment.root / "data" / "omemo.json",
        "avatar": avatar,
    }
    monkeypatch.setattr(deploy, "_runtime_paths", lambda _deployment: runtime)
    monkeypatch.setattr(deploy, "_git_path_is_tracked", lambda _deployment, _path: False)

    protected = deploy._protected_paths(deployment)

    assert protected["avatar"] == avatar


def test_local_coverage_profile_enforces_ci_threshold():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    testing = pyproject["tool"]["envs-xmpp"]["testing"]

    assert testing["coverage-source"] == "banbot"
    assert testing["coverage-report"] == "term-missing"
    assert testing["coverage-fail-under"] == 55
