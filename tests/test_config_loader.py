from __future__ import annotations

import sys
from pathlib import Path

import pytest

from banbot.config_loader import (
    CONFIG_ENV_VAR,
    format_config_import_error,
    load_config_module,
    reload_config_module,
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


def test_missing_config_error_mentions_optional_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "missing.py"))
    _clear_config_module(monkeypatch)

    with pytest.raises(ModuleNotFoundError) as exc_info:
        load_config_module()

    message = format_config_import_error(exc_info.value)
    assert "the configured config file could not be loaded" in message
    assert str(tmp_path / "missing.py") in message
    assert f"Current override: {CONFIG_ENV_VAR}={tmp_path / 'missing.py'}" in message


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


def test_reload_config_module_uses_active_external_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "etc" / "muc_banbot"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.py"
    config_path.write_text('VALUE = "before"\n', encoding="utf-8")
    working_dir = tmp_path / "srv" / "muc_banbot"
    working_dir.mkdir(parents=True)

    monkeypatch.chdir(working_dir)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _clear_config_module(monkeypatch)

    module = load_config_module()
    assert module.VALUE == "before"
    config_path.write_text('VALUE = "after"\n', encoding="utf-8")

    reloaded = reload_config_module(module)

    assert reloaded is module
    assert module.VALUE == "after"
    assert Path(module.__file__).resolve() == config_path.resolve()


def test_reload_config_module_restores_previous_values_on_load_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "external-config.py"
    config_path.write_text('VALUE = "good"\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _clear_config_module(monkeypatch)

    module = load_config_module()
    config_path.write_text('VALUE = "partial"\nBROKEN = missing_name\n', encoding="utf-8")

    with pytest.raises(NameError):
        reload_config_module(module)

    assert module.VALUE == "good"
    assert not hasattr(module, "BROKEN")
    assert sys.modules["config"] is module


def test_reload_config_module_reads_source_even_with_stale_bytecode_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.py"
    config_path.write_text('VALUE = "aaaa"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    _clear_config_module(monkeypatch)

    module = load_config_module()
    original_stat = config_path.stat()
    config_path.write_text('VALUE = "bbbb"\n', encoding="utf-8")
    # Preserve timestamp and size to model the classic timestamp-pyc stale case.
    config_path.touch()
    import os
    os.utime(config_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    reload_config_module(module)

    assert module.VALUE == "bbbb"


def test_config_loader_does_not_create_bytecode_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.py"
    config_path.write_text('VALUE = 1\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    _clear_config_module(monkeypatch)

    module = load_config_module()
    config_path.write_text('VALUE = 2\n', encoding="utf-8")
    reload_config_module(module)

    assert module.VALUE == 2
    assert not (tmp_path / "__pycache__").exists()
