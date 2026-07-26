"""MUC connection, presence tracking, and occupant handling."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK

from .locks import ban_state_lock
from .muc_join import start_muc_join_task
from .occupants import BotOccupantMixin
from .utils import domain_matches

log = logging.getLogger(__name__)


class MucMixin(BotOccupantMixin):
    def _get_reconnect_success_event(self) -> asyncio.Event:
        """Return the event used to signal that session_start completed after reconnect."""
        event = getattr(self, "reconnect_success_event", None)
        if event is None:
            event = asyncio.Event()
            self.reconnect_success_event = event
        return event


    def _get_muc_join_event(self, room: str) -> asyncio.Event:
        """Return the self-presence event for one room."""
        events = getattr(self, "room_join_events", None)
        if events is None:
            events = {}
            self.room_join_events = events
        event = events.get(room)
        if event is None:
            event = asyncio.Event()
            events[room] = event
        return event


    def _start_muc_join_task(
        self,
        room: str,
        nick: str,
        timeout: float,
    ) -> tuple[asyncio.Future | asyncio.Task | None, str]:
        """Start the best available Slixmpp MUC join API as a tracked task."""
        return start_muc_join_task(
            self.plugin["xep_0045"],
            room,
            nick,
            timeout=timeout,
        )


    async def _wait_for_muc_self_presence(
        self,
        room: str,
        event: asyncio.Event,
        timeout: float,
    ) -> bool:
        """Wait for tracked self-presence while retaining cache compatibility."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.0)

        while True:
            if self._bot_occupant_entry(room)[1] is not None:
                event.set()
                return True
            if event.is_set():
                return self._bot_occupant_entry(room)[1] is not None

            remaining = deadline - loop.time()
            if remaining <= 0:
                return False

            try:
                await asyncio.wait_for(event.wait(), timeout=min(0.1, remaining))
            except asyncio.TimeoutError:
                continue


    async def _settle_muc_join_task(
        self,
        task: asyncio.Future | asyncio.Task | None,
        *,
        cancel: bool,
    ) -> Exception | None:
        """Cancel/consume a join task so no delayed exception is orphaned."""
        if task is None:
            return None
        if cancel and not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return None
        except Exception as exc:
            return exc
        return None


    async def ensure_muc_joined(
        self,
        room: str,
        *,
        nick: str = NICK,
        timeout: float | None = None,
        retries: int | None = None,
        force: bool = False,
    ) -> bool:
        """Join a room and confirm the bot's actual self-presence.

        Prefer Slixmpp's non-deprecated ``join_muc_wait()`` API, but do not
        treat reception of a room subject as a requirement for a successful
        BanBot join. Once the bot's own MUC presence is tracked, the remaining
        Slixmpp waiter is cancelled and consumed. Failed waiters are consumed as
        well, preventing ``Task exception was never retrieved`` errors.
        """
        if not force and self._bot_occupant_entry(room)[1] is not None:
            return True

        if timeout is None:
            timeout = float(getattr(self, "muc_join_timeout_seconds", 20))
        else:
            timeout = float(timeout)
        if retries is None:
            retries = int(getattr(self, "muc_join_retries", 2))
        else:
            retries = int(retries)

        attempts = max(1, retries)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            if force or attempt > 1:
                current_nick, _info = self._bot_occupant_entry(room)
                left_room = False
                try:
                    self.plugin["xep_0045"].leave_muc(room, current_nick or nick)
                    left_room = True
                except Exception as exc:
                    log.debug("Could not clear previous MUC join state for %s: %s", room, exc)
                if left_room:
                    await asyncio.sleep(0.5)

            self.occupants.pop(room, None)
            getattr(self, "room_bot_nicks", {}).pop(room, None)
            join_event = self._get_muc_join_event(room)
            join_event.clear()
            self.room_join_time[room] = time.time()
            join_task: asyncio.Future | asyncio.Task | None = None
            presence_task: asyncio.Task | None = None
            last_error = None
            api_name = "unknown"

            try:
                join_task, api_name = self._start_muc_join_task(room, nick, timeout)
                presence_task = asyncio.create_task(
                    self._wait_for_muc_self_presence(room, join_event, timeout)
                )

                if join_task is None:
                    joined = await presence_task
                else:
                    done, _pending = await asyncio.wait(
                        {join_task, presence_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if presence_task in done:
                        joined = presence_task.result()
                    else:
                        try:
                            join_task.result()
                        except asyncio.CancelledError as exc:
                            last_error = exc
                            joined = False
                        except Exception as exc:
                            last_error = exc
                            joined = self._bot_occupant_entry(room)[1] is not None
                        else:
                            # A successful full waiter already observed our
                            # presence. Allow the BanBot handler/cache to finish.
                            joined = await presence_task

            except asyncio.CancelledError:
                if presence_task is not None and not presence_task.done():
                    presence_task.cancel()
                await self._settle_muc_join_task(join_task, cancel=True)
                raise
            except Exception as exc:
                last_error = exc
                joined = False

            if presence_task is not None and not presence_task.done():
                presence_task.cancel()
                try:
                    await presence_task
                except asyncio.CancelledError:
                    pass

            if joined:
                waiter_error = await self._settle_muc_join_task(join_task, cancel=True)
                if waiter_error is not None:
                    # Self-presence is authoritative. A waiter can still fail
                    # while waiting for a subject after a successful join.
                    log.debug(
                        "%s ended after self-presence for %s: %s",
                        api_name,
                        room,
                        waiter_error,
                    )

                actual_nick, _info = self._bot_occupant_entry(room)
                log.info("✅ Joined MUC %s as %s", room, actual_nick or nick)
                return True

            waiter_error = await self._settle_muc_join_task(join_task, cancel=True)
            if last_error is None:
                last_error = waiter_error
            if last_error is None:
                last_error = asyncio.TimeoutError(
                    f"No self-presence received within {float(timeout):g}s"
                )

            log.warning(
                "⚠️ MUC join failed for %s via %s (attempt %d/%d): %s",
                room,
                api_name,
                attempt,
                attempts,
                str(last_error).strip() or type(last_error).__name__,
            )
            if attempt < attempts:
                await asyncio.sleep(min(2.0 * attempt, 5.0))

        self.room_join_time.pop(room, None)
        getattr(self, "room_join_events", {}).pop(room, None)
        return False


    async def on_disconnect(self, _) -> None:
        # During the initial connection/STARTTLS negotiation Slixmpp may emit a
        # disconnect-like event before session_start completed. Do not start a
        # reconnect loop there; the original connection may still finish.
        if (
            hasattr(self, "server_connect_time")
            and getattr(self, "server_connect_time", None) is None
            and not getattr(self, "reconnecting", False)
        ):
            log.debug("Pre-session disconnect event received before session_start completed; ignoring")
            return

        if self.reconnect_task and not self.reconnect_task.done():
            log.info("🔄 Disconnect event received while reconnect is already scheduled")
            return

        log.warning("⚠️  Disconnected from server")
        self.reconnecting = True
        self._get_reconnect_success_event().clear()

        # Remember existing occupants before clearing runtime state.  If the
        # XMPP server restarts, everyone can leave and rejoin in a burst; those
        # rejoin presences should not be counted as a JoinWave raid.
        if hasattr(self, "protection_remember_current_occupants"):
            self.protection_remember_current_occupants()

        # runtime state reset
        self.occupants.clear()
        self.bot_admin_state.clear()
        self.room_join_time.clear()
        getattr(self, "room_bot_nicks", {}).clear()
        getattr(self, "room_join_events", {}).clear()
        log.info("🧹 Cleaned up occupants dictionary and states")

        self.reconnect_task = asyncio.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        """Reconnect until session_start confirms that the connection is usable."""
        current_task = asyncio.current_task()
        success_event = self._get_reconnect_success_event()
        delay = 5

        try:
            while True:
                log.info("🔄 Attempting reconnect in %ds...", delay)
                await asyncio.sleep(delay)
                success_event.clear()

                try:
                    connect_with_config = getattr(self, "connect_with_config", None)
                    if connect_with_config:
                        connect_with_config()
                    else:
                        self.connect()

                    log.info("🔌 Reconnect initiated")
                except Exception as e:
                    log.error("Reconnect error: %s", e)
                    delay = min(delay * 2, 60)
                    continue

                try:
                    await asyncio.wait_for(success_event.wait(), timeout=30)
                    log.info("🔄 Reconnect completed")
                    return
                except asyncio.TimeoutError:
                    log.warning("Reconnect did not complete within 30s; retrying")
                    self.reconnecting = True
                    delay = min(delay * 2, 60)

        except asyncio.CancelledError:
            log.info("Reconnect task cancelled")
            raise
        finally:
            if getattr(self, "reconnect_task", None) is current_task:
                self.reconnect_task = None


    async def wait_for_occupants(self, timeout: int = 20) -> None:
        """Wait until the bot's own presence is known in every managed room."""
        for _ in range(max(1, int(timeout / 2))):
            if all(
                self._bot_occupant_entry(room)[1] is not None
                for room in self.protected_rooms | {ADMIN_ROOM}
            ):
                return
            await asyncio.sleep(2)
        log.warning("Timeout waiting for bot self-presence in one or more rooms")


    async def wait_for_bot_online(self, room: str, timeout: int = 10) -> bool:
        """Wait until the bot's self-presence is recognized in a room."""
        for _ in range(max(1, timeout)):
            if self._bot_occupant_entry(room)[1] is not None:
                return True
            await asyncio.sleep(1)
        log.warning("Bot self-presence not recognized in %s after %ds", room, timeout)
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

        boundjid = getattr(self, "boundjid", None)
        if (
            jid_str
            and boundjid is not None
            and self.bare_jid(jid_str) == self.bare_jid(str(boundjid.bare))
        ):
            if not hasattr(self, "room_bot_nicks"):
                self.room_bot_nicks = {}
            self.room_bot_nicks[room] = nick
            self._get_muc_join_event(room).set()

        # --- Skip admins/owners ---
        if self.is_admin_or_owner(room, nick=nick, jid=jid_str):
            return

        # --- Protection join hooks (protected rooms only, not admin room) ---
        if room in self.protected_rooms and hasattr(self, "protection_on_join"):
            await self.protection_on_join(room, nick, jid_str)

        # --- RTBL check (protected rooms only, not admin room) ---
        if jid_str and room in self.protected_rooms:
            rtbl_hit = await self.check_jid_against_rtbl(jid_str, nick)
            if rtbl_hit:
                return  # already banned via RTBL, skip the rest

        # --- Auto-update JID if a nick-only ban exists ---
        if jid_str and nick:
            nick_key = nick.lower()
            existing_ban = self.ban_index_by_nick.get(nick_key)

            if existing_ban:
                # Found an active nick-only ban in the in-memory index; convert it
                # to a JID ban without querying the database on every MUC join.
                ban_jid_bare = self.bare_jid(jid_str)
                _ban_jid, _ban_nick, until, issuer, comment = existing_ban
                async with ban_state_lock(self):
                    await self.upsert_ban_db(ban_jid_bare, nick_key, int(until or 0), issuer, comment)
                    await self.db.execute(
                        "DELETE FROM bans WHERE target_type = 'nick' AND target = ?",
                        (nick_key,)
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


    def _muc_presence_status_codes(self, presence) -> set[str]:
        """Extract MUC user status codes from common Slixmpp/XML shapes."""
        codes: set[str] = set()

        try:
            muc = presence["muc"]
        except Exception:
            muc = None

        # Do not probe the unregistered Slixmpp ``statuses`` interface here.
        # Some servers/clients include MUC status XML, but asking Slixmpp for
        # an unknown stanza interface emits noisy root warnings.  Registered
        # mapping-style values cover tests and known Slixmpp shapes; raw XML is
        # inspected below for the actual <status code="..."/> elements.
        for key in ("status_codes", "status"):
            value = self._muc_mapping_value(muc, key)
            if value is None:
                continue
            if isinstance(value, dict):
                codes.update(str(code) for code in value.keys())
            elif isinstance(value, (set, list, tuple)):
                codes.update(str(code) for code in value)
            else:
                codes.add(str(value))

        xml = getattr(presence, "xml", None)
        if xml is not None:
            try:
                for element in xml.iter():
                    tag = str(getattr(element, "tag", "")).lower()
                    if tag.endswith("status"):
                        code = getattr(element, "attrib", {}).get("code")
                        if code:
                            codes.add(str(code))
            except Exception as exc:
                log.debug("Failed to inspect MUC presence status XML: %s", exc)

        return codes


    def _muc_mapping_value(self, muc, key: str):
        """Return a registered/explicit MUC mapping value without probing unknown interfaces."""
        if muc is None or not hasattr(muc, "get"):
            return None

        if isinstance(muc, dict):
            return muc.get(key)

        interfaces = getattr(muc, "interfaces", None)
        if interfaces is not None and key not in interfaces:
            return None

        try:
            return muc.get(key)
        except Exception:
            return None


    def _muc_presence_ban_reason(self, presence) -> str | None:
        """Extract a MUC ban reason from common Slixmpp/XML presence shapes."""
        try:
            muc = presence["muc"]
        except Exception:
            muc = None

        for key in ("reason", "status_text", "status_message"):
            value = self._muc_mapping_value(muc, key)
            if value:
                return str(value).strip() or None

        xml = getattr(presence, "xml", None)
        if xml is not None:
            try:
                for element in xml.iter():
                    tag = str(getattr(element, "tag", "")).lower()
                    if tag.endswith("reason") and getattr(element, "text", None):
                        return str(element.text).strip() or None
            except Exception as exc:
                log.debug("Failed to inspect MUC presence reason XML: %s", exc)

        return None


    async def _handle_manual_muc_ban_presence(
        self,
        room: str,
        nick: str,
        jid: str | None,
        reason: str | None,
    ) -> None:
        """Recover and optionally redact a live manual MUC ban presence event."""
        if room not in getattr(self, "protected_rooms", set()):
            return
        if not jid:
            log.debug("Manual MUC ban for %s in %s had no visible real JID; cannot recover", nick, room)
            return

        jid_bare = self.bare_jid(jid)
        issuer = "manual_muc_ban"
        comment = reason or "Recovered from room"
        now = int(time.time())

        existing_ban = self.ban_index_by_jid.get(jid_bare)
        if existing_ban:
            _existing_jid, _existing_nick, existing_until, _existing_issuer, existing_comment = existing_ban
            if existing_until <= 0 or existing_until > now:
                if not (existing_until <= 0 and reason and existing_comment == "Recovered from room"):
                    log.debug(
                        "Ignoring live manual MUC ban recovery for %s in %s; active ban already exists",
                        jid_bare,
                        room,
                    )
                    return

        if hasattr(self, "_sync_outcast_is_expired_tempban"):
            try:
                if await self._sync_outcast_is_expired_tempban(jid_bare, now):
                    await self.unban_all(jid_bare, issuer="system", notify_policy=False)
                    return
            except Exception as exc:
                log.debug("Could not check expired tempban state for manual MUC ban %s: %s", jid_bare, exc)

        async with ban_state_lock(self):
            await self.upsert_ban_db(jid_bare, nick, 0, issuer, comment)
            await self.load_bans_from_db()

        log.info("✅ Recovered live manual MUC ban for %s in %s", jid_bare, room)

        if hasattr(self, "maybe_auto_redact_after_manual_muc_ban"):
            await self.maybe_auto_redact_after_manual_muc_ban(jid_bare, reason, actor=issuer)


    async def muc_offline(self, presence) -> None:
        """
        Called when a user goes offline in a MUC.
        - Removes them from self.occupants[room]
        - Logs offline info
        - Recovers live manual MUC bans when the server sends status 301
        """
        room = presence["from"].bare
        nick = presence["muc"]["nick"]

        room_occ = self.occupants.get(room)
        if room_occ and nick in room_occ:
            info = room_occ.pop(nick)
            jid = info.get("jid")
            log.debug("⛔ %s went offline in %s (jid=%s, affiliation=%s, role=%s)",
                     nick,
                     room,
                     jid or "unknown",
                     info.get("affiliation", "none"),
                     info.get("role", "none"))

            if getattr(self, "room_bot_nicks", {}).get(room) == nick:
                self.room_bot_nicks.pop(room, None)
                self.bot_admin_state.pop(room, None)
                join_event = getattr(self, "room_join_events", {}).get(room)
                if join_event is not None:
                    join_event.clear()

            if "301" in self._muc_presence_status_codes(presence):
                await self._handle_manual_muc_ban_presence(
                    room,
                    nick,
                    jid,
                    self._muc_presence_ban_reason(presence),
                )


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
        jid = presence["muc"].get("jid")
        jid_str = str(jid) if jid else None
        status_codes = self._muc_presence_status_codes(presence)
        known_nick = getattr(self, "room_bot_nicks", {}).get(room)
        is_self_presence = (
            "110" in status_codes
            or (known_nick is not None and nick == known_nick)
            or (
                jid_str is not None
                and getattr(self, "boundjid", None) is not None
                and self.bare_jid(jid_str) == self.bare_jid(str(self.boundjid.bare))
            )
            or (
                not hasattr(self, "room_bot_nicks")
                and str(nick).lower() == str(NICK).lower()
            )
        )

        if not is_self_presence:
            return

        if not hasattr(self, "room_bot_nicks"):
            self.room_bot_nicks = {}
        self.room_bot_nicks[room] = nick
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
        self._get_muc_join_event(room).set()

        # Ignore briefly after join/reconnect to let occupant state stabilize.
        grace_seconds = 5
        now = time.time()
        join_time = self.room_join_time.get(room)
        if (
            self.reconnecting
            and (now - (join_time or 0)) < grace_seconds
        ) or (
            not self.reconnecting
            and join_time
            and (now - join_time) < grace_seconds
        ):
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
