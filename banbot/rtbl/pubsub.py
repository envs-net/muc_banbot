"""Inbound RTBL PubSub subscribe/fetch/event/refresh handling."""

import asyncio
import logging
import time

from slixmpp.exceptions import IqError, IqTimeout

from ..locks import is_maintenance_mode
from .utils import RTBL_PUBLISH_SANITY_CHECK_REASON, _is_domain, _is_sha256
from ..task_supervisor import sleep_with_heartbeat

log = logging.getLogger(__name__)


class RtblPubSubMixin:
    def _rtbl_is_own_publish_sanity_item(self, service_jid: str, node: str, reason: str | None) -> bool:
        """Return True for temporary sanity-check items from our own publish nodes."""
        if reason != RTBL_PUBLISH_SANITY_CHECK_REASON:
            return False

        is_own_publish_node = getattr(self, "_rtbl_is_own_publish_node", None)
        if not callable(is_own_publish_node):
            return False

        return bool(is_own_publish_node(service_jid, node))

    async def _rtbl_subscribe_node(self, service_jid: str, node: str) -> tuple[bool, str | None]:
        """Subscribe to a PubSub node and return a user-facing error on failure."""
        try:
            await self.plugin["xep_0060"].subscribe(service_jid, node)
            log.info("RTBL: Subscribed to '%s' @ %s", node, service_jid)
            return True, None
        except IqTimeout:
            msg = f"timeout while subscribing to '{node}' @ {service_jid}"
            log.warning("RTBL: Could not subscribe: %s", msg)
            return False, msg
        except IqError as e:
            msg = f"subscription failed for '{node}' @ {service_jid}: {e}"
            log.warning("RTBL: %s", msg)
            return False, msg
        except Exception as e:
            msg = f"unexpected error while subscribing to '{node}' @ {service_jid}: {e}"
            log.warning("RTBL: %s", msg)
            return False, msg

    async def _rtbl_subscribe_and_fetch(self, service_jid: str, node: str) -> None:
        """Subscribe to a PubSub node and fetch all existing items."""
        ok, _error = await self._rtbl_subscribe_node(service_jid, node)
        if not ok:
            return

        await self._rtbl_fetch_all_items(service_jid, node)

    async def _rtbl_fetch_all_items(
        self,
        service_jid: str,
        node: str,
        scan_occupants: bool = True,
    ) -> bool:
        """
        Fetch all current items from an RTBL node using manual RSM pagination
        (XEP-0059) via fully raw XML IQ construction.

        RTBL nodes are treated as active snapshots:
        - items present in the successful fetch stay active
        - items missing from the successful fetch are removed locally
        - stale persisted issuer=rtbl bans are unbanned afterwards

        If the fetch fails, times out, returns malformed data, or pagination
        aborts unexpectedly, stale-entry cleanup is skipped to avoid deleting
        valid local RTBL state based on an incomplete refresh.

        If scan_occupants is True, all currently known occupants are checked
        against the rebuilt RTBL caches after the fetch. This makes freshly
        added subscriptions and startup fetches effective immediately without
        waiting for users to rejoin or for a manual sync. Periodic refresh uses
        scan_occupants=False to avoid noisy re-application when nothing changed.

        Returns True only when the full snapshot was fetched successfully.
        Callers can use the return value to report manual refresh failures
        without raising for transient remote PubSub errors.
        """
        from xml.etree import ElementTree as ET

        from config import ADMIN_ROOM

        _PUBSUB = "http://jabber.org/protocol/pubsub"
        _RSM = "http://jabber.org/protocol/rsm"

        hash_count = 0
        domain_count = 0
        new_hash_count = 0
        new_domain_count = 0
        updated_hash_count = 0
        updated_domain_count = 0
        removed_hash_count = 0
        removed_domain_count = 0
        removed_stale_bans = 0

        seen_hashes: set[str] = set()
        seen_domains: set[str] = set()

        page = 0
        last_id = None
        page_size = 200
        seen_rsm_last_ids: set[str] = set()

        fetch_successful = False
        fetch_failed = False

        status_key = (service_jid.lower(), node)
        fetch_error: str | None = None

        while True:
            try:
                iq = self.make_iq_get(ito=service_jid)

                pubsub_el = ET.Element(f"{{{_PUBSUB}}}pubsub")
                items_el = ET.SubElement(pubsub_el, f"{{{_PUBSUB}}}items")
                items_el.set("node", node)

                rsm_set = ET.SubElement(pubsub_el, f"{{{_RSM}}}set")
                max_el = ET.SubElement(rsm_set, f"{{{_RSM}}}max")
                max_el.text = str(page_size)

                if last_id is not None:
                    after_el = ET.SubElement(rsm_set, f"{{{_RSM}}}after")
                    after_el.text = last_id

                # Replace generated IQ children with our raw PubSub/RSM payload.
                for child in list(iq.xml):
                    iq.xml.remove(child)
                iq.xml.append(pubsub_el)

                result = await iq.send()

                result_pubsub = result.xml.find(f"{{{_PUBSUB}}}pubsub")
                if result_pubsub is None:
                    fetch_failed = True
                    fetch_error = "missing pubsub element"
                    log.warning(
                        "RTBL: Invalid fetch response from '%s' @ %s on page %d: missing pubsub element",
                        node,
                        service_jid,
                        page,
                    )
                    break

                result_items_el = result_pubsub.find(f"{{{_PUBSUB}}}items")
                if result_items_el is None:
                    fetch_failed = True
                    fetch_error = "missing items element"
                    log.warning(
                        "RTBL: Invalid fetch response from '%s' @ %s on page %d: missing items element",
                        node,
                        service_jid,
                        page,
                    )
                    break

                fetch_successful = True
                items = list(result_items_el)

            except (IqError, IqTimeout) as e:
                fetch_failed = True
                fetch_error = str(e)
                log.warning(
                    "RTBL: Could not fetch items from '%s' @ %s (page %d): %s",
                    node,
                    service_jid,
                    page,
                    e,
                )
                break

            if not items:
                break

            for item_el in items:
                item_id = item_el.get("id", "").lower().strip()
                if not item_id:
                    continue

                payload = item_el[0] if len(item_el) > 0 else None
                reason = self._rtbl_extract_reason(payload)
                if self._rtbl_is_own_publish_sanity_item(service_jid, node, reason):
                    log.debug(
                        "RTBL: Skipping own publish sanity-check item '%s' from %s/%s",
                        item_id,
                        service_jid,
                        node,
                    )
                    continue

                if _is_sha256(item_id):
                    seen_hashes.add(item_id)

                    async with self.db.execute(
                        """
                        SELECT reason FROM rtbl_hashes
                        WHERE hash = ? AND service_jid = ? AND node = ?
                        """,
                        (item_id, service_jid, node),
                    ) as cursor:
                        existing = await cursor.fetchone()

                    if existing is None:
                        new_hash_count += 1
                    elif existing[0] != reason:
                        updated_hash_count += 1

                    await self.db.execute(
                        """
                        INSERT INTO rtbl_hashes (hash, service_jid, node, reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(hash, service_jid, node)
                        DO UPDATE SET reason = excluded.reason
                        """,
                        (item_id, service_jid, node, reason),
                    )
                    hash_count += 1

                elif _is_domain(item_id):
                    domain = item_id.lstrip("*.").lower()
                    seen_domains.add(domain)

                    async with self.db.execute(
                        """
                        SELECT reason FROM rtbl_domains
                        WHERE domain = ? AND service_jid = ? AND node = ?
                        """,
                        (domain, service_jid, node),
                    ) as cursor:
                        existing = await cursor.fetchone()

                    if existing is None:
                        new_domain_count += 1
                    elif existing[0] != reason:
                        updated_domain_count += 1

                    await self.db.execute(
                        """
                        INSERT INTO rtbl_domains (domain, service_jid, node, reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(domain, service_jid, node)
                        DO UPDATE SET reason = excluded.reason
                        """,
                        (domain, service_jid, node, reason),
                    )
                    domain_count += 1

                else:
                    log.debug(
                        "RTBL: Skipping unrecognised item ID '%s' from %s/%s",
                        item_id,
                        service_jid,
                        node,
                    )

            await self.db.commit()

            page += 1
            last_id = items[-1].get("id") if items else None

            log.debug(
                "RTBL: Page %d — %d hashes, %d domains so far (last: %s…)",
                page,
                hash_count,
                domain_count,
                last_id[:12] if last_id else "none",
            )

            rsm_el = result.xml.find(f".//{{{_RSM}}}set")
            rsm_last = None
            if rsm_el is not None:
                last_el = rsm_el.find(f"{{{_RSM}}}last")
                rsm_last = last_el.text.strip() if last_el is not None and last_el.text else None
                log.debug("RTBL: RSM last=%s", rsm_last)

            if not rsm_last:
                if len(items) >= page_size:
                    fetch_failed = True
                    fetch_error = "snapshot may be incomplete: missing RSM continuation"
                    log.warning(
                        (
                            "RTBL: Fetch for '%s' @ %s returned %d items without RSM continuation; "
                            "treating snapshot as potentially incomplete and skipping stale cleanup"
                        ),
                        node,
                        service_jid,
                        len(items),
                    )
                break

            if len(items) < page_size:
                break

            # Safety guard: if a server ignores RSM 'after' and repeats the same page,
            # do not loop forever and do not run stale cleanup from an incomplete fetch.
            if rsm_last == last_id or rsm_last in seen_rsm_last_ids:
                fetch_failed = True
                fetch_error = f"pagination loop: repeated RSM last={rsm_last}"
                log.warning(
                    "RTBL: Pagination loop while fetching '%s' @ %s; repeated RSM last=%s",
                    node,
                    service_jid,
                    rsm_last,
                )
                break

            seen_rsm_last_ids.add(rsm_last)
            last_id = rsm_last

        if fetch_successful and not fetch_failed:
            async with self.db.execute(
                """
                SELECT hash FROM rtbl_hashes
                WHERE service_jid = ? AND node = ?
                """,
                (service_jid, node),
            ) as cursor:
                existing_hashes = {row[0] for row in await cursor.fetchall()}

            stale_hashes = existing_hashes - seen_hashes
            for stale_hash in stale_hashes:
                await self.db.execute(
                    """
                    DELETE FROM rtbl_hashes
                    WHERE hash = ? AND service_jid = ? AND node = ?
                    """,
                    (stale_hash, service_jid, node),
                )
                removed_hash_count += 1

            async with self.db.execute(
                """
                SELECT domain FROM rtbl_domains
                WHERE service_jid = ? AND node = ?
                """,
                (service_jid, node),
            ) as cursor:
                existing_domains = {row[0] for row in await cursor.fetchall()}

            stale_domains = existing_domains - seen_domains
            for stale_domain in stale_domains:
                await self.db.execute(
                    """
                    DELETE FROM rtbl_domains
                    WHERE domain = ? AND service_jid = ? AND node = ?
                    """,
                    (stale_domain, service_jid, node),
                )
                removed_domain_count += 1

            if removed_hash_count or removed_domain_count:
                await self.db.commit()
                log.info(
                    (
                        "RTBL: Snapshot reconciliation for '%s' @ %s — "
                        "removed %d stale hashes and %d stale domains"
                    ),
                    node,
                    service_jid,
                    removed_hash_count,
                    removed_domain_count,
                )
        else:
            log.warning(
                "RTBL: Skipping stale-entry cleanup for '%s' @ %s because fetch was not successful",
                node,
                service_jid,
            )

        await self._rebuild_rtbl_caches()

        if removed_hash_count or removed_domain_count:
            removed_stale_bans = await self._rtbl_cleanup_stale_persisted_bans(
                issuer="rtbl_refresh"
            )

        changed_count = (
            new_hash_count
            + new_domain_count
            + updated_hash_count
            + updated_domain_count
            + removed_hash_count
            + removed_domain_count
            + removed_stale_bans
        )

        if fetch_successful and not fetch_failed:
            self.rtbl_last_fetch[status_key] = time.time()
            self.rtbl_last_counts[status_key] = (hash_count, domain_count)
            self.rtbl_last_error[status_key] = None

            if changed_count > 0:
                self.rtbl_last_change[status_key] = time.time()
        else:
            self.rtbl_last_error[status_key] = fetch_error or "fetch was not successful"

        log_msg = (
            "RTBL: Fetched from '%s' @ %s — %d hashes, %d domains (%d pages; "
            "%d new hashes, %d new domains, %d updated hashes, %d updated domains, "
            "%d removed hashes, %d removed domains, %d stale bans unbanned)"
        )
        log_args = (
            node,
            service_jid,
            hash_count,
            domain_count,
            page,
            new_hash_count,
            new_domain_count,
            updated_hash_count,
            updated_domain_count,
            removed_hash_count,
            removed_domain_count,
            removed_stale_bans,
        )

        if changed_count > 0:
            log.info(log_msg, *log_args)
        else:
            log.debug(log_msg, *log_args)

        # Only announce refresh results when something actually changed.
        # This keeps the hourly/default periodic refresh quiet when there are
        # no RTBL changes.
        if self.rtbl_announce and changed_count > 0:
            parts = []

            if new_hash_count:
                parts.append(f"{new_hash_count} new JID hashes")
            if new_domain_count:
                parts.append(f"{new_domain_count} new domain bans")
            if updated_hash_count:
                parts.append(f"{updated_hash_count} updated JID hashes")
            if updated_domain_count:
                parts.append(f"{updated_domain_count} updated domain bans")
            if removed_hash_count:
                parts.append(f"{removed_hash_count} removed JID hashes")
            if removed_domain_count:
                parts.append(f"{removed_domain_count} removed domain bans")
            if removed_stale_bans:
                parts.append(f"{removed_stale_bans} stale RTBL bans unbanned")

            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ RTBL: Updates from {service_jid} (node: {node}) — "
                    + ", ".join(parts)
                ),
                mtype="groupchat",
            )

        if scan_occupants and (hash_count + domain_count) > 0:
            await self._rtbl_check_all_occupants_against_caches(
                source=f"{service_jid}/{node}"
            )

        return fetch_successful and not fetch_failed

    async def _on_rtbl_publish(self, msg) -> None:
        """
        Handle an incoming PubSub publish event.

        Only processes events from nodes we are subscribed to. Classifies each item
        and stores/applies it accordingly.
        """
        if not getattr(self, "rtbl_enabled", False):
            return

        try:
            node = msg["pubsub_event"]["items"]["node"]
            service_jid = msg["from"].bare
        except Exception:
            return

        subscribed = {(s.lower(), n) for s, n in self.rtbl_subscriptions}
        if (service_jid.lower(), node) not in subscribed:
            return

        status_key = (service_jid.lower(), node)

        for item in msg["pubsub_event"]["items"]:
            try:
                item_id = item["id"].lower().strip()
            except Exception:
                continue

            if not item_id:
                continue

            reason = self._rtbl_extract_reason(item.get("payload"))
            if self._rtbl_is_own_publish_sanity_item(service_jid, node, reason):
                log.debug(
                    "RTBL: Ignoring own publish sanity-check event '%s' from %s/%s",
                    item_id,
                    service_jid,
                    node,
                )
                continue

            if _is_sha256(item_id):
                await self.db.execute(
                    """
                    INSERT INTO rtbl_hashes (hash, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(hash, service_jid, node)
                    DO UPDATE SET reason = excluded.reason
                    """,
                    (item_id, service_jid, node, reason),
                )
                await self.db.commit()

                self.rtbl_last_change[status_key] = time.time()
                self.rtbl_last_error[status_key] = None

                self.rtbl_hash_cache[item_id] = reason

                log.info(
                    "RTBL: New JID hash from %s/%s: %s… (reason: %s)",
                    service_jid,
                    node,
                    item_id[:12],
                    reason,
                )

                await self._rtbl_check_all_occupants_for_hash(item_id, reason)

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.").lower()

                await self.db.execute(
                    """
                    INSERT INTO rtbl_domains (domain, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(domain, service_jid, node)
                    DO UPDATE SET reason = excluded.reason
                    """,
                    (domain, service_jid, node, reason),
                )
                await self.db.commit()

                self.rtbl_last_change[status_key] = time.time()
                self.rtbl_last_error[status_key] = None

                self.rtbl_domain_cache[domain] = reason

                log.info(
                    "RTBL: New domain ban from %s/%s: *.%s (reason: %s)",
                    service_jid,
                    node,
                    domain,
                    reason,
                )

                await self._rtbl_check_all_occupants_for_domain(domain, reason)

            else:
                log.debug(
                    "RTBL: Skipping unrecognised item '%s' from %s/%s",
                    item_id,
                    service_jid,
                    node,
                )

    async def _on_rtbl_retract(self, msg) -> None:
        """
        Handle a PubSub retract event.

        Removes the item from DB and from the in-memory cache, but only if no
        other subscription still references the same entry.
        """
        if not getattr(self, "rtbl_enabled", False):
            return

        try:
            node = msg["pubsub_event"]["items"]["node"]
            service_jid = msg["from"].bare
        except Exception:
            return

        subscribed = {(s.lower(), n) for s, n in self.rtbl_subscriptions}
        if (service_jid.lower(), node) not in subscribed:
            return

        status_key = (service_jid.lower(), node)

        for item in msg["pubsub_event"]["items"]:
            try:
                item_id = item["id"].lower().strip()
            except Exception:
                continue

            if not item_id:
                continue

            if _is_sha256(item_id):
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE hash = ? AND service_jid = ? AND node = ?",
                    (item_id, service_jid, node),
                )
                await self.db.commit()

                self.rtbl_last_change[status_key] = time.time()
                self.rtbl_last_error[status_key] = None

                async with self.db.execute(
                    "SELECT 1 FROM rtbl_hashes WHERE hash = ? LIMIT 1",
                    (item_id,),
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_hash_cache.pop(item_id, None)

                await self._rtbl_cleanup_stale_persisted_bans(issuer="rtbl_retract")

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.").lower()

                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE domain = ? AND service_jid = ? AND node = ?",
                    (domain, service_jid, node),
                )
                await self.db.commit()

                self.rtbl_last_change[status_key] = time.time()
                self.rtbl_last_error[status_key] = None

                async with self.db.execute(
                    "SELECT 1 FROM rtbl_domains WHERE domain = ? LIMIT 1",
                    (domain,),
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_domain_cache.pop(domain, None)

                await self._rtbl_cleanup_stale_persisted_bans(issuer="rtbl_retract")

    async def _rtbl_refresh_worker(self) -> None:
        """
        Periodically re-fetch all items from every subscribed RTBL node.

        Interval is controlled by RTBL_REFRESH_INTERVAL (seconds, 0 = disabled).
        Acts as a fallback for missed PubSub events.
        """
        while True:
            interval = getattr(self, "rtbl_refresh_interval", 3600)
            if interval <= 0:
                await sleep_with_heartbeat(
                    self,
                    "rtbl-refresh-worker",
                    60,
                    sleep_func=asyncio.sleep,
                )  # check again in 60s in case config changes
                continue

            await sleep_with_heartbeat(
                self,
                "rtbl-refresh-worker",
                interval,
                sleep_func=asyncio.sleep,
            )

            if is_maintenance_mode(self):
                log.debug("RTBL: periodic refresh skipped while maintenance operation is active")
                continue

            if not getattr(self, "rtbl_enabled", False):
                continue

            log.info("RTBL: Starting periodic refresh (%ds interval)", interval)

            for service_jid, node in list(self.rtbl_subscriptions):
                alert_key = f"rtbl_refresh:{str(service_jid).lower()}:{node}"
                try:
                    await self._rtbl_fetch_all_items(
                        service_jid,
                        node,
                        scan_occupants=False,
                    )
                except Exception as e:
                    log.warning(
                        "RTBL: Periodic refresh failed for '%s' @ %s: %s",
                        node,
                        service_jid,
                        e,
                    )
                    await self.record_alert_failure(
                        alert_key,
                        "RTBL periodic refresh failed",
                        f"RTBL refresh failed for {node} @ {service_jid}: {e}",
                        enabled=bool(getattr(self, "alert_on_rtbl_refresh_failures", 3)),
                        threshold=int(getattr(self, "alert_on_rtbl_refresh_failures", 3) or 1),
                        details={"service_jid": service_jid, "node": node, "error": str(e)},
                    )
                else:
                    self.record_alert_success(alert_key)

            log.info("RTBL: Periodic refresh complete")
