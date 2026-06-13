from __future__ import annotations

from banbot.config import ConfigMixin
from banbot.config.common import (
    NEVER_WRITABLE_CONFIG_KEYS,
    RUNTIME_WRITABLE_CONFIG_KEYS,
    SECRET_CONFIG_KEYS,
    STARTUP_ONLY_CONFIG_KEYS,
)


def test_config_common_constants_drive_config_mixin() -> None:
    assert ConfigMixin.CONFIG_KEYS == RUNTIME_WRITABLE_CONFIG_KEYS
    assert ConfigMixin.STARTUP_ONLY_CONFIG_KEYS == STARTUP_ONLY_CONFIG_KEYS
    assert ConfigMixin.CONFIG_SECRET_KEYS == SECRET_CONFIG_KEYS
    assert ConfigMixin.CONFIG_NEVER_WRITABLE_KEYS == NEVER_WRITABLE_CONFIG_KEYS


def test_config_key_sets_are_intentionally_separated() -> None:
    runtime_keys = set(RUNTIME_WRITABLE_CONFIG_KEYS)
    startup_keys = set(STARTUP_ONLY_CONFIG_KEYS)

    assert "LOG_LEVEL" in runtime_keys
    assert "REDACTION_ENABLED" in runtime_keys
    assert "DB_BACKUP_ON_START" in runtime_keys
    assert "JID" in startup_keys
    assert "DB_FILE" in startup_keys
    assert "PASSWORD" in SECRET_CONFIG_KEYS
    assert runtime_keys.isdisjoint(startup_keys)
    assert SECRET_CONFIG_KEYS <= NEVER_WRITABLE_CONFIG_KEYS
    assert startup_keys <= NEVER_WRITABLE_CONFIG_KEYS
