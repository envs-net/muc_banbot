"""Bot vCard and avatar update helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from envs_xmpp_core.xmpp.avatar import (
    AvatarPayload,
    cache_xep0153_hash,
    load_avatar_payload,
    publish_xep0084_avatar,
    set_presence_avatar_hash,
    xmpp_strict_active,
)

import config

from .bundled_assets import resolve_bundled_asset

log = logging.getLogger(__name__)


class VCardMixin:
    """Publish the BanBot profile through XEP-0054, XEP-0084 and XEP-0153."""

    async def _load_avatar_payload(self) -> AvatarPayload | None:
        avatar_path = getattr(config, "AVATAR_PATH", None)
        if not avatar_path:
            return None

        try:
            resolved_avatar = resolve_bundled_asset(str(avatar_path))
            payload = await asyncio.to_thread(load_avatar_payload, resolved_avatar)
        except FileNotFoundError:
            log.warning("⚠️ AVATAR_PATH does not exist: %s", avatar_path)
            return None
        except (OSError, ValueError) as exc:
            log.warning("⚠️ Failed to load avatar image: %s", exc)
            return None

        log.info("✅ Avatar loaded from: %s", resolved_avatar)
        return payload

    def _make_avatar_hash_presence(
        self: Any,
        avatar_hash: str,
        *,
        pto: str | None = None,
    ) -> Any | None:
        """Build one stream-bound XEP-0153 presence stanza."""
        if not xmpp_strict_active(self):
            return None

        kwargs: dict[str, Any] = {}
        if pto is not None:
            kwargs["pto"] = pto

        boundjid = getattr(self, "boundjid", None)
        sender = str(getattr(boundjid, "full", "") or "").strip()
        if sender:
            # Slixmpp's XEP-0153 outgoing filter resolves its cached hash from
            # the stanza sender.  Supplying the bound full JID keeps the
            # explicit payload and the plugin cache aligned.
            kwargs["pfrom"] = sender

        presence = self.make_presence(**kwargs)
        set_presence_avatar_hash(presence, avatar_hash)
        if getattr(presence, "stream", None) is None:
            log.debug(
                "Skipping unbound XEP-0153 avatar presence%s",
                f" to {pto}" if pto else "",
            )
            return None
        return presence

    def _broadcast_avatar_hash_presence(self: Any, avatar_hash: str) -> int:
        """Send XEP-0153 presence globally and to all joined MUC identities."""
        if not xmpp_strict_active(self):
            log.debug(
                "Skipping XEP-0153 avatar hash presence because XMPP stream is not connected"
            )
            return 0

        targets: list[str | None] = [None]
        room_nicks = dict(getattr(self, "room_bot_nicks", {}) or {})
        targets.extend(
            f"{room}/{nick}"
            for room, nick in sorted(room_nicks.items())
            if nick
        )

        sent = 0
        for target in targets:
            presence = self._make_avatar_hash_presence(avatar_hash, pto=target)
            if presence is None:
                continue
            presence.send()
            sent += 1
        return sent

    async def update_vcard(self: Any) -> bool:
        """Update bot vCard and avatar information.

        XEP-0054 and XEP-0084 are attempted independently.  XEP-0153 is only
        advertised after XEP-0054 successfully published a matching PHOTO,
        preventing clients from receiving a hash for an unavailable vCard
        image.
        """
        payload = await self._load_avatar_payload()

        # --- XEP-0054: vCard with optional photo + configured profile fields ---
        xep0054_ok = False
        try:
            vcard = self["xep_0054"].make_vcard()

            if payload is not None:
                vcard["PHOTO"]["TYPE"] = payload.media_type
                vcard["PHOTO"]["BINVAL"] = payload.data

            if getattr(config, "VCARD_NICKNAME", None):
                vcard["NICKNAME"] = config.VCARD_NICKNAME
            if getattr(config, "VCARD_FN", None):
                vcard["FN"] = config.VCARD_FN
            if getattr(config, "VCARD_ORG", None):
                vcard["ORG"]["ORGNAME"] = config.VCARD_ORG
            if getattr(config, "VCARD_ROLE", None):
                vcard["ROLE"] = config.VCARD_ROLE
            if getattr(config, "VCARD_URL", None):
                vcard["URL"] = config.VCARD_URL
            if getattr(config, "VCARD_NOTE", None):
                vcard["NOTE"] = config.VCARD_NOTE

            await self["xep_0054"].publish_vcard(vcard)
            xep0054_ok = True
            log.info("✅ XEP-0054 vCard updated successfully")
        except Exception as exc:  # Slixmpp plugin versions expose different IQ errors.
            log.warning("⚠️ Failed to update XEP-0054 vCard: %s", exc)

        if payload is None:
            self.avatar_hash = None
            log.info("ℹ️ No avatar to publish (XEP-0084, XEP-0153 skipped)")
            return True

        # --- XEP-0084: PEP avatar data + mandatory metadata ---
        try:
            await publish_xep0084_avatar(self, payload)
            log.info("✅ XEP-0084 avatar and metadata updated successfully")
        except Exception as exc:  # Slixmpp plugin versions expose different IQ errors.
            log.warning("⚠️ Failed to update XEP-0084 avatar: %s", exc)

        # --- XEP-0153: advertise only the hash of a published XEP-0054 PHOTO ---
        if not xep0054_ok:
            self.avatar_hash = None
            log.warning(
                "⚠️ XEP-0153 avatar hash skipped because XEP-0054 PHOTO was not published"
            )
            return True

        if not await cache_xep0153_hash(self, payload.sha1):
            self.avatar_hash = None
            log.warning("⚠️ Failed to seed XEP-0153 avatar hash cache")
            return True

        self.avatar_hash = payload.sha1
        sent = self._broadcast_avatar_hash_presence(payload.sha1)
        if sent:
            log.info("✅ XEP-0153 avatar hash updated successfully")
        return True
