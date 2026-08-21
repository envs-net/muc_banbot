from __future__ import annotations

import os
from pathlib import Path

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
