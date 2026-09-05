"""Managed backup package."""

import config

from .archive import BackupArchiveMixin
from .base import BackupBaseMixin
from .commands import BackupCommandMixin
from .common import DatabaseBackup
from .create import BackupCreateMixin
from .restore import BackupRestoreMixin
from .verify import BackupVerifyMixin


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
