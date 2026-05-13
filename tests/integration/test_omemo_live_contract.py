"""Opt-in OMEMO live-test contract.

Real OMEMO interoperability requires actual accounts/devices and should not run
as part of the normal CI suite.  This test documents the knobs needed for a
future manual/live OMEMO smoke job.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.integration
@pytest.mark.omemo
def test_omemo_live_environment_contract():
    if os.environ.get("RUN_OMEMO_INTEGRATION") != "1":
        pytest.skip("set RUN_OMEMO_INTEGRATION=1 to run live OMEMO integration checks")

    required = {
        "BANBOT_TEST_JID": "bot account JID with OMEMO enabled",
        "BANBOT_TEST_PASSWORD": "password for BANBOT_TEST_JID",
        "BANBOT_TEST_OMEMO_ROOM": "MUC where live OMEMO command/reply checks may run",
        "BANBOT_TEST_OMEMO_PEER_JID": "peer account with at least one valid OMEMO device",
    }
    missing = [f"{name} ({description})" for name, description in required.items() if not os.environ.get(name)]
    assert not missing, "missing live OMEMO integration settings: " + ", ".join(missing)
