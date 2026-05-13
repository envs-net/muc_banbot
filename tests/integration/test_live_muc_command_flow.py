"""Opt-in live MUC command smoke test against an already running BanBot.

This test is intentionally skipped unless RUN_XMPP_INTEGRATION=1 is set.  It
uses a separate sender account to join a test MUC, send a command and wait for a
BanBot response.  It is meant for a dedicated Prosody test environment, not the
normal offline CI job.
"""

from __future__ import annotations

import asyncio
import os

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_muc_command_gets_bot_response():
    if os.environ.get("RUN_XMPP_INTEGRATION") != "1":
        pytest.skip("set RUN_XMPP_INTEGRATION=1 to run live XMPP integration tests")

    slixmpp = pytest.importorskip("slixmpp")

    required = [
        "BANBOT_TEST_SENDER_JID",
        "BANBOT_TEST_SENDER_PASSWORD",
        "BANBOT_TEST_PROTECTED_ROOM",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip("missing live command test settings: " + ", ".join(missing))

    jid = os.environ["BANBOT_TEST_SENDER_JID"]
    password = os.environ["BANBOT_TEST_SENDER_PASSWORD"]
    room = os.environ["BANBOT_TEST_PROTECTED_ROOM"]
    nick = os.environ.get("BANBOT_TEST_SENDER_NICK", "pytest-user")
    command = os.environ.get("BANBOT_TEST_COMMAND", "!help")
    expected = os.environ.get("BANBOT_TEST_EXPECT", "BanBot")
    timeout = float(os.environ.get("BANBOT_TEST_TIMEOUT", "20"))

    class ProbeClient(slixmpp.ClientXMPP):
        def __init__(self):
            super().__init__(jid, password)
            self.response = asyncio.get_event_loop().create_future()
            self.add_event_handler("session_start", self._on_start)
            self.add_event_handler("groupchat_message", self._on_groupchat_message)

        async def _on_start(self, _event):
            self.plugin["xep_0045"].join_muc(room, nick)
            await asyncio.sleep(2)
            self.send_message(mto=room, mbody=command, mtype="groupchat")

        def _on_groupchat_message(self, msg):
            body = str(msg.get("body", ""))
            mucnick = str(msg.get("mucnick", ""))
            if mucnick == nick:
                return
            if expected in body and not self.response.done():
                self.response.set_result(body)

    client = ProbeClient()
    client.register_plugin("xep_0030")
    client.register_plugin("xep_0045")
    client.connect()

    try:
        response = await asyncio.wait_for(client.response, timeout=timeout)
        assert expected in response
    finally:
        client.disconnect()
