#!/usr/bin/env python3
"""Smoke-test the built BanBot wheel using shared release tooling."""

from __future__ import annotations

from pathlib import Path

from _envs_xmpp_bootstrap import ensure_envs_xmpp

ensure_envs_xmpp()

from envs_xmpp_ops.release import WheelAsset, WheelCheckSpec, wheel_check_main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = WheelCheckSpec(
    distribution="muc-banbot",
    wheel_glob="muc_banbot-*.whl",
    console_script="muc_banbot",
    entry_point="banbot.cli:main",
    version_prefix="muc_banbot ",
    version_contains=("(envs-xmpp ",),
    assets=(
        WheelAsset(
            source="banbot/bundled/avatar.png",
            member="banbot/bundled/avatar.png",
            resolver="banbot.bundled_assets:bundled_asset",
            resolver_argument="avatar.png",
            expected_runtime_fragment="banbot/bundled/avatar.png",
        ),
    ),
    required_members=("config_sample.py",),
)


if __name__ == "__main__":
    raise SystemExit(wheel_check_main(root=ROOT, spec=SPEC))
