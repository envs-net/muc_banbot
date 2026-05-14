"""Optional OMEMO support for BanBot outgoing messages.

This module intentionally keeps OMEMO isolated behind the central
``bot_send_message()`` wrapper.  When OMEMO is disabled, unavailable, or not
selected for a target room, the bot continues to use Slixmpp's normal plaintext
``send_message()`` path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from slixmpp import JID

try:  # Optional dependency; only required when OMEMO_ENABLED=True.
    from omemo.storage import Just, Maybe, Nothing, Storage
    from omemo.types import DeviceInformation, JSONType
    from slixmpp.plugins import register_plugin  # type: ignore[attr-defined]
    from slixmpp_omemo import XEP_0384

    OMEMO_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional runtime dependency
    Just = Maybe = Nothing = Storage = None  # type: ignore[assignment]
    DeviceInformation = JSONType = Any  # type: ignore[misc,assignment]
    XEP_0384 = None  # type: ignore[assignment]
    OMEMO_AVAILABLE = False

log = logging.getLogger(__name__)


def _prepare_omemo_storage_file(path: str) -> Path:
    """Create and secure the OMEMO JSON storage path.

    OMEMO storage contains identity keys, session state and trust data, so the
    parent directory is kept private (0700) and the JSON file itself is kept
    readable/writable only by the bot user (0600).
    """
    storage_path = Path(path).expanduser()

    if not str(storage_path).strip():
        raise RuntimeError("OMEMO storage path must not be empty")

    parent = storage_path.parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)

    if storage_path.exists():
        if storage_path.is_dir():
            raise RuntimeError(f"OMEMO storage path is a directory: {storage_path}")
        os.chmod(storage_path, 0o600)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(storage_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write("{}\n")

    return storage_path


if OMEMO_AVAILABLE:

    class JsonFileStorage(Storage):  # type: ignore[misc,valid-type]
        """Small JSON-file backed OMEMO storage.

        The storage shape is intentionally compatible with slixmpp-omemo's
        generic ``omemo.storage.Storage`` interface and mirrors the approach
        used by slixbot.
        """

        def __init__(self, json_file_path: Path) -> None:
            super().__init__()
            self._json_file_path = _prepare_omemo_storage_file(str(json_file_path))
            self._data: dict[str, JSONType] = {}

            with self._json_file_path.open(encoding="utf8") as f:
                content = f.read().strip()
                self._data = json.loads(content) if content else {}

        async def _load(self, key: str) -> Maybe[JSONType]:  # type: ignore[valid-type]
            if key in self._data:
                return Just(self._data[key])
            return Nothing()

        async def _store(self, key: str, value: JSONType) -> None:  # type: ignore[valid-type]
            self._data[key] = value
            self._write()

        async def _delete(self, key: str) -> None:
            self._data.pop(key, None)
            self._write()

        def _write(self) -> None:
            tmp_path = self._json_file_path.with_suffix(self._json_file_path.suffix + ".tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(tmp_path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f)
            os.chmod(tmp_path, 0o600)
            tmp_path.replace(self._json_file_path)
            os.chmod(self._json_file_path, 0o600)


    class XEP_0384Impl(XEP_0384):  # type: ignore[misc,valid-type]
        """slixmpp-omemo plugin implementation for BanBot.

        Uses Blind Trust Before Verification for pragmatic bot operation.  This
        matches the slixbot approach and avoids interactive trust prompts.
        """

        default_config = {
            "fallback_message": "This message is OMEMO encrypted.",
            "json_file_path": None,
        }

        def plugin_init(self) -> None:
            if not self.json_file_path:
                raise RuntimeError("OMEMO JSON storage path not specified")
            self._storage = JsonFileStorage(Path(self.json_file_path))
            super().plugin_init()

        @property
        def storage(self) -> Storage:  # type: ignore[valid-type]
            return self._storage

        @property
        def _btbv_enabled(self) -> bool:
            return True

        async def _devices_blindly_trusted(
            self,
            blindly_trusted: frozenset[DeviceInformation],  # type: ignore[valid-type]
            identifier: str | None,
        ) -> None:
            trusted_jids = sorted({device.bare_jid for device in blindly_trusted})
            log.info(
                "OMEMO: [%s] blindly trusted %d device(s) for %d JID(s): %s",
                identifier,
                len(blindly_trusted),
                len(trusted_jids),
                ", ".join(trusted_jids),
            )

        async def _prompt_manual_trust(
            self,
            manually_trusted: frozenset[DeviceInformation],  # type: ignore[valid-type]
            identifier: str | None,
        ) -> None:
            log.warning(
                "OMEMO: [%s] manual trust requested for devices, but interactive trust is disabled: %s",
                identifier,
                manually_trusted,
            )


    register_plugin(XEP_0384Impl)
else:
    XEP_0384Impl = None  # type: ignore[assignment]


class OmemoMixin:
    """Optional OMEMO setup and encrypted send helpers."""

    def _configure_omemo_dependency_logging(self) -> None:
        """Reduce noisy third-party OMEMO logs during normal bot operation.

        slixmpp-omemo/omemo can emit very noisy warnings for broken, empty or
        inaccessible device bundles. Those devices/recipients are handled by
        skipping unusable entries, so keep dependency logs quiet unless the bot
        is running with DEBUG logging enabled.
        """
        root_level = logging.getLogger().getEffectiveLevel()

        # Keep full dependency logging when the operator explicitly asks for DEBUG.
        if root_level <= logging.DEBUG:
            return

        for logger_name in (
            "omemo",
            "omemo.core",
            "slixmpp_omemo",
            "slixmpp_omemo.xep_0384",
        ):
            logging.getLogger(logger_name).setLevel(logging.ERROR)

    def configure_omemo(self) -> None:
        """Load OMEMO config and register the plugin when enabled."""
        import config

        self.omemo_enabled: bool = bool(getattr(config, "OMEMO_ENABLED", False))
        self.omemo_storage_file: str = str(
            getattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json")
        )
        self.omemo_auto_encrypt_admin_room: bool = bool(
            getattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", False)
        )
        self.omemo_plaintext_fallback: bool = bool(
            getattr(config, "OMEMO_PLAINTEXT_FALLBACK", False)
        )
        self.omemo_ready_timeout: int = 15
        self.omemo_ready: asyncio.Event = asyncio.Event()

        if not self.omemo_enabled:
            log.info("OMEMO: disabled")
            return

        self._configure_omemo_dependency_logging()

        if not OMEMO_AVAILABLE or XEP_0384Impl is None:
            log.warning(
                "OMEMO: enabled but optional dependencies are missing; continuing with OMEMO disabled. "
                "Install system libraries such as libsodium-dev and libxeddsa-dev, then run "
                "pip install -r requirements-omemo.txt."
            )
            self.omemo_enabled = False
            return

        try:
            storage_path = _prepare_omemo_storage_file(self.omemo_storage_file)
        except Exception as exc:
            log.error("OMEMO: could not prepare secure storage %s: %s", self.omemo_storage_file, exc)
            raise RuntimeError(f"Could not prepare OMEMO storage: {exc}") from exc

        self.omemo_storage_file = str(storage_path)

        self.register_plugin(
            "xep_0384",
            {"json_file_path": self.omemo_storage_file},
            module=XEP_0384.__name__,
        )
        self.add_event_handler("omemo_initialized", self._on_omemo_initialized)
        log.info("OMEMO: enabled with storage %s", self.omemo_storage_file)

    async def _on_omemo_initialized(self, _event: object) -> None:
        log.info("OMEMO: initialized")
        self.omemo_ready.set()

    def _should_encrypt_message(
        self,
        *,
        mto: str,
        mtype: str,
        encrypted: bool | None,
    ) -> bool:
        """Return whether this outgoing bot message should be OMEMO encrypted."""
        if encrypted is False:
            return False
        if not getattr(self, "omemo_enabled", False):
            return False
        if encrypted is True:
            return True

        # Proactive messages have no incoming-message context.  Keep them
        # plaintext by default, except for the admin room when explicitly enabled.
        if mtype == "groupchat" and getattr(self, "omemo_auto_encrypt_admin_room", False):
            try:
                import config

                room = str(mto).split("/")[0].lower().strip()
                admin_room = str(getattr(config, "ADMIN_ROOM", "")).split("/")[0].lower().strip()
                return bool(room and room == admin_room)
            except Exception:
                return False

        return False

    async def _wait_for_omemo_ready(self) -> bool:
        if not getattr(self, "omemo_enabled", False):
            return False
        if self.omemo_ready.is_set():
            return True
        timeout = max(0, int(getattr(self, "omemo_ready_timeout", 15)))
        try:
            await asyncio.wait_for(self.omemo_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("OMEMO: initialization did not complete within %ss", timeout)
            return False

    async def _send_omemo_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any:
        """Encrypt and send a bot-generated message using slixmpp-omemo."""
        if not await self._wait_for_omemo_ready():
            raise RuntimeError("OMEMO is not initialized")

        if "xep_0384" not in self.plugin:
            raise RuntimeError("OMEMO plugin is not registered")

        msg = self.make_message(mto=mto, mbody=mbody, mtype=mtype)
        self._apply_message_kwargs(msg, kwargs)

        if mtype == "groupchat":
            recipients: set[JID] | JID = await self._omemo_recipients_for_room(mto)
        else:
            recipients = JID(JID(mto).bare)

        if isinstance(recipients, set) and not recipients:
            raise RuntimeError(f"No OMEMO recipients available for {mto}")

        return await self._encrypt_and_send_omemo_message(msg, recipients, mto=mto)

    async def _encrypt_and_send_omemo_message(
        self,
        msg: Any,
        recipients: set[JID] | JID,
        *,
        mto: str,
    ) -> Any:
        """Encrypt and send, skipping recipients without usable OMEMO devices.

        slixmpp-omemo aborts the whole encryption attempt when one or more
        intended recipients have no active trusted device/session.  For MUC
        replies we still want the answer to be readable by all occupants for
        whom OMEMO is usable, so we remove the reported unusable bare JIDs and
        retry until the send succeeds or no recipients are left.
        """
        if not isinstance(recipients, set):
            return await self._encrypt_and_send_omemo_once(msg, recipients, mto=mto)

        current_recipients: set[JID] = set(recipients)
        skipped_recipients: set[str] = set()
        max_attempts = max(1, len(current_recipients) + 1)

        for attempt in range(1, max_attempts + 1):
            if not current_recipients:
                raise RuntimeError(f"No usable OMEMO recipients left for {mto}")

            try:
                return await self._encrypt_and_send_omemo_once(
                    msg,
                    current_recipients,
                    mto=mto,
                )
            except Exception as exc:
                missing = self._extract_unusable_omemo_recipients(exc)
                if not missing:
                    raise

                before = set(current_recipients)
                current_recipients = {
                    jid
                    for jid in current_recipients
                    if self._bare_jid(jid).lower() not in missing
                }
                removed = {
                    self._bare_jid(jid).lower()
                    for jid in before
                    if self._bare_jid(jid).lower() in missing
                }

                if not removed or current_recipients == before:
                    raise

                skipped_recipients.update(removed)
                log.warning(
                    (
                        "OMEMO: skipping %d recipient(s) without usable OMEMO "
                        "devices for %s: %s"
                    ),
                    len(removed),
                    mto,
                    ", ".join(sorted(removed)),
                )

                if not current_recipients:
                    raise RuntimeError(
                        f"No usable OMEMO recipients left for {mto}; skipped: "
                        + ", ".join(sorted(skipped_recipients))
                    ) from exc

        raise RuntimeError(
            f"Could not encrypt OMEMO message for {mto}; skipped: "
            + ", ".join(sorted(skipped_recipients))
        )

    async def _encrypt_and_send_omemo_once(
        self,
        msg: Any,
        recipients: set[JID] | JID,
        *,
        mto: str,
    ) -> Any:
        """Encrypt and send one OMEMO message attempt."""
        log.debug("OMEMO: encrypting message for %s", recipients)

        encrypted_result = await self.plugin["xep_0384"].encrypt_message(
            msg,
            recipients,
        )

        # slixmpp-omemo versions differ slightly here:
        # - some return (dict[JID, Message], errors)
        # - some return (Message, errors) for groupchat messages
        # - some may return a Message directly
        errors = None
        encrypted_messages = encrypted_result
        if isinstance(encrypted_result, tuple) and len(encrypted_result) == 2:
            encrypted_messages, errors = encrypted_result

        if errors:
            log.warning("OMEMO: encryption errors for %s: %s", mto, errors)

        if not encrypted_messages:
            raise RuntimeError(f"OMEMO produced no encrypted messages for {mto}")

        echo = None
        if hasattr(encrypted_messages, "items"):
            for _jid, encrypted_msg in encrypted_messages.items():
                echo = encrypted_msg
                encrypted_msg.send()
        else:
            echo = encrypted_messages
            echo.send()

        return echo

    def _extract_unusable_omemo_recipients(self, exc: Exception) -> set[str]:
        """Best-effort extraction of recipient JIDs from slixmpp-omemo errors."""
        text = str(exc)
        matches = re.findall(r"['\"]([^'\"]+@[^'\"]+)['\"]", text)
        return {JID(match).bare.lower() for match in matches if JID(match).bare}

    def _bare_jid(self, value: object) -> str:
        """Return a bare JID string from a JID-like value."""
        bare = getattr(value, "bare", None)
        if bare:
            return str(bare)
        return JID(str(value)).bare

    def _message_has_omemo_payload(self, msg: Any) -> bool:
        """Return True only if the stanza contains an actual OMEMO encrypted payload."""
        namespaces = (
            "eu.siacs.conversations.axolotl",
            "urn:xmpp:omemo:2",
        )

        try:
            xml = msg.xml
        except Exception:
            return False

        for namespace in namespaces:
            if xml.find(f".//{{{namespace}}}encrypted") is not None:
                return True

        return False

    async def _decrypt_incoming_omemo_message(self, msg: Any) -> tuple[Any | None, bool]:
        """Decrypt an incoming OMEMO message if it is encrypted.

        Returns ``(message, encrypted)``.  ``message`` is ``None`` when the
        stanza was encrypted but could not be decrypted, in which case callers
        should stop processing it.
        """
        if not getattr(self, "omemo_enabled", False):
            return msg, False

        if "xep_0384" not in self.plugin:
            return msg, False

        omemo = self.plugin["xep_0384"]

        if not self._message_has_omemo_payload(msg):
            return msg, False

        try:
            namespace = omemo.is_encrypted(msg)
        except Exception as exc:
            log.warning("OMEMO: could not check incoming message encryption: %s", exc)
            return msg, False

        if namespace is None:
            return msg, False

        if not await self._wait_for_omemo_ready():
            log.warning("OMEMO: encrypted incoming message received before OMEMO was ready")
            return None, True

        try:
            result = await omemo.decrypt_message(msg)

            # slixmpp-omemo usually returns (message, device_information),
            # but keep this tolerant in case versions differ.
            decrypted_msg = result[0] if isinstance(result, tuple) else result
            return decrypted_msg, True

        except Exception as exc:
            if self._is_expected_omemo_device_info_error(exc):
                log.info(
                    (
                        "OMEMO: could not decrypt incoming message from %s "
                        "because sender device information is unavailable: %s"
                    ),
                    msg.get("from"),
                    exc,
                )
            else:
                log.warning(
                    "OMEMO: failed to decrypt incoming message from %s: %s",
                    msg.get("from"),
                    exc,
                )
            return None, True


    def _is_expected_omemo_device_info_error(self, exc: Exception) -> bool:
        """Return True for common sender-device/bundle lookup failures.

        These are usually caused by stale or unpublished OMEMO device metadata
        on the sender side.  The encrypted message cannot be processed, but the
        condition is common enough in MUCs that it should not be logged as a bot
        warning during normal operation.
        """
        error_text = str(exc)
        known_markers = (
            "Couldn't find public information about the device",
            "device either does not appear in the device list",
            "bundle of the sending device could not be downloaded",
            "Bundle download failed",
            "Bundle not available",
            "could not be downloaded",
        )
        return any(marker in error_text for marker in known_markers)

    def _apply_message_kwargs(self, msg: Any, kwargs: dict[str, Any]) -> None:
        """Best-effort transfer of supported send_message kwargs to a Message stanza."""
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "msubject":
                msg["subject"] = value
            elif key == "mhtml":
                msg["html"]["body"] = value
            else:
                log.debug("OMEMO: ignoring unsupported message kwarg for encrypted send: %s", key)

    async def _omemo_recipients_for_room(self, room_jid: str) -> set[JID]:
        """Collect current real bare JIDs for an encrypted MUC message."""
        room = str(room_jid).split("/")[0].lower().strip()
        recipients: set[JID] = set()

        own_bare = self.boundjid.bare if getattr(self, "boundjid", None) is not None else ""

        for info in self.occupants.get(room, {}).values():
            jid = info.get("jid") if isinstance(info, dict) else None
            if jid:
                bare = JID(jid).bare
                if bare and bare != own_bare:
                    recipients.add(JID(bare))

        # Include the bot's own devices only when there are actual human/client
        # recipients.  Otherwise an encrypted MUC reply could be sent only to
        # the bot itself when real occupant JIDs are not visible.
        if recipients and own_bare:
            recipients.add(JID(own_bare))

        recipients = {jid for jid in recipients if jid and jid.bare}
        log.debug("OMEMO: recipients for %s: %s", room, recipients)
        return recipients
