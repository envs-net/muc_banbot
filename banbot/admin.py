"""Admin/owner permission checks, ban protection, and !whoami."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from envs_xmpp_core.xmpp import AffiliationQueryOptions, query_muc_affiliation
from envs_xmpp_core.xmpp.occupants import (
    find_occupant_by_jid,
    find_occupant_by_nick,
    occupant_is_admin_or_owner,
    occupant_is_moderator,
)

from config import ADMIN_ROOM

from .occupants import BotOccupantMixin
from .utils import domain_matches

log = logging.getLogger(__name__)

_ADMIN_AFFILIATION_QUERY_OPTIONS = AffiliationQueryOptions(
    timeout_seconds=10.0,
    attempts=2,
    retry_delay_seconds=1.0,
)
_ADMIN_AFFILIATION_CACHE_SECONDS = 10.0
_ADMIN_AFFILIATION_QUERY_CONCURRENCY = 4

if TYPE_CHECKING:
    from .contracts import AdminMixinHost

    class _AdminMixinContract(AdminMixinHost):
        pass
else:
    class _AdminMixinContract:
        pass


class AdminMixin(BotOccupantMixin, _AdminMixinContract):
    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        """Check if a user is admin or owner in a room using the live occupant cache."""
        occupants = self.occupants.get(room, {})
        occupant = None
        if nick:
            occupant = find_occupant_by_nick(occupants, nick, room=room)
        if occupant is None and jid:
            occupant = find_occupant_by_jid(occupants, jid, room=room)
        return bool(occupant and occupant_is_admin_or_owner(occupant))


    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool:
        """Check if the bot itself is admin or owner in a given room."""
        _nick, bot_info = self._bot_occupant_entry(room)

        if not bot_info:
            if log_missing:
                log.warning("⚠️ Bot self-presence not found in occupants for room %s", room)
            return False

        return occupant_is_admin_or_owner(bot_info)


    def is_authorized(self, msg) -> bool:
        """Check if a message sender is authorized to issue admin commands."""
        room = str(getattr(msg["from"], "bare", "") or "").strip()
        nick = str(msg["mucnick"] or "").strip()
        if not room or not nick or room.casefold() != ADMIN_ROOM.casefold():
            return False
        occupant = find_occupant_by_nick(
            self.occupants.get(room, {}),
            nick,
            room=room,
        )
        return bool(occupant and occupant_is_admin_or_owner(occupant))


    async def verify_admin_rights(self, room: str) -> bool:
        """Return whether the bot has server-side owner/admin affiliation."""
        muc = self.plugin["xep_0045"]
        bare_bot_jid = str(self.boundjid.bare).lower()
        for affiliation in ("owner", "admin"):
            result = await query_muc_affiliation(
                muc,
                room,
                affiliation,
                options=_ADMIN_AFFILIATION_QUERY_OPTIONS,
            )
            if not result.ok:
                log.warning(
                    "Server admin-rights check failed for %s (%s): %s",
                    room,
                    affiliation,
                    result.summary,
                )
                return False
            for jid in result.items:
                if str(jid).split("/", 1)[0].lower() == bare_bot_jid:
                    return True
        return False


    def _admin_affiliation_cache(self) -> dict[str, tuple[float, frozenset[str]]]:
        """Return the short-lived successful affiliation-query cache.

        Lightweight mixin tests do not run :class:`BanBot.__init__`, so keep a
        lazy fallback here even though production initializes the cache
        centrally.  Only successful server responses are cached.
        """
        cache = getattr(self, "_admin_affiliation_cache_entries", None)
        if cache is None:
            cache = {}
            self._admin_affiliation_cache_entries = cache
        return cache

    def _cache_room_admin_owner_jids(self, room: str, jids: set[str]) -> None:
        """Cache one successful owner/admin snapshot for a very short window."""
        key = room.strip().casefold()
        if not key:
            return
        expires_at = time.monotonic() + _ADMIN_AFFILIATION_CACHE_SECONDS
        self._admin_affiliation_cache()[key] = (expires_at, frozenset(jids))

    def _invalidate_room_admin_owner_cache(self, room: str | None = None) -> None:
        """Invalidate cached affiliation data after an explicit refresh/change."""
        cache = self._admin_affiliation_cache()
        if room is None:
            cache.clear()
            return
        cache.pop(room.strip().casefold(), None)

    def _cached_room_admin_owner_jids(self, room: str) -> set[str] | None:
        key = room.strip().casefold()
        cached = self._admin_affiliation_cache().get(key)
        if cached is None:
            return None
        expires_at, jids = cached
        if time.monotonic() >= expires_at:
            self._admin_affiliation_cache().pop(key, None)
            return None
        return set(jids)

    async def get_room_admin_owner_jids(
        self,
        room: str,
        *,
        refresh: bool = False,
    ) -> set[str]:
        """Return server-known owner/admin bare JIDs with bounded, cached IQs.

        Successful results are cached only briefly; this keeps consecutive ban
        commands off the network without turning the cache into long-lived
        authorization state.  A room that rejects full affiliation lists with
        ``forbidden`` is remembered exactly as before and falls back to live
        occupant data.
        """
        if room in self.admin_affiliation_query_forbidden_rooms:
            return set()

        if refresh:
            self._invalidate_room_admin_owner_cache(room)
        else:
            cached = self._cached_room_admin_owner_jids(room)
            if cached is not None:
                return cached

        protected: set[str] = set()
        for affiliation in ("owner", "admin"):
            result = await query_muc_affiliation(
                self.plugin["xep_0045"],
                room,
                affiliation,
                options=_ADMIN_AFFILIATION_QUERY_OPTIONS,
            )
            if not result.ok:
                if result.error_condition == "forbidden":
                    self.admin_affiliation_query_forbidden_rooms.add(room)
                    self._invalidate_room_admin_owner_cache(room)
                    log.warning(
                        "Full owner/admin affiliation lists are unavailable for %s; "
                        "using the live occupant cache for admin protection. "
                        "This is expected when BanBot is room admin rather than owner; "
                        "offline admins cannot be detected for this room.",
                        room,
                    )
                else:
                    log.warning(
                        "Could not fetch %s affiliation list for %s: %s",
                        affiliation,
                        room,
                        result.summary,
                    )
                return set()
            for server_jid in result.items:
                bare = self.bare_jid(str(server_jid))
                if bare:
                    protected.add(bare)

        self._cache_room_admin_owner_jids(room, protected)
        return protected


    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]:
        """Return whether a target would ban an owner/admin in a managed room.

        Live occupant state remains the fastest and freshest check.  Offline
        owner/admin protection still uses server affiliation lists, but room
        queries are bounded and concurrent instead of serializing two IQs for
        every room on every ban command.
        """
        target = target.lower().strip()
        bare_target_jid = self.bare_jid(jid or target) if "@" in target or jid else None
        domain_target = target[2:].strip(".") if target.startswith("*.") else None
        rooms_to_check = sorted(set(self.protected_rooms) | {ADMIN_ROOM})

        # First use the live cache.  Online admins/owners never need a server
        # round-trip and are protected even if full affiliation queries are
        # forbidden for the bot.
        for room in rooms_to_check:
            if nick and self.is_admin_or_owner(room, nick=nick):
                return True, f"{nick} is admin/owner in {room}"
            if bare_target_jid and self.is_admin_or_owner(room, jid=bare_target_jid):
                return True, f"{bare_target_jid} is admin/owner in {room}"

        if not bare_target_jid and not domain_target:
            return False, None

        query_limit = asyncio.Semaphore(
            max(1, min(_ADMIN_AFFILIATION_QUERY_CONCURRENCY, len(rooms_to_check)))
        )

        async def fetch(room: str) -> tuple[str, set[str]]:
            async with query_limit:
                return room, await self.get_room_admin_owner_jids(room)

        room_affiliations = await asyncio.gather(*(fetch(room) for room in rooms_to_check))

        for room, protected_jids in room_affiliations:
            if bare_target_jid and bare_target_jid in protected_jids:
                return True, f"{bare_target_jid} is admin/owner in {room}"

            if domain_target:
                for protected_jid in protected_jids:
                    protected_domain = protected_jid.split("@", 1)[1] if "@" in protected_jid else None
                    if domain_matches(protected_domain, domain_target):
                        return True, f"domain ban *.{domain_target} would include admin/owner {protected_jid} in {room}"

        return False, None


    async def check_bot_admin_rights(self) -> None:
        """Check protected-room join state and admin/owner affiliation."""
        not_joined: list[str] = []
        missing_rights: list[str] = []

        for room in self.protected_rooms:
            # Startup joins are awaited already; this short grace period only
            # covers async event-handler scheduling on slower runtimes.
            for _ in range(3):
                if self._bot_occupant_entry(room)[1] is not None:
                    break
                await asyncio.sleep(1)

            if self._bot_occupant_entry(room)[1] is None:
                self.bot_admin_state.pop(room, None)
                not_joined.append(room)
                continue

            try:
                self.bot_admin_state[room] = self.is_bot_admin_or_owner(room, log_missing=False)
                if not self.bot_admin_state[room]:
                    missing_rights.append(room)
            except Exception as exc:
                log.warning("Error checking admin rights in %s: %s", room, exc)
                missing_rights.append(room)

        if not_joined or missing_rights:
            sections = ["⚠️ Bot room access check failed:"]
            if not_joined:
                sections.append("Not joined:\n" + "\n".join(not_joined))
            if missing_rights:
                sections.append("Joined without admin/owner rights:\n" + "\n".join(missing_rights))
            message = "\n\n".join(sections)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=message,
                mtype="groupchat",
            )
            if not_joined:
                log.warning("Bot not joined in rooms: %s", not_joined)
            if missing_rights:
                log.warning("Bot missing admin rights in rooms: %s", missing_rights)
            return

        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody="✅ Bot has admin/owner rights in all protected rooms.",
            mtype="groupchat",
        )
        log.info("Bot has admin rights in all protected rooms.")


    async def _cmd_whoami(self, room: str, nick: str) -> None:
        """Show the caller's current MUC affiliation, role, JID, and permissions."""
        occupant = find_occupant_by_nick(self.occupants.get(room, {}), nick, room=room)
        affiliation = occupant.affiliation if occupant is not None else "none"
        role = occupant.role if occupant is not None else "none"
        jid = occupant.jid if occupant is not None and occupant.jid else "unknown"

        permissions = []
        if occupant is not None and occupant_is_admin_or_owner(occupant):
            permissions.append("✅ Can ban/kick users")
            permissions.append("✅ Can manage room")
        elif occupant is not None and occupant_is_moderator(occupant):
            permissions.append("✅ Can kick users")
        else:
            permissions.append("❌ Regular participant")

        perms_text = "\n".join(permissions)

        if room == ADMIN_ROOM:
            message = (
                f"👤 **Your Status:**\n"
                f"  Nick: {nick}\n"
                f"  JID: {jid}\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )
        else:
            emoji = "🔑" if occupant is not None and occupant_is_admin_or_owner(occupant) else "👤"
            message = (
                f"{emoji} **Your Status:**\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )

        await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
