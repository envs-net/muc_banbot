"""Opt-in live XMPP command integration contract.

This module intentionally keeps the default test suite offline.  When a full
Prosody/XMPP fixture is available, enable it with RUN_XMPP_INTEGRATION=1 and
provide the documented environment variables.  The test currently validates the
integration environment so CI jobs fail early with a clear message instead of
starting half-configured live clients.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.integration
def test_live_command_environment_contract(disable_integration_guard):
    required = {
        "BANBOT_TEST_JID": "bot account JID used by the live integration job",
        "BANBOT_TEST_PASSWORD": "password for BANBOT_TEST_JID",
        "BANBOT_TEST_ADMIN_ROOM": "admin MUC used for live command tests",
        "BANBOT_TEST_PROTECTED_ROOM": "protected MUC used for live command tests",
    }
    missing = [f"{name} ({description})" for name, description in required.items() if not os.environ.get(name)]
    assert not missing, "missing live XMPP integration settings: " + ", ".join(missing)
