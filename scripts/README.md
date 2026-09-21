# Repository helper scripts

Run these helpers from the repository root inside the project virtualenv.

| Script | Purpose | Common usage |
| --- | --- | --- |
| `deploy.sh` | Preservation-first install/update/status/check helper. A bare invocation only prints help. | `./scripts/deploy.sh status`, `./scripts/deploy.sh install --dry-run`, `sudo ./scripts/deploy.sh update` |
| `deploy.py` | Python backend for `deploy.sh`; normally invoke the shell wrapper instead. | `python scripts/deploy.py --help` |
| `_envs_xmpp_bootstrap.py` | Stdlib-only bootstrap for shared `envs-xmpp` operational tooling used before the package is importable. | Internal helper; `deploy.py` uses it automatically. |
| `deploy_profile.py` | Declarative envs-xmpp deployment profile used by the bootstrap deploy path. | Defines bot-specific defaults while shared deployment mechanics live in `envs_xmpp_ops`. |
| `quality.sh` | Local release gate: compilation, config syntax, warning-strict tests, Ruff, focused mypy and dependency audit. | `./scripts/quality.sh`, `./scripts/quality.sh --fix` |
| `test.sh` | Fast non-integration pytest wrapper with warnings treated as errors. | `./scripts/test.sh`, `./scripts/test.sh --coverage`, `./scripts/test.sh --last-failed` |
| `check_release_tag.py` | Thin wrapper over `envs_xmpp_ops.release`; declares `banbot/_version.py` as the package version source. | `python scripts/check_release_tag.py v3.3.0` |
| `check_wheel.py` | Thin wrapper over the shared wheel checker; declares the BanBot entry point, required members and runtime asset. | `rm -rf dist && python -m build && python scripts/check_wheel.py` |

`quality.sh` and `test.sh` intentionally use the same shared runners as the
other envs.net XMPP bot. Repository-specific source roots, project validation
commands, integration markers and coverage thresholds are declared under
`[tool.envs-xmpp.quality]` and `[tool.envs-xmpp.testing]` in `pyproject.toml`;
the runner implementation lives in `envs-xmpp`. Release-tag and wheel verification use the same model: repository-specific declarations stay local while generic checking lives in `envs_xmpp_ops.release`. The project-validation stage also runs the shared-core release audit, so dependency metadata, constraints and the deployment bootstrap cannot silently drift to different envs-xmpp versions.

`deploy.sh status` also reports runtime dependency drift against the matching
Python constraint snapshot. `deploy.sh check` treats any missing, unpinned or
version-mismatched runtime dependency as an operator-visible failure.

The deploy helper deliberately does not replace an existing systemd unit or
operator configuration. New installs default to the hardened
`/etc/muc_banbot/config.py` + `/var/lib/muc_banbot/` layout. Existing
source-tree deployments remain supported.
