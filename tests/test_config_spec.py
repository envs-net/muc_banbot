from __future__ import annotations

import ast
from pathlib import Path

from envs_xmpp_core.config.schema import MISSING

from banbot.config.common import (
    RUNTIME_WRITABLE_CONFIG_KEYS,
    SECRET_CONFIG_KEYS,
    STARTUP_ONLY_CONFIG_KEYS,
)
from banbot.config.spec import CONFIG_FIELDS


def _sample_values() -> dict[str, object]:
    path = Path(__file__).resolve().parents[1] / "config_sample.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    values[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            try:
                values[node.target.id] = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                continue
    return values


def test_declarative_schema_matches_documented_sample_values() -> None:
    documented: dict[str, object] = {}
    for field in CONFIG_FIELDS.values():
        value = field.sample if field.sample is not MISSING else field.default
        if value is not MISSING:
            documented[field.python_key] = value

    assert documented == _sample_values()


def test_declarative_schema_drives_config_lifecycle_sets() -> None:
    runtime = tuple(
        field.python_key for field in CONFIG_FIELDS.values() if field.runtime_writable
    )
    startup = tuple(
        field.python_key for field in CONFIG_FIELDS.values() if field.startup_only
    )
    secrets = {
        field.python_key for field in CONFIG_FIELDS.values() if field.sensitive
    }

    assert runtime == RUNTIME_WRITABLE_CONFIG_KEYS
    assert startup == STARTUP_ONLY_CONFIG_KEYS
    assert secrets == SECRET_CONFIG_KEYS
    assert set(runtime).isdisjoint(startup)
