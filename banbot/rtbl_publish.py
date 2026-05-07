"""Own outbound RTBL publish-feed setup and item publish/retract helpers."""

import logging

from slixmpp.exceptions import IqError, IqTimeout

log = logging.getLogger(__name__)


class RtblPublishMixin:
    def _rtbl_is_own_publish_node(self, service_jid: str, node: str) -> bool:
        """Return True if service/node is one of our own RTBL publish nodes."""
        if not getattr(self, "rtbl_publish_enabled", False):
            return False

        publish_service = (getattr(self, "rtbl_publish_service", "") or "").strip().lower()
        if not publish_service or service_jid.strip().lower() != publish_service:
            return False

        publish_nodes = {
            (getattr(self, "rtbl_publish_jid_node", "") or "").strip(),
            (getattr(self, "rtbl_publish_domain_node", "") or "").strip(),
        }
        publish_nodes.discard("")

        return node.strip() in publish_nodes


    async def _rtbl_count_active_publish_bans(self) -> tuple[int, int]:
        """Return the number of active local bans mirrored into the own publish feed."""
        async with self.db.execute(
            """SELECT COUNT(*) FROM bans
               WHERE target_type = 'jid' AND jid IS NOT NULL
                 AND (until = 0 OR until > strftime('%s','now'))
                 AND COALESCE(issuer, '') != 'rtbl'"""
        ) as cursor:
            row = await cursor.fetchone()
            jid_count = int(row[0] or 0) if row else 0

        async with self.db.execute(
            """SELECT COUNT(*) FROM bans
               WHERE target_type = 'domain'
                 AND (until = 0 OR until > strftime('%s','now'))
                 AND COALESCE(issuer, '') != 'rtbl'"""
        ) as cursor:
            row = await cursor.fetchone()
            domain_count = int(row[0] or 0) if row else 0

        return jid_count, domain_count


    async def setup_rtbl_publish(self) -> None:
        """
        Create/configure own JID and domain publish nodes when possible, then
        perform an initial sync of all active bans.
        Called from start() after setup_rtbl().
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        from config import ADMIN_ROOM

        jid_node_ready = await self._rtbl_ensure_node(
            self.rtbl_publish_service,
            self.rtbl_publish_jid_node,
        )
        domain_node_ready = await self._rtbl_ensure_node(
            self.rtbl_publish_service,
            self.rtbl_publish_domain_node,
        )

        if not jid_node_ready or not domain_node_ready:
            log.warning(
                "RTBL Publish: One or more publish nodes are not ready on %s "
                "(jid_node_ready=%s, domain_node_ready=%s)",
                self.rtbl_publish_service,
                jid_node_ready,
                domain_node_ready,
            )

        jid_count, domain_count, jid_failures, domain_failures = await self._rtbl_sync_all_bans_to_nodes()
        log.info(
            (
                "RTBL Publish: Initial sync complete — %d JID hashes published, "
                "%d domain bans published, %d JID failures, %d domain failures"
            ),
            jid_count,
            domain_count,
            jid_failures,
            domain_failures,
        )

        if self.rtbl_announce:
            status_emoji = "✅" if jid_failures == 0 and domain_failures == 0 else "⚠️"
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"{status_emoji} RTBL Publish: Nodes ready on {self.rtbl_publish_service} — "
                    f"{jid_count} JID hashes ({self.rtbl_publish_jid_node}), "
                    f"{domain_count} domain bans ({self.rtbl_publish_domain_node})"
                    + (
                        f"\nFailures: {jid_failures} JID hashes, {domain_failures} domain bans."
                        if jid_failures or domain_failures else ""
                    )
                ),
                mtype="groupchat",
            )


    async def _rtbl_node_exists(self, service: str, node: str) -> bool:
        """Return True if a PubSub node exists and is reachable."""
        try:
            await self.plugin["xep_0060"].get_node_config(service, node)
            return True
        except IqError as e:
            if "item-not-found" in str(e).lower() or "nodeid-required" in str(e).lower():
                return False
            # Some services may forbid config reads. Fall back to a cheap item fetch.
            try:
                await self.plugin["xep_0060"].get_items(service, node, max_items=1)
                return True
            except Exception:
                log.debug("RTBL Publish: Could not verify node '%s' on %s: %s", node, service, e)
                return False
        except IqTimeout:
            return False
        except Exception as e:
            log.debug("RTBL Publish: Node existence check failed for '%s' on %s: %s", node, service, e)
            return False


    def _rtbl_make_node_config_form(self):
        """Build a XEP-0004 submit form for RTBL publish node configuration."""
        if "xep_0004" not in self.plugin:
            self.register_plugin("xep_0004")

        form = self.plugin["xep_0004"].make_form(ftype="submit")
        form.add_field(var="FORM_TYPE", ftype="hidden", value="http://jabber.org/protocol/pubsub#node_config")
        form.add_field(var="pubsub#access_model", value="open")
        form.add_field(var="pubsub#publish_model", value="open")
        form.add_field(var="pubsub#persist_items", value="1")
        form.add_field(var="pubsub#max_items", value="1000")
        form.add_field(var="pubsub#send_last_published_item", value="never")
        form.add_field(var="pubsub#deliver_payloads", value="1")
        return form


    async def _rtbl_ensure_node(self, service: str, node: str) -> bool:
        """Create a PubSub node if needed and configure it when permitted."""
        node_exists = await self._rtbl_node_exists(service, node)

        if node_exists:
            log.info("RTBL Publish: Node '%s' already exists on %s", node, service)
        else:
            try:
                await self.plugin["xep_0060"].create_node(service, node)
                node_exists = True
                log.info("RTBL Publish: Created node '%s' on %s", node, service)
            except IqError as e:
                if "conflict" in str(e).lower():
                    node_exists = True
                    log.info("RTBL Publish: Node '%s' already exists on %s", node, service)
                else:
                    if await self._rtbl_node_exists(service, node):
                        node_exists = True
                        log.info(
                            "RTBL Publish: Node '%s' exists on %s, but create was rejected; continuing",
                            node,
                            service,
                        )
                    else:
                        log.warning("RTBL Publish: Could not create node '%s': %s", node, e)
                        return False
            except IqTimeout as e:
                log.warning("RTBL Publish: Timeout creating node '%s': %s", node, e)
                return False

        try:
            await self.plugin["xep_0060"].set_node_config(
                service,
                node,
                config=self._rtbl_make_node_config_form(),
            )
            log.info("RTBL Publish: Node '%s' configured", node)
        except IqError as e:
            if "forbidden" in str(e).lower():
                log.info(
                    "RTBL Publish: Node '%s' exists but cannot be configured; using existing node config.",
                    node,
                )
            else:
                log.warning("RTBL Publish: Could not configure node '%s': %s", node, e)
        except IqTimeout as e:
            log.warning("RTBL Publish: Timeout configuring node '%s': %s", node, e)
        except TypeError as e:
            log.warning("RTBL Publish: Could not build config form for node '%s': %s", node, e)

        return node_exists


    async def _rtbl_sync_all_bans_to_nodes(self) -> tuple[int, int, int, int]:
        """
        Publish all active JID bans and domain bans from the main bans table
        to the respective own publish nodes.
        Returns (jid_count, domain_count, jid_failures, domain_failures).
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return 0, 0, 0, 0

        jid_count = 0
        domain_count = 0
        jid_failures = 0
        domain_failures = 0

        async with self.db.execute(
            """SELECT jid, comment FROM bans
               WHERE target_type = 'jid' AND jid IS NOT NULL
                 AND (until = 0 OR until > strftime('%s','now'))
                 AND COALESCE(issuer, '') != 'rtbl'"""
        ) as cursor:
            jid_rows = await cursor.fetchall()

        for jid, comment in jid_rows:
            bare = self.bare_jid(jid) if jid else None
            if not bare or bare.startswith("*." ):
                continue
            if await self._rtbl_publish_jid_item(bare, comment):
                jid_count += 1
            else:
                jid_failures += 1

        async with self.db.execute(
            """SELECT target, comment FROM bans
               WHERE target_type = 'domain'
                 AND (until = 0 OR until > strftime('%s','now'))
                 AND COALESCE(issuer, '') != 'rtbl'"""
        ) as cursor:
            domain_rows = await cursor.fetchall()

        for domain, comment in domain_rows:
            if not domain:
                continue
            if await self._rtbl_publish_domain_item(domain, comment):
                domain_count += 1
            else:
                domain_failures += 1

        return jid_count, domain_count, jid_failures, domain_failures


    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None:
        """
        Publish a single ban to the appropriate own RTBL node.
        Called from ban_all() after upsert_ban_db() succeeds.
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        if jid:
            bare = self.bare_jid(jid)
            if bare and not bare.startswith("*." ):
                await self._rtbl_publish_jid_item(bare, comment)

        if domain:
            clean = domain.lstrip("*.")
            if clean:
                await self._rtbl_publish_domain_item(clean, comment)


    async def rtbl_retract_ban(
        self,
        jid: str | None,
        domain: str | None,
    ) -> None:
        """
        Retract a ban from the appropriate own RTBL node.
        Called from unban_all() after the ban has been removed from DB.
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        if jid:
            bare = self.bare_jid(jid)
            if bare and not bare.startswith("*." ):
                hash_val = self._rtbl_hash_jid(bare)
                await self._rtbl_retract_item(self.rtbl_publish_jid_node, hash_val)

        if domain:
            clean = domain.lstrip("*.")
            if clean:
                await self._rtbl_retract_item(self.rtbl_publish_domain_node, clean)


    async def _rtbl_publish_jid_item(self, bare_jid: str, comment: str | None) -> bool:
        """Publish a single SHA-256 hashed JID to the own JID pubsub node."""
        hash_val = self._rtbl_hash_jid(bare_jid)
        try:
            await self.plugin["xep_0060"].publish(
                self.rtbl_publish_service,
                self.rtbl_publish_jid_node,
                id=hash_val,
                payload=self._rtbl_build_payload(comment),
            )
            log.info(
                "RTBL Publish: JID hash published for %s (%s…)",
                bare_jid, hash_val[:12],
            )
            return True
        except (IqError, IqTimeout) as e:
            log.warning("RTBL Publish: Could not publish JID hash for %s: %s", bare_jid, e)
            return False


    async def _rtbl_publish_domain_item(self, domain: str, comment: str | None) -> bool:
        """Publish a plaintext domain ban to the own domain pubsub node."""
        try:
            await self.plugin["xep_0060"].publish(
                self.rtbl_publish_service,
                self.rtbl_publish_domain_node,
                id=domain,
                payload=self._rtbl_build_payload(comment),
            )
            log.info("RTBL Publish: Domain ban published for *.%s", domain)
            return True
        except (IqError, IqTimeout) as e:
            log.warning("RTBL Publish: Could not publish domain ban for *.%s: %s", domain, e)
            return False


    async def _rtbl_retract_item(self, node: str, item_id: str) -> None:
        """Retract a single item from one of the own publish nodes."""
        try:
            await self.plugin["xep_0060"].retract(
                self.rtbl_publish_service,
                node,
                id=item_id,
                notify=True,
            )
            log.info(
                "RTBL Publish: Retracted '%s…' from node '%s'",
                item_id[:16], node,
            )
        except IqError as e:
            # If the item is not present in the node, the desired final state
            # is already reached. This can happen when an old permanent ban is
            # converted to a tempban but was never published successfully, or
            # when the node was manually recreated. Keep admin logs quiet.
            if "item-not-found" in str(e).lower():
                log.info(
                    "RTBL Publish: Item '%s…' was already absent from node '%s'",
                    item_id[:16], node,
                )
                return

            log.warning(
                "RTBL Publish: Could not retract '%s…' from '%s': %s",
                item_id[:16], node, e,
            )
        except IqTimeout as e:
            log.warning(
                "RTBL Publish: Timeout retracting '%s…' from '%s': %s",
                item_id[:16], node, e,
            )
