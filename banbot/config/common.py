"""Shared configuration key definitions derived from the declarative schema."""

from __future__ import annotations

from envs_xmpp_core.config.schema import (
    schema_runtime_writable_keys,
    schema_sensitive_keys,
)

from .spec import CONFIG_FIELDS

RUNTIME_WRITABLE_CONFIG_KEYS = schema_runtime_writable_keys(
    CONFIG_FIELDS,
    python_keys=True,
)
STARTUP_ONLY_CONFIG_KEYS = tuple(
    field.python_key for field in CONFIG_FIELDS.values() if field.startup_only
)
SECRET_CONFIG_KEYS = schema_sensitive_keys(CONFIG_FIELDS, python_keys=True)
NEVER_WRITABLE_CONFIG_KEYS = set(STARTUP_ONLY_CONFIG_KEYS) | SECRET_CONFIG_KEYS
