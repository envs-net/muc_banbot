from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import config
from banbot.config import ConfigMixin
from banbot.config.imports import get_config_resource
from banbot.config.runtime import ConfigRuntimeMixin
from banbot.locks import get_database_file_lock


@pytest.fixture(autouse=True)
def restore_config_namespace():
    """Keep tests that exercise in-place config reloads isolated."""
    state = dict(config.__dict__)
    try:
        yield
    finally:
        config.__dict__.clear()
        config.__dict__.update(state)


class _ReloadBot(ConfigMixin):
    CONFIG_KEYS = ("LOG_LEVEL",)
    STARTUP_ONLY_CONFIG_KEYS: tuple[str, ...] = ()
    CONFIG_NEVER_WRITABLE_KEYS: set[str] = set()
    CONFIG_SECRET_KEYS: set[str] = set()

    def __init__(self) -> None:
        self.runtime_level = str(getattr(config, "LOG_LEVEL", "INFO"))
        self.vcard_calls = 0

    def _runtime_config_snapshot(self) -> dict[str, object]:
        return {"LOG_LEVEL": self.runtime_level}

    def _startup_config_snapshot(self) -> dict[str, object]:
        if "RESOURCE" not in self.STARTUP_ONLY_CONFIG_KEYS:
            return {}
        return {"RESOURCE": get_config_resource()}

    def _validate_config(self) -> tuple[list[str], list[str]]:
        return [], []

    def apply_runtime_config(self) -> None:
        self.runtime_level = str(getattr(config, "LOG_LEVEL", "INFO"))

    async def update_vcard(self) -> bool:
        self.vcard_calls += 1
        return True


@pytest.mark.asyncio
async def test_reload_validation_failure_restores_exact_config_namespace(monkeypatch):
    bot = _ReloadBot()
    config.LOG_LEVEL = "INFO"
    config.CUSTOM_OLD = "keep"
    if hasattr(config, "CUSTOM_NEW"):
        delattr(config, "CUSTOM_NEW")

    def fake_reload(module):
        module.LOG_LEVEL = "DEBUG"
        delattr(module, "CUSTOM_OLD")
        module.CUSTOM_NEW = "candidate-only"
        return module

    monkeypatch.setattr("banbot.config.runtime.reload_config_module", fake_reload)
    monkeypatch.setattr(bot, "_validate_config", lambda: (["invalid candidate"], []))

    changes, errors, warnings = await bot.reload_runtime_config()

    assert changes == []
    assert errors == ["invalid candidate"]
    assert warnings == []
    assert config.LOG_LEVEL == "INFO"
    assert config.CUSTOM_OLD == "keep"
    assert not hasattr(config, "CUSTOM_NEW")
    assert bot.runtime_level == "INFO"


@pytest.mark.asyncio
async def test_reload_apply_failure_rolls_back_module_and_runtime(monkeypatch):
    bot = _ReloadBot()
    config.LOG_LEVEL = "INFO"
    bot.runtime_level = "INFO"

    def fake_reload(module):
        module.LOG_LEVEL = "DEBUG"
        module.CANDIDATE_ONLY = True
        return module

    async def failing_then_recovering_vcard() -> bool:
        bot.vcard_calls += 1
        if bot.vcard_calls == 1:
            raise RuntimeError("vCard apply failed")
        return True

    monkeypatch.setattr("banbot.config.runtime.reload_config_module", fake_reload)
    monkeypatch.setattr(bot, "update_vcard", failing_then_recovering_vcard)

    changes, errors, warnings = await bot.reload_runtime_config()

    assert changes == []
    assert warnings == []
    assert errors == ["Failed to apply reloaded config: vCard apply failed"]
    assert config.LOG_LEVEL == "INFO"
    assert not hasattr(config, "CANDIDATE_ONLY")
    assert bot.runtime_level == "INFO"
    assert bot.vcard_calls == 2


@pytest.mark.asyncio
async def test_reload_cancellation_rolls_back_before_propagating(monkeypatch):
    bot = _ReloadBot()
    config.LOG_LEVEL = "INFO"
    bot.runtime_level = "INFO"
    entered_vcard = asyncio.Event()
    release_vcard = asyncio.Event()

    def fake_reload(module):
        module.LOG_LEVEL = "DEBUG"
        module.CANDIDATE_ONLY = True
        return module

    async def blocking_then_recovering_vcard() -> bool:
        bot.vcard_calls += 1
        if bot.vcard_calls == 1:
            entered_vcard.set()
            await release_vcard.wait()
        return True

    monkeypatch.setattr("banbot.config.runtime.reload_config_module", fake_reload)
    monkeypatch.setattr(bot, "update_vcard", blocking_then_recovering_vcard)

    task = asyncio.create_task(bot.reload_runtime_config())
    await entered_vcard.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert config.LOG_LEVEL == "INFO"
    assert not hasattr(config, "CANDIDATE_ONLY")
    assert bot.runtime_level == "INFO"
    assert bot.vcard_calls == 2


@pytest.mark.asyncio
async def test_reload_holds_canonical_file_lock_through_live_apply(monkeypatch):
    bot = _ReloadBot()
    config.LOG_LEVEL = "INFO"

    def fake_reload(module):
        module.LOG_LEVEL = "DEBUG"
        return module

    async def assert_locked_vcard() -> bool:
        assert get_database_file_lock(bot).locked()
        return True

    monkeypatch.setattr("banbot.config.runtime.reload_config_module", fake_reload)
    monkeypatch.setattr(bot, "update_vcard", assert_locked_vcard)

    changes, errors, warnings = await bot.reload_runtime_config()

    assert changes == ["- LOG_LEVEL: 'INFO' → 'DEBUG'"]
    assert errors == []
    assert warnings == []


@pytest.mark.asyncio
async def test_reload_restores_legacy_ressource_when_startup_only_change_is_deferred(monkeypatch):
    bot = _ReloadBot()
    bot.STARTUP_ONLY_CONFIG_KEYS = ("RESOURCE",)
    if hasattr(config, "RESOURCE"):
        delattr(config, "RESOURCE")
    config.RESSOURCE = "old-resource"

    def fake_reload(module):
        if hasattr(module, "RESOURCE"):
            delattr(module, "RESOURCE")
        module.RESSOURCE = "new-resource"
        return module

    monkeypatch.setattr("banbot.config.runtime.reload_config_module", fake_reload)

    _changes, errors, warnings = await bot.reload_runtime_config()

    assert errors == []
    assert any("RESOURCE" in warning and "old-resource" in warning and "new-resource" in warning for warning in warnings)
    assert not hasattr(config, "RESOURCE")
    assert config.RESSOURCE == "old-resource"
    assert get_config_resource() == "old-resource"


class _BackupFailWriter(ConfigRuntimeMixin):
    CONFIG_KEYS = ("OPTIONAL_RUNTIME",)
    STARTUP_ONLY_CONFIG_KEYS: tuple[str, ...] = ()
    CONFIG_NEVER_WRITABLE_KEYS: set[str] = set()

    def _validate_config(self) -> tuple[list[str], list[str]]:
        return [], []

    async def create_database_backup(self, *_args, **_kwargs):
        return False, "backup failed"

    def _config_file_path(self) -> Path:
        raise AssertionError("config file should not be touched after backup failure")


@pytest.mark.asyncio
async def test_set_backup_failure_preserves_previously_missing_config_key():
    if hasattr(config, "OPTIONAL_RUNTIME"):
        delattr(config, "OPTIONAL_RUNTIME")
    bot = _BackupFailWriter()

    ok, message = await bot.set_runtime_config_value(
        "OPTIONAL_RUNTIME",
        "123",
        actor="tester",
        _locked=True,
    )

    assert ok is False
    assert "pre-change backup failed" in message
    assert not hasattr(config, "OPTIONAL_RUNTIME")


@pytest.mark.asyncio
async def test_set_validation_exception_restores_module_state(monkeypatch):
    if hasattr(config, "OPTIONAL_RUNTIME"):
        delattr(config, "OPTIONAL_RUNTIME")
    bot = _BackupFailWriter()
    monkeypatch.setattr(
        bot,
        "_validate_config",
        lambda: (_ for _ in ()).throw(RuntimeError("validator exploded")),
    )

    ok, message = await bot.set_runtime_config_value(
        "OPTIONAL_RUNTIME",
        "123",
        actor="tester",
        _locked=True,
    )

    assert ok is False
    assert "validator exploded" in message
    assert not hasattr(config, "OPTIONAL_RUNTIME")
