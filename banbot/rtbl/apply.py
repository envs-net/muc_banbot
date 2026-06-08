"""RTBL lookup checks, occupant scans, and ban application."""

import logging

from ..locks import ban_state_lock
from ..utils import domain_matches

log = logging.getLogger(__name__)


class RtblApplyMixin:
    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool:
        """
        Check a joining user against both in-memory RTBL caches.

        Exact JID ignorelist entries protect the user from all RTBL matches.
        Domain ignorelist entries protect against RTBL domain matches, but do
        not suppress RTBL hash matches for an explicitly listed JID hash.
        """
        if not getattr(self, "rtbl_enabled", False):
            return False

        bare = self.bare_jid(jid)
        if not bare:
            return False

        # Exact JID ignorelist entries protect this user from all RTBL bans.
        if self.is_ignored_jid(bare):
            log.debug("RTBL: Ignoring JID %s (exact ignorelist match)", bare)
            return False

        # JID hash check. Domain ignorelist entries intentionally do not block
        # hash-based RTBL bans; only an exact JID ignore entry does.
        h = self._rtbl_hash_jid(bare)
        if h in self.rtbl_hash_cache:
            await self._rtbl_apply_ban_jid(bare, nick, self.rtbl_hash_cache[h])
            return True

        # Domain check. Domain ignorelist entries protect against domain-based
        # RTBL bans for that domain and its subdomains.
        if "@" in bare:
            user_domain = bare.split("@", 1)[1].lower()
            if self.is_ignored_domain(user_domain):
                log.debug("RTBL: Ignoring domain match for %s (domain ignorelist)", bare)
                return False

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

        Domain RTBL entries are applied once per matching domain, but the
        resulting local bans are persisted as concrete JID bans for each
        matching occupant. This keeps !banlist actionable and allows per-user
        !unban / !ignore exceptions for domain-list matches.

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

                # Exact JID ignorelist entries protect this user from all RTBL bans.
                if self.is_ignored_jid(bare):
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
                if self.is_ignored_domain(user_domain):
                    continue

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
                if self.is_ignored_jid(bare):
                    continue
                if self._rtbl_hash_jid(bare) == hash_val:
                    await self._rtbl_apply_ban_jid(bare, nick, reason)


    async def _rtbl_check_all_occupants_for_domain(
        self, domain: str, reason: str | None
    ) -> None:
        """
        Scan current occupants for a newly received domain ban.

        _rtbl_apply_ban_domain() scans all protected rooms itself, so call it
        only once with the first matching occupant as announcement context.
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

                if self.is_ignored_jid(bare):
                    continue

                user_domain = bare.split("@", 1)[1].lower()
                if self.is_ignored_domain(user_domain):
                    continue

                if domain_matches(user_domain, domain):
                    await self._rtbl_apply_ban_domain(
                        domain, reason, nick=nick, jid=bare
                    )
                    return


    async def _rtbl_apply_ban_jid(
        self,
        jid: str,
        nick: str | None,
        reason: str | None,
    ) -> None:
        """Apply an RTBL JID ban while holding the shared ban-state lock."""
        async with ban_state_lock(self):
            await self._rtbl_apply_ban_jid_locked(jid, nick, reason)

    async def _rtbl_apply_ban_jid_locked(
        self,
        jid: str,
        nick: str | None,
        reason: str | None,
    ) -> None:
        """
        Apply an RTBL JID ban via MUC outcast affiliation in all protected rooms.

        Admin/owner protection is always checked first. Exact JID ignorelist
        entries protect the target from RTBL hash bans; domain ignorelist entries
        do not suppress hash bans for a specifically listed JID hash.
        """
        from config import ADMIN_ROOM

        if self.is_ignored_jid(jid):
            log.info("RTBL: Refusing JID ban for %s — exact ignorelist match", jid)
            if self.rtbl_announce:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ RTBL: Ignored ban for {jid} — exact ignorelist match",
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
                await self.bot_send_message(
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
            await self.bot_send_message(
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
        """Apply an RTBL domain ban while holding the shared ban-state lock."""
        async with ban_state_lock(self):
            await self._rtbl_apply_ban_domain_locked(domain, reason, nick=nick, jid=jid)

    async def _rtbl_apply_ban_domain_locked(
        self,
        domain: str,
        reason: str | None,
        nick: str | None = None,
        jid: str | None = None,
    ) -> None:
        """
        Apply an RTBL domain entry to all currently matching occupants.

        The RTBL source rule remains stored in rtbl_domains, but the local
        applied bans are persisted as concrete JID bans. This makes the normal
        banlist actionable: admins can !unban or !ignore a specific JID that was
        matched by a domain RTBL entry without having to ignore the whole domain.
        """
        from config import ADMIN_ROOM

        domain = domain.lstrip("*.").lower()
        wildcard = f"*.{domain}"

        if self.is_ignored_domain(domain):
            log.info("RTBL: Refusing domain ban %s — domain ignorelist", wildcard)
            if self.rtbl_announce:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ RTBL: Ignored domain ban {wildcard} — domain ignorelist",
                    mtype="groupchat",
                )
            return

        comment = f"RTBL domain ban: {wildcard}"
        if reason:
            comment += f" — {reason}"
        log.info("RTBL: Applying domain ban *.%s (reason: %s)", domain, reason)

        matched: dict[str, tuple[str | None, set[str]]] = {}
        skipped_protected: list[tuple[str, str | None, str | None]] = []

        for room in self.protected_rooms:
            for occ_nick, info in list(self.occupants.get(room, {}).items()):
                occ_jid = info.get("jid")
                if not occ_jid:
                    continue

                bare = self.bare_jid(occ_jid)
                if not bare or "@" not in bare:
                    continue

                occ_domain = bare.split("@", 1)[1].lower()
                if not domain_matches(occ_domain, domain):
                    continue

                # Exact JID ignorelist entries protect that user from all bans.
                if self.is_ignored_jid(bare):
                    log.debug("RTBL: Ignoring matched JID %s for domain *.%s", bare, domain)
                    continue

                # More specific ignored subdomains should also be respected.
                if self.is_ignored_domain(occ_domain):
                    log.debug("RTBL: Ignoring matched domain %s for *.%s", occ_domain, domain)
                    continue

                protected, protect_reason = await self.is_protected_admin_target(
                    bare, nick=occ_nick, jid=bare
                )
                if protected:
                    skipped_protected.append((bare, occ_nick, protect_reason))
                    log.warning(
                        "RTBL: Ignoring domain match %s for *.%s — admin/owner protected: %s",
                        bare, domain, protect_reason,
                    )
                    continue

                matched.setdefault(bare, (occ_nick, set()))[1].add(room)

        if not matched:
            if skipped_protected and self.rtbl_announce:
                preview = ", ".join(
                    f"{nick or bare} ({bare})" if nick else bare
                    for bare, nick, _reason in skipped_protected[:5]
                )
                if len(skipped_protected) > 5:
                    preview += f", … +{len(skipped_protected) - 5} more"
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⚠️ RTBL: Ignored domain ban {wildcard} — "
                        f"only protected admin/owner matches found: {preview}"
                    ),
                    mtype="groupchat",
                )
            return

        # Remove legacy persisted wildcard RTBL bans from older versions. RTBL
        # domain entries are now represented locally as concrete JID bans.
        await self.db.execute(
            "DELETE FROM bans WHERE issuer = 'rtbl' AND target_type = 'domain' AND target = ?",
            (domain,),
        )
        if hasattr(self, "_remove_domain_bans_from_cache"):
            self._remove_domain_bans_from_cache(domain)

        for bare, (matched_nick, _rooms) in matched.items():
            await self.upsert_ban_db(
                jid=bare, nick=matched_nick, until=0, issuer="rtbl", comment=comment
            )
        await self.db.commit()
        log.debug(
            "RTBL: Persisted %d concrete JID ban(s) for domain *.%s",
            len(matched), domain,
        )

        if self.rtbl_announce:
            first_bare, (first_nick, _rooms) = next(iter(matched.items()))
            affected = f"\n   Matched: {first_nick} ({first_bare})" if first_nick else f"\n   Matched: {first_bare}"
            if len(matched) > 1:
                affected += f"\n   Also matched: {len(matched) - 1} more occupant(s)"

            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ RTBL: Domain ban {wildcard}"
                    f"{affected}"
                    + (f"\n   Reason: {reason}" if reason else "")
                ),
                mtype="groupchat",
            )

        for bare, (matched_nick, _rooms) in matched.items():
            self.log_event(
                logging.INFO, "rtbl_ban_applied",
                actor="rtbl", identifier=wildcard, target_type="jid",
                target=bare, jid=bare, nick=matched_nick, comment=comment,
            )
            await self.audit_event(
                "rtbl_ban_applied", actor="rtbl", target_type="jid",
                target=bare, jid=bare, nick=matched_nick, comment=comment,
                details={"source_type": "domain", "source": wildcard},
            )

            for room in self.protected_rooms:
                try:
                    await self.apply_ban_to_room(
                        room, bare, matched_nick, comment, issuer="rtbl"
                    )
                except Exception as e:
                    log.warning("RTBL: Failed to apply domain-derived JID ban in %s: %s", room, e)


    async def _rtbl_ban_is_still_covered(self, jid: str | None) -> bool:
        """Return True if a persisted RTBL JID ban is still covered by active RTBL caches."""
        if not jid:
            return False

        bare = self.bare_jid(jid)
        if not bare or bare.startswith("*."):
            return False

        if self._rtbl_hash_jid(bare) in getattr(self, "rtbl_hash_cache", {}):
            return True

        if "@" in bare:
            user_domain = bare.split("@", 1)[1].lower()
            for banned_domain in getattr(self, "rtbl_domain_cache", {}):
                if domain_matches(user_domain, banned_domain):
                    return True

        return False


    async def _rtbl_cleanup_stale_persisted_bans(self, issuer: str = "rtbl_cleanup") -> int:
        """Remove stale RTBL bans while holding the shared ban-state lock."""
        async with ban_state_lock(self):
            return await self._rtbl_cleanup_stale_persisted_bans_locked(issuer=issuer)

    async def _rtbl_cleanup_stale_persisted_bans_locked(self, issuer: str = "rtbl_cleanup") -> int:
        """
        Remove persisted issuer=rtbl bans that are no longer backed by active RTBL caches.

        This is called after RTBL retractions or subscription deletion. It also
        removes legacy wildcard-domain RTBL bans because domain RTBL matches are
        now stored locally as concrete JID bans.
        """
        async with self.db.execute(
            "SELECT jid FROM bans WHERE issuer = 'rtbl'"
        ) as cursor:
            rows = [row[0] for row in await cursor.fetchall()]

        removed = 0
        for banned_jid in rows:
            if not banned_jid:
                continue

            if banned_jid.startswith("*."):
                await self.unban_all(banned_jid, issuer=issuer)
                removed += 1
                continue

            if not await self._rtbl_ban_is_still_covered(banned_jid):
                await self.unban_all(banned_jid, issuer=issuer)
                removed += 1

        if removed:
            log.info("RTBL: Removed %d stale persisted RTBL ban(s)", removed)

        return removed
