"""Shared helpers for identifying the bot's own live MUC occupant."""

from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp.occupants import (
    find_self_occupant,
    occupant_is_admin_or_owner,
)

from config import NICK

from .utils import bare_jid

if TYPE_CHECKING:
    from .contracts import BotOccupantMixinHost

    class _BotOccupantMixinContract(BotOccupantMixinHost):
        pass
else:
    class _BotOccupantMixinContract:
        pass


class BotOccupantMixin(_BotOccupantMixinContract):
    """Provide one authoritative bot-occupant lookup for all MUC consumers."""

    def _bot_occupant_entry(self, room: str) -> tuple[str | None, dict[str, Any] | None]:
        """Return the bot's live occupant entry without trusting one exact nick.

        The shared identity helper prefers the nickname learned from an actual
        self-presence, then the authenticated bare JID. Only lightweight users
        that do not track self-presence may fall back to the configured nick.
        """
        occupants = getattr(self, "occupants", {}).get(room, {})
        actual_nick = getattr(self, "room_bot_nicks", {}).get(room)
        boundjid = getattr(self, "boundjid", None)
        normalize = getattr(self, "bare_jid", bare_jid)
        self_bare = normalize(str(boundjid.bare)) if boundjid is not None else None
        fallback_nick = None if hasattr(self, "room_bot_nicks") else str(NICK)

        occupant = find_self_occupant(
            room,
            occupants,
            self_bare_jid=self_bare,
            preferred_nick=actual_nick,
            fallback_nick=fallback_nick,
        )
        if occupant is None:
            return None, None
        info = occupants.get(occupant.nick)
        if info is None:
            # Case-insensitive lookup fallback for compatibility mappings.
            for nick, candidate in occupants.items():
                if str(nick).casefold() == occupant.nick.casefold():
                    return str(nick), candidate
            return occupant.nick, None
        return occupant.nick, info


def bot_room_status_line(bot, room: str) -> str:
    """Return one room-list line with join state and bot affiliation."""
    _nick, info = BotOccupantMixin._bot_occupant_entry(bot, room)
    if info is None:
        return f"🔴 {room} | not joined | bot affiliation: unknown"

    affiliation = str(info.get("affiliation") or "none").lower()
    if occupant_is_admin_or_owner(info):
        icon = "🟢"
        rights = affiliation
    else:
        icon = "🟠"
        rights = f"{affiliation} (no admin rights)"

    return f"{icon} {room} | joined | bot affiliation: {rights}"
