"""Regression tests for the domain-split host-contract package."""

from __future__ import annotations

import importlib

import banbot.contracts as contracts

EXPECTED_EXPORTS = [
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

DOMAIN_EXPORTS = {
    "common": ["ActorJidResolverHost"],
    "moderation": [
        "DatabaseMixinHost",
        "ModerationMixinHost",
        "CommandModerationMixinHost",
        "SyncMixinHost",
        "AuditMixinHost",
    ],
    "protections": [
        "ProtectionCoordinatorHost",
        "ProtectionActionsMixinHost",
        "ProtectionChecksMixinHost",
        "ProtectionCommandsMixinHost",
        "ProtectionNotificationMixinHost",
        "ProtectionStorageMixinHost",
    ],
    "rtbl": [
        "CommandRtblMixinHost",
        "RtblRuntimeHost",
        "RtblCommandMixinHost",
        "RtblDatabaseMixinHost",
        "RtblApplyMixinHost",
        "RtblPubSubMixinHost",
        "RtblPublishMixinHost",
    ],
    "rooms": [
        "CommandRoomsMixinHost",
        "BotOccupantMixinHost",
        "ProtectedRoomMixinHost",
        "RoomInviteMixinHost",
        "MucMixinHost",
        "AdminMixinHost",
    ],
    "messaging": ["MessagingMixinHost", "DirectMessageMixinHost"],
    "omemo": [
        "OmemoCoreMixinHost",
        "OmemoDeviceMixinHost",
        "OmemoResetMixinHost",
        "OmemoStatusMixinHost",
        "CommandOmemoMixinHost",
    ],
    "runtime": [
        "CommandEntryPointMixinHost",
        "CommandRouterMixinHost",
        "CommandRuntimeMixinHost",
        "HealthCheckMixinHost",
        "OutboxMixinHost",
        "ReleaseStateHost",
        "UpdateMixinHost",
        "AlertMixinHost",
        "ImportExportMixinHost",
        "CommandImportExportMixinHost",
        "VCardMixinHost",
        "IgnorelistMixinHost",
        "CommandIgnoreMixinHost",
        "CommandPolicyMixinHost",
        "CommandUsageMixinHost",
        "CommandHelpMixinHost",
    ],
    "status": ["StatusHealthHost", "StatusMixinHost"],
    "redaction": ["RedactionMixinHost"],
    "backup": [
        "BackupArchiveMixinHost",
        "BackupCreateMixinHost",
        "BackupRestoreMixinHost",
        "BackupVerifyMixinHost",
        "BackupCommandMixinHost",
        "CommandBackupMixinHost",
    ],
    "config": [
        "ConfigSnapshotMixinHost",
        "ConfigDisplayMixinHost",
        "ConfigRuntimeMixinHost",
        "ConfigCommandMixinHost",
    ],
}


def test_legacy_contract_surface_is_preserved() -> None:
    assert contracts.__all__ == EXPECTED_EXPORTS
    assert all(getattr(contracts, name) is not None for name in EXPECTED_EXPORTS)


def test_domain_modules_reexport_same_protocol_objects() -> None:
    for module_name, names in DOMAIN_EXPORTS.items():
        module = importlib.import_module(f"banbot.contracts.{module_name}")
        for name in names:
            assert getattr(contracts, name) is getattr(module, name)


def test_shared_protocol_inheritance_is_preserved() -> None:
    actor = contracts.ActorJidResolverHost
    assert actor in contracts.CommandModerationMixinHost.__mro__
    assert actor in contracts.ProtectionCommandsMixinHost.__mro__
    assert actor in contracts.CommandRtblMixinHost.__mro__
    assert actor in contracts.CommandEntryPointMixinHost.__mro__
    assert actor in contracts.CommandRuntimeMixinHost.__mro__
    assert actor in contracts.CommandOmemoMixinHost.__mro__
    assert actor in contracts.CommandBackupMixinHost.__mro__
    assert actor in contracts.CommandImportExportMixinHost.__mro__
    assert actor in contracts.CommandIgnoreMixinHost.__mro__

    assert contracts.ProtectionCoordinatorHost in contracts.ProtectionChecksMixinHost.__mro__
    assert contracts.RtblRuntimeHost in contracts.RtblCommandMixinHost.__mro__
    assert contracts.RtblRuntimeHost in contracts.RtblDatabaseMixinHost.__mro__
    assert contracts.RtblRuntimeHost in contracts.RtblApplyMixinHost.__mro__
    assert contracts.RtblRuntimeHost in contracts.RtblPubSubMixinHost.__mro__
    assert contracts.RtblRuntimeHost in contracts.RtblPublishMixinHost.__mro__
    assert contracts.BotOccupantMixinHost in contracts.AdminMixinHost.__mro__
    assert contracts.ReleaseStateHost in contracts.UpdateMixinHost.__mro__
    assert contracts.StatusHealthHost in contracts.StatusMixinHost.__mro__
