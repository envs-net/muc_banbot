# Protections

BanBot includes a protection subsystem for protected XMPP MUC rooms.
Protections are stored in SQLite and can be enabled, disabled, and tuned at runtime from the admin room.

## Commands

| Command | Description |
| --- | --- |
| `!protections list [all\|page\|last]` | Lists all protections with 🟢 enabled / 🔴 disabled / 👁️ observe state and their short alias |
| `!protection enable <name>` | Enables a protection |
| `!protection disable <name>` | Disables a protection |
| `!protections <name> config` | Shows the current config for one protection |
| `!protections <name> set <key> <value>` | Updates one config value for a protection |
| `!protections <name> reset` | Resets one protection to its built-in defaults |
| `!protections reporters add <jid>` | Adds a trusted reporter JID |
| `!protections reporters remove <jid>` | Removes a trusted reporter JID (`delete`, `del`, and `rm` also work) |
| `!protections reporters list [all\|page\|last]` | Lists configured trusted reporter JIDs |
| `!report <nick\|jid> [reason]` | Trusted reporter command, only useful when `TrustedReporters` is enabled |

Common aliases such as `flood`, `similar`, `media`, `mentions`, `wordlist`, `joinwave`, `reporters`, and `policy` can be used instead of the full protection name. The list output shows the main alias in square brackets.

Use `!protections <name> reset` to return a protection to its built-in defaults, including its default enabled/disabled state.

## Available protections

| Protection | Default | Purpose |
| --- | --- | --- |
| `FloodSpamProtection` | disabled | Detects too many messages from one user in a time window |
| `SimilarMessageProtection` | disabled | Reacts when identical or highly similar messages appear repeatedly in a room |
| `FirstMessageMediaProtection` | disabled | Reacts when an unknown newly observed joiner sends media as their first message |
| `MentionLimitProtection` | disabled | Reacts to messages mentioning too many current room occupants |
| `WordListNewJoinerProtection` | disabled | Reacts to configured words/phrases from recent joiners |
| `JoinWaveShortCircuitProtection` | disabled | Detects join waves and can set the room members-only and moderated |
| `TrustedReporters` | disabled | Counts reports from configured trusted JIDs and takes an action at a threshold |
| `PolicyChangeNotification` | enabled | Announces ban/unban/protection config policy changes in the admin room |

## Recommended starting points

Start with one protection at a time and use conservative actions until the behavior matches your rooms.

| Protection | Suggested first action | Notes |
| --- | --- | --- |
| `FloodSpamProtection` | `notify` or short `tempban` | Tune `max_messages` and `window_seconds` from real room traffic before using permanent `ban` in busy rooms. |
| `SimilarMessageProtection` | short `tempban` | Usually the highest-value spam detector. Keep `min_length` and `min_words` high enough to ignore normal short chatter. |
| `FirstMessageMediaProtection` | short `tempban` | Useful for media-link spam from throwaway accounts; established room participants are ignored. |
| `MentionLimitProtection` | short `tempban` | Use a limit above normal room behavior; it counts known room occupants mentioned by display nick. |
| `WordListNewJoinerProtection` | short `tempban` | Keep word lists specific to spam phrases; broad words can cause false positives for new users. |
| `JoinWaveShortCircuitProtection` | `notify` first, then `lockdown` | Verify room-config support and bot rights before enabling lockdown in active rooms. |
| `TrustedReporters` | `notify` first | Add explicit trusted reporter bare JIDs and raise `threshold` for busier communities. |
| `PolicyChangeNotification` | enabled | Keep enabled unless admin-room policy notifications are too noisy. |

## Actions

Message protections support these actions:

| Action | Effect |
| --- | --- |
| `notify` | Only notify the admin room |
| `warn` | Notify the admin room and warn in the protected room |
| `kick` | Kick the sender from the room |
| `tempban` | Add a temporary global ban through the existing ban system |
| `ban` | Add a permanent global ban through the existing ban system |

When `redact=True` and `REDACTION_ENABLED=True`, message protections first try to retract the triggering message and punitive actions (`kick`, `tempban`, `ban`) also run the normal indexed JID redaction path with an admin-room summary.

`observe`, `notify`, and `warn` matches do not stop evaluation of later message protections. This prevents a tuning/notification rule from masking a later `kick`, `tempban`, or `ban` rule that also matches the same message. Punitive matches stop the message-protection chain after they run.

## Overlapping protections and ban arbitration

Multiple protections may match the same sender during a spam wave. BanBot deliberately makes automated protection updates monotonic so one detector cannot accidentally weaken another:

* punitive action strength is `kick < tempban < ban`;
* the short `action_cooldown_seconds` window suppresses equal or weaker duplicate actions for the same room/target, but a stronger action bypasses that cooldown;
* observe-only matches do not create an enforcement cooldown and do not stop a later enforcing protection from evaluating the same message;
* an automated protection never converts an existing permanent ban to a tempban;
* an automated protection never shortens an existing tempban; it may extend it or promote it to permanent;
* distinct meaningful reasons from protection actors are merged once (for example `spam/flood detected | repeated/similar spam detected`) instead of replacing each other;
* a meaningful human or RTBL reason/issuer remains authoritative when a protection later matches the same target.

These rules apply to automatic `protection:*` actors. Explicit human moderation keeps the normal command semantics, including intentional permanent/tempban conversion.

`Recovered from room` is a synchronization fallback for an outcast whose original metadata is unavailable. If a later protection supplies a real reason for such a placeholder row, BanBot may enrich that metadata without shortening the ban.

## Examples

```text
!protection enable flood
!protections flood set max_messages 8
!protections flood set window_seconds 60
!protections flood set action ban
!protections flood set tempban_seconds 1h
!protections flood set action_cooldown_seconds 5
```


```text
!protection enable similar
!protections similar set max_similar 3
!protections similar set window_seconds 2m
!protections similar set similarity_percent 90
!protections similar set min_length 20
!protections similar set min_words 3
!protections similar set action tempban
!protections similar set tempban_seconds 1d
```

```text
!protection enable mention
!protections mention set max_mentions 5
!protections mention set action tempban
```

```text
!protection enable wordlist
!protections wordlist set words ["free crypto", "airdrop", "telegram me"]
!protections wordlist set join_grace_seconds 15m
```

```text
!protection enable joinwave
!protections joinwave set max_joins 8
!protections joinwave set window_seconds 60
!protections joinwave set action lockdown
!protections joinwave set cooldown_seconds 60
!protections joinwave set startup_grace_seconds 30
!protections joinwave set rejoin_grace_seconds 5m
!protections joinwave set ignore_member_affiliations true
```

```text
!protection enable reporters
!protections reporters add alice@example.org
!protections reporters add bob@example.org
!protections reporters list all
!protections reporters set threshold 2
!protections reporters set action tempban
!protections reporters set tempban_seconds 1d
```

## Live smoke testing

A destructive live smoke-test helper is available as `tools/live_protection_smoke.py`. It connects real XMPP test accounts to real rooms and exercises the protection flow end-to-end. It is not part of CI and should only be run against dedicated test rooms/accounts with `--destructive`.

See [Testing and CI](testing.md#live-protection-smoke-test) for setup, environment variables, and operational notes.

## Notes

`SimilarMessageProtection` normalizes URLs and email addresses before comparison, so repeated spam with changing tracking URLs can still be detected. Short messages are ignored through `min_length` and `min_words` to avoid false positives from normal chatter.

`FirstMessageMediaProtection` only treats a participant as new until BanBot has seen a clean first message from that participant. Known bare JIDs are persisted in SQLite, so an established user who leaves and later rejoins is ignored by this protection instead of being treated as a fresh account again. Nick-only identities are deliberately *not* trusted across joins because another occupant can reuse the same nick. Occupants already present when BanBot joins a room are treated as established for that runtime when a real JID is visible. Observe-mode matches from new-user protections do not establish/whitelist the sender.

`WordListNewJoinerProtection` acts only on users whose join was observed by the running bot **and who were not already established before that join**. A genuinely new participant remains under the word-list grace window even after a clean first message; becoming known during the current session does not end that grace period early. Initial room population is ignored so a bot restart does not make existing occupants look like new joiners.

For XEP-0461 message replies, BanBot excludes the XEP-0428 reply fallback range before evaluating content-based protections. Quoted media URLs, mentions, blocked words, or similar text from the replied-to message therefore do not count as newly authored content; content written after the quote is still evaluated normally.

`JoinWaveShortCircuitProtection` uses the MUC service's room configuration support. The bot must have sufficient room admin/owner rights, and the server must support changing the relevant MUC config fields. The protection counts **unique participant subjects** inside the active window, so repeated reconnects from the same bare JID cannot manufacture a join wave. It ignores the initial room roster for `startup_grace_seconds` after the bot joins a room and remembers occupants that were present before a reconnect for `rejoin_grace_seconds`. By default, established bare JIDs are not counted (`ignore_known_participants=true`), and occupants with `member`, `admin`, or `owner` affiliation are also ignored (`ignore_member_affiliations=true`). Either filter can be disabled explicitly. A short `cooldown_seconds` period prevents repeated notifications during the same wave.

`MentionLimitProtection` collapses display aliases such as `Bob` and `~Bob` into one participant before counting. Short plain-word nicks are counted only with an explicit mention-style prefix (`@`, `~`, `&`, `%`, `+`), preventing ordinary prose from becoming a mass-mention match merely because users have generic nicks such as `the`, `in`, or `room`. Longer display nicks can still be recognized without a prefix for compatibility with clients that insert the raw nick.

`TrustedReporters` binds each report to the target's verified bare JID at report time, not merely to a display nick. A nick that maps to different JIDs across protected rooms is rejected as ambiguous and the reporter is asked to use the bare JID. Reporters themselves must also have a verifiable real JID in the room where `!report` is issued; BanBot never borrows identity from a same-named occupant in another room.


## Observe mode

Use `!protections <name> observe on` to evaluate a protection without consequences. The bot records and announces what the configured action would have been, while suppressing kicks, bans, tempbans, redactions, warnings, and room lockdowns. Use `observe off` (or `enforce`) to return to enforcement mode. The protection list labels action-capable entries with `[observe]`. `PolicyChangeNotification` is labeled `[notify-only]` because it only emits notifications and therefore does not support observe mode; attempts to set `observe` for it are rejected.
