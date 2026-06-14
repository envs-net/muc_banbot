"""Protection subsystem coordinator and shared helpers."""

from __future__ import annotations

from collections import defaultdict, deque
import logging
from typing import Any

from ..utils import bare_jid
from .actions import ProtectionActionsMixin
from .checks import ProtectionChecksMixin
from .commands import ProtectionCommandsMixin
from .definitions import (
    PROTECTION_DEFAULTS,
    canonical_protection_name,
    default_protection_config,
)
from .notifications import ProtectionNotificationMixin
from .storage import ProtectionStorageMixin


log = logging.getLogger(__name__)


class ProtectionMixin(
    ProtectionStorageMixin,
    ProtectionActionsMixin,
    ProtectionChecksMixin,
    ProtectionNotificationMixin,
    ProtectionCommandsMixin,
):
    """Draupnir-inspired protection subsystem for protected XMPP MUCs."""
    def init_protection_state(self) -> None:
        """Initialize in-memory protection config and runtime counters."""
        self.protections: dict[str, dict[str, Any]] = default_protection_config()
        self.protection_message_windows: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
        self.protection_join_windows: dict[str, deque[float]] = defaultdict(deque)
        self.protection_joined_at: dict[tuple[str, str], float] = {}
        self.protection_first_message_seen: set[tuple[str, str]] = set()
        self.protection_trusted_reports: dict[tuple[str, str], list[tuple[float, str, str]]] = defaultdict(list)
        self.protection_room_lockdown_until: dict[str, float] = {}

    def protection_enabled(self, name: str) -> bool:
        """Return True when a protection is enabled."""
        return bool(self.protections.get(name, {}).get("enabled", False))

    def protection_config(self, name: str) -> dict[str, Any]:
        """Return the effective mutable config for a protection."""
        return self.protections.setdefault(name, dict(PROTECTION_DEFAULTS[name]))

    def _resolve_protection_or_error(self, name: str) -> tuple[str | None, str | None]:
        canonical = canonical_protection_name(name)
        if canonical and canonical in self.protections:
            return canonical, None
        return None, f"❌ Unknown protection: {name}\nUse {self.command_prefix}protections list all."

    def _protection_actor_jid(self, room: str, nick: str) -> str | None:
        info = getattr(self, "occupants", {}).get(room, {}).get(nick)
        if info and info.get("jid"):
            return bare_jid(info.get("jid"))
        return nick

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]:
        info = getattr(self, "occupants", {}).get(room, {}).get(nick, {})
        jid = bare_jid(info.get("jid")) if info.get("jid") else None
        return jid, nick.lower()

    def _protection_known_nicks(self, room: str) -> list[str]:
        """Return known room nicks from BanBot state and the XEP-0045 roster cache."""
        nicks: set[str] = set()

        occupants = getattr(self, "occupants", {}).get(room, {})
        if isinstance(occupants, dict):
            nicks.update(str(nick) for nick in occupants if str(nick).strip())

        try:
            muc_plugin = self.plugin["xep_0045"]
            rooms = getattr(muc_plugin, "rooms", {})
            room_roster = rooms.get(room, {}) if isinstance(rooms, dict) else {}
            if isinstance(room_roster, dict):
                nicks.update(str(nick) for nick in room_roster if str(nick).strip())
            elif isinstance(room_roster, (set, list, tuple)):
                nicks.update(str(nick) for nick in room_roster if str(nick).strip())
        except Exception as exc:
            log.debug("Unable to read XEP-0045 room roster cache for %s: %s", room, exc)

        return sorted(nicks, key=str.lower)

    def _protection_is_exempt(self, room: str, nick: str, jid: str | None = None) -> bool:
        if room not in getattr(self, "protected_rooms", set()):
            return True
        if self.is_admin_or_owner(room, nick=nick, jid=jid):
            return True
        return False
