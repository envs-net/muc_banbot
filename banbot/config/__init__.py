"""Runtime configuration package."""

from ..config_loader import format_config_import_error
from .common import (
    NEVER_WRITABLE_CONFIG_KEYS as _COMMON_NEVER_WRITABLE_CONFIG_KEYS,
)
from .common import (
    RUNTIME_WRITABLE_CONFIG_KEYS as _COMMON_RUNTIME_WRITABLE_CONFIG_KEYS,
)
from .common import (
    SECRET_CONFIG_KEYS as _COMMON_SECRET_CONFIG_KEYS,
)
from .common import (
    STARTUP_ONLY_CONFIG_KEYS as _COMMON_STARTUP_ONLY_CONFIG_KEYS,
)
from .display import ConfigDisplayMixin
from .imports import get_config_resource
from .runtime import ConfigRuntimeMixin
from .snapshots import ConfigSnapshotMixin
from .validation import ConfigValidationMixin


class ConfigMixin(
    ConfigRuntimeMixin,
    ConfigDisplayMixin,
    ConfigValidationMixin,
    ConfigSnapshotMixin,
):
    CONFIG_KEYS = _COMMON_RUNTIME_WRITABLE_CONFIG_KEYS
    CONFIG_SECRET_KEYS = _COMMON_SECRET_CONFIG_KEYS
    STARTUP_ONLY_CONFIG_KEYS = _COMMON_STARTUP_ONLY_CONFIG_KEYS
    CONFIG_NEVER_WRITABLE_KEYS = _COMMON_NEVER_WRITABLE_CONFIG_KEYS

    """Combined configuration mixin."""


__all__ = ["ConfigMixin", "format_config_import_error", "get_config_resource"]
