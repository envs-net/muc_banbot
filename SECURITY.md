# Security Policy

BanBot is an XMPP moderation and ban-management bot. Security-sensitive reports should be handled carefully because issues may affect protected rooms, admin permissions, ban synchronization, RTBL handling, OMEMO behavior, or server-side moderation workflows.

## Supported Versions

Security fixes are generally made against the current `main` branch and the latest released version.

Older releases may not receive separate security patches unless the maintainer explicitly decides otherwise.

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Report security-sensitive issues privately to the project maintainer through the preferred envs.net contact channel or by email if listed in the repository metadata.

When reporting, include as much relevant detail as possible:

* Affected BanBot version or commit
* Relevant configuration, with secrets removed
* Steps to reproduce
* Expected vs. actual behavior
* Logs, with JIDs/tokens/passwords redacted where needed
* Whether the issue affects admin authorization, ban enforcement, RTBL, OMEMO, imports, or database state

## What Counts as Security-Sensitive?

Examples:

* Bypassing admin-room authorization
* Banning or unbanning protected admins/owners
* Applying bans to unintended targets
* RTBL data causing unsafe ban behavior
* Import/export behavior that corrupts or exposes data
* Command injection or unsafe file handling
* OMEMO behavior that leaks encrypted-only replies into plaintext when not configured to do so
* Exposure of credentials, tokens, OMEMO storage, or private database contents

## Non-Sensitive Bugs

General bugs, usability problems, documentation issues, and feature requests can be reported using the normal issue templates.

## Responsible Disclosure

Please give the maintainer reasonable time to investigate and prepare a fix before public disclosure.

The maintainer may publish a security note, changelog entry, or release once a fix is available.
