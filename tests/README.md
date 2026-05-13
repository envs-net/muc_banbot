# BanBot Test Suite

This directory contains the offline pytest suite plus opt-in live integration contracts.

## Test Groups

* `test_utils*.py` — pure helper, parsing, paging, and normalization tests
* `test_utils_properties.py` — Hypothesis/property-based tests for general helpers
* `test_rtbl_utils_properties.py` — Hypothesis/property-based tests for RTBL helper logic
* `test_*_extra.py` — focused regression tests for DB, commands, RTBL, moderation, sync, MUC presence, vCard, and config behavior
* `integration/` — opt-in live XMPP/Prosody/OMEMO checks, skipped by default

## Common Commands

Run the normal offline suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=banbot --cov-report=term-missing
```

Run only property-based tests:

```bash
pytest -m property
```

Run live integration tests only in a dedicated test environment:

```bash
RUN_XMPP_INTEGRATION=1 pytest -m integration -v
RUN_OMEMO_INTEGRATION=1 pytest -m omemo -v
```

## Notes

The tests install a fallback `config` module in `conftest.py` so the suite can run in clean CI environments without a real local `config.py`.

Hypothesis may create `.hypothesis/` artifacts and coverage may create `.coverage`, `coverage.xml`, or `htmlcov/`. These files are local test artifacts and should not be committed.
