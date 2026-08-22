from __future__ import annotations

import os
from pathlib import Path

import pytest

import config

from banbot.config.runtime import ConfigRuntimeMixin
from tests.test_config_validation_more import ConfigValidationBot, set_valid_config


class ConfigWriter(ConfigRuntimeMixin):
    def __init__(self, path: Path) -> None:
        self.path = path

    def _config_file_path(self) -> Path:
        return self.path


def test_runtime_config_rewrite_forces_private_mode_even_with_open_umask(tmp_path):
    path = tmp_path / "config.py"
    path.write_text("LOG_LEVEL = 'INFO'\n", encoding="utf-8")
    path.chmod(0o644)
    writer = ConfigWriter(path)

    old_umask = os.umask(0o022)
    try:
        writer.update_config_file_assignment("LOG_LEVEL", "DEBUG")
    finally:
        os.umask(old_umask)

    assert path.stat().st_mode & 0o777 == 0o600
    assert "LOG_LEVEL = 'DEBUG'" in path.read_text(encoding="utf-8")


def test_config_validation_warns_when_config_file_is_group_or_world_accessible(
    tmp_path, monkeypatch
):
    set_valid_config(monkeypatch)
    path = tmp_path / "config.py"
    path.write_text("PASSWORD = 'secret'\n", encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(config, "__file__", str(path), raising=False)
    bot = ConfigValidationBot()

    errors, warnings = bot._validate_config()

    assert errors == []
    assert any("permissions are too open (0644)" in warning for warning in warnings)


def test_runtime_config_rewrite_replaces_complete_multiline_assignment(tmp_path):
    path = tmp_path / "config.py"
    path.write_text(
        "REDACTION_AUTO_REASONS = [\n"
        "    'spam',\n"
        "    'abuse',\n"
        "]\n"
        "LOG_LEVEL = 'INFO'\n",
        encoding="utf-8",
    )
    writer = ConfigWriter(path)

    writer.update_config_file_assignment("REDACTION_AUTO_REASONS", ["malware"])

    text = path.read_text(encoding="utf-8")
    assert "REDACTION_AUTO_REASONS = ['malware']" in text
    assert "'spam'" not in text
    assert "'abuse'" not in text
    assert "LOG_LEVEL = 'INFO'" in text
    compile(text, str(path), "exec")


def test_runtime_config_rewrite_rejects_duplicate_top_level_assignments(tmp_path):
    path = tmp_path / "config.py"
    original = "LOG_LEVEL = 'INFO'\nLOG_LEVEL = 'DEBUG'\n"
    path.write_text(original, encoding="utf-8")
    writer = ConfigWriter(path)

    try:
        writer.update_config_file_assignment("LOG_LEVEL", "WARNING")
    except ValueError as exc:
        assert "multiple top-level assignments" in str(exc)
    else:
        raise AssertionError("duplicate config assignment should have been rejected")

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_runtime_config_apply_failure_restores_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "config.py"
    original = "LOG_LEVEL = 'INFO'\n"
    path.write_text(original, encoding="utf-8")

    class FailingApplyWriter(ConfigRuntimeMixin):
        CONFIG_KEYS = ("LOG_LEVEL",)
        STARTUP_ONLY_CONFIG_KEYS = ()
        CONFIG_NEVER_WRITABLE_KEYS = ()

        def _config_file_path(self) -> Path:
            return path

        def _validate_config(self):
            return [], []

        def apply_runtime_config(self):
            return None

        async def update_vcard(self):
            raise RuntimeError("vCard update failed")

    def fake_reload(module):
        namespace = {}
        exec(path.read_text(encoding="utf-8"), namespace)
        module.LOG_LEVEL = namespace["LOG_LEVEL"]
        return module

    monkeypatch.setattr(config, "LOG_LEVEL", "INFO", raising=False)
    monkeypatch.setattr("banbot.config.runtime.importlib.reload", fake_reload)
    writer = FailingApplyWriter()

    ok, message = await writer.set_runtime_config_value(
        "LOG_LEVEL",
        "DEBUG",
        actor="tester",
        _locked=True,
    )

    assert ok is False
    assert "vCard update failed" in message
    assert path.read_text(encoding="utf-8") == original
    assert config.LOG_LEVEL == "INFO"
