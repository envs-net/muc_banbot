# Repository helper scripts

Run these helpers from the repository root inside the project virtualenv.

| Script | Purpose | Common usage |
| --- | --- | --- |
| `deploy.sh` | Preservation-first install/update/status/check helper. A bare invocation only prints help. | `./scripts/deploy.sh status`, `./scripts/deploy.sh install --dry-run`, `sudo ./scripts/deploy.sh update` |
| `deploy.py` | Python backend for `deploy.sh`; normally invoke the shell wrapper instead. | `python scripts/deploy.py --help` |
| `quality.sh` | Local release gate: compilation, config syntax, warning-strict tests, Ruff, focused mypy and dependency audit. | `./scripts/quality.sh`, `./scripts/quality.sh --fix` |
| `test.sh` | Fast non-integration pytest wrapper with warnings treated as errors. | `./scripts/test.sh`, `./scripts/test.sh --coverage`, `./scripts/test.sh --last-failed` |

The deploy helper deliberately does not replace an existing systemd unit or
operator configuration. New installs default to the hardened
`/etc/muc_banbot/config.py` + `/var/lib/muc_banbot/` layout. Existing
source-tree deployments remain supported.
