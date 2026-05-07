"""Admin-room !rtbl command handling."""

import logging

from .rtbl_utils import _looks_like_pubsub_node, _looks_like_pubsub_service_jid

log = logging.getLogger(__name__)


class RtblCommandMixin:
    async def cmd_rtbl(self, args: list[str], room: str, actor: str = "unknown") -> None:
        """
        Manage RTBL subscriptions and the optional own publish feed.

        !rtbl list
        !rtbl add <service_jid> <node>
        !rtbl delete <service_jid> [node]
        !rtbl publish status
        !rtbl publish sync
        """
        p = self.command_prefix

        if not args:
            lines = [
                "Usage:",
                f"  {p}rtbl list",
                f"  {p}rtbl add <service_jid> <node>",
                f"  {p}rtbl delete <service_jid> [node]",
            ]
            if getattr(self, "rtbl_publish_enabled", False):
                lines += [
                    f"  {p}rtbl publish status",
                    f"  {p}rtbl publish sync",
                ]
            self.send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        action = args[0].lower()

        # ----------------------------------------------------------------
        # list
        # ----------------------------------------------------------------
        if action == "list":
            lines = ["🛡️ RTBL Subscriptions:"]

            if not self.rtbl_subscriptions:
                lines.append("  (none)")
            else:
                for service_jid, node in self.rtbl_subscriptions:
                    async with self.db.execute(
                        "SELECT COUNT(*) FROM rtbl_hashes WHERE service_jid = ? AND node = ?",
                        (service_jid, node),
                    ) as cursor:
                        row = await cursor.fetchone()
                        h_count = int(row[0] or 0) if row else 0
                    async with self.db.execute(
                        "SELECT COUNT(*) FROM rtbl_domains WHERE service_jid = ? AND node = ?",
                        (service_jid, node),
                    ) as cursor:
                        row = await cursor.fetchone()
                        d_count = int(row[0] or 0) if row else 0
                    lines.append(
                        f"  • {service_jid}  /  {node}"
                        f"  ({h_count} hashes, {d_count} domains)"
                    )

            if getattr(self, "rtbl_publish_enabled", False):
                jid_publish_count, domain_publish_count = await self._rtbl_count_active_publish_bans()
                lines += [
                    "",
                    "📡 Own publish feed:",
                    (
                        f"  • JID hashes  → {self.rtbl_publish_service} / "
                        f"{self.rtbl_publish_jid_node}  ({jid_publish_count} hashes)"
                    ),
                    (
                        f"  • Domain bans → {self.rtbl_publish_service} / "
                        f"{self.rtbl_publish_domain_node}  ({domain_publish_count} domains)"
                    ),
                ]

            self.send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # add
        # ----------------------------------------------------------------
        if action == "add":
            if len(args) < 3:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Usage: {p}rtbl add <service_jid> <node>",
                    mtype="groupchat",
                )
                return

            service_jid = args[1].strip().lower()
            node = args[2].strip()

            if not _looks_like_pubsub_service_jid(service_jid):
                self.send_message(
                    mto=room,
                    mbody=(
                        f"❌ Invalid RTBL service JID: {service_jid}\n"
                        "Expected a PubSub service JID/domain like pubsub.example.org."
                    ),
                    mtype="groupchat",
                )
                return

            if not _looks_like_pubsub_node(node):
                self.send_message(
                    mto=room,
                    mbody=(
                        f"❌ Invalid RTBL node: {node or '(empty)'}\n"
                        "Node names must be non-empty and must not contain whitespace."
                    ),
                    mtype="groupchat",
                )
                return

            if self._rtbl_is_own_publish_node(service_jid, node):
                self.send_message(
                    mto=room,
                    mbody=(
                        f"❌ RTBL: Refusing to subscribe to own publish node "
                        f"'{node}' @ {service_jid}.\n"
                        "Own RTBL publish nodes are outbound feeds and should not be "
                        "subscribed as inbound RTBL sources."
                    ),
                    mtype="groupchat",
                )
                return

            if (service_jid, node) in self.rtbl_subscriptions:
                self.send_message(
                    mto=room,
                    mbody=f"⚠️ RTBL: Already subscribed to '{node}' @ {service_jid}.",
                    mtype="groupchat",
                )
                return

            subscribed, error = await self._rtbl_subscribe_node(service_jid, node)
            if not subscribed:
                self.send_message(
                    mto=room,
                    mbody=(
                        f"❌ RTBL: Could not subscribe to '{node}' @ {service_jid}.\n"
                        f"{error}"
                    ),
                    mtype="groupchat",
                )
                return

            await self.db.execute(
                "INSERT OR IGNORE INTO rtbl_subscriptions (service_jid, node) VALUES (?, ?)",
                (service_jid, node),
            )
            await self.db.commit()
            await self._load_rtbl_subscriptions_from_db()

            self.send_message(
                mto=room,
                mbody=(
                    f"✅ RTBL: Added subscription to '{node}' @ {service_jid}. "
                    f"Fetching items…"
                ),
                mtype="groupchat",
            )
            await self._rtbl_fetch_all_items(service_jid, node)
            self.log_event(
                logging.INFO, "rtbl_subscription_added",
                actor=actor, target_type="rtbl", target=f"{service_jid}/{node}",
            )
            await self.audit_event(
                "rtbl_subscription_added",
                actor=actor,
                target_type="rtbl",
                target=f"{service_jid}/{node}",
                comment=f"Subscribed to node '{node}' @ {service_jid}",
            )
            return

        # ----------------------------------------------------------------
        # delete
        # ----------------------------------------------------------------
        if action in ("delete", "del", "remove"):
            if len(args) < 2:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Usage: {p}rtbl delete <service_jid> [node]",
                    mtype="groupchat",
                )
                return

            service_jid = args[1].strip().lower()
            node = args[2].strip() if len(args) >= 3 else None

            if node:
                async with self.db.execute(
                    "SELECT 1 FROM rtbl_subscriptions WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                ) as cursor:
                    existing = await cursor.fetchone()

                if not existing:
                    self.send_message(
                        mto=room,
                        mbody=f"⚠️ RTBL: Subscription '{node}' @ {service_jid} does not exist.",
                        mtype="groupchat",
                    )
                    return

                await self.db.execute(
                    "DELETE FROM rtbl_subscriptions WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                label = f"'{node}' @ {service_jid}"
            else:
                async with self.db.execute(
                    "SELECT COUNT(*) FROM rtbl_subscriptions WHERE service_jid = ?",
                    (service_jid,),
                ) as cursor:
                    row = await cursor.fetchone()
                    existing_count = int(row[0] or 0) if row else 0

                if existing_count <= 0:
                    self.send_message(
                        mto=room,
                        mbody=f"⚠️ RTBL: No subscriptions found for {service_jid}.",
                        mtype="groupchat",
                    )
                    return

                await self.db.execute(
                    "DELETE FROM rtbl_subscriptions WHERE service_jid = ?", (service_jid,)
                )
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE service_jid = ?", (service_jid,)
                )
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE service_jid = ?", (service_jid,)
                )
                label = f"all subscriptions @ {service_jid}"

            await self.db.commit()
            await self._load_rtbl_subscriptions_from_db()

            async with self.db.execute(
                "SELECT jid FROM bans WHERE issuer = 'rtbl'"
            ) as cursor:
                rtbl_bans = [row[0] for row in await cursor.fetchall()]
            for banned_jid in rtbl_bans:
                if not banned_jid:
                    continue
                if banned_jid.startswith("*." ):
                    domain = banned_jid[2:]
                    if domain not in self.rtbl_domain_cache:
                        await self.unban_all(banned_jid, issuer="rtbl_delete")
                else:
                    if self._rtbl_hash_jid(banned_jid) not in self.rtbl_hash_cache:
                        await self.unban_all(banned_jid, issuer="rtbl_delete")

            self.log_event(
                logging.INFO, "rtbl_subscription_removed",
                actor=actor, target_type="rtbl", target=f"{service_jid}/{node or '*'}",
            )
            await self.audit_event(
                "rtbl_subscription_removed",
                actor=actor,
                target_type="rtbl",
                target=f"{service_jid}/{node or '*'}",
                comment=f"Removed {label}",
            )

            msg = (
                f"✅ RTBL: Removed {label}. All hashes and domains purged from DB."
                "\n♻️ Persisted RTBL bans that are no longer present in active subscriptions "
                "were removed."
            )
            self.send_message(mto=room, mbody=msg, mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # publish
        # ----------------------------------------------------------------
        if action == "publish":
            if not getattr(self, "rtbl_publish_enabled", False):
                self.send_message(
                    mto=room,
                    mbody=(
                        "❌ RTBL publish is disabled. "
                        "Set RTBL_PUBLISH_ENABLED = True in config.py and restart."
                    ),
                    mtype="groupchat",
                )
                return

            sub_action = args[1].lower() if len(args) >= 2 else "status"

            if sub_action == "status":
                jid_count, domain_count = await self._rtbl_count_active_publish_bans()
                self.send_message(
                    mto=room,
                    mbody=(
                        "📡 RTBL Publish status:\n"
                        f"  Enabled:     {self.rtbl_publish_enabled}\n"
                        f"  Service:     {self.rtbl_publish_service}\n"
                        f"  JID node:    {self.rtbl_publish_jid_node}"
                        f"  ({jid_count} active bans)\n"
                        f"  Domain node: {self.rtbl_publish_domain_node}"
                        f"  ({domain_count} active bans)"
                    ),
                    mtype="groupchat",
                )
                return

            if sub_action == "sync":
                self.send_message(
                    mto=room,
                    mbody="📡 RTBL Publish: Syncing all active bans to nodes…",
                    mtype="groupchat",
                )
                jid_count, domain_count, jid_failures, domain_failures = await self._rtbl_sync_all_bans_to_nodes()
                status_emoji = "✅" if jid_failures == 0 and domain_failures == 0 else "⚠️"
                self.send_message(
                    mto=room,
                    mbody=(
                        f"{status_emoji} RTBL Publish: Sync complete — "
                        f"{jid_count} JID hashes, {domain_count} domain bans published."
                        + (
                            f"\nFailures: {jid_failures} JID hashes, {domain_failures} domain bans."
                            if jid_failures or domain_failures else ""
                        )
                    ),
                    mtype="groupchat",
                )
                return

            self.send_message(
                mto=room,
                mbody=f"❌ Unknown RTBL publish action: {sub_action}\nAvailable: status / sync",
                mtype="groupchat",
            )
            return

        self.send_message(
            mto=room,
            mbody=(
                f"❌ Unknown RTBL action: {action}\n"
                f"Available: list / add / delete / publish"
            ),
            mtype="groupchat",
        )
