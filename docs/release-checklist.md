# Release Checklist

Use this before tagging a new release.

## 1. Local Checks

```bash
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile banbot/*.py tests/*.py tests/integration/*.py
pytest -q
pytest --cov=banbot --cov-report=term-missing
```

The offline test suite should be green before tagging. Keep the configured coverage threshold stable unless there is a deliberate reason to change it.

Optional mutation run:

```bash
PYTHONPATH="$PWD" mutmut run
mutmut results
```

Optional live tests in a dedicated test environment:

```bash
RUN_XMPP_INTEGRATION=1 pytest -m integration -v
RUN_OMEMO_INTEGRATION=1 pytest -m omemo -v
```

Optional live protection smoke test in dedicated test rooms:

```bash
python tools/live_protection_smoke.py --destructive --pause-between-tests 5
```

## 2. Manual Bot Smoke Test

Start the bot in a test environment:

```bash
python muc_banbot.py
```

Check:

* Bot connects and joins admin/protected rooms
* `!status` works
* `!config` works
* `!banlist` works
* `!room list` works
* Optional: `tools/live_protection_smoke.py --destructive` passes in dedicated test rooms
* RTBL subscriptions load if enabled
* OMEMO command/reply behavior works if enabled
* No unexpected warning spam at INFO level

## 3. Database and Config

* Review `config_sample.py` for new, renamed, or removed options.
* Ensure startup-only vs runtime-reloadable behavior is documented.
* Confirm migrations/schema changes are covered by tests.
* Confirm SQLite table documentation is current if schema changed.
* Confirm README/docs mention any new operator-facing behavior.
* Verify `config.py` is not committed and `config_sample.py` remains safe to publish.

## 4. Documentation

Update as needed:

* `README.md`
* `docs/README.md`
* `docs/configuration.md`
* `docs/commands.md`
* `docs/backups.md`
* `docs/rooms.md`
* `docs/import-export.md`
* `docs/omemo.md`
* `docs/rtbl.md`
* `docs/rtbl_pubsub-setup.md`
* `docs/policy.md`
* `docs/protections.md`
* `docs/admin-protection.md`
* `docs/database.md`
* `docs/testing.md`
* `docs/troubleshooting.md`

Check that command examples still use the correct default prefix and that paginated commands document `all`, `last`, and page-number behavior.

## 5. Git and CI

```bash
git status
git log --oneline --decorate -n 20
```

Before pushing, make sure generated local artifacts are not staged:

* `.coverage`
* `htmlcov/`
* `.pytest_cache/`
* `.hypothesis/`
* `mutants/`
* `.mutmut-cache/`
* `config.py`
* runtime DB/CSV/OMEMO data

Push and wait for Drone CI to pass.

## 6. Tagging

Use the versioning style already used by the project, for example:

```bash
git tag -a v2.2.0 -m "Release v2.2.0"
git push origin v2.2.0
```

## 7. Release Notes

Mention user-facing changes, config changes, migration notes, and compatibility notes.

Suggested sections:

* Highlights
* New features
* Improvements
* Fixes
* Configuration changes
* Database/migration notes
* RTBL/OMEMO notes, if relevant
* Testing/CI
* Upgrade notes

## 8. Post-Release Smoke Check

After deploying the release in a test or production-like environment:

* Restart the bot and check startup logs.
* Run `!status`, `!config`, `!banlist`, and `!room list`.
* If RTBL is enabled, run `!rtbl list [all|page|last]` and optionally `!rtbl refresh`.
* If OMEMO is enabled, test one plaintext command and one encrypted command.
* Check Drone/release badge links in the README.
