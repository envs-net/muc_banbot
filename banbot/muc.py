"""MUC connection, presence tracking, and occupant handling."""

import asyncio
import inspect
import logging
import time
from typing import TYPE_CHECKING

from envs_xmpp_core.runtime.reconnect import run_reconnect_loop
from envs_xmpp_core.xmpp.muc_join import join_muc_confirmed
from envs_xmpp_core.xmpp.occupants import (
    normalize_affiliation,
    normalize_role,
    occupant_is_admin_or_owner,
)

from config import ADMIN_ROOM, NICK

from .ban_target import BanTarget
from .locks import ban_state_lock
from .occupants import BotOccupantMixin
from .utils import domain_matches, looks_like_domain

log = logging.getLogger(__name__)

_RECONNECT_STARTUP_TIMEOUT_SECONDS = 120

if TYPE_CHECKING:
    from .contracts import MucMixinHost

    class _MucMixinContract(MucMixinHost):
        pass
else:
    class _MucMixinContract:
        pass


class MucMixin(BotOccupantMixin, _MucMixinContract):
    _startup_task: asyncio.Task | None
    reconnect_success_event: asyncio.Event | None
    reconnect_failure_event: asyncio.Event | None

    def _mark_session_reconnecting(self, reason: str) -> None:
        lifecycle = getattr(self, "session_lifecycle", None)
        marker = getattr(lifecycle, "mark_reconnecting", None)
        if callable(marker):
            marker(reason)

    def _get_reconnect_success_event(self) -> asyncio.Event:
        """Return the event used to signal that session_start completed after reconnect."""
        event = getattr(self, "reconnect_success_event", None)
        if event is None:
            event = asyncio.Event()
            self.reconnect_success_event = event
        return event


    async def _cancel_incomplete_startup(
        self,
        reason: str,
        *,
        exclude: asyncio.Task | None = None,
    ) -> bool:
        """Cancel a stale ``session_start`` lifecycle before stream teardown."""
        task = getattr(self, "_startup_task", None)
        if task is None or task is exclude or task.done():
            return False

        log.warning("Cancelling incomplete XMPP startup before %s", reason)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("Incomplete XMPP startup ended while cancelling: %s", exc)
        finally:
            if getattr(self, "_startup_task", None) is task:
                self._startup_task = None
        return True


    async def _disconnect_partial_reconnect(self, reason: str) -> None:
        """Drop a reconnect session that never reached usable startup state."""
        await self._cancel_incomplete_startup(reason, exclude=asyncio.current_task())
        self.reconnecting = True
        self._session_start_received = False
        self._mark_session_reconnecting(reason)

        # The partially initialized session must not leak occupant/admin/join
        # state into the next connection attempt. on_disconnect() normally does
        # this cleanup, but it intentionally does not schedule a second loop
        # while the current reconnect task is still active.
        self.occupants.clear()
        self.bot_admin_state.clear()
        self.room_join_time.clear()
        getattr(self, "room_bot_nicks", {}).clear()
        getattr(self, "room_join_events", {}).clear()

        try:
            abort = getattr(self, "abort", None)
            if callable(abort):
                # This path is intentionally forceful.  Draining Slixmpp's
                # waiting/send queues after startup has timed out can race with
                # connection_lost and produce NotConnectedError from stale IQs.
                abort()
                return

            try:
                result = self.disconnect(
                    wait=0.0,
                    reason=f"partial reconnect reset: {reason}",
                    ignore_send_queue=True,
                )
            except TypeError:
                # Compatibility with older Slixmpp signatures.
                try:
                    result = self.disconnect(wait=0.0)
                except TypeError:
                    result = self.disconnect()
            if inspect.isawaitable(result):
                try:
                    disconnect_task = asyncio.ensure_future(result)
                    await asyncio.wait_for(disconnect_task, timeout=5.0)
                except TimeoutError:
                    log.warning(
                        "Partial reconnect session did not disconnect within 5.0s "
                        "after %s",
                        reason,
                    )
        except Exception as exc:
            log.warning(
                "Failed to disconnect partial reconnect session after %s: %s",
                reason,
                exc,
            )


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

        The shared envs-xmpp join transaction prefers ``join_muc_wait()``,
        races that waiter against BanBot's authoritative occupant cache,
        consumes/cancels delayed waiter failures, cleans partial joins, and
        performs bounded retries. Self-presence remains the success criterion.
        """
        if timeout is None:
            timeout = float(getattr(self, "muc_join_timeout_seconds", 20))
        else:
            timeout = float(timeout)
        if retries is None:
            retries = int(getattr(self, "muc_join_retries", 2))
        else:
            retries = int(retries)

        join_event = self._get_muc_join_event(room)

        def is_joined() -> bool:
            return self._bot_occupant_entry(room)[1] is not None

        def clear_state() -> None:
            self.occupants.pop(room, None)
            getattr(self, "room_bot_nicks", {}).pop(room, None)
            self.room_join_time[room] = time.time()

        def on_cleanup_error(exc: Exception) -> None:
            log.debug("Could not clear previous MUC join state for %s: %s", room, exc)

        result = await join_muc_confirmed(
            self.plugin["xep_0045"],
            room,
            nick,
            is_joined=is_joined,
            timeout=timeout,
            retries=max(1, retries),
            event=join_event,
            force=force,
            clear_state=clear_state,
            retry_delays=lambda attempt: min(2.0 * attempt, 5.0),
            leave_delay=0.5,
            cleanup_on_failure=True,
            on_cleanup_error=on_cleanup_error,
        )

        if result.joined:
            if result.waiter_error is not None:
                # Self-presence is authoritative. A waiter may still fail while
                # waiting for a subject after a successful membership change.
                log.debug(
                    "%s ended after self-presence for %s: %s",
                    result.api_name,
                    room,
                    result.waiter_error,
                )
            actual_nick, _info = self._bot_occupant_entry(room)
            log.info("✅ Joined MUC %s as %s", room, actual_nick or nick)
            return True

        error = result.error or TimeoutError(
            f"No self-presence received within {float(timeout):g}s"
        )
        log.warning(
            "⚠️ MUC join failed for %s via %s after %d attempt(s): %s",
            room,
            result.api_name,
            result.attempts,
            str(error).strip() or type(error).__name__,
        )
        self.room_join_time.pop(room, None)
        getattr(self, "room_join_events", {}).pop(room, None)
        return False


    async def on_connection_failed(self, _) -> None:
        """Let Slixmpp retry failed connection attempts without a second loop."""
        if (
            getattr(self, "_shutdown_in_progress", False)
            or getattr(self, "_shutdown_complete", False)
        ):
            log.debug(
                "connection_failed received during shutdown; retry handling suppressed"
            )
            return

        # ``connection_failed`` describes a failed transport/negotiation
        # attempt, not the loss of an established XMPP session. Slixmpp owns
        # retry scheduling for this event. Starting BanBot's reconnect loop as
        # well would create two independent retry mechanisms which can race and
        # cancel each other's connection attempts.
        if not bool(getattr(self, "_session_start_received", False)):
            if bool(getattr(self, "_startup_completed_once", False)):
                log.info(
                    "XMPP reconnect attempt failed before session_start; "
                    "waiting for Slixmpp retry"
                )
            else:
                log.info(
                    "Initial XMPP transport attempt failed before session_start; "
                    "waiting for Slixmpp retry/fallback"
                )

            # On the very first process startup, keep Type=notify alive while
            # Slixmpp retries an unavailable remote server. This helper is
            # idempotent, so repeated connection_failed events only keep the
            # existing extender armed.
            self._mark_session_reconnecting("connection attempt failed before session_start")
            if not bool(getattr(self, "_startup_completed_once", False)):
                runtime_watchdog = getattr(self, "runtime_watchdog", None)
                arm_startup_timeout = getattr(
                    runtime_watchdog,
                    "arm_startup_timeout_extension",
                    None,
                )
                if callable(arm_startup_timeout):
                    arm_startup_timeout()
            return

        log.info(
            "XMPP connection attempt failed after session_start; "
            "waiting for disconnected/session lifecycle handling"
        )


    async def on_disconnect(self, _) -> None:
        if getattr(self, "_shutdown_in_progress", False) or getattr(self, "_shutdown_complete", False):
            log.debug("Disconnect event received during shutdown; reconnect suppressed")
            return

        await self._cancel_incomplete_startup(
            "connection loss",
            exclude=asyncio.current_task(),
        )

        pre_session_disconnect = (
            hasattr(self, "server_connect_time")
            and getattr(self, "server_connect_time", None) is None
            and not getattr(self, "reconnecting", False)
        )
        if pre_session_disconnect:
            # connect() is asynchronous in Slixmpp: it can return True even
            # though the TCP/TLS/XMPP negotiation later fails. Ignoring this
            # event leaves a Type=notify service stuck in "activating" until
            # TimeoutStartSec kills it. Schedule the normal reconnect loop
            # instead; its backoff gives any already queued session_start event
            # a chance to win before another connect attempt is made.
            log.warning(
                "Initial XMPP connection ended before session_start; "
                "scheduling reconnect"
            )

        existing_reconnect = getattr(self, "reconnect_task", None)
        if existing_reconnect is not None and not existing_reconnect.done():
            if getattr(self, "reconnecting", False):
                log.info("🔄 Disconnect event received while reconnect is already scheduled")
                return

            # A successful startup clears reconnecting before the old reconnect
            # waiter necessarily gets its final event-loop turn.  If another
            # disconnect lands in that narrow window, the old waiter is stale:
            # cancel it and schedule a fresh reconnect instead of swallowing the
            # new outage.
            log.info("🔄 Replacing stale reconnect waiter after a new disconnect")
            existing_reconnect.cancel()
            if getattr(self, "reconnect_task", None) is existing_reconnect:
                self.reconnect_task = None

        log.warning("⚠️  Disconnected from server")
        self.reconnecting = True
        self._session_start_received = False
        self._mark_session_reconnecting("connection lost")
        self._get_reconnect_success_event().clear()

        # Before the first successful READY=1, a remote XMPP outage may last
        # longer than systemd's normal startup timeout (for example while the
        # server is offline for backups). Keep the startup deadline alive while
        # the event loop is healthy and we are waiting for a new session.
        runtime_watchdog = getattr(self, "runtime_watchdog", None)
        arm_startup_timeout = getattr(
            runtime_watchdog,
            "arm_startup_timeout_extension",
            None,
        )
        if callable(arm_startup_timeout):
            arm_startup_timeout()

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
        try:
            await run_reconnect_loop(
                connect=lambda: self.connect_with_config(),
                disconnect_partial=self._disconnect_partial_reconnect,
                ready_event=self._get_reconnect_success_event(),
                session_started=lambda: bool(
                    getattr(self, "_session_start_received", False)
                ),
                shutdown_requested=lambda: bool(
                    getattr(self, "_shutdown_in_progress", False)
                    or getattr(self, "_shutdown_complete", False)
                ),
                startup_completed=lambda: bool(
                    getattr(self, "_startup_completed_once", False)
                ),
                logger=log,
                initial_delay=5,
                max_delay=60,
                startup_timeout=_RECONNECT_STARTUP_TIMEOUT_SECONDS,
            )
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
                if ban_jid_bare:
                    _ban_jid, _ban_nick, until, issuer, comment = existing_ban
                    async with ban_state_lock(self):
                        # upsert_ban_db() already merges/deletes the duplicate
                        # nick-only row transactionally when a JID is supplied.
                        await self.upsert_ban_db(
                            ban_jid_bare,
                            nick_key,
                            int(until or 0),
                            issuer,
                            comment,
                        )
                        await self.load_bans_from_db()

                    log.info("✅ Auto-updated ban for nick '%s': JID set to %s", nick, ban_jid_bare)
                else:
                    log.debug(
                        "Skipping nick-only ban conversion for %s in %s: real JID did not normalize",
                        nick,
                        room,
                    )

        # --- Fetch all bans ---
        # Use indexes for O(1) lookups instead of O(n)
        now = int(time.time())
        tasks = []

        # Check by JID
        if jid_str:
            jid_bare = self.bare_jid(jid_str)
            if jid_bare and jid_bare in self.ban_index_by_jid:
                ban_jid, ban_nick, until, issuer, comment = self.ban_index_by_jid[jid_bare]
                if until <= 0 or until > now:  # Check if not expired
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

            # Check by wildcard domain bans (*.domain.tld matches domain.tld and sub.domain.tld)
            domain = jid_bare.split("@", 1)[1].lower() if jid_bare and "@" in jid_bare else None
            if domain:
                for banned_domain, bans in self.ban_index_by_domain.items():
                    if domain_matches(domain, banned_domain):
                        for ban_jid, ban_nick, until, _issuer, comment in bans:
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
        if not jid_bare:
            log.debug(
                "Manual MUC ban for %s in %s had an unusable real JID; cannot recover",
                nick,
                room,
            )
            return

        # A status-301 unavailable presence can also describe BanBot itself
        # being removed from a managed room. Never turn that operational
        # failure into a persisted ban of the bot's own account that would be
        # propagated to every protected room on the next synchronization.
        boundjid = getattr(self, "boundjid", None)
        own_bare = (
            self.bare_jid(str(boundjid.bare))
            if boundjid is not None
            else None
        )
        if own_bare and jid_bare == own_bare:
            log.warning(
                "Ignoring status-301 ban recovery for BanBot's own JID in %s",
                room,
            )
            return

        is_domain_outcast = looks_like_domain(jid_bare)
        recovered_target = BanTarget.from_identifier(
            jid_bare,
            plain_domain=is_domain_outcast,
        )
        ban_target = recovered_target.identifier
        issuer = "manual_muc_ban"
        comment = reason or "Recovered from room"
        now = int(time.time())

        existing_ban = None
        if is_domain_outcast:
            domain_bans = self.ban_index_by_domain.get(jid_bare.strip("."), [])
            existing_ban = domain_bans[0] if domain_bans else None
        else:
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
                    await self.unban_all(ban_target, issuer="system", notify_policy=False)
                    return
            except Exception as exc:
                log.debug("Could not check expired tempban state for manual MUC ban %s: %s", jid_bare, exc)

        async with ban_state_lock(self):
            await self.upsert_ban_db(ban_target, None if is_domain_outcast else nick, 0, issuer, comment)
            await self.load_bans_from_db()

        log.info("✅ Recovered live manual MUC ban for %s in %s", jid_bare, room)

        if not is_domain_outcast and hasattr(self, "maybe_auto_redact_after_manual_muc_ban"):
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
        info = room_occ.pop(nick) if room_occ and nick in room_occ else None
        cached_jid = info.get("jid") if info is not None else None
        if info is not None:
            log.debug("⛔ %s went offline in %s (jid=%s, affiliation=%s, role=%s)",
                     nick,
                     room,
                     cached_jid or "unknown",
                     info.get("affiliation", "none"),
                     info.get("role", "none"))

        # Clear self-presence tracking even if the occupant cache entry was
        # already missing. A late unavailable presence must not leave a stale
        # join event or bot nick behind.
        if getattr(self, "room_bot_nicks", {}).get(room) == nick:
            self.room_bot_nicks.pop(room, None)
            self.bot_admin_state.pop(room, None)
            join_event = getattr(self, "room_join_events", {}).get(room)
            if join_event is not None:
                join_event.clear()

        if "301" in self._muc_presence_status_codes(presence):
            # Prefer the cached real JID, but recover from the presence itself
            # when the join event was missed or the cache was cleared before
            # the unavailable stanza arrived.
            presence_jid = None
            try:
                muc = presence["muc"]
            except Exception:
                muc = None
            if muc is not None and hasattr(muc, "get"):
                try:
                    raw_jid = muc.get("jid")
                except Exception:
                    raw_jid = None
                if raw_jid:
                    presence_jid = str(raw_jid)
            await self._handle_manual_muc_ban_presence(
                room,
                nick,
                cached_jid or presence_jid,
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
        affiliation = normalize_affiliation(presence["muc"]["affiliation"], default="none")
        role = normalize_role(presence["muc"]["role"], default="none")

        previous_info = self.occupants.get(room, {}).get(nick, {})
        previous_affiliation = previous_info.get("affiliation")

        if previous_affiliation != affiliation:
            # A remembered ``forbidden`` affiliation-list query is only valid
            # for the bot's previous room privileges. Re-probe lazily after a
            # promotion/demotion instead of keeping offline-admin protection
            # disabled until the whole process restarts.
            forbidden_rooms = getattr(
                self,
                "admin_affiliation_query_forbidden_rooms",
                None,
            )
            if forbidden_rooms is not None:
                forbidden_rooms.discard(room)
            invalidate_admin_cache = getattr(
                self,
                "_invalidate_room_admin_owner_cache",
                None,
            )
            if callable(invalidate_admin_cache):
                invalidate_admin_cache(room)

        # Keep our own live occupant cache in sync.
        #
        # This is important for status output: if the bot is downgraded
        # from admin/owner to member/participant, the status admin list is
        # built from self.occupants and must not keep showing stale rights.
        self.occupants.setdefault(room, {})[nick] = {
            "role": role,
            "affiliation": affiliation,
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

        is_admin_now = occupant_is_admin_or_owner({"affiliation": affiliation})
        was_admin = self.bot_admin_state.get(room)

        # If bot_admin_state has not been initialized yet, fall back to the
        # previously cached affiliation. This catches downgrades that happen
        # before on_muc_presence() has seen an initial admin/owner presence.
        if was_admin is None and previous_affiliation:
            was_admin = occupant_is_admin_or_owner({"affiliation": previous_affiliation})

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
