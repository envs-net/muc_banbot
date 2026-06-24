#!/usr/bin/env python3
"""Live XMPP smoke test for BanBot protections.

This script is intentionally not part of the normal pytest/CI suite. It connects
real XMPP accounts to real MUC rooms and can trigger moderation actions,
tempbans/bans, room configuration changes, and admin-room notifications.

Use only dedicated test accounts and dedicated test rooms.

Default mode is safe: actions are set to notify where possible.
Use --destructive for real tempban/ban/redaction actions.
Use --skip-joinwave to skip joinwave-related scenarios when needed.
The script announces every test in the admin room and waits 5s between tests by default.

Requirements:
    pip install slixmpp

Example:
    python3 live_protection_smoke.py \
      --admin-jid creme@example.org \
      --admin-password 'secret' \
      --admin-room admin@conference.example.org \
      --protected-room test@conference.example.org \
      --test-jid spamtest@example.org \
      --test-password 'secret' \
      --domain example.org \
      --bot-command-prefix .
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import string
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

try:
    import slixmpp
except ImportError as exc:  # pragma: no cover - operator convenience path
    raise SystemExit(
        "slixmpp is required. Install it with 'pip install slixmpp' "
        "(or install your project's requirements file) and retry."
    ) from exc

log = logging.getLogger("protection-smoke")

FLOOD_MESSAGE_COUNT = 6
SIMILAR_MESSAGE_COUNT = 3
JOINWAVE_CLIENT_COUNT = 5
JOIN_NICK_TOKEN_LENGTH = 3


@dataclass(frozen=True)
class SmokeConfig:
    admin_jid: str
    admin_password: str
    admin_room: str
    protected_room: str
    test_jid: str
    test_password: str
    domain: str
    command_prefix: str
    admin_nick: str
    test_nick: str
    join_nick_prefix: str
    pause_between_tests: float
    command_delay: float
    join_delay: float
    destructive: bool
    skip_joinwave: bool
    skip_reporters: bool


class SmokeClient(slixmpp.ClientXMPP):
    """Small Slixmpp client used by the live protection smoke test."""

    def __init__(self, jid: str, password: str, nick: str) -> None:
        super().__init__(jid, password)
        self.nick = nick
        self.ready = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.register_plugin("xep_0030")
        self.register_plugin("xep_0045")

    async def _on_session_start(self, _event: Any) -> None:
        self.ready.set()

    def _on_disconnected(self, _event: Any) -> None:
        self.disconnected.set()

    async def start(self) -> None:
        await maybe_await(self.connect())
        await asyncio.wait_for(self.ready.wait(), timeout=30)

    async def join(self, room: str) -> None:
        await maybe_await(self.plugin["xep_0045"].join_muc(room, self.nick))
        await asyncio.sleep(1)

    async def groupchat(self, room: str, body: str) -> None:
        await maybe_await(
            self.send_message(mto=room, mbody=body, mtype="groupchat")
        )

    def _jid_for_log(self) -> str:
        """Return the best-effort JID string for log messages."""
        boundjid = getattr(self, "boundjid", None)
        bare_jid = getattr(boundjid, "bare", None)
        return bare_jid or self.jid

    async def stop(self) -> None:
        await maybe_await(self.disconnect())
        try:
            await asyncio.wait_for(self.disconnected.wait(), timeout=5)
        except TimeoutError:
            log.debug("Timed out waiting for %s to disconnect cleanly.", self._jid_for_log())
        # Give Slixmpp filter tasks a short chance to settle before the next test.
        await asyncio.sleep(0.3)


async def maybe_await(value: Any) -> Any:
    """Await value when it is awaitable and return the final result."""
    if isinstance(value, Awaitable):
        return await value
    return value


def env_default(name: str, fallback: str | None = None) -> str | None:
    """Return a non-empty environment value or fallback."""
    value = os.environ.get(name)
    return value if value else fallback


def random_token(length: int = 5) -> str:
    """Return a short random token used for dummy targets and nicks."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


async def admin_command(admin: SmokeClient, cfg: SmokeConfig, command: str) -> None:
    """Send an admin-room command and wait briefly for the bot response."""
    log.info("ADMIN -> %s", command)
    await admin.groupchat(cfg.admin_room, command)
    await asyncio.sleep(cfg.command_delay)


async def announce(admin: SmokeClient, cfg: SmokeConfig, title: str) -> None:
    """Announce the current smoke scenario in the admin room and local log."""
    message = f"🧪 Protection smoke test: {title}"
    log.info(message)
    await admin.groupchat(cfg.admin_room, message)
    await asyncio.sleep(1)


async def pause(cfg: SmokeConfig) -> None:
    """Pause between smoke scenarios to avoid protection overlap."""
    if cfg.pause_between_tests <= 0:
        return
    log.info("Pausing %.1fs before next protection test", cfg.pause_between_tests)
    await asyncio.sleep(cfg.pause_between_tests)


async def with_test_client(
    cfg: SmokeConfig,
    nick: str,
    scenario: Callable[[SmokeClient], Awaitable[None]],
) -> None:
    """Connect one temporary test client, join the protected room, run scenario."""
    client = SmokeClient(cfg.test_jid, cfg.test_password, nick)
    await client.start()
    try:
        await client.join(cfg.protected_room)
        await scenario(client)
        await asyncio.sleep(3)
    finally:
        await client.stop()


async def run_policy_notification(admin: SmokeClient, cfg: SmokeConfig) -> None:
    target = f"protection-smoke-{random_token()}@{cfg.domain}"
    await announce(admin, cfg, f"PolicyChangeNotification — dummy target {target}")
    await admin_command(admin, cfg, f"{cfg.command_prefix}ban {target} protection smoke policy notification")
    await admin_command(admin, cfg, f"{cfg.command_prefix}unban {target}")


async def run_first_media(admin: SmokeClient, cfg: SmokeConfig) -> None:
    await announce(admin, cfg, "FirstMessageMediaProtection — first message is a media URL")

    async def scenario(client: SmokeClient) -> None:
        await client.groupchat(cfg.protected_room, "https://example.invalid/protection-smoke.jpg")

    await with_test_client(cfg, cfg.test_nick, scenario)
    await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")


async def run_flood(admin: SmokeClient, cfg: SmokeConfig) -> None:
    await announce(
        admin,
        cfg,
        f"FloodSpamProtection — send {FLOOD_MESSAGE_COUNT} quick messages",
    )

    async def scenario(client: SmokeClient) -> None:
        for index in range(FLOOD_MESSAGE_COUNT):
            await client.groupchat(cfg.protected_room, f"protection smoke flood message {index}")
            await asyncio.sleep(0.25)

    await with_test_client(cfg, cfg.test_nick, scenario)
    await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")


async def run_mentions(admin: SmokeClient, cfg: SmokeConfig) -> None:
    await announce(admin, cfg, "MentionLimitProtection — mention two known occupants")

    async def scenario(client: SmokeClient) -> None:
        mention_a = f"{cfg.join_nick_prefix}a"
        mention_b = f"{cfg.join_nick_prefix}b"
        await client.groupchat(cfg.protected_room, f"hi {cfg.admin_nick} {mention_a} {mention_b}")

    # Create two occupants so mention matching has known room nicks to count.
    helpers = [
        SmokeClient(cfg.test_jid, cfg.test_password, f"{cfg.join_nick_prefix}a"),
        SmokeClient(cfg.test_jid, cfg.test_password, f"{cfg.join_nick_prefix}b"),
    ]
    for helper in helpers:
        await helper.start()
        await helper.join(cfg.protected_room)
    try:
        await with_test_client(cfg, cfg.test_nick, scenario)
    finally:
        for helper in helpers:
            await helper.stop()
    await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")


async def run_wordlist(admin: SmokeClient, cfg: SmokeConfig) -> None:
    await announce(admin, cfg, "WordListNewJoinerProtection — new joiner sends monitored word")

    async def scenario(client: SmokeClient) -> None:
        await client.groupchat(cfg.protected_room, "zz-protection-smoke")

    await with_test_client(cfg, cfg.test_nick, scenario)
    await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")


async def run_similar(admin: SmokeClient, cfg: SmokeConfig) -> None:
    await announce(
        admin,
        cfg,
        "SimilarMessageProtection — disables flood and joinwave; "
        f"sends {SIMILAR_MESSAGE_COUNT} repeated messages",
    )
    await admin_command(admin, cfg, f"{cfg.command_prefix}protection disable flood")
    await admin_command(admin, cfg, f"{cfg.command_prefix}protection disable joinwave")

    async def scenario(client: SmokeClient) -> None:
        message = "this is a repeated protection smoke message with enough words"
        for _ in range(SIMILAR_MESSAGE_COUNT):
            await client.groupchat(cfg.protected_room, message)
            await asyncio.sleep(0.5)

    ban_possible = False
    try:
        await with_test_client(cfg, cfg.test_nick, scenario)
        ban_possible = True
    finally:
        await admin_command(admin, cfg, f"{cfg.command_prefix}protection enable flood")
        if ban_possible:
            await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")


async def run_joinwave(admin: SmokeClient, cfg: SmokeConfig) -> None:
    if cfg.skip_joinwave:
        return
    await announce(admin, cfg, "JoinWaveShortCircuitProtection — several occupants join quickly")
    await admin_command(admin, cfg, f"{cfg.command_prefix}protection enable joinwave")
    clients: list[SmokeClient] = []
    try:
        for index in range(JOINWAVE_CLIENT_COUNT):
            client = SmokeClient(
                cfg.test_jid,
                cfg.test_password,
                f"{cfg.join_nick_prefix}{index}-{random_token(JOIN_NICK_TOKEN_LENGTH)}",
            )
            clients.append(client)
            await client.start()
            await client.join(cfg.protected_room)
            await asyncio.sleep(cfg.join_delay)
        await asyncio.sleep(5)
    finally:
        for client in clients:
            await client.stop()
        await admin_command(admin, cfg, f"{cfg.command_prefix}protection disable joinwave")


async def run_trusted_reporters(admin: SmokeClient, cfg: SmokeConfig) -> None:
    if cfg.skip_reporters:
        return
    target = f"reported-{random_token()}@{cfg.domain}"
    await announce(admin, cfg, f"TrustedReporters — trusted reporter reports {target}")
    await admin_command(admin, cfg, f"{cfg.command_prefix}report {target} protection smoke report")


async def run_smoke(cfg: SmokeConfig) -> None:
    """Run all protection smoke scenarios."""
    admin = SmokeClient(cfg.admin_jid, cfg.admin_password, cfg.admin_nick)
    await admin.start()
    try:
        await admin.join(cfg.admin_room)
        await pause(cfg)
        scenarios = [
            run_policy_notification,
            run_first_media,
            run_flood,
            run_mentions,
            run_wordlist,
            run_similar,
            run_joinwave,
            run_trusted_reporters,
        ]
        for scenario in scenarios:
            await scenario(admin, cfg)
            await pause(cfg)

        log.info("Cleanup: show protections and try to unban test JID")
        await admin_command(admin, cfg, f"{cfg.command_prefix}unban {cfg.test_jid}")
        if not cfg.skip_joinwave:
            await admin_command(admin, cfg, f"{cfg.command_prefix}protection disable joinwave")
        await admin_command(admin, cfg, f"{cfg.command_prefix}protections list all")
        log.info("Done. Check the admin room for protection notifications/results.")
    finally:
        await admin.stop()


def parse_args(argv: list[str]) -> SmokeConfig:
    """Parse command-line arguments and validate smoke-test safety settings."""
    admin_jid_default = env_default("BANBOT_SMOKE_ADMIN_JID")
    admin_password_default = env_default("BANBOT_SMOKE_ADMIN_PASSWORD")
    admin_room_default = env_default("BANBOT_SMOKE_ADMIN_ROOM")
    protected_room_default = env_default("BANBOT_SMOKE_PROTECTED_ROOM")
    test_jid_default = env_default("BANBOT_SMOKE_TEST_JID")
    test_password_default = env_default("BANBOT_SMOKE_TEST_PASSWORD")
    domain_default = env_default("BANBOT_SMOKE_DOMAIN", "example.org")
    command_prefix_default = env_default("BANBOT_SMOKE_COMMAND_PREFIX", "!")
    admin_nick_default = env_default(
        "BANBOT_SMOKE_ADMIN_NICK",
        "protection-admin-smoke",
    )
    test_nick_default = env_default(
        "BANBOT_SMOKE_TEST_NICK",
        "protection-test-smoke",
    )
    join_nick_prefix_default = env_default(
        "BANBOT_SMOKE_JOIN_NICK_PREFIX",
        "protection-join-",
    )
    pause_between_tests_default = env_default(
        "BANBOT_SMOKE_PAUSE_BETWEEN_TESTS",
        "5",
    )
    command_delay_default = env_default("BANBOT_SMOKE_COMMAND_DELAY", "2")
    join_delay_default = env_default("BANBOT_SMOKE_JOIN_DELAY", "1")

    parser = argparse.ArgumentParser(
        description=(
            "Run a destructive live smoke test for BanBot protections against "
            "dedicated XMPP test rooms."
        ),
    )
    parser.add_argument(
        "--admin-jid",
        default=admin_jid_default,
        required=not admin_jid_default,
    )
    parser.add_argument(
        "--admin-password",
        default=admin_password_default,
        required=not admin_password_default,
    )
    parser.add_argument(
        "--admin-room",
        default=admin_room_default,
        required=not admin_room_default,
    )
    parser.add_argument(
        "--protected-room",
        default=protected_room_default,
        required=not protected_room_default,
    )
    parser.add_argument(
        "--test-jid",
        default=test_jid_default,
        required=not test_jid_default,
    )
    parser.add_argument(
        "--test-password",
        default=test_password_default,
        required=not test_password_default,
    )
    parser.add_argument("--domain", default=domain_default)
    parser.add_argument("--bot-command-prefix", default=command_prefix_default)
    parser.add_argument("--admin-nick", default=admin_nick_default)
    parser.add_argument("--test-nick", default=test_nick_default)
    parser.add_argument("--join-nick-prefix", default=join_nick_prefix_default)
    parser.add_argument(
        "--pause-between-tests",
        type=float,
        default=float(pause_between_tests_default),
    )
    parser.add_argument(
        "--command-delay",
        type=float,
        default=float(command_delay_default),
    )
    parser.add_argument(
        "--join-delay",
        type=float,
        default=float(join_delay_default),
    )
    parser.add_argument(
        "--destructive",
        action="store_true",
        help=(
            "Mandatory safety acknowledgement. This script refuses to run unless "
            "--destructive is explicitly provided, confirming dedicated test "
            "accounts/rooms are being used."
        ),
    )
    parser.add_argument(
        "--skip-joinwave",
        action="store_true",
        help="Skip the JoinWaveShortCircuitProtection scenario.",
    )
    parser.add_argument(
        "--skip-reporters",
        action="store_true",
        help="Skip the TrustedReporters scenario.",
    )
    args = parser.parse_args(argv)

    if not args.destructive:
        parser.error(
            "--destructive is required. Use dedicated test rooms/accounts only."
        )

    return SmokeConfig(
        admin_jid=args.admin_jid,
        admin_password=args.admin_password,
        admin_room=args.admin_room,
        protected_room=args.protected_room,
        test_jid=args.test_jid,
        test_password=args.test_password,
        domain=args.domain,
        command_prefix=args.bot_command_prefix,
        admin_nick=args.admin_nick,
        test_nick=args.test_nick,
        join_nick_prefix=args.join_nick_prefix,
        pause_between_tests=args.pause_between_tests,
        command_delay=args.command_delay,
        join_delay=args.join_delay,
        destructive=args.destructive,
        skip_joinwave=args.skip_joinwave,
        skip_reporters=args.skip_reporters,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = parse_args(argv or sys.argv[1:])
    asyncio.run(run_smoke(cfg))


if __name__ == "__main__":
    main()
