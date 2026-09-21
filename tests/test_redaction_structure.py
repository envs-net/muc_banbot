"""Architecture guards for the redaction subsystem split."""

from __future__ import annotations

from pathlib import Path

from banbot.redaction import RedactionMixin
from banbot.redaction_cleanup import RedactionCleanupMixin
from banbot.redaction_commands import RedactionCommandMixin
from banbot.redaction_confirmation import RedactionConfirmationMixin
from banbot.redaction_execution import RedactionExecutionMixin
from banbot.redaction_index import RedactionIndexMixin
from banbot.redaction_mam import RedactionMamMixin


def test_redaction_public_facade_stays_small() -> None:
    """Keep orchestration in focused modules instead of growing the facade again."""
    source = Path("banbot/redaction.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 120


def test_redaction_mixins_keep_domain_ownership() -> None:
    """Pin representative methods to their focused implementation modules."""
    assert "_redaction_index_message" in RedactionIndexMixin.__dict__
    assert "_redaction_confirmation_ids" in RedactionConfirmationMixin.__dict__
    assert "_redaction_verify_mam_tombstones" in RedactionMamMixin.__dict__
    assert "redact_jid_messages" in RedactionExecutionMixin.__dict__
    assert "redaction_cleanup_worker" in RedactionCleanupMixin.__dict__
    assert "cmd_redact" in RedactionCommandMixin.__dict__


def test_redaction_public_mixin_composes_all_domains() -> None:
    """The historical RedactionMixin surface must expose every split concern."""
    for name in (
        "_redaction_index_message",
        "_redaction_confirmation_ids",
        "_redaction_verify_mam_tombstones",
        "redact_jid_messages",
        "redaction_cleanup_worker",
        "cmd_redact",
    ):
        assert hasattr(RedactionMixin, name)
