from __future__ import annotations

import sys
from pathlib import Path

import pytest

from banbot.config_loader import (
    CONFIG_ENV_VAR,
    format_config_import_error,
    load_config_module,
)


def _clear_config_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "config", raising=False)


def test_load_config_module_from_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.py"
    config_path.write_text(
        'JID = "bot@example.org"\nENABLED = true\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    _clear_config_module(monkeypatch)

    module = load_config_module()

    assert module.JID == "bot@example.org"
    assert module.ENABLED is True
    assert Path(module.__file__).resolve() == config_path.resolve()
    assert sys.modules["config"] is module


def test_load_config_module_prefers_explicit_environment_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "config.py").write_text('SOURCE = "cwd"\n', encoding="utf-8")
    explicit = tmp_path / "service-config.py"
    explicit.write_text('SOURCE = "environment"\n', encoding="utf-8")

    monkeypatch.chdir(cwd)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(explicit))
    _clear_config_module(monkeypatch)

    module = load_config_module()

    assert module.SOURCE == "environment"
    assert Path(module.__file__).resolve() == explicit.resolve()


def test_load_config_module_removes_partial_module_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.py"
    config_path.write_text("BROKEN = missing_name\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    _clear_config_module(monkeypatch)

    with pytest.raises(NameError):
        load_config_module()

    assert "config" not in sys.modules


def test_missing_config_error_mentions_service_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "missing.py"))
    _clear_config_module(monkeypatch)

    with pytest.raises(ModuleNotFoundError) as exc_info:
        load_config_module()

    message = format_config_import_error(exc_info.value)
    assert "config.py is missing or its path is not configured" in message
    assert "MUC_BANBOT_CONFIG=/absolute/path/to/config.py" in message


def test_explicit_environment_path_does_not_fall_back_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.py").write_text('SOURCE = "cwd"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "missing-service-config.py"))
    _clear_config_module(monkeypatch)

    with pytest.raises(ModuleNotFoundError):
        load_config_module()


def test_named_config_file_error_includes_source_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "production-settings.py"
    config_path.write_text("BROKEN = missing_name\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _clear_config_module(monkeypatch)

    with pytest.raises(NameError) as exc_info:
        load_config_module()

    message = format_config_import_error(exc_info.value)
    assert "production-settings.py:1" in message
    assert "BROKEN = missing_name" in message
