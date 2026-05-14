# Contributing to BanBot

Thanks for your interest in contributing to BanBot.

BanBot is an envs.net project maintained by its project maintainer. The codebase has been developed with help from ChatGPT and GitHub Copilot, but all contributions should still be reviewed, tested, and understood by the person submitting them.

## Before You Start

Please read:

* [README.md](README.md)
* [docs/README.md](docs/README.md)
* [docs/configuration.md](docs/configuration.md)
* [docs/testing.md](docs/testing.md)

For security-sensitive issues, read [SECURITY.md](SECURITY.md) before opening a public issue.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp config_sample.py config.py
```

OMEMO support is optional. Only install it when needed:

```bash
sudo apt install libsodium-dev libxeddsa-dev
pip install -r requirements-omemo.txt
```

## Running Tests

Run the offline test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=banbot --cov-report=term-missing
```

Run property-based tests:

```bash
pytest -m property
```

Optional mutation testing:

```bash
PYTHONPATH="$PWD" mutmut run
```

Live XMPP/OMEMO integration tests are opt-in and require a dedicated test environment. Do not run them against production rooms unless you know what you are doing.

See [docs/testing.md](docs/testing.md) and [tests/README.md](tests/README.md).

## Pull Request Guidelines

A good pull request should:

* Describe what changed and why.
* Include tests for new behavior or bug fixes.
* Update documentation when commands, config, setup, or behavior changes.
* Update `config_sample.py` when adding or changing config options.
* Keep unrelated refactors out of feature/bugfix PRs.
* Avoid committing local runtime files such as `config.py`, databases, `.coverage`, `.hypothesis/`, `mutants/`, or OMEMO storage.

Before submitting:

```bash
pytest
```

Recommended for larger changes:

```bash
pytest --cov=banbot --cov-report=term-missing
```

## AI-Assisted Changes

AI tools such as ChatGPT and GitHub Copilot may be used, but please:

* Review generated code carefully.
* Make sure the code matches the project style and behavior.
* Run the relevant tests.
* Avoid submitting code you do not understand.
* Mention AI assistance in the pull request when it materially helped produce the change.

## Commit Messages

Use concise commit messages with a clear prefix when helpful:

```text
fix: avoid stale RTBL bans after refresh
feat: add command option
test: cover moderation edge case
docs: update OMEMO setup notes
chore: update CI config
```

## Maintainer Decisions

The maintainer may ask for changes, close issues, reject pull requests, or choose a different implementation path. Please keep discussions constructive.
