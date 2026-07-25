"""Shared helpers for identifying the bot's own live MUC occupant."""

from config import NICK

from .utils import bare_jid


class BotOccupantMixin:
    """Provide one authoritative bot-occupant lookup for all MUC consumers."""

    def _bot_occupant_entry(self, room: str) -> tuple[str | None, dict | None]:
        """Return the bot's live occupant entry without trusting one exact nick.

        The MUC service may assign a different nickname or temporarily retain a
        stale occupant after a reconnect. Prefer the nickname learned from an
        actual self-presence, then match the authenticated bare JID. Only
        lightweight users that do not track self-presence may fall back to the
        configured nickname for compatibility.
        """
        occupants = getattr(self, "occupants", {}).get(room, {})
        actual_nick = getattr(self, "room_bot_nicks", {}).get(room)

        if actual_nick:
            info = occupants.get(actual_nick)
            if info is not None:
                return actual_nick, info

        boundjid = getattr(self, "boundjid", None)
        if boundjid is not None:
            normalize = getattr(self, "bare_jid", bare_jid)
            bot_bare = normalize(str(boundjid.bare))
            for nick, info in occupants.items():
                occupant_jid = info.get("jid")
                if occupant_jid and normalize(str(occupant_jid)) == bot_bare:
                    return nick, info

        # Production BanBot always has ``room_bot_nicks``. There an exact nick
        # alone is not proof of identity because it may belong to a stale
        # occupant from the previous connection. Keep the fallback only for
        # standalone mixin users and compatibility tests.
        if not hasattr(self, "room_bot_nicks"):
            configured_nick = str(NICK).lower()
            for nick, info in occupants.items():
                if str(nick).lower() == configured_nick:
                    return nick, info

        return None, None


def bot_room_status_line(bot, room: str) -> str:
    """Return one room-list line with join state and bot affiliation."""
    _nick, info = BotOccupantMixin._bot_occupant_entry(bot, room)
    if info is None:
        return f"🔴 {room} | not joined | bot affiliation: unknown"

    affiliation = str(info.get("affiliation") or "none").lower()
    if affiliation in {"owner", "admin"}:
        icon = "🟢"
        rights = affiliation
    else:
        icon = "🟠"
        rights = f"{affiliation} (no admin rights)"

    return f"{icon} {room} | joined | bot affiliation: {rights}"
