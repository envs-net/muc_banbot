"""OMEMO support package."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, cast

try:  # Optional dependency; only required when OMEMO_ENABLED=True.
    from omemo.storage import Just, Maybe, Nothing, Storage
    from omemo.types import DeviceInformation, JSONType
    from slixmpp.plugins import register_plugin  # type: ignore[attr-defined]
    import slixmpp_omemo as XEP_0384_module
    from slixmpp_omemo import XEP_0384

    OMEMO_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional runtime dependency
    Just = Maybe = Nothing = Storage = None  # type: ignore[assignment]
    DeviceInformation = JSONType = Any  # type: ignore[misc,assignment]
    XEP_0384 = None  # type: ignore[assignment]
    XEP_0384_module = None  # type: ignore[assignment]
    OMEMO_AVAILABLE = False

log = logging.getLogger(__name__)

OMEMO_RESET_RESTART_DELAY_SECONDS = 3

from .helpers import (  # noqa: E402
    _backup_existing_path,
    _backup_path,
    _current_omemo_identity,
    _ensure_omemo_identity_metadata,
    _omemo_identity_metadata_path,
    _prepare_omemo_storage_file,
    _read_omemo_identity_metadata,
    _write_omemo_identity_metadata,
)

if OMEMO_AVAILABLE and XEP_0384 is not None:

    class JsonFileStorage(Storage):  # type: ignore[misc,valid-type]
        """Small JSON-file backed OMEMO storage."""

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
        """slixmpp-omemo plugin implementation for BanBot."""

        default_config = {
            "fallback_message": "This message is OMEMO encrypted.",
            "json_file_path": None,
        }

        def plugin_init(self) -> None:
            if not self.json_file_path:
                raise RuntimeError("OMEMO JSON storage path not specified")

            storage_factory = cast(Callable[[Path], Storage], JsonFileStorage)
            self._storage = storage_factory(Path(self.json_file_path))
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
            jid_count = len({
                getattr(device, "bare_jid", None)
                for device in blindly_trusted
                if getattr(device, "bare_jid", None)
            })
            log.info(
                "OMEMO: [%s] blindly trusted %d device(s) for %d JID(s)",
                identifier,
                len(blindly_trusted),
                jid_count,
            )

        async def _prompt_manual_trust(
            self,
            manually_trusted: frozenset[DeviceInformation],  # type: ignore[valid-type]
            identifier: str | None,
        ) -> None:
            log.warning(
                "OMEMO: [%s] manual trust requested for %d device(s), but interactive trust is disabled",
                identifier,
                len(manually_trusted),
            )


    register_plugin(XEP_0384Impl)
else:
    XEP_0384Impl = None  # type: ignore[assignment]

from .core import OmemoCoreMixin  # noqa: E402
from .devices import OmemoDeviceMixin  # noqa: E402
from .reset import OmemoResetMixin  # noqa: E402
from .status import OmemoStatusMixin  # noqa: E402


class OmemoMixin(
    OmemoResetMixin,
    OmemoDeviceMixin,
    OmemoStatusMixin,
    OmemoCoreMixin,
):
    """Combined OMEMO mixin."""


__all__ = [
    "OMEMO_AVAILABLE",
    "OMEMO_RESET_RESTART_DELAY_SECONDS",
    "XEP_0384Impl",
    "XEP_0384_module",
    "OmemoMixin",
    "_backup_existing_path",
    "_backup_path",
    "_current_omemo_identity",
    "_ensure_omemo_identity_metadata",
    "_omemo_identity_metadata_path",
    "_prepare_omemo_storage_file",
    "_read_omemo_identity_metadata",
    "_write_omemo_identity_metadata",
]
