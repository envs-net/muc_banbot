#!/usr/bin/env python3
"""Check installed dependency closure against a reviewed constraints file."""

from envs_xmpp_ops.constraints import check_constraints, main

__all__ = ["check_constraints", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
