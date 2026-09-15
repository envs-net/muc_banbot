"""Configuration import helpers."""

from __future__ import annotations

import logging

import config

log = logging.getLogger(__name__)

type ConfigModuleState = dict[str, object]


def get_config_resource() -> str | None:
    """Return RESOURCE with backwards-compatible support for legacy RESSOURCE."""
    resource = getattr(config, "RESOURCE", None)
    if resource is not None:
        return resource
    return getattr(config, "RESSOURCE", None)


def snapshot_config_module_state() -> ConfigModuleState:
    """Return an exact shallow snapshot of the active config module namespace.

    Runtime reloads execute into the existing module object so every ``import
    config`` reference remains valid.  Keeping the complete namespace lets a
    failed reload restore removed/custom attributes as well as supported schema
    keys, preserving the exact last-known-good in-process configuration.
    """
    return dict(config.__dict__)


def restore_config_module_state(state: ConfigModuleState) -> None:
    """Restore an exact config-module namespace snapshot in place."""
    config.__dict__.clear()
    config.__dict__.update(state)
