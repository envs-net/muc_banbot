"""Configuration mixin helpers."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import linecache
import logging
import os
import builtins
import pprint
import pathlib
import sys
from typing import Any

import config

from .imports import get_config_resource
from .common import RUNTIME_WRITABLE_CONFIG_KEYS, SECRET_CONFIG_KEYS, STARTUP_ONLY_CONFIG_KEYS

log = logging.getLogger(__name__)

class ConfigDisplayMixin:

    def _config_file_path(self) -> pathlib.Path:
        path = getattr(config, "__file__", None)
        if path:
            return pathlib.Path(path).resolve()
        return pathlib.Path("config.py").resolve()

    def _config_sample_path(self) -> pathlib.Path:
        return pathlib.Path(__file__).resolve().parent.parent / "config_sample.py"

    def _ordered_config_keys_from_sample(self) -> list[str]:
        """Return config keys in config.py order, with config_sample.py as fallback.

        The active config.py order is preferred because that is what admins edit.
        Any keys missing from config.py are appended in config_sample.py order so
        !config show still gives a complete view of supported options.
        """
        keys: list[str] = []

        def add_keys_from(path: pathlib.Path) -> None:
            try:
                tree = ast.parse(path.read_text(encoding="utf8"))
            except Exception:
                return
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper() and target.id not in keys:
                            keys.append(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id.isupper() and node.target.id not in keys:
                        keys.append(node.target.id)

        add_keys_from(self._config_file_path())
        add_keys_from(self._config_sample_path())

        if not keys:
            return list(self.STARTUP_ONLY_CONFIG_KEYS) + list(self.CONFIG_KEYS)
        return keys

    def _config_default_values_from_sample(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        try:
            tree = ast.parse(self._config_sample_path().read_text(encoding="utf8"))
        except Exception:
            return defaults
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        defaults[target.id] = value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                try:
                    defaults[node.target.id] = ast.literal_eval(node.value)
                except Exception:
                    continue
        return defaults

    def get_ordered_config_items(self) -> list[tuple[str, Any, bool]]:
        """Return config values in config_sample.py order as (key, value, writable)."""
        keys = self._ordered_config_keys_from_sample()
        # Include custom/runtime keys even if an older config_sample.py was copied.
        for key in (*self.CONFIG_KEYS, *self.STARTUP_ONLY_CONFIG_KEYS):
            if key not in keys:
                keys.append(key)

        items: list[tuple[str, Any, bool]] = []
        for key in keys:
            if not key.isupper() or key == "RESSOURCE":
                continue
            value = get_config_resource() if key == "RESOURCE" else getattr(config, key, None)
            writable = key in self.CONFIG_KEYS and key not in self.CONFIG_NEVER_WRITABLE_KEYS
            items.append((key, value, writable))
        return items

    def format_config_value_for_display(self, key: str, value: Any) -> str:
        if key in self.CONFIG_SECRET_KEYS or any(token in key for token in ("PASSWORD", "SECRET", "TOKEN")):
            return "****" if value not in (None, "") else "None"
        return repr(value)
