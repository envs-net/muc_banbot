"""Own outbound RTBL publish-feed setup and item publish/retract helpers."""

import asyncio
import hashlib
import logging
import uuid

from slixmpp.exceptions import IqError, IqTimeout

from .rtbl_utils import RTBL_PUBLISH_SANITY_CHECK_REASON

log = logging.getLogger(__name__)


RTBL_PUBLISH_MIN_MAX_ITEMS = 1000
RTBL_PUBLISH_MAX_ITEMS_STEP = 1000


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


    def _rtbl_publish_required_max_items(self, active_count: int) -> int:
        """Return PubSub max_items rounded up to 1000-item steps."""
        count = max(0, int(active_count or 0))
        wanted = max(RTBL_PUBLISH_MIN_MAX_ITEMS, count)
        return ((wanted + RTBL_PUBLISH_MAX_ITEMS_STEP - 1) // RTBL_PUBLISH_MAX_ITEMS_STEP) * RTBL_PUBLISH_MAX_ITEMS_STEP


    def _rtbl_publish_node_limit_attr(self, node: str) -> str | None:
        """Return the cached max_items attribute name for an own publish node."""
        if node == getattr(self, "rtbl_publish_jid_node", None):
            return "rtbl_publish_jid_max_items"
        if node == getattr(self, "rtbl_publish_domain_node", None):
            return "rtbl_publish_domain_max_items"
        return None


    async def _rtbl_ensure_publish_capacity(self, node: str, active_count: int) -> int:
        """Ensure an own publish node can retain the given number of active items."""
        max_items = self._rtbl_publish_required_max_items(active_count)
        attr = self._rtbl_publish_node_limit_attr(node)
        current = int(getattr(self, attr, 0) or 0) if attr else 0

        if current >= max_items:
            return current

        await self._rtbl_ensure_node(self.rtbl_publish_service, node, max_items=max_items)
        return int(getattr(self, attr, 0) or 0) if attr else max_items


    async def _rtbl_ensure_publish_capacity_for_counts(
        self,
        jid_count: int,
        domain_count: int,
    ) -> tuple[int, int]:
        """Ensure both own publish nodes have enough PubSub retention capacity."""
        jid_max_items = await self._rtbl_ensure_publish_capacity(
            self.rtbl_publish_jid_node,
            jid_count,
        )
        domain_max_items = await self._rtbl_ensure_publish_capacity(
            self.rtbl_publish_domain_node,
            domain_count,
        )
        return jid_max_items, domain_max_items


    async def _rtbl_ensure_publish_capacity_for_next_item(self, item_type: str) -> None:
        """Grow the matching own publish node before publishing one more item."""
        jid_count, domain_count = await self._rtbl_count_active_publish_bans()
        if item_type == "jid":
            await self._rtbl_ensure_publish_capacity(self.rtbl_publish_jid_node, jid_count)
        elif item_type == "domain":
            await self._rtbl_ensure_publish_capacity(self.rtbl_publish_domain_node, domain_count)


    async def setup_rtbl_publish(self) -> None:
        """
        Create/configure own JID and domain publish nodes when possible, then
        perform an initial sync of all active bans.
        Called from start() after setup_rtbl().
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        self.rtbl_publish_config_enabled = True
        self.rtbl_publish_sanity_check_ok = None
        self.rtbl_publish_disabled_reason = None

        from config import ADMIN_ROOM

        jid_active_count, domain_active_count = await self._rtbl_count_active_publish_bans()
        jid_max_items = self._rtbl_publish_required_max_items(jid_active_count)
        domain_max_items = self._rtbl_publish_required_max_items(domain_active_count)

        jid_node_ready = await self._rtbl_ensure_node(
            self.rtbl_publish_service,
            self.rtbl_publish_jid_node,
            max_items=jid_max_items,
        )

        domain_node_ready = await self._rtbl_ensure_node(
            self.rtbl_publish_service,
            self.rtbl_publish_domain_node,
            max_items=domain_max_items,
        )

        publish_errors = []
        if not jid_node_ready:
            publish_errors.append(
                f"JID node '{self.rtbl_publish_jid_node}' is not ready on {self.rtbl_publish_service}"
            )
        if not domain_node_ready:
            publish_errors.append(
                f"Domain node '{self.rtbl_publish_domain_node}' is not ready on {self.rtbl_publish_service}"
            )

        if not publish_errors:
            _check_ok, publish_errors = await self._rtbl_publish_sanity_check()

        if publish_errors:
            self.rtbl_publish_enabled = False
            self.rtbl_publish_sanity_check_ok = False
            self.rtbl_publish_disabled_reason = "; ".join(publish_errors)
            message = (
                "⚠️ RTBL Publish disabled: startup sanity check failed.\n"
                + "\n".join(f"- {error}" for error in publish_errors)
                + "\nCheck PubSub node permissions, publish_model/access_model, "
                "and bot affiliation."
            )
            log.warning(message)

            if getattr(self, "rtbl_announce", False):
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=message,
                    mtype="groupchat",
                )
            return

        self.rtbl_publish_sanity_check_ok = True
        self.rtbl_publish_disabled_reason = None

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

        if getattr(self, "rtbl_announce", False):
            status_emoji = "✅" if jid_failures == 0 and domain_failures == 0 else "⚠️"
            await self.bot_send_message(
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


    def _rtbl_make_node_config_form(self, max_items: int = RTBL_PUBLISH_MIN_MAX_ITEMS):
        """Build a XEP-0004 submit form for RTBL publish node configuration."""
        if "xep_0004" not in self.plugin:
            self.register_plugin("xep_0004")

        form = self.plugin["xep_0004"].make_form(ftype="submit")
        form.add_field(var="FORM_TYPE", ftype="hidden", value="http://jabber.org/protocol/pubsub#node_config")
        form.add_field(var="pubsub#access_model", value="open")
        form.add_field(var="pubsub#publish_model", value="publishers")
        form.add_field(var="pubsub#persist_items", value="1")
        form.add_field(var="pubsub#max_items", value=str(self._rtbl_publish_required_max_items(max_items)))
        form.add_field(var="pubsub#send_last_published_item", value="never")
        form.add_field(var="pubsub#deliver_payloads", value="1")
        return form


    async def _rtbl_ensure_node(self, service: str, node: str, max_items: int = RTBL_PUBLISH_MIN_MAX_ITEMS) -> bool:
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
                config=self._rtbl_make_node_config_form(max_items=max_items),
            )
            configured_max_items = self._rtbl_publish_required_max_items(max_items)
            attr = self._rtbl_publish_node_limit_attr(node)
            if attr:
                setattr(self, attr, configured_max_items)
            log.info(
                "RTBL Publish: Node '%s' configured (publish_model=publishers, max_items=%d)",
                node,
                configured_max_items,
            )
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


    def _rtbl_publish_sanity_item_id(self, node_type: str) -> str:
        """Return a temporary item ID suitable for the given publish node type."""
        marker = f"banbot-publish-check:{uuid.uuid4()}"
        if node_type == "jid":
            return hashlib.sha256(marker.encode("utf-8")).hexdigest()
        return f"banbot-publish-check-{uuid.uuid4().hex}.invalid"


    def _rtbl_pubsub_result_contains_item(self, result, item_id: str) -> bool:
        """Best-effort check whether a PubSub get_items result contains item_id."""
        if result is None:
            return False

        if isinstance(result, dict):
            for key in ("id", "item_id"):
                if result.get(key) == item_id:
                    return True
            for value in result.values():
                if self._rtbl_pubsub_result_contains_item(value, item_id):
                    return True
            return False

        if isinstance(result, (list, tuple, set)):
            return any(self._rtbl_pubsub_result_contains_item(item, item_id) for item in result)

        getter = getattr(result, "get", None)
        if callable(getter):
            try:
                if getter("id") == item_id:
                    return True
            except Exception as exc:
                log.debug("RTBL Publish: could not inspect PubSub result id: %s", exc)

        xml = getattr(result, "xml", None)
        if xml is not None and item_id in str(xml):
            return True

        return item_id in str(result)


    async def _rtbl_get_sanity_item(self, node: str, item_id: str):
        """Fetch a just-published sanity-check item from a publish node."""
        try:
            return await self.plugin["xep_0060"].get_items(
                self.rtbl_publish_service,
                node,
                item_ids=[item_id],
            )
        except TypeError:
            # Older/slimmer test doubles or plugin versions may not support
            # item_ids. Fetch a single latest item; most PubSub services return
            # the freshly published item first, which is enough for a sanity check.
            return await self.plugin["xep_0060"].get_items(
                self.rtbl_publish_service,
                node,
                max_items=1,
            )


    async def _rtbl_publish_sanity_check_node(self, node: str, node_type: str) -> tuple[bool, str | None]:
        """Publish, fetch and retract a temporary item to validate one publish node."""
        item_id = self._rtbl_publish_sanity_item_id(node_type)
        published = False
        primary_error = None

        try:
            await self.plugin["xep_0060"].publish(
                self.rtbl_publish_service,
                node,
                id=item_id,
                payload=self._rtbl_build_payload(RTBL_PUBLISH_SANITY_CHECK_REASON),
            )
            published = True
            fetch_error = None

            for attempt in range(3):
                try:
                    result = await self._rtbl_get_sanity_item(node, item_id)
                except (IqError, IqTimeout) as e:
                    fetch_error = f"{node}: test item fetch failed: {e}"
                except Exception as e:
                    fetch_error = f"{node}: test item fetch failed: {e}"
                else:
                    if self._rtbl_pubsub_result_contains_item(result, item_id):
                        fetch_error = None
                        break

                    fetch_error = f"{node}: test item was published but not visible when fetched"

                if attempt < 2:
                    await asyncio.sleep(0.3)

            if fetch_error:
                primary_error = fetch_error

        except (IqError, IqTimeout) as e:
            primary_error = f"{node}: test publish failed: {e}"
        except Exception as e:
            primary_error = f"{node}: test publish failed: {e}"
        finally:
            if published:
                try:
                    await self.plugin["xep_0060"].retract(
                        self.rtbl_publish_service,
                        node,
                        id=item_id,
                        notify=False,
                    )
                except (IqError, IqTimeout) as e:
                    cleanup_error = f"{node}: test retract failed: {e}"
                    primary_error = primary_error or cleanup_error
                    log.warning("RTBL Publish: %s", cleanup_error)
                except Exception as e:
                    cleanup_error = f"{node}: test retract failed: {e}"
                    primary_error = primary_error or cleanup_error
                    log.warning("RTBL Publish: %s", cleanup_error)

        if primary_error:
            return False, primary_error

        log.info("RTBL Publish: Sanity check passed for node '%s'", node)
        return True, None


    async def _rtbl_publish_sanity_check(self) -> tuple[bool, list[str]]:
        """Validate that configured own RTBL publish nodes can publish/read/retract."""
        checks = [
            (self.rtbl_publish_jid_node, "jid"),
            (self.rtbl_publish_domain_node, "domain"),
        ]
        errors = []

        for node, node_type in checks:
            ok, error = await self._rtbl_publish_sanity_check_node(node, node_type)
            if not ok and error:
                errors.append(error)

        return not errors, errors


    async def _rtbl_sync_all_bans_to_nodes(self) -> tuple[int, int, int, int]:
        """
        Publish all active JID bans and domain bans from the main bans table
        to the respective own publish nodes.
        Returns (jid_count, domain_count, jid_failures, domain_failures).
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return 0, 0, 0, 0

        jid_active_count, domain_active_count = await self._rtbl_count_active_publish_bans()
        await self._rtbl_ensure_publish_capacity_for_counts(jid_active_count, domain_active_count)

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
                await self._rtbl_ensure_publish_capacity_for_next_item("jid")
                await self._rtbl_publish_jid_item(bare, comment)

        if domain:
            clean = domain.lstrip("*.")
            if clean:
                await self._rtbl_ensure_publish_capacity_for_next_item("domain")
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
            log.debug(
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
            log.debug("RTBL Publish: Domain ban published for *.%s", domain)
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
