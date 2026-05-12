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
            log.info("OMEMO: [%s] blindly trusted devices: %s", identifier, blindly_trusted)

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

    def configure_omemo(self) -> None:
        """Load OMEMO config and register the plugin when enabled."""
        import config

        self.omemo_enabled: bool = bool(getattr(config, "OMEMO_ENABLED", False))
        self.omemo_storage_file: str = str(
            getattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json")
        )
        self.omemo_plaintext_fallback: bool = bool(
            getattr(config, "OMEMO_PLAINTEXT_FALLBACK", False)
        )
        self.omemo_encrypt_direct_messages: bool = bool(
            getattr(config, "OMEMO_ENCRYPT_DIRECT_MESSAGES", False)
        )
        self.omemo_auto_encrypt_protected_rooms: bool = bool(
            getattr(config, "OMEMO_AUTO_ENCRYPT_PROTECTED_ROOMS", True)
        )
        self.omemo_auto_encrypt_admin_room: bool = bool(
            getattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", False)
        )
        self.omemo_include_affiliations: bool = bool(
            getattr(config, "OMEMO_INCLUDE_MUC_AFFILIATIONS", True)
        )
        self.omemo_include_occupants: bool = bool(
            getattr(config, "OMEMO_INCLUDE_MUC_OCCUPANTS", True)
        )
        self.omemo_include_own_devices: bool = bool(
            getattr(config, "OMEMO_INCLUDE_OWN_DEVICES", True)
        )
        self.omemo_ready_timeout: int = int(getattr(config, "OMEMO_READY_TIMEOUT", 15))
        self.omemo_encrypted_rooms: set[str] = self._normalize_omemo_rooms(
            getattr(config, "OMEMO_ENCRYPTED_ROOMS", []),
            option_name="OMEMO_ENCRYPTED_ROOMS",
        )
        self.omemo_plaintext_rooms: set[str] = self._normalize_omemo_rooms(
            getattr(config, "OMEMO_PLAINTEXT_ROOMS", []),
            option_name="OMEMO_PLAINTEXT_ROOMS",
        )
        self.omemo_ready: asyncio.Event = asyncio.Event()

        if not self.omemo_enabled:
            log.info("OMEMO: disabled")
            return

        if not OMEMO_AVAILABLE or XEP_0384Impl is None:
            log.error(
                "OMEMO: enabled but optional dependencies are missing. Install slixmpp-omemo>=2,<3."
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


    def _normalize_omemo_rooms(self, rooms: object, option_name: str) -> set[str]:
        if rooms is None:
            return set()
        if isinstance(rooms, str):
            rooms = [rooms]
        try:
            return {str(room).split("/")[0].lower().strip() for room in rooms if str(room).strip()}
        except TypeError:
            log.warning("%s must be a list/set/tuple of room JIDs", option_name)
            return set()


    async def _on_omemo_initialized(self, _event: object) -> None:
        log.info("OMEMO: initialized")
        self.omemo_ready.set()


    def _omemo_room_is_encrypted(self, room_jid: str) -> bool:
        """Return whether a MUC should receive OMEMO-encrypted bot output."""
        import config

        room = str(room_jid).split("/")[0].lower().strip()

        if "*" in self.omemo_plaintext_rooms or room in self.omemo_plaintext_rooms:
            return False

        if "*" in self.omemo_encrypted_rooms or room in self.omemo_encrypted_rooms:
            return True

        if getattr(self, "omemo_auto_encrypt_admin_room", False):
            admin_room = str(getattr(config, "ADMIN_ROOM", "")).split("/")[0].lower().strip()
            if room and room == admin_room:
                return True

        if getattr(self, "omemo_auto_encrypt_protected_rooms", True):
            protected_rooms = {
                str(protected_room).split("/")[0].lower().strip()
                for protected_room in getattr(self, "protected_rooms", set())
            }
            if room in protected_rooms:
                return True

        return False


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
        if encrypted is True:
            return bool(getattr(self, "omemo_enabled", False))
        if not getattr(self, "omemo_enabled", False):
            return False
        if mtype == "groupchat":
            return self._omemo_room_is_encrypted(mto)
        if mtype in {"chat", "normal"}:
            return bool(getattr(self, "omemo_encrypt_direct_messages", False))
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
            recipients = await self._omemo_recipients_for_room(mto)
        else:
            recipients = JID(JID(mto).bare)

        if isinstance(recipients, set) and not recipients:
            raise RuntimeError(f"No OMEMO recipients available for {mto}")

        log.debug("OMEMO: encrypting message for %s", recipients)
        encrypted_result = await self.plugin["xep_0384"].encrypt_message(msg, recipients)

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
            log.warning(
                "OMEMO: failed to decrypt incoming message from %s: %s",
                msg.get("from"),
                exc,
            )
            return None, True


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
        """Collect real bare JIDs for an encrypted MUC message."""
        room = str(room_jid).split("/")[0].lower().strip()
        recipients: set[JID] = set()

        if getattr(self, "omemo_include_affiliations", True):
            for affiliation in ("member", "admin", "owner"):
                try:
                    affiliation_jids = await self.plugin["xep_0045"].get_affiliation_list(
                        room,
                        affiliation,
                    )
                except Exception as exc:
                    log.debug(
                        "OMEMO: could not fetch %s affiliations for %s: %s",
                        affiliation,
                        room,
                        exc,
                    )
                    continue

                for jid in self._iter_jids(affiliation_jids):
                    recipients.add(JID(JID(jid).bare))

        if getattr(self, "omemo_include_occupants", True):
            for info in self.occupants.get(room, {}).values():
                jid = info.get("jid") if isinstance(info, dict) else None
                if jid:
                    recipients.add(JID(JID(jid).bare))

        if getattr(self, "omemo_include_own_devices", True):
            recipients.add(JID(self.boundjid.bare))

        recipients = {jid for jid in recipients if jid and jid.bare}
        log.debug("OMEMO: recipients for %s: %s", room, recipients)
        return recipients


    def _iter_jids(self, value: object):
        """Yield JID-like values from Slixmpp affiliation-list return shapes."""
        if value is None:
            return
        if isinstance(value, dict):
            iterable = value.keys()
        else:
            iterable = value
        for jid in iterable:
            if jid:
                yield jid
