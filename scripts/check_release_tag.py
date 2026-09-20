#!/usr/bin/env python3
"""Require a release tag to match the BanBot package version exactly."""

from __future__ import annotations

from pathlib import Path

from _envs_xmpp_bootstrap import ensure_envs_xmpp

ensure_envs_xmpp()

from envs_xmpp_ops.release import ReleaseTagSpec, release_tag_main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    raise SystemExit(
        release_tag_main(
            root=ROOT,
            spec=ReleaseTagSpec("banbot/_version.py"),
        )
    )
