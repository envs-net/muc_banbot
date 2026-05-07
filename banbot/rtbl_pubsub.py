"""Inbound RTBL PubSub subscribe/fetch/event/refresh handling."""

import asyncio
import logging

from slixmpp.exceptions import IqError, IqTimeout

from .rtbl_utils import _is_domain, _is_sha256

log = logging.getLogger(__name__)


class RtblPubSubMixin:
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
    ) -> None:
        """
        Fetch all current items from an RTBL node using manual RSM pagination
        (XEP-0059) via fully raw XML IQ construction.
        Items are classified as JID hashes or domain bans by their ID format.
        Unknown formats are skipped with a debug log entry.

        If scan_occupants is True, all currently known occupants are checked
        against the rebuilt RTBL caches after the fetch. This makes freshly
        added subscriptions and startup fetches effective immediately without
        waiting for users to rejoin or for a manual sync. Periodic refresh uses
        scan_occupants=False to avoid noisy re-application when nothing changed.
        """
        from config import ADMIN_ROOM
        from xml.etree import ElementTree as ET

        _PUBSUB = "http://jabber.org/protocol/pubsub"
        _RSM = "http://jabber.org/protocol/rsm"

        hash_count = 0
        domain_count = 0
        new_hash_count = 0
        new_domain_count = 0
        updated_hash_count = 0
        updated_domain_count = 0
        page = 0
        last_id = None
        page_size = 200

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

                for child in list(iq.xml):
                    iq.xml.remove(child)
                iq.xml.append(pubsub_el)

                result = await iq.send()

                result_pubsub = result.xml.find(f"{{{_PUBSUB}}}pubsub")
                if result_pubsub is None:
                    break
                result_items_el = result_pubsub.find(f"{{{_PUBSUB}}}items")
                items = list(result_items_el) if result_items_el is not None else []

            except (IqError, IqTimeout) as e:
                log.warning(
                    "RTBL: Could not fetch items from '%s' @ %s (page %d): %s",
                    node, service_jid, page, e,
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

                if _is_sha256(item_id):
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
                        ON CONFLICT(hash, service_jid, node) DO UPDATE SET
                            reason = excluded.reason
                        """,
                        (item_id, service_jid, node, reason),
                    )
                    hash_count += 1

                elif _is_domain(item_id):
                    domain = item_id.lstrip("*.")
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
                        ON CONFLICT(domain, service_jid, node) DO UPDATE SET
                            reason = excluded.reason
                        """,
                        (domain, service_jid, node, reason),
                    )
                    domain_count += 1

                else:
                    log.debug(
                        "RTBL: Skipping unrecognised item ID '%s' from %s/%s",
                        item_id, service_jid, node,
                    )

            await self.db.commit()
            page += 1
            last_id = items[-1].get("id") if items else None

            log.debug(
                "RTBL: Page %d — %d hashes, %d domains so far (last: %s…)",
                page, hash_count, domain_count,
                last_id[:12] if last_id else "none",
            )

            rsm_el = result.xml.find(f".//{{{_RSM}}}set")
            rsm_last = None
            if rsm_el is not None:
                last_el = rsm_el.find(f"{{{_RSM}}}last")
                rsm_last = last_el.text.strip() if last_el is not None and last_el.text else None

            log.debug("RTBL: RSM last=%s", rsm_last)

            if not rsm_last or len(items) < page_size:
                break

        await self._rebuild_rtbl_caches()

        changed_count = (
            new_hash_count
            + new_domain_count
            + updated_hash_count
            + updated_domain_count
        )

        log.info(
            (
                "RTBL: Fetched from '%s' @ %s — %d hashes, %d domains (%d pages; "
                "%d new hashes, %d new domains, %d updated hashes, %d updated domains)"
            ),
            node, service_jid, hash_count, domain_count, page,
            new_hash_count, new_domain_count, updated_hash_count, updated_domain_count,
        )

        # Only announce refresh results when something actually changed.
        # This keeps the hourly/default periodic refresh quiet when there are
        # no new RTBL entries or reason updates.
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

            self.send_message(
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


    async def _on_rtbl_publish(self, msg) -> None:
        """
        Handle an incoming PubSub publish event.
        Only processes events from nodes we are subscribed to.
        Classifies each item and stores/applies it accordingly.
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

        for item in msg["pubsub_event"]["items"]:
            try:
                item_id = item["id"].lower().strip()
            except Exception:
                continue
            if not item_id:
                continue

            reason = self._rtbl_extract_reason(item.get("payload"))

            if _is_sha256(item_id):
                await self.db.execute(
                    """
                    INSERT INTO rtbl_hashes (hash, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(hash, service_jid, node) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (item_id, service_jid, node, reason),
                )
                await self.db.commit()
                self.rtbl_hash_cache[item_id] = reason
                log.info(
                    "RTBL: New JID hash from %s/%s: %s… (reason: %s)",
                    service_jid, node, item_id[:12], reason,
                )
                await self._rtbl_check_all_occupants_for_hash(item_id, reason)

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.")
                await self.db.execute(
                    """
                    INSERT INTO rtbl_domains (domain, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(domain, service_jid, node) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (domain, service_jid, node, reason),
                )
                await self.db.commit()
                self.rtbl_domain_cache[domain] = reason
                log.info(
                    "RTBL: New domain ban from %s/%s: *.%s (reason: %s)",
                    service_jid, node, domain, reason,
                )
                await self._rtbl_check_all_occupants_for_domain(domain, reason)

            else:
                log.debug(
                    "RTBL: Skipping unrecognised item '%s' from %s/%s",
                    item_id, service_jid, node,
                )


    async def _on_rtbl_retract(self, msg) -> None:
        """
        Handle a PubSub retract event.
        Removes the item from DB and from the in-memory cache, but only if
        no other subscription still references the same entry.
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
                async with self.db.execute(
                    "SELECT 1 FROM rtbl_hashes WHERE hash = ? LIMIT 1", (item_id,)
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_hash_cache.pop(item_id, None)
                        async with self.db.execute(
                            "SELECT jid FROM bans WHERE issuer = 'rtbl'"
                        ) as c:
                            async for (banned_jid,) in c:
                                if banned_jid and self._rtbl_hash_jid(banned_jid) == item_id:
                                    await self.unban_all(banned_jid, issuer="rtbl_retract")
                                    break

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.")
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE domain = ? AND service_jid = ? AND node = ?",
                    (domain, service_jid, node),
                )
                await self.db.commit()
                async with self.db.execute(
                    "SELECT 1 FROM rtbl_domains WHERE domain = ? LIMIT 1", (domain,)
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_domain_cache.pop(domain, None)
                        await self.unban_all(f"*.{domain}", issuer="rtbl_retract")


    async def _rtbl_refresh_worker(self) -> None:
        """
        Periodically re-fetch all items from every subscribed RTBL node.
        Interval is controlled by RTBL_REFRESH_INTERVAL (seconds, 0 = disabled).
        Acts as a fallback for missed PubSub events.
        """
        while True:
            interval = getattr(self, "rtbl_refresh_interval", 3600)
            if interval <= 0:
                await asyncio.sleep(60)  # check again in 60s in case config changes
                continue

            await asyncio.sleep(interval)

            if not getattr(self, "rtbl_enabled", False):
                continue

            log.info("RTBL: Starting periodic refresh (%ds interval)", interval)
            for service_jid, node in list(self.rtbl_subscriptions):
                try:
                    await self._rtbl_fetch_all_items(
                        service_jid, node, scan_occupants=False
                    )
                except Exception as e:
                    log.warning(
                        "RTBL: Periodic refresh failed for '%s' @ %s: %s",
                        node, service_jid, e,
                    )
            log.info("RTBL: Periodic refresh complete")
