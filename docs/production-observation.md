# Production observation after a release

Use this checklist for the first 72 hours after a significant muc_banbot release. Capture anomalies before changing state so transient failures are diagnosable.

## Baseline: immediately after deployment

Verify the installed versions without starting another XMPP session:

```bash
muc_banbot --version
./scripts/deploy.sh status
systemctl status muc_banbot --no-pager
```

In the admin room capture:

```text
!status
!tasks all
!tasks failed
```

Expected baseline:

- the BanBot and `envs-xmpp` versions are the intended release pair;
- every protected room is joined;
- admin/owner rights are confirmed where required;
- no failed or restarting supervised workers;
- outbox dead count is zero;
- RTBL subscriptions/publish state matches configuration;
- startup backup and database state are healthy.

## Journal review

Check the current boot and the period since deployment:

```bash
journalctl -u muc_banbot -b --no-pager
journalctl -u muc_banbot --since "24 hours ago" --no-pager \
  | grep -Ei 'warning|error|exception|traceback|restart|locked|timeout|reconnect'
```

Pay particular attention to repeated reconnect loops, repeated `database is locked`, outbox-worker restarts, lost admin rights, RTBL refresh failures and watchdog warnings. A single recovered network event is not by itself a release failure.

## 24-hour checkpoint

Repeat `!status`, `!tasks all` and `!tasks failed`. Compare:

- process memory/CPU and system load;
- task restart counters;
- watchdog lag/suppressed heartbeats;
- outbox pending/inflight/dead counts;
- protected-room/admin-right state;
- expired tempbans pending auto-unban;
- RTBL subscription/publish health.

The durable outbox may briefly contain pending work, but the queue must drain under normal connectivity.

## 48-hour checkpoint

Repeat the 24-hour checks. Confirm that periodic RTBL refresh, health checks, tempban cleanup, backup retention and any enabled version checks have executed without accumulating failures.

Review memory and worker restart counts for monotonic growth. The redaction index and outbox should coexist without recurring SQLite writer-lock warnings.

## 72-hour acceptance checkpoint

Treat the release as production-stable when all of the following are true:

- no unhandled exceptions or crash/reconnect loops;
- no persistent failed/restarting workers;
- no recurring SQLite lock errors;
- no dead outbox entries and no sustained pending growth;
- protected rooms remain joined and authorization state is correct;
- moderation/protections/RTBL/OMEMO behavior is normal for enabled features;
- watchdog health is stable;
- backups remain current;
- CPU/RSS are broadly stable for the workload;
- no user-visible moderation regression has been reported.

If a problem appears, preserve the relevant journal range plus `!status` and `!tasks all` output before restarting whenever operationally safe.
