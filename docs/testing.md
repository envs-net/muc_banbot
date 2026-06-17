# Testing and CI

BanBot includes an extensive pytest suite for offline unit/mixin behavior and opt-in live integration contracts.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Run Tests

```bash
pytest
pytest -q
pytest -v
```

Run one file:

```bash
pytest tests/test_rtbl_pubsub.py -v
```

Run one test:

```bash
pytest tests/test_rtbl_pubsub.py::test_name -v
```

## Coverage

```bash
pytest --cov=banbot --cov-report=term-missing
```

Coverage is intended as a regression guard, not as a goal by itself. Runtime-heavy modules that mostly wire Slixmpp or live XMPP behavior are better covered by focused regression tests and opt-in integration tests than by fragile mocks.

Drone CI uses coverage and can enforce a minimum threshold, for example:

```bash
pytest --cov=banbot --cov-report=term-missing --cov-fail-under=55
```

Runtime-heavy entrypoints such as `bot.py` may be excluded from coverage if they mainly wire Slixmpp runtime behavior.

## Property-Based Tests

Hypothesis property tests cover pure parsing, matching, and normalization helpers. They generate many input variants and check behavior that should stay true across all generated examples.

Current property-test coverage includes helpers such as:

* `parse_duration()`
* `human_time()`
* `safe_jid()` / `bare_jid()`
* `validate_jid_format()`
* `validate_domain_ban()`
* `domain_matches()`
* `looks_like_domain()`
* `normalize_ban_target()`
* paging helpers such as `paginate_lines()` and `resolve_page()`
* RTBL helpers such as JID hashing, SHA-256 detection, domain detection, PubSub service/node validation, payload generation, and reason extraction

They run as part of the normal pytest suite after installing `requirements-dev.txt`.

Run only property-based tests:

```bash
pytest -m property
```

Run the property-test files directly:

```bash
pytest tests/test_utils_properties.py tests/test_rtbl_utils_properties.py -v
```

When Hypothesis finds a failing example, it prints the smallest counterexample it could shrink to and may write a patch under `.hypothesis/patches/`. Treat these as debugging aids. Do not commit `.hypothesis/` artifacts.

## Mutation Testing

Mutation testing is optional and slower. It is not part of Drone CI by default. Run it locally when changing critical parser, normalization, moderation, or RTBL logic.

```bash
PYTHONPATH="$PWD" mutmut run
mutmut results
mutmut show <mutant-id>
```

The explicit `PYTHONPATH` keeps the local `banbot` package importable inside mutmut's temporary `mutants/` workspace.

The default mutmut configuration focuses on low-noise, high-value targets:

* `banbot/utils.py`
* `banbot/rtbl/apply.py`

These modules have pure helper logic and RTBL business rules where mutation testing gives useful signal. Complex XMPP-heavy moderation flows are better covered by targeted regression tests and opt-in integration tests.

Prioritize survived mutants as follows:

1. `utils.py` parser/validation/matching behavior
2. RTBL apply/unban safety behavior
3. Security-relevant moderation behavior, tested with focused regression tests
4. Ignore or defer equivalent/noisy runtime-I/O mutants

## Live Integration Tests

Live XMPP/Prosody and OMEMO tests are skipped by default. Enable them only in a dedicated test environment.

```bash
RUN_XMPP_INTEGRATION=1 pytest -m integration -v
RUN_OMEMO_INTEGRATION=1 pytest -m omemo -v
```

Live MUC command smoke test:

```bash
RUN_XMPP_INTEGRATION=1 \
BANBOT_TEST_SENDER_JID='tester@example.org' \
BANBOT_TEST_SENDER_PASSWORD='secret' \
BANBOT_TEST_PROTECTED_ROOM='test@conference.example.org' \
BANBOT_TEST_COMMAND='!help' \
BANBOT_TEST_EXPECT='BanBot' \
pytest tests/integration/test_live_muc_command_flow.py -v
```

## Live Protection Smoke Test

`tools/live_protection_smoke.py` is an opt-in operator/developer tool for testing the protection system against real XMPP rooms. It is intentionally not part of Drone CI or the normal pytest suite because it connects real accounts, sends real MUC messages, and can trigger bans, tempbans, redactions, protection config changes, and room configuration changes.

Use only dedicated test accounts and dedicated test rooms. The script refuses to run unless `--destructive` is passed.

Recommended setup:

* one admin/test-operator account that is allowed to send commands in the admin room
* one separate test account that may be banned/tempbanned during the run
* one admin/control room
* one protected test room already managed by BanBot
* protections enabled/configured for the scenarios you want to exercise

Use environment variables for secrets so passwords do not end up in shell history:

```bash
export BANBOT_SMOKE_ADMIN_JID='admin@example.org'
export BANBOT_SMOKE_ADMIN_PASSWORD='secret'
export BANBOT_SMOKE_ADMIN_ROOM='admin@conference.example.org'
export BANBOT_SMOKE_TEST_JID='smoke-user@example.org'
export BANBOT_SMOKE_TEST_PASSWORD='secret'
export BANBOT_SMOKE_PROTECTED_ROOM='test@conference.example.org'
export BANBOT_SMOKE_DOMAIN='example.org'
export BANBOT_SMOKE_COMMAND_PREFIX='!'

python tools/live_protection_smoke.py \
  --destructive \
  --pause-between-tests 5
```

The script announces each scenario in the admin room before it starts. It currently exercises policy-change notifications, first-message media, flood spam, mention limits, wordlist-new-joiner behavior, similar-message spam, join waves, and trusted reporters.

Operational notes:

* `SimilarMessageProtection` temporarily disables `FloodSpamProtection` and `JoinWaveShortCircuitProtection` so similar-message detection can win the test race.
* `JoinWaveShortCircuitProtection` can change room configuration when its action is `lockdown`; the smoke script disables joinwave again during cleanup.
* Redaction summaries depend on messages being indexed by BanBot after redaction indexing was enabled. Older messages or already-redacted messages may produce a “no redactable indexed stanza IDs” summary.
* The final output is the admin-room transcript. Review it for expected protection notifications and cleanup messages.

## Drone CI

Recommended `.drone.yml` pattern:

```yaml
---
kind: pipeline
type: docker
name: pytest

steps:
  - name: build pytest
    image: python:3.13
    commands:
      - python --version
      - python -m pip install --upgrade pip setuptools wheel
      - cp config_sample.py config.py
      - pip install -r requirements.txt
      - pip install -r requirements-dev.txt
      - python -m py_compile banbot/*.py tests/*.py tests/integration/*.py
      - pytest --cov=banbot --cov-report=term-missing --cov-fail-under=55

trigger:
  branch:
    - main
  event:
    - push
    - tag
```

## Cleanup Local Test Artifacts

The following files/directories are generated by tests, coverage, Hypothesis, or mutmut and should not be committed:

```bash
rm -rf .coverage htmlcov coverage.xml
rm -rf .pytest_cache .hypothesis
rm -rf mutants .mutmut-cache
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

`config.py`, runtime databases, OMEMO storage, and local CSV import/export files should also stay local.
