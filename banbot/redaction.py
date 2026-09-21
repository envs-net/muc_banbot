"""Message redaction index and XEP-0425 moderation helpers.

The public ``RedactionMixin`` API remains here while implementation concerns are
split into focused internal mixins.
"""

from __future__ import annotations

# Keep asyncio imported from this compatibility module: existing tests and
# downstream diagnostics patch ``banbot.redaction.asyncio.sleep``. All modules
# observe the same asyncio module object.
import asyncio as asyncio

from .redaction_cleanup import RedactionCleanupMixin
from .redaction_commands import RedactionCommandMixin
from .redaction_common import (
    FASTEN_NS,
    LEGACY_MODERATE_NS,
    LEGACY_RETRACT_NS,
    MAM_NS,
    MODERATE_NS,
    REDACTION_CLEANUP_INTERVAL_SECONDS,
    REDACTION_IQ_TIMEOUT_SECONDS,
    REDACTION_MAM_VERIFY_BATCH_SIZE,
    REDACTION_MAM_VERIFY_MAX_MESSAGES,
    REDACTION_MAM_VERIFY_WINDOW_SECONDS,
    RETRACT_NS,
    SID_NS,
    XDATA_NS,
    RedactionSummary,
)
from .redaction_common import (
    _redaction_error_is_already_retracted as _redaction_error_is_already_retracted,
)
from .redaction_common import (
    _redaction_error_is_unconfirmed as _redaction_error_is_unconfirmed,
)
from .redaction_common import (
    _redaction_exception_condition as _redaction_exception_condition,
)
from .redaction_common import (
    _redaction_exception_summary as _redaction_exception_summary,
)
from .redaction_common import (
    _xml_local_name as _xml_local_name,
)
from .redaction_common import (
    _xml_namespace as _xml_namespace,
)
from .redaction_confirmation import RedactionConfirmationMixin
from .redaction_execution import RedactionExecutionMixin
from .redaction_index import RedactionIndexMixin
from .redaction_mam import RedactionMamMixin


class RedactionMixin(
    RedactionIndexMixin,
    RedactionConfirmationMixin,
    RedactionMamMixin,
    RedactionExecutionMixin,
    RedactionCleanupMixin,
    RedactionCommandMixin,
):
    """Compose the redaction subsystem while preserving its public API."""

    pass


__all__ = [
    "FASTEN_NS",
    "LEGACY_MODERATE_NS",
    "LEGACY_RETRACT_NS",
    "MAM_NS",
    "MODERATE_NS",
    "REDACTION_CLEANUP_INTERVAL_SECONDS",
    "REDACTION_IQ_TIMEOUT_SECONDS",
    "REDACTION_MAM_VERIFY_BATCH_SIZE",
    "REDACTION_MAM_VERIFY_MAX_MESSAGES",
    "REDACTION_MAM_VERIFY_WINDOW_SECONDS",
    "RETRACT_NS",
    "RedactionMixin",
    "RedactionSummary",
    "SID_NS",
    "XDATA_NS",
]
