"""RTBL lookup checks, occupant scans, and ban application."""

import logging

from .utils import domain_matches

log = logging.getLogger(__name__)


class RtblApplyMixin:
    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool:
        """
        Check a joining user against both in-memory RTBL caches.

        1. SHA-256 hash of the bare JID is looked up in rtbl_hash_cache.
        2. The user's domain (and all parent domains) is checked against
           rtbl_domain_cache using suffix matching.

        Returns True if a ban was applied so that muc_online() can skip
        further processing for this user.
        """
        if not getattr(self, "rtbl_enabled", False):
            return False

        bare = self.bare_jid(jid)
        if not bare:
            return False

        # --- Global ignorelist check ---
        if self.is_ignored_target(bare):
            log.debug("RTBL: Ignoring JID %s (global ignorelist)", bare)
            return False

        # JID hash check
        h = self._rtbl_hash_jid(bare)
        if h in self.rtbl_hash_cache:
            await self._rtbl_apply_ban_jid(bare, nick, self.rtbl_hash_cache[h])
            return True

        # Domain check
        if "@" in bare:
            user_domain = bare.split("@", 1)[1].lower()
            for banned_domain, reason in self.rtbl_domain_cache.items():
                if domain_matches(user_domain, banned_domain):
                    await self._rtbl_apply_ban_domain(banned_domain, reason, nick=nick, jid=bare)
                    return True

        return False


    async def _rtbl_check_all_occupants_against_caches(
        self, source: str | None = None
    ) -> tuple[int, int]:
        """
        Scan all currently known occupants against the complete RTBL caches.

        This is used after startup fetches and after adding a new subscription so
        current occupants are banned immediately when they already match a loaded
        RTBL hash/domain entry. Periodic refresh deliberately does not call this
        helper to avoid noisy re-application of unchanged bans.

        Domain bans are persisted/applied once per matching domain, but all
        currently matching occupants are counted and will be kicked by
        _rtbl_apply_ban_domain(), which scans all protected rooms itself.

        Returns (matched_jids, matched_domain_occupants).
        """
        if not getattr(self, "rtbl_enabled", False):
            return 0, 0

        matched_jids: set[str] = set()
        matched_domain_occupants: set[tuple[str, str]] = set()
        domains_to_apply: dict[str, tuple[str | None, str | None, str | None]] = {}

        for room, occupants in list(self.occupants.items()):
            if room not in self.protected_rooms:
                continue

            for nick, info in list(occupants.items()):
                jid = info.get("jid")
                if not jid:
                    continue

                bare = self.bare_jid(jid)
                if not bare:
                    continue

                if self.is_ignored_target(bare):
                    continue

                hash_val = self._rtbl_hash_jid(bare)
                if hash_val in self.rtbl_hash_cache and bare not in matched_jids:
                    matched_jids.add(bare)
                    await self._rtbl_apply_ban_jid(
                        bare, nick, self.rtbl_hash_cache[hash_val]
                    )
                    continue

                if "@" not in bare:
                    continue

                user_domain = bare.split("@", 1)[1].lower()
                for banned_domain, reason in list(self.rtbl_domain_cache.items()):
                    if domain_matches(user_domain, banned_domain):
                        matched_domain_occupants.add((banned_domain, bare))
                        domains_to_apply.setdefault(banned_domain, (reason, nick, bare))
                        break

        for banned_domain, (reason, nick, bare) in domains_to_apply.items():
            await self._rtbl_apply_ban_domain(
                banned_domain, reason, nick=nick, jid=bare
            )

        if matched_jids or matched_domain_occupants:
            log.info(
                (
                    "RTBL: Current occupant scan%s matched %d JID(s), "
                    "%d domain occupant(s) across %d domain(s)"
                ),
                f" after {source}" if source else "",
                len(matched_jids),
                len(matched_domain_occupants),
                len(domains_to_apply),
            )

        return len(matched_jids), len(matched_domain_occupants)


    async def _rtbl_check_all_occupants_for_hash(
        self, hash_val: str, reason: str | None
    ) -> None:
        """
        Scan all current occupants of every protected room against a
        newly received JID hash. Called immediately on pubsub_publish.
        """
        for room, occupants in self.occupants.items():
            if room not in self.protected_rooms:
                continue
            for nick, info in list(occupants.items()):
                jid = info.get("jid")
                if not jid:
                    continue
                bare = self.bare_jid(jid)
                if not bare:
                    continue
                # Global ignorelist check
                if self.is_ignored_target(bare):
                    continue
                if self._rtbl_hash_jid(bare) == hash_val:
                    await self._rtbl_apply_ban_jid(bare, nick, reason)


    async def _rtbl_check_all_occupants_for_domain(
        self, domain: str, reason: str | None
    ) -> None:
        """
        Scan all current occupants of every protected room against a
        newly received domain ban. Called immediately on pubsub_publish.
        """
        for room, occupants in self.occupants.items():
            if room not in self.protected_rooms:
                continue
            for nick, info in list(occupants.items()):
                jid = info.get("jid")
                if not jid:
                    continue
                bare = self.bare_jid(jid)
                if not bare or "@" not in bare:
                    continue
                user_domain = bare.split("@", 1)[1].lower()
                # Global ignorelist check
                if self.is_ignored_target(bare):
                    continue
                if domain_matches(user_domain, domain):
                    await self._rtbl_apply_ban_domain(domain, reason, nick=nick, jid=bare)


    async def _rtbl_apply_ban_jid(
        self,
        jid: str,
        nick: str | None,
        reason: str | None,
    ) -> None:
        """
        Apply an RTBL JID ban via MUC outcast affiliation in all protected rooms.

        Admin/owner protection is always checked first. If the target is an
        admin or owner in any protected room or the admin room the ban is
        silently dropped and, if RTBL_ANNOUNCE is True, a warning is sent to
        the admin room.

        The applied RTBL ban is persisted in the main bans table immediately
        so the local banlist and room state stay consistent.
        """
        from config import ADMIN_ROOM

        if self.is_ignored_target(jid):
            log.info("RTBL: Refusing JID ban for %s — global ignorelist", jid)
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ RTBL: Ignored ban for {jid} — global ignorelist",
                    mtype="groupchat",
                )
            return

        protected, protect_reason = await self.is_protected_admin_target(
            jid, nick=nick, jid=jid
        )
        if protected:
            log.warning(
                "RTBL: Ignoring JID ban for %s — admin/owner protected: %s",
                jid, protect_reason,
            )
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⚠️ RTBL: Ignored JID ban for {jid} "
                        f"— protected admin/owner ({protect_reason})"
                    ),
                    mtype="groupchat",
                )
            return

        comment = f"RTBL: {reason}" if reason else "RTBL ban"
        log.info("RTBL: Banning JID %s (reason: %s)", jid, reason)

        await self.upsert_ban_db(
            jid=jid, nick=nick, until=0, issuer="rtbl", comment=comment
        )
        await self.db.commit()
        log.debug("RTBL: Persisted JID ban for %s to bans table", jid)

        if self.rtbl_announce:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"🛡️ RTBL: Banning {jid}" + (f" — {reason}" if reason else ""),
                mtype="groupchat",
            )

        self.log_event(
            logging.INFO, "rtbl_ban_applied",
            actor="rtbl", identifier=jid, target_type="jid",
            target=jid, jid=jid, nick=nick, comment=comment,
        )
        await self.audit_event(
            "rtbl_ban_applied", actor="rtbl", target_type="jid",
            target=jid, jid=jid, nick=nick, comment=comment,
        )

        for room in self.protected_rooms:
            try:
                await self.apply_ban_to_room(room, jid, nick, comment, issuer="rtbl")
            except Exception as e:
                log.warning("RTBL: Failed to apply JID ban in %s: %s", room, e)


    async def _rtbl_apply_ban_domain(
        self,
        domain: str,
        reason: str | None,
        nick: str | None = None,
        jid: str | None = None,
    ) -> None:
        """
        Apply an RTBL domain ban to all currently matching occupants in every
        protected room via MUC outcast affiliation.

        Admin/owner protection is always checked first (same as a manual !ban).
        The applied RTBL ban is persisted in the main bans table immediately
        so the local banlist and room state stay consistent.
        """
        from config import ADMIN_ROOM

        wildcard = f"*.{domain}"

        if self.is_ignored_target(wildcard):
            log.info("RTBL: Refusing domain ban %s — global ignorelist", wildcard)
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ RTBL: Ignored domain ban {wildcard} — global ignorelist",
                    mtype="groupchat",
                )
            return

        protected, protect_reason = await self.is_protected_admin_target(
            wildcard, nick=nick, jid=jid
        )
        if protected:
            log.warning(
                "RTBL: Ignoring domain ban *.%s — admin/owner protected: %s",
                domain, protect_reason,
            )
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⚠️ RTBL: Ignored domain ban *.{domain} "
                        f"— protected admin/owner ({protect_reason})"
                    ),
                    mtype="groupchat",
                )
            return

        comment = f"RTBL: {reason}" if reason else "RTBL domain ban"
        log.info("RTBL: Applying domain ban *.%s (reason: %s)", domain, reason)

        await self.upsert_ban_db(
            jid=f"*.{domain}", nick=None, until=0, issuer="rtbl", comment=comment
        )
        await self.db.commit()
        log.debug("RTBL: Persisted domain ban *.%s to bans table", domain)

        if self.rtbl_announce:
            affected = ""
            if jid and nick:
                affected = f"\n   Matched: {nick} ({jid})"
            elif jid:
                affected = f"\n   Matched: {jid}"
            elif nick:
                affected = f"\n   Matched: {nick}"

            self.send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ RTBL: Domain ban *.{domain}"
                    f"{affected}"
                    + (f"\n   Reason: {reason}" if reason else "")
                ),
                mtype="groupchat",
            )

        self.log_event(
            logging.INFO, "rtbl_ban_applied",
            actor="rtbl", identifier=wildcard, target_type="domain",
            target=domain, jid=wildcard, nick=nick, comment=comment,
        )
        await self.audit_event(
            "rtbl_ban_applied", actor="rtbl", target_type="domain",
            target=domain, jid=wildcard, nick=nick, comment=comment,
        )

        for room in self.protected_rooms:
            try:
                for occ_nick, info in list(self.occupants.get(room, {}).items()):
                    occ_jid = info.get("jid")
                    if not occ_jid:
                        continue
                    bare = self.bare_jid(occ_jid)
                    if not bare or "@" not in bare:
                        continue
                    occ_domain = bare.split("@", 1)[1].lower()
                    if domain_matches(occ_domain, domain):
                        await self.apply_ban_to_room(
                            room, bare, occ_nick, comment, issuer="rtbl"
                        )
            except Exception as e:
                log.warning("RTBL: Failed to apply domain ban in %s: %s", room, e)
