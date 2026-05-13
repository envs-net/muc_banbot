"""Optional live integration test placeholder.

These tests are intentionally opt-in because they require a running XMPP server,
real accounts, test MUCs and credentials.  They document the environment knobs
needed for a future full Prosody integration job without making the normal test
suite depend on network services.
"""

import os

import pytest


@pytest.mark.integration
def test_xmpp_integration_environment_is_explicit(disable_integration_guard):
    required = [
        "BANBOT_TEST_JID",
        "BANBOT_TEST_PASSWORD",
        "BANBOT_TEST_ADMIN_ROOM",
        "BANBOT_TEST_PROTECTED_ROOM",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    assert not missing, f"missing integration environment variables: {', '.join(missing)}"
