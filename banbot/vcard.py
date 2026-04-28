"""Bot vCard and avatar update helpers."""

import asyncio
import hashlib
import logging
import pathlib

import config
from slixmpp.stanza.presence import Presence
from slixmpp.xmlstream import ET

log = logging.getLogger(__name__)


class VCardMixin:
    async def update_vcard(self) -> bool:
        """
        Update bot vCard and avatar information.
        - Avatar: optional (from config.AVATAR_PATH)
        - vCard fields: always updated (VCARD_NICKNAME, VCARD_FN, etc.)
        Updates:
          - XEP-0054 (vCard photo + optional vCard fields)
          - XEP-0084 (User Avatar / vCard4) - only if avatar present
          - XEP-0153 (Avatar hash in presence) - only if avatar present
        """
        avatar_path = getattr(config, "AVATAR_PATH", None)
        image_data = None
        avatar_type = None

        # --- Try to load avatar if path is provided ---
        if avatar_path and pathlib.Path(avatar_path).exists():
            try:
                with open(avatar_path, "rb") as f:
                    image_data = f.read()
                avatar_type = f"image/{pathlib.Path(avatar_path).suffix.lstrip('.').lower()}"
                log.info("✅ Avatar loaded from: %s", avatar_path)
            except (FileNotFoundError, IOError) as e:
                log.warning("⚠️ Failed to load avatar image: %s", e)
        else:
            if avatar_path:
                log.warning("⚠️ AVATAR_PATH not set or file does not exist: %s", avatar_path)

        # --- XEP-0054: vCard with optional photo + vCard fields ---
        try:
            vcard = self['xep_0054'].make_vcard()

            # Set avatar only if image data was loaded
            if image_data and avatar_type:
                vcard['PHOTO']['TYPE'] = avatar_type
                vcard['PHOTO']['BINVAL'] = image_data

            # Add optional vCard fields from config (always, regardless of avatar)
            if hasattr(config, 'VCARD_NICKNAME') and config.VCARD_NICKNAME:
                vcard['NICKNAME'] = config.VCARD_NICKNAME

            if hasattr(config, 'VCARD_FN') and config.VCARD_FN:
                vcard['FN'] = config.VCARD_FN

            if hasattr(config, 'VCARD_ORG') and config.VCARD_ORG:
                vcard['ORG']['ORGNAME'] = config.VCARD_ORG

            if hasattr(config, 'VCARD_ROLE') and config.VCARD_ROLE:
                vcard['ROLE'] = config.VCARD_ROLE

            if hasattr(config, 'VCARD_URL') and config.VCARD_URL:
                vcard['URL'] = config.VCARD_URL

            if hasattr(config, 'VCARD_NOTE') and config.VCARD_NOTE:
                vcard['NOTE'] = config.VCARD_NOTE

            await self['xep_0054'].publish_vcard(vcard)
            log.info("✅ XEP-0054 vCard updated successfully")
        except Exception as e:
            log.warning("⚠️ Failed to update XEP-0054 vCard: %s", e)

        # --- XEP-0084: User Avatar / vCard4 (only if avatar present) ---
        if image_data:
            try:
                await self['xep_0084'].publish_avatar(image_data)
                log.info("✅ XEP-0084 avatar updated successfully")
            except Exception as e:
                log.warning("⚠️ Failed to update XEP-0084 avatar: %s", e)

            # --- XEP-0153: Avatar hash in presence (only if avatar present) ---
            try:
                # SHA1 hex hash (XEP-0153 requires hex)
                sha1_hex = hashlib.sha1(image_data).hexdigest()

                # Build <x xmlns='vcard-temp:x:update'><photo>HASH</photo></x>
                x = ET.Element("{vcard-temp:x:update}x")
                photo = ET.SubElement(x, "photo")
                photo.text = sha1_hex

                await asyncio.sleep(1)

                # Build a Presence stanza and send
                presence = Presence()
                presence.append(x)
                self.send(presence)

                log.info("✅ XEP-0153 avatar hash updated successfully")
            except Exception as e:
                log.warning("⚠️ Failed to update XEP-0153 avatar: %s", e)
        else:
            log.info("ℹ️ No avatar to publish (XEP-0084, XEP-0153 skipped)")

        return True
