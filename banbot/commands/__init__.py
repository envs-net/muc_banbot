"""BanBot command package.

This package is the compatibility import target for ``from banbot.commands import
CommandMixin`` while the command implementation is split into focused modules.
"""

# Compatibility attributes intentionally remain patchable by tests/downstream users.
import asyncio
import os

from config import ADMIN_ROOM, NICK

from .backups import CommandBackupMixin
from .config_display import ConfigCommandMixin
from .omemo import CommandOmemoMixin
from .runtime import CommandRuntimeMixin
from .constants import ADMIN_COMMANDS, PUBLIC_COMMANDS
from .entrypoint import CommandEntryPointMixin
from .help import CommandHelpMixin
from .ignore import CommandIgnoreMixin
from .import_export import CommandImportExportMixin
from .moderation import CommandModerationMixin
from .policy import CommandPolicyMixin
from .rooms import CommandRoomsMixin
from .router import CommandRouterMixin
from .rtbl import CommandRtblMixin
from .usage import CommandUsageMixin


class CommandMixin(
    ConfigCommandMixin,
    CommandRuntimeMixin,
    CommandUsageMixin,
    CommandHelpMixin,
    CommandBackupMixin,
    CommandOmemoMixin,
    CommandRoomsMixin,
    CommandModerationMixin,
    CommandImportExportMixin,
    CommandRtblMixin,
    CommandIgnoreMixin,
    CommandPolicyMixin,
    CommandRouterMixin,
    CommandEntryPointMixin,
):
    """Combined command mixin used by the main bot class."""

    pass


__all__ = [
    "ADMIN_COMMANDS",
    "ADMIN_ROOM",
    "NICK",
    "PUBLIC_COMMANDS",
    "CommandMixin",
    "asyncio",
    "os",
]
