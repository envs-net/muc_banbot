"""Bot vCard and avatar update helpers."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
from .locks import identity_publish_lock
from .managed_io import run_blocking_io

if TYPE_CHECKING:
    from .contracts import VCardMixinHost

    class _VCardMixinContract(VCardMixinHost):
        pass
else:
    class _VCardMixinContract:
        pass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _VCardProfile:
    """One immutable identity snapshot used by a complete publish attempt."""

    avatar_path: str | None
    nickname: str | None
    full_name: str | None
    organization: str | None
    role: str | None
    url: str | None
    note: str | None


def _optional_config_text(name: str) -> str | None:
    value = getattr(config, name, None)
    if not value:
        return None
    return str(value)


class VCardMixin(_VCardMixinContract):
    """Publish the BanBot profile through XEP-0054, XEP-0084 and XEP-0153."""

    def _vcard_profile_snapshot(self) -> _VCardProfile:
        """Capture all profile inputs before the first await in a publish."""
        return _VCardProfile(
            avatar_path=_optional_config_text("AVATAR_PATH"),
            nickname=_optional_config_text("VCARD_NICKNAME"),
            full_name=_optional_config_text("VCARD_FN"),
            organization=_optional_config_text("VCARD_ORG"),
            role=_optional_config_text("VCARD_ROLE"),
            url=_optional_config_text("VCARD_URL"),
            note=_optional_config_text("VCARD_NOTE"),
        )

    async def _load_avatar_payload(self, avatar_path: str) -> AvatarPayload | None:
        """Load one configured avatar while keeping cancellation file-safe."""
        try:
            resolved_avatar = resolve_bundled_asset(avatar_path)
            payload = await run_blocking_io(load_avatar_payload, resolved_avatar)
        except FileNotFoundError:
            log.warning("⚠️ AVATAR_PATH does not exist: %s", avatar_path)
            return None
        except (OSError, ValueError) as exc:
            log.warning("⚠️ Failed to load avatar image: %s", exc)
            return None

        log.info("✅ Avatar loaded from: %s", resolved_avatar)
        return payload

    def _make_avatar_hash_presence(
        self,
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
            # the stanza sender. Supplying the bound full JID keeps the
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

    def _broadcast_avatar_hash_presence(self, avatar_hash: str) -> int:
        """Send XEP-0153 presence globally and to all joined MUC identities.

        One failed room-specific presence must not prevent the global identity
        or the remaining joined rooms from receiving the updated hash.
        """
        if not xmpp_strict_active(self):
            log.debug(
                "Skipping XEP-0153 avatar hash presence because XMPP stream is not connected"
            )
            return 0

        targets: list[str | None] = [None]
        room_nicks = dict(self.room_bot_nicks)
        targets.extend(
            f"{room}/{nick}"
            for room, nick in sorted(room_nicks.items())
            if nick
        )

        sent = 0
        for target in targets:
            try:
                presence = self._make_avatar_hash_presence(avatar_hash, pto=target)
                if presence is None:
                    continue
                presence.send()
                sent += 1
            except Exception as exc:
                log.warning(
                    "⚠️ Failed to send XEP-0153 avatar presence%s: %s",
                    f" to {target}" if target else "",
                    exc,
                )
        return sent

    async def _clear_xep0084_avatar(self) -> bool:
        """Publish empty XEP-0084 metadata so clients drop a removed avatar."""
        try:
            stop = getattr(self["xep_0084"], "stop", None)
            if not callable(stop):
                log.warning("⚠️ XEP-0084 plugin cannot clear avatar metadata")
                return False
            result = stop()
            if inspect.isawaitable(result):
                await result
            log.info("✅ XEP-0084 avatar metadata cleared successfully")
            return True
        except Exception as exc:
            log.warning("⚠️ Failed to clear XEP-0084 avatar metadata: %s", exc)
            return False

    async def _update_vcard_locked(self, profile: _VCardProfile) -> bool:
        payload: AvatarPayload | None = None
        if profile.avatar_path is not None:
            payload = await self._load_avatar_payload(profile.avatar_path)
            if payload is None:
                # Do not publish an avatar-less vCard merely because the
                # configured file is temporarily missing/unreadable. That
                # would erase a previously valid server-side identity.
                log.warning("⚠️ vCard update skipped because the configured avatar could not be loaded")
                return False

        # --- XEP-0054: vCard with optional photo + configured profile fields ---
        xep0054_ok = False
        try:
            vcard = self["xep_0054"].make_vcard()

            if payload is not None:
                vcard["PHOTO"]["TYPE"] = payload.media_type
                vcard["PHOTO"]["BINVAL"] = payload.data

            if profile.nickname:
                vcard["NICKNAME"] = profile.nickname
            if profile.full_name:
                vcard["FN"] = profile.full_name
            if profile.organization:
                vcard["ORG"]["ORGNAME"] = profile.organization
            if profile.role:
                vcard["ROLE"] = profile.role
            if profile.url:
                vcard["URL"] = profile.url
            if profile.note:
                vcard["NOTE"] = profile.note

            await self["xep_0054"].publish_vcard(vcard)
            xep0054_ok = True
            log.info("✅ XEP-0054 vCard updated successfully")
        except Exception as exc:  # Slixmpp plugin versions expose different IQ errors.
            log.warning("⚠️ Failed to update XEP-0054 vCard: %s", exc)

        if payload is None:
            # An explicitly disabled avatar must withdraw the modern PEP
            # metadata as well as the legacy XEP-0153 hash. Merely setting the
            # local attribute to None leaves remote clients caching the old
            # avatar and lets Slixmpp keep advertising its cached hash.
            await self._clear_xep0084_avatar()

            if not xep0054_ok:
                log.warning(
                    "⚠️ XEP-0153 avatar clear skipped because the avatar-less XEP-0054 vCard was not published"
                )
                return False

            if not await cache_xep0153_hash(self, ""):
                log.warning("⚠️ Failed to clear XEP-0153 avatar hash cache")
                return True

            self.avatar_hash = None
            sent = self._broadcast_avatar_hash_presence("")
            if sent:
                log.info("✅ XEP-0153 avatar hash cleared successfully")
            return True

        # --- XEP-0084: PEP avatar data + mandatory metadata ---
        try:
            await publish_xep0084_avatar(self, payload)
            log.info("✅ XEP-0084 avatar and metadata updated successfully")
        except Exception as exc:  # Slixmpp plugin versions expose different IQ errors.
            log.warning("⚠️ Failed to update XEP-0084 avatar: %s", exc)

        # --- XEP-0153: advertise only the hash of a published XEP-0054 PHOTO ---
        if not xep0054_ok:
            log.warning(
                "⚠️ XEP-0153 avatar hash skipped because XEP-0054 PHOTO was not published"
            )
            return False

        if not await cache_xep0153_hash(self, payload.sha1):
            log.warning("⚠️ Failed to seed XEP-0153 avatar hash cache")
            return True

        self.avatar_hash = payload.sha1
        sent = self._broadcast_avatar_hash_presence(payload.sha1)
        if sent:
            log.info("✅ XEP-0153 avatar hash updated successfully")
        return True

    async def update_vcard(self) -> bool:
        """Update bot vCard and avatar information from one consistent snapshot.

        XEP-0054 and XEP-0084 are attempted independently. XEP-0153 is only
        advertised after XEP-0054 successfully published a matching PHOTO,
        preventing clients from receiving a hash for an unavailable vCard
        image. Concurrent startup/reload publication is serialized so two
        identity generations cannot interleave their protocol updates.
        """
        async with identity_publish_lock(self):
            profile = self._vcard_profile_snapshot()
            return await self._update_vcard_locked(profile)
