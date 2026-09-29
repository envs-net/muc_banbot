"""BanBot OMEMO policy adapters backed by envs-xmpp shared primitives."""

from __future__ import annotations

import asyncio
import time

from envs_xmpp_core.xmpp.omemo import OMEMO_AVAILABLE, XEP_0384_module, XEP_0384Impl

from .core import OmemoCoreMixin
from .devices import OmemoDeviceMixin
from .helpers import (
    _backup_existing_path,
    _backup_path,
    _current_omemo_identity,
    _ensure_omemo_identity_metadata,
    _omemo_identity_metadata_path,
    _prepare_omemo_storage_file,
    _read_omemo_identity_metadata,
    _write_omemo_identity_metadata,
)
from .reset import OmemoResetMixin
from .status import OmemoStatusMixin

OMEMO_RESET_RESTART_DELAY_SECONDS = 3


class OmemoMixin(
    OmemoResetMixin,
    OmemoDeviceMixin,
    OmemoStatusMixin,
    OmemoCoreMixin,
):
    """Combined BanBot OMEMO policy mixin."""


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
    "asyncio",
    "time",
]
