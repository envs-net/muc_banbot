"""Lightweight console entry point for muc_banbot.

Version reporting intentionally avoids importing :mod:`banbot.bot`, because the
runtime module loads operator configuration during import.
"""

from __future__ import annotations

import sys

from envs_xmpp_core import __version__ as envs_xmpp_version

from ._version import __version__


def _run_bot() -> None:
    from .bot import main as bot_main

    bot_main()


def main(argv: list[str] | None = None) -> int:
    """Run the BanBot or print version information without loading config."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (["--version"], ["-V"]):
        print(f"muc_banbot {__version__} (envs-xmpp {envs_xmpp_version})")
        return 0

    _run_bot()
    return 0
