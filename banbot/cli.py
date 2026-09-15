"""Lightweight console entry point for muc_banbot.

Version/help reporting intentionally avoids importing :mod:`banbot.bot`,
because the runtime module loads operator configuration during import.
"""

from __future__ import annotations

import argparse
import sys

from envs_xmpp_core import __version__ as envs_xmpp_version

from ._version import __version__


def _run_bot() -> None:
    from .bot import main as bot_main

    bot_main()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muc_banbot",
        description="Run the muc_banbot XMPP moderation service.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="show muc_banbot and envs-xmpp versions and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run BanBot or handle lightweight CLI metadata without loading config.

    Unknown arguments are rejected by argparse instead of silently starting the
    production bot.  This is important for operator typos such as ``--hepl`` or
    an accidentally malformed ``--version`` invocation.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = _build_parser().parse_args(arguments)
    if options.version:
        print(f"muc_banbot {__version__} (envs-xmpp {envs_xmpp_version})")
        return 0

    _run_bot()
    return 0
