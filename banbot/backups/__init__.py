"""Managed backup package."""

import config

from .archive import BackupArchiveMixin
from .base import BackupBaseMixin
from .commands import BackupCommandMixin
from .create import BackupCreateMixin
from .restore import BackupRestoreMixin
from .verify import BackupVerifyMixin
from .common import DatabaseBackup


class BackupMixin(
    BackupCommandMixin,
    BackupRestoreMixin,
    BackupCreateMixin,
    BackupVerifyMixin,
    BackupArchiveMixin,
    BackupBaseMixin,
):
    """Combined managed backup mixin."""


__all__ = ["BackupMixin", "DatabaseBackup", "config"]
