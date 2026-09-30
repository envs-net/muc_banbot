"""BanBot OMEMO policy adapter over envs-xmpp shared transport primitives."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp.omemo import (
    decrypt_incoming_message,
    encrypt_and_send,
    extract_unusable_recipients,
    normalize_bare_jid,
    recipient_bare_jids,
    wait_for_omemo_ready,
)
from envs_xmpp_core.xmpp.outbound import ensure_message_origin_id
from slixmpp import JID

from .helpers import _current_omemo_identity, _ensure_omemo_identity_metadata, _prepare_omemo_storage_file

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..contracts import OmemoCoreMixinHost

    class _OmemoCoreMixinContract(OmemoCoreMixinHost):
        pass
else:
    class _OmemoCoreMixinContract:
        pass


class OmemoCoreMixin(_OmemoCoreMixinContract):
    def _configure_omemo_dependency_logging(self) -> None:
        if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
            return
        for logger_name in ("omemo", "omemo.core", "slixmpp_omemo", "slixmpp_omemo.xep_0384"):
            logging.getLogger(logger_name).setLevel(logging.ERROR)

    def configure_omemo(self) -> None:
        import banbot.omemo as omemo_package
        import config

        self.omemo_enabled = bool(getattr(config, "OMEMO_ENABLED", False))
        self.omemo_storage_file = str(getattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json"))
        self.omemo_auto_encrypt_admin_room = bool(getattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", True))
        self.omemo_plaintext_fallback = bool(getattr(config, "OMEMO_PLAINTEXT_FALLBACK", False))
        self.omemo_reset_on_identity_change = bool(getattr(config, "OMEMO_RESET_ON_IDENTITY_CHANGE", True))
        self.omemo_reset_pending_restart = False
        self.omemo_ready_timeout = 15
        self.omemo_ready = asyncio.Event()

        if not self.omemo_enabled:
            log.info("OMEMO: disabled")
            return
        self._configure_omemo_dependency_logging()
        if not omemo_package.OMEMO_AVAILABLE or omemo_package.XEP_0384Impl is None:
            log.warning(
                "OMEMO: enabled but the runtime is incomplete; continuing with OMEMO disabled. "
                "Install any required platform libraries such as libsodium/libxeddsa, then "
                "reinstall the normal project dependencies."
            )
            self.omemo_enabled = False
            return

        storage_path = Path(self.omemo_storage_file).expanduser()
        backup = _ensure_omemo_identity_metadata(
            storage_path,
            _current_omemo_identity(config),
            reset_on_change=self.omemo_reset_on_identity_change,
        )
        if backup is not None:
            log.warning("OMEMO: previous storage moved to %s", backup)
        storage_path = _prepare_omemo_storage_file(str(storage_path))
        self.omemo_storage_file = str(storage_path)
        self.register_plugin(
            "xep_0384",
            {"json_file_path": self.omemo_storage_file},
            module=omemo_package.XEP_0384_module,
        )
        self.add_event_handler("omemo_initialized", self._on_omemo_initialized)
        log.info("OMEMO: enabled with storage %s", self.omemo_storage_file)

    async def _on_omemo_initialized(self, _event: object) -> None:
        log.info("OMEMO: initialized")
        self.omemo_ready.set()

    def _should_encrypt_message(self, *, mto: str, mtype: str, encrypted: bool | None) -> bool:
        if encrypted is False or getattr(self, "omemo_reset_pending_restart", False):
            return False
        if not getattr(self, "omemo_enabled", False):
            return False
        if encrypted is True:
            return True
        if mtype == "groupchat" and getattr(self, "omemo_auto_encrypt_admin_room", True):
            try:
                import config

                room = str(mto).split("/")[0].lower().strip()
                admin_room = str(getattr(config, "ADMIN_ROOM", "")).split("/")[0].lower().strip()
                return bool(room and room == admin_room)
            except Exception:
                return False
        return False

    async def _wait_for_omemo_ready(self) -> bool:
        ready = await wait_for_omemo_ready(
            self.omemo_ready,
            enabled=bool(getattr(self, "omemo_enabled", False)),
            timeout=getattr(self, "omemo_ready_timeout", 15),
            reset_pending=bool(getattr(self, "omemo_reset_pending_restart", False)),
        )
        if not ready and getattr(self, "omemo_enabled", False):
            log.warning("OMEMO: initialization is not ready")
        return ready

    async def _send_omemo_message(
        self, *, mto: str, mbody: str, mtype: str = "groupchat",
        origin_id: str | None = None, **kwargs: Any
    ) -> Any:
        if not await self._wait_for_omemo_ready():
            raise RuntimeError("OMEMO is not initialized")
        plugin = self.plugin.get("xep_0384", None)
        if plugin is None:
            raise RuntimeError("OMEMO plugin is not registered")
        msg = self.make_message(mto=mto, mbody=mbody, mtype=mtype)
        if origin_id is not None:
            ensure_message_origin_id(msg, origin_id, require_stanza_id=True)
        self._apply_message_kwargs(msg, kwargs)
        recipients: set[JID] | JID
        if mtype == "groupchat":
            recipients = await self._omemo_recipients_for_room(mto)
        else:
            recipients = self._omemo_recipient_for_chat(mto)
        if isinstance(recipients, set) and not recipients:
            raise RuntimeError(f"No OMEMO recipients available for {mto}")
        return await encrypt_and_send(plugin, msg, recipients, mto=mto, origin_id=origin_id)

    async def _encrypt_and_send_omemo_message(self, msg: Any, recipients: set[JID] | JID, *, mto: str) -> Any:
        plugin = self.plugin.get("xep_0384", None)
        if plugin is None:
            raise RuntimeError("OMEMO plugin is not registered")
        return await encrypt_and_send(plugin, msg, recipients, mto=mto)

    def _extract_unusable_omemo_recipients(self, exc: Exception) -> set[str]:
        return extract_unusable_recipients(exc)

    @staticmethod
    def _normalize_omemo_bare_jid(value: object) -> str | None:
        return normalize_bare_jid(value)

    def _bare_jid(self, value: object) -> str:
        bare = normalize_bare_jid(value)
        if not bare:
            raise ValueError("OMEMO recipient does not contain a valid bare JID")
        return bare

    @staticmethod
    def _message_has_omemo_payload(msg: Any) -> bool:
        from envs_xmpp_core.xmpp.omemo import message_has_omemo_payload

        return message_has_omemo_payload(msg)

    async def _decrypt_incoming_omemo_message(self, msg: Any) -> tuple[Any | None, bool]:
        plugin = self.plugin.get("xep_0384", None)
        decrypted, encrypted, reason = await decrypt_incoming_message(
            msg,
            enabled=bool(getattr(self, "omemo_enabled", False)),
            plugin_map={"xep_0384": plugin} if plugin is not None else {},
            ready_event=getattr(self, "omemo_ready", asyncio.Event()),
            timeout=getattr(self, "omemo_ready_timeout", 15),
            reset_pending=bool(getattr(self, "omemo_reset_pending_restart", False)),
        )
        if reason == "device-info-unavailable":
            log.info(
                "OMEMO: could not decrypt incoming message because sender device information is unavailable"
            )
        elif reason == "inspection-failed":
            log.warning("OMEMO: could not inspect encrypted incoming message; ignoring stanza")
        elif reason == "decrypt-failed":
            log.warning("OMEMO: failed to decrypt incoming message")
        elif reason:
            log.warning("OMEMO: encrypted message ignored: %s", reason)
        return decrypted, encrypted

    @staticmethod
    def _is_expected_omemo_device_info_error(exc: Exception) -> bool:
        from envs_xmpp_core.xmpp.omemo import expected_device_info_error

        return expected_device_info_error(exc)

    def _apply_message_kwargs(self, msg: Any, kwargs: dict[str, Any]) -> None:
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "msubject":
                msg["subject"] = value
            elif key == "mhtml":
                msg["html"]["body"] = value
            else:
                log.debug("OMEMO: ignoring unsupported message kwarg for encrypted send: %s", key)

    def _omemo_recipient_for_chat(self, target: object) -> JID:
        """Resolve a direct chat or MUC-PM target to the real bare JID."""
        jid = JID(str(target))
        room = normalize_bare_jid(jid.bare)
        nick = str(jid.resource or "").strip()
        if nick and room:
            occupants = self.occupants.get(room)
            if occupants is None:
                occupants = next(
                    (
                        cached
                        for cached_room, cached in self.occupants.items()
                        if str(cached_room).casefold() == room.casefold()
                    ),
                    None,
                )
            if isinstance(occupants, dict):
                info = occupants.get(nick)
                if info is None:
                    info = next(
                        (
                            cached_info
                            for cached_nick, cached_info in occupants.items()
                            if str(cached_nick).casefold() == nick.casefold()
                        ),
                        None,
                    )
                if isinstance(info, dict):
                    real_bare = normalize_bare_jid(info.get("jid"))
                    if real_bare:
                        return JID(real_bare)
        return JID(jid.bare)

    async def _omemo_recipients_for_room(self, room_jid: str) -> set[JID]:
        room = normalize_bare_jid(room_jid)
        if not room:
            return set()
        values: set[object] = set()
        for info in self.occupants.get(room, {}).values():
            if isinstance(info, dict) and info.get("jid"):
                values.add(info["jid"])
        own = self.boundjid.bare if getattr(self, "boundjid", None) is not None else None
        return {JID(jid) for jid in recipient_bare_jids(values, own_jid=own)}
