#!/usr/bin/env python3
"""Check the reviewed constraints snapshot for the active Python interpreter."""

from envs_xmpp_ops.constraints import current_main


def main() -> int:
    return current_main()


if __name__ == "__main__":
    raise SystemExit(main())
