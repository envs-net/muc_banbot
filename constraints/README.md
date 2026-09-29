# Dependency constraints

`python312.txt`, `python313.txt`, and `python314.txt` are fully resolved dependency
snapshots for muc_banbot runtime and development dependencies, including the
shared `envs-xmpp[omemo]` dependency closure.

After changing dependency ranges, refresh all supported Python snapshots on a
networked development host and review the resulting diff:

```bash
scripts/update-constraints.sh 3.12 --refresh
scripts/update-constraints.sh 3.13 --refresh
scripts/update-constraints.sh 3.14 --refresh
```

Reproduce an existing snapshot without deliberately upgrading dependencies by
omitting `--refresh`. The update script installs into a clean virtualenv, writes
the complete dependency closure, and validates it with
`scripts/check_constraints.py`.

Always use the snapshot matching the Python minor version.
