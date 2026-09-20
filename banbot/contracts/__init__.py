"""Static host contracts for BanBot's cooperative mixins.

The concrete Protocols live in domain-focused modules.  This package re-exports
the historical ``banbot.contracts`` surface so existing type-checking imports
remain source-compatible.
"""

from .backup import (
    BackupArchiveMixinHost,
    BackupCommandMixinHost,
    BackupCreateMixinHost,
    BackupRestoreMixinHost,
    BackupVerifyMixinHost,
    CommandBackupMixinHost,
)
from .common import (
    ActorJidResolverHost,
)
from .config import (
    ConfigCommandMixinHost,
    ConfigDisplayMixinHost,
    ConfigRuntimeMixinHost,
    ConfigSnapshotMixinHost,
)
from .messaging import (
    DirectMessageMixinHost,
    MessagingMixinHost,
)
from .moderation import (
    AuditMixinHost,
    CommandModerationMixinHost,
    DatabaseMixinHost,
    ModerationMixinHost,
    SyncMixinHost,
)
from .omemo import (
    CommandOmemoMixinHost,
    OmemoCoreMixinHost,
    OmemoDeviceMixinHost,
    OmemoResetMixinHost,
    OmemoStatusMixinHost,
)
from .protections import (
    ProtectionActionsMixinHost,
    ProtectionChecksMixinHost,
    ProtectionCommandsMixinHost,
    ProtectionCoordinatorHost,
    ProtectionNotificationMixinHost,
    ProtectionStorageMixinHost,
)
from .redaction import (
    RedactionMixinHost,
)
from .rooms import (
    AdminMixinHost,
    BotOccupantMixinHost,
    CommandRoomsMixinHost,
    MucMixinHost,
    ProtectedRoomMixinHost,
    RoomInviteMixinHost,
)
from .rtbl import (
    CommandRtblMixinHost,
    RtblApplyMixinHost,
    RtblCommandMixinHost,
    RtblDatabaseMixinHost,
    RtblPublishMixinHost,
    RtblPubSubMixinHost,
    RtblRuntimeHost,
)
from .runtime import (
    AlertMixinHost,
    CommandEntryPointMixinHost,
    CommandHelpMixinHost,
    CommandIgnoreMixinHost,
    CommandImportExportMixinHost,
    CommandPolicyMixinHost,
    CommandRouterMixinHost,
    CommandRuntimeMixinHost,
    CommandUsageMixinHost,
    HealthCheckMixinHost,
    IgnorelistMixinHost,
    ImportExportMixinHost,
    OutboxMixinHost,
    ReleaseStateHost,
    UpdateMixinHost,
    VCardMixinHost,
)
from .status import (
    StatusHealthHost,
    StatusMixinHost,
)

__all__ = [
    "ActorJidResolverHost",
    "DatabaseMixinHost",
    "ModerationMixinHost",
    "CommandModerationMixinHost",
    "SyncMixinHost",
    "ProtectionCoordinatorHost",
    "ProtectionActionsMixinHost",
    "ProtectionChecksMixinHost",
    "ProtectionCommandsMixinHost",
    "ProtectionNotificationMixinHost",
    "ProtectionStorageMixinHost",
    "CommandRtblMixinHost",
    "RtblRuntimeHost",
    "RtblCommandMixinHost",
    "RtblDatabaseMixinHost",
    "RtblApplyMixinHost",
    "RtblPubSubMixinHost",
    "RtblPublishMixinHost",
    "CommandRoomsMixinHost",
    "BotOccupantMixinHost",
    "ProtectedRoomMixinHost",
    "RoomInviteMixinHost",
    "MucMixinHost",
    "AdminMixinHost",
    "CommandEntryPointMixinHost",
    "CommandRouterMixinHost",
    "CommandRuntimeMixinHost",
    "MessagingMixinHost",
    "DirectMessageMixinHost",
    "OmemoCoreMixinHost",
    "OmemoDeviceMixinHost",
    "OmemoResetMixinHost",
    "OmemoStatusMixinHost",
    "CommandOmemoMixinHost",
    "HealthCheckMixinHost",
    "OutboxMixinHost",
    "ReleaseStateHost",
    "UpdateMixinHost",
    "AlertMixinHost",
    "StatusHealthHost",
    "StatusMixinHost",
    "AuditMixinHost",
    "RedactionMixinHost",
    "BackupArchiveMixinHost",
    "BackupCreateMixinHost",
    "BackupRestoreMixinHost",
    "BackupVerifyMixinHost",
    "BackupCommandMixinHost",
    "CommandBackupMixinHost",
    "ImportExportMixinHost",
    "CommandImportExportMixinHost",
    "ConfigSnapshotMixinHost",
    "VCardMixinHost",
    "ConfigDisplayMixinHost",
    "ConfigRuntimeMixinHost",
    "ConfigCommandMixinHost",
    "IgnorelistMixinHost",
    "CommandIgnoreMixinHost",
    "CommandPolicyMixinHost",
    "CommandUsageMixinHost",
    "CommandHelpMixinHost",
]
