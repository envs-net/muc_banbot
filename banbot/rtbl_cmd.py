"""Admin-room !rtbl command handling."""

import logging
import time

from .utils import (
    get_list_page_size,
    human_time,
    paginate_lines,
    resolve_page,
    wants_all_pages,
    without_all_pages_arg,
)
from .rtbl_utils import _looks_like_pubsub_node, _looks_like_pubsub_service_jid

log = logging.getLogger(__name__)


class RtblCommandMixin:
    def _rtbl_status_age(self, ts: float | None) -> str:
        """Return a compact human-readable age for RTBL timestamps."""
        if not ts:
            return "never"

        age = max(0, int(time.time() - int(ts)))
        return f"{human_time(age)} ago"

    async def cmd_rtbl(self, args: list[str], room: str, actor: str = "unknown") -> None:
        """
        Manage RTBL subscriptions and the optional own publish feed.

        !rtbl list [all|page|last]
        !rtbl add <service_jid> <node>
        !rtbl delete <service_jid> [node]
        !rtbl publish status
        !rtbl publish sync
        """
        p = self.command_prefix

        if not args:
            lines = [
                "Usage:",
                f"  {p}rtbl list [all|page|last]",
                f"  {p}rtbl add <service_jid> <node>",
                f"  {p}rtbl delete <service_jid> [node]",
                f"  {p}rtbl refresh [service_jid] [node]",
            ]
            if getattr(self, "rtbl_publish_enabled", False):
                lines += [
                    f"  {p}rtbl publish status",
                    f"  {p}rtbl publish sync",
                ]
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        action = args[0].lower()

        # ----------------------------------------------------------------
        # list
        # ----------------------------------------------------------------
        if action == "list":
            show_all = wants_all_pages(args[1:])
            list_args = without_all_pages_arg(args[1:])
            page = 1
            if list_args:
                if list_args[0].lower() == "last":
                    page = -1
                else:
                    try:
                        page = max(1, int(list_args[0]))
                    except ValueError:
                        await self.bot_send_message(
                            mto=room,
                            mbody=f"❌ Usage: {p}rtbl list [all|page|last]",
                            mtype="groupchat",
                        )
                        return

            entries = []
            for service_jid, node in self.rtbl_subscriptions:
                key = (service_jid.lower(), node)

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

                last_fetch = self._rtbl_status_age(
                    getattr(self, "rtbl_last_fetch", {}).get(key)
                )
                last_change = self._rtbl_status_age(
                    getattr(self, "rtbl_last_change", {}).get(key)
                )
                last_error = getattr(self, "rtbl_last_error", {}).get(key)

                entries.append(
                    f"  • {service_jid}  /  {node}\n"
                    f"    Entries: {h_count} hashes, {d_count} domains\n"
                    f"    Last fetch: {last_fetch}\n"
                    f"    Last change: {last_change}\n"
                    f"    Last error: {last_error or 'none'}"
                )

            if not entries:
                lines = ["🛡️ RTBL Subscriptions:", "  (none)"]
                current_page = total_pages = 0
            elif show_all:
                total_items = len(entries)
                lines = [f"🛡️ RTBL Subscriptions ({total_items}) - All:"]
                lines.extend(entries)
                current_page = total_pages = 1
            else:
                per_page = get_list_page_size(self)
                page = resolve_page(page, len(entries), per_page)
                page_entries, current_page, total_pages, total_items = paginate_lines(
                    entries, page, per_page=per_page
                )
                lines = [f"🛡️ RTBL Subscriptions ({total_items}) - Page {current_page}/{total_pages}:"]
                lines.extend(page_entries)

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

            if entries and not show_all and current_page < total_pages:
                lines.append("")
                lines.append(f"Use {p}rtbl list {current_page + 1} for the next page.")

            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # add
        # ----------------------------------------------------------------
        if action == "add":
            if len(args) < 3:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {p}rtbl add <service_jid> <node>",
                    mtype="groupchat",
                )
                return

            service_jid = args[1].strip().lower()
            node = args[2].strip()

            if not _looks_like_pubsub_service_jid(service_jid):
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"❌ Invalid RTBL service JID: {service_jid}\n"
                        "Expected a PubSub service JID/domain like pubsub.example.org."
                    ),
                    mtype="groupchat",
                )
                return

            if not _looks_like_pubsub_node(node):
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"❌ Invalid RTBL node: {node or '(empty)'}\n"
                        "Node names must be non-empty and must not contain whitespace."
                    ),
                    mtype="groupchat",
                )
                return

            if self._rtbl_is_own_publish_node(service_jid, node):
                await self.bot_send_message(
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
                await self.bot_send_message(
                    mto=room,
                    mbody=f"⚠️ RTBL: Already subscribed to '{node}' @ {service_jid}.",
                    mtype="groupchat",
                )
                return

            subscribed, error = await self._rtbl_subscribe_node(service_jid, node)
            if not subscribed:
                await self.bot_send_message(
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

            await self.bot_send_message(
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
                await self.bot_send_message(
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
                    await self.bot_send_message(
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
                    await self.bot_send_message(
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

            await self._rtbl_cleanup_stale_persisted_bans(issuer="rtbl_delete")

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
            await self.bot_send_message(mto=room, mbody=msg, mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # refresh
        # ----------------------------------------------------------------
        if action == "refresh":
            if not self.rtbl_subscriptions:
                await self.bot_send_message(
                    mto=room,
                    mbody="⚠️ RTBL: No subscriptions configured.",
                    mtype="groupchat",
                )
                return

            service_filter = args[1].strip().lower() if len(args) >= 2 else None
            node_filter = args[2].strip() if len(args) >= 3 else None

            selected = []
            for service_jid, node in self.rtbl_subscriptions:
                if service_filter and service_jid.lower() != service_filter:
                    continue
                if node_filter and node != node_filter:
                    continue
                selected.append((service_jid, node))

            if not selected:
                await self.bot_send_message(
                    mto=room,
                    mbody="⚠️ RTBL: No matching subscription found.",
                    mtype="groupchat",
                )
                return

            await self.bot_send_message(
                mto=room,
                mbody=f"🔄 RTBL: Refreshing {len(selected)} subscription(s)…",
                mtype="groupchat",
            )

            refreshed = 0
            failed = 0

            for service_jid, node in selected:
                try:
                    await self._rtbl_fetch_all_items(
                        service_jid,
                        node,
                        scan_occupants=True,
                    )
                    refreshed += 1
                except Exception as e:
                    failed += 1
                    log.warning(
                        "RTBL: Manual refresh failed for '%s' @ %s: %s",
                        node,
                        service_jid,
                        e,
                    )

            status = "✅" if failed == 0 else "⚠️"
            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"{status} RTBL: Refresh complete — "
                    f"{refreshed} refreshed, {failed} failed."
                ),
                mtype="groupchat",
            )
            return

        # ----------------------------------------------------------------
        # publish
        # ----------------------------------------------------------------
        if action == "publish":
            if not getattr(self, "rtbl_publish_enabled", False):
                await self.bot_send_message(
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
                await self.bot_send_message(
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
                await self.bot_send_message(
                    mto=room,
                    mbody="📡 RTBL Publish: Syncing all active bans to nodes…",
                    mtype="groupchat",
                )
                jid_count, domain_count, jid_failures, domain_failures = await self._rtbl_sync_all_bans_to_nodes()
                status_emoji = "✅" if jid_failures == 0 and domain_failures == 0 else "⚠️"
                await self.bot_send_message(
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

            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Unknown RTBL publish action: {sub_action}\nAvailable: status / sync",
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                f"❌ Unknown RTBL action: {action}\n"
                f"Available: list / add / delete / refresh / publish"
            ),
            mtype="groupchat",
        )
