"""Compatibility helpers for command modules.

Tests and downstream users historically patched attributes on ``banbot.commands``
(e.g. ``ADMIN_ROOM`` or ``NICK``).  Command modules should read those values via
this helper instead of importing config constants directly at call time.
"""

import sys
from typing import Any

from config import ADMIN_ROOM, NICK


def commands_module_attr(name: str, fallback: Any) -> Any:
    commands_module = sys.modules.get("banbot.commands")
    return getattr(commands_module, name, fallback)


def admin_room() -> str:
    return commands_module_attr("ADMIN_ROOM", ADMIN_ROOM)


def bot_nick() -> str:
    return commands_module_attr("NICK", NICK)
