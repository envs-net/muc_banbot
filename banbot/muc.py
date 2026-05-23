"""MUC connection, presence tracking, and occupant handling."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK

from .utils import domain_matches

log = logging.getLogger(__name__)


class MucMixin:
    async def on_disconnect(self, _) -> None:
        # During the initial connection/STARTTLS negotiation Slixmpp may emit a
        # disconnect-like event before session_start completed. Do not start a
        # reconnect loop there; the original connection may still finish.
        if (
            hasattr(self, "server_connect_time")
            and getattr(self, "server_connect_time", None) is None
            and not getattr(self, "reconnecting", False)
        ):
            log.info("Disconnect event received before session_start completed; ignoring")
            return

        if self.reconnect_task and not self.reconnect_task.done():
            log.info("🔄 Disconnect event received while reconnect is already scheduled")
            return

        log.warning("⚠️  Disconnected from server")
        self.reconnecting = True

        # runtime state reset
        self.occupants.clear()
        self.bot_admin_state.clear()
        self.room_join_time.clear()
        log.info("🧹 Cleaned up occupants dictionary and states")

        self.reconnect_task = asyncio.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        try:
            delay = 5
            log.info("🔄 Attempting reconnect in %ds...", delay)
            await asyncio.sleep(delay)
            connect_with_config = getattr(self, "connect_with_config", None)
            if connect_with_config:
                connect_with_config()
            else:
                self.connect()

            log.info("🔌 Reconnect initiated")
        except Exception as e:
            log.error("Reconnect error: %s", e)
        finally:
            self.reconnect_task = None


    async def wait_for_occupants(self, timeout: int = 20) -> None:
        """
        Wait until all protected rooms and admin room have at least one occupant loaded.
        Fallback to timeout if rooms are empty. Helps avoid race conditions at startup.
        """
        start = time.time()
        while time.time() - start < timeout:
            ready = True
            for r in self.protected_rooms | {ADMIN_ROOM}:
                occ = self.occupants.get(r)
                if occ is None or len(occ) == 0:
                    ready = False
                    break
            if ready:
                return
            await asyncio.sleep(2)
        log.warning("Timeout waiting for occupants; some users may not be kicked immediately")


    async def wait_for_bot_online(self, room: str, timeout: int = 10) -> bool:
        """
        Wait until the bot is recognized as a participant in a room.
        Prevents race conditions after joining.
        """
        for _ in range(timeout):
            occ = self.occupants.get(room, {})
            if NICK in occ:
                return True
            await asyncio.sleep(1)
        log.warning("Bot not recognized in %s after %ds", room, timeout)
        return False


    async def notify_protected(self, room: str, message: str) -> None:
        """Notify users in protected rooms if SHOW_BAN_IN_MUC=True"""
        if self.show_ban_in_muc:
            await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")


    async def muc_online(self, presence) -> None:
        """
        Called when a user comes online in a MUC.
        - Updates occupants
        - Skips admins/owners
        - AUTO-UPDATES JID IF NICK-ONLY BAN EXISTS
        - Applies all relevant bans from DB in parallel
        """
        room = presence["from"].bare
        nick = presence["muc"]["nick"]
        jid = presence["muc"].get("jid")
        jid_str = str(jid) if jid else None

        # --- Update occupants dict ---
        self.occupants.setdefault(room, {})[nick] = {
            "role": presence["muc"]["role"],
            "affiliation": presence["muc"]["affiliation"],
            "jid": jid_str,
        }

        # --- Skip admins/owners ---
        if self.is_admin_or_owner(room, nick=nick, jid=jid_str):
            return

        # --- RTBL check (protected rooms only, not admin room) ---
        if jid_str and room in self.protected_rooms:
            rtbl_hit = await self.check_jid_against_rtbl(jid_str, nick)
            if rtbl_hit:
                return  # already banned via RTBL, skip the rest

        # --- Auto-update JID if nick-only ban exists ---
        if jid_str and nick:
            async with self.db.execute(
                "SELECT until, issuer, comment FROM bans WHERE target_type = 'nick' AND target = ?",
                (nick.lower(),)
            ) as cursor:
                existing_ban = await cursor.fetchone()

            if existing_ban:
                # Found a nick-only ban, convert it to a JID ban.
                ban_jid_bare = self.bare_jid(jid_str)
                until, issuer, comment = existing_ban
                await self.upsert_ban_db(ban_jid_bare, nick.lower(), int(until or 0), issuer, comment)
                await self.db.execute(
                    "DELETE FROM bans WHERE target_type = 'nick' AND target = ?",
                    (nick.lower(),)
                )
                await self.db.commit()

                # Reload ban cache
                await self.load_bans_from_db()

                log.info("✅ Auto-updated ban for nick '%s': JID set to %s", nick, ban_jid_bare)

        # --- Fetch all bans ---
        # Use indexes for O(1) lookups instead of O(n)
        now = int(time.time())
        tasks = []

        # Check by JID
        if jid_str:
            jid_bare = self.bare_jid(jid_str)
            if jid_bare in self.ban_index_by_jid:
                ban_jid, ban_nick, until, issuer, comment = self.ban_index_by_jid[jid_bare]
                if until <= 0 or until > now:  # Check if not expired
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

            # Check by wildcard domain bans (*.domain.tld matches domain.tld and sub.domain.tld)
            domain = jid_bare.split("@")[1].lower() if "@" in jid_bare else None
            if domain:
                for banned_domain, bans in self.ban_index_by_domain.items():
                    if domain_matches(domain, banned_domain):
                        for ban_jid, ban_nick, until, issuer, comment in bans:
                            if until <= 0 or until > now:
                                tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

        # Check by nick
        if nick.lower() in self.ban_index_by_nick:
            ban_jid, ban_nick, until, issuer, comment = self.ban_index_by_nick[nick.lower()]
            if until <= 0 or until > now:
                tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

        # --- Run all bans in parallel ---
        if tasks:
            await asyncio.gather(*tasks)


    async def muc_offline(self, presence) -> None:
        """
        Called when a user goes offline in a MUC.
        - Removes them from self.occupants[room]
        - Logs offline info
        """
        room = presence["from"].bare
        nick = presence["muc"]["nick"]

        room_occ = self.occupants.get(room)
        if room_occ and nick in room_occ:
            info = room_occ.pop(nick)
            log.debug("⛔ %s went offline in %s (jid=%s, affiliation=%s, role=%s)",
                     nick,
                     room,
                     info.get("jid", "unknown"),
                     info.get("affiliation", "none"),
                     info.get("role", "none"))


    async def on_muc_presence(self, presence) -> None:
        """
        Detect if bot loses or regains admin/owner rights.
        Spam-safe (only reacts on real state changes).

        Also keep the bot's own occupant cache in sync. Affiliation/role
        changes can arrive as presence updates without going through
        muc_online(), so status output must not rely on stale cache data.
        """
        room = presence["from"].bare
        nick = presence["from"].resource

        if nick != NICK:
            return

        jid = presence["muc"].get("jid")
        jid_str = str(jid) if jid else None
        affiliation = presence["muc"]["affiliation"]
        role = presence["muc"]["role"]

        previous_info = self.occupants.get(room, {}).get(nick, {})
        previous_affiliation = previous_info.get("affiliation")

        # Keep our own live occupant cache in sync.
        #
        # This is important for status output: if the bot is downgraded
        # from admin/owner to member/participant, the status admin list is
        # built from self.occupants and must not keep showing stale rights.
        self.occupants.setdefault(room, {})[nick] = {
            "role": role or "none",
            "affiliation": affiliation or "none",
            "jid": jid_str,
        }

        # Ignore during reconnect stabilization
        if self.reconnecting and time.time() - self.room_join_time.get(room, 0) < 5:
            return

        # Ignore first few seconds after join
        join_time = self.room_join_time.get(room)
        if join_time and (time.time() - join_time < 5):
            return

        if not affiliation:
            return

        is_admin_now = affiliation in ("admin", "owner")
        was_admin = self.bot_admin_state.get(room)

        # If bot_admin_state has not been initialized yet, fall back to the
        # previously cached affiliation. This catches downgrades that happen
        # before on_muc_presence() has seen an initial admin/owner presence.
        if was_admin is None and previous_affiliation:
            was_admin = previous_affiliation in ("admin", "owner")

        # First time → just store
        if was_admin is None:
            self.bot_admin_state[room] = is_admin_now
            return

        if was_admin == is_admin_now:
            return

        self.bot_admin_state[room] = is_admin_now

        if not is_admin_now:
            is_admin_verified = await self.verify_admin_rights(room)

            if not is_admin_verified:
                log.warning("⚠️ Verified: Bot truly lost admin rights in %s", room)

                if room == ADMIN_ROOM:
                    log.warning(
                        "⚠️ Bot lost admin/owner rights in ADMIN_ROOM %s "
                        "(affiliation=%s, role=%s); warning message may not be deliverable there",
                        room,
                        affiliation,
                        role,
                    )

                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=(
                            f"⚠️ Bot lost admin/owner rights in admin room {room}\n"
                            f"Affiliation: {affiliation}\n"
                            f"Role: {role}\n"
                            "Admin commands may no longer work until rights are restored."
                        ),
                        mtype="groupchat",
                    )
                else:
                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=(
                            f"⚠️ Bot lost admin/owner rights in {room}\n"
                            f"Affiliation: {affiliation}\n"
                            f"Role: {role}"
                        ),
                        mtype="groupchat",
                    )
            else:
                log.info("✅ False alarm: server confirms bot is still admin in %s", room)
                self.bot_admin_state[room] = True  # correct state

        else:
            log.info("✅ Bot regained admin rights in %s", room)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Bot regained admin/owner rights in {room}",
                mtype="groupchat"
            )
