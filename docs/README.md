# BanBot Documentation

This directory contains the operator and developer documentation for BanBot.

## Operator Guides

* [Configuration](configuration.md) - required settings, runtime reload behavior, startup-only options, and systemd setup
* [Commands](commands.md) - full admin-room and protected-room command reference, including paging, `all` mode, room invites, and redaction commands
* [Admin Protection](admin-protection.md) - how BanBot protects admins/owners from manual, domain, nick-based, and RTBL bans
* [Import / Export](import-export.md) - CSV backup, migration, validation, duplicate handling, and restore workflow
* [Troubleshooting](troubleshooting.md) - common runtime, config, RTBL, OMEMO, and room-rights issues

## Feature Guides

* [Database](database.md) - SQLite tables and persisted state
* [OMEMO](omemo.md) - encrypted command/reply behavior, MUC recipients, fallback behavior, and storage notes
* [RTBL / PubSub](rtbl.md) - inbound RTBL subscriptions, snapshot reconciliation, stale-ban cleanup, and own publish feed
* [Prosody PubSub Setup](rtbl_pubsub-setup.md) - manual setup for RTBL publish nodes on Prosody

## Development and Release

* [Testing and CI](testing.md) - pytest, coverage, Drone CI, Hypothesis, mutmut, and opt-in live integration tests
* [Release Checklist](release-checklist.md) - pre-release checks, manual smoke tests, docs review, CI, tagging, and release notes

## Start Here

New operators should read:

1. [README](../README.md)
2. [Configuration](configuration.md)
3. [Commands](commands.md)
4. [Troubleshooting](troubleshooting.md)

Operators using RTBL or OMEMO should additionally read:

* [OMEMO](omemo.md)
* [RTBL / PubSub](rtbl.md)
* [Prosody PubSub Setup](rtbl_pubsub-setup.md), if using BanBot's own RTBL publish feed on Prosody
