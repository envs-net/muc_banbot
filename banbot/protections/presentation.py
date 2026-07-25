"""Shared presentation helpers for protection runtime state."""

from __future__ import annotations

from typing import Mapping, Any

from .definitions import (
    PROTECTION_ACTIONS_BY_PROTECTION,
    PROTECTION_ALLOWED_ACTIONS,
    PROTECTION_DEFAULTS,
    PROTECTION_DISPLAY_ALIASES,
)


def protection_status_line(name: str, config: Mapping[str, Any]) -> str:
    """Return one consistent human-readable protection status line."""
    enabled = bool(config.get("enabled"))
    observe = bool(config.get("observe", False))

    if enabled and observe:
        icon = "👁️"
    else:
        icon = "🟢" if enabled else "🔴"

    state = "enabled" if enabled else "disabled"
    if observe:
        state += ", observe"

    alias = PROTECTION_DISPLAY_ALIASES.get(name, name)
    defaults = PROTECTION_DEFAULTS[name]
    if "observe" in defaults:
        capability = "observe"
    elif "action" not in defaults:
        capability = "notify-only"
    else:
        allowed_actions = PROTECTION_ACTIONS_BY_PROTECTION.get(
            name,
            PROTECTION_ALLOWED_ACTIONS,
        )
        non_enforcing_actions = {"none", "notify", "warn"}
        capability = (
            "enforcing"
            if any(action not in non_enforcing_actions for action in allowed_actions)
            else "notify-only"
        )

    return f"{icon} ({state}) {name} [{alias}] [{capability}]"
