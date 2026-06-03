# BanBot Documentation

This directory contains the operator and developer documentation for BanBot.

## Start Here

New operators should read:

1. [Project README](../README.md)
2. [Configuration](configuration.md)
3. [Commands](commands.md)
4. [Backups and Restore](backups.md)
5. [Troubleshooting](troubleshooting.md)

## Operator Guides

* [Configuration](configuration.md) - required settings, runtime-writable settings, startup-only settings, validation, output modes, and systemd setup
* [Commands](commands.md) - compact admin-room and protected-room command reference aligned with runtime `!help`
* [Backups and Restore](backups.md) - managed ZIP backup archives, manifest format, verification, restore, safety backups, and legacy compatibility
* [Rooms and Invites](rooms.md) - protected rooms, room lifecycle, room invites, invite expiry, and cleanup
* [Import / Export](import-export.md) - managed CSV exports, import dry-runs, deduplication, and import safety backups
* [OMEMO](omemo.md) - optional encrypted command/reply behavior, storage, identity reset, and backup integration
* [RTBL / PubSub](rtbl.md) - inbound RTBL subscriptions, snapshot reconciliation, stale-ban cleanup, and own publish feed
* [Prosody PubSub Setup](rtbl_pubsub-setup.md) - manual setup for RTBL publish nodes on Prosody
* [Public Policy / Rules](policy.md) - public `!rules` text, placeholders, enable/disable behavior, and admin commands
* [Admin Protection](admin-protection.md) - how BanBot protects admins/owners from manual, domain, nick-based, and RTBL bans
* [Troubleshooting](troubleshooting.md) - common runtime, config, RTBL, OMEMO, backup, and room-rights issues

## Reference Guides

* [Database](database.md) - SQLite tables and persisted state

## Development and Release

* [Testing and CI](testing.md) - pytest, coverage, Drone CI, Hypothesis, mutmut, and opt-in live integration tests
* [Release Checklist](release-checklist.md) - pre-release checks, smoke tests, docs review, CI, tagging, and release notes

## Topic Map

| Topic | Primary doc |
| --- | --- |
| Runtime config and `!config` | [Configuration](configuration.md) |
| Long output paging | [Configuration](configuration.md#output-modes-and-paging), [Commands](commands.md#paging-and-long-output) |
| ZIP backups and restore | [Backups and Restore](backups.md) |
| CSV import/export | [Import / Export](import-export.md) |
| Protected room lifecycle | [Rooms and Invites](rooms.md) |
| Room invites | [Rooms and Invites](rooms.md#room-invites) |
| OMEMO | [OMEMO](omemo.md) |
| RTBL subscriptions/publish | [RTBL / PubSub](rtbl.md) |
| Public `!rules` | [Public Policy / Rules](policy.md) |
| Schema details | [Database](database.md) |
| Tests and mutation tests | [Testing and CI](testing.md) |
