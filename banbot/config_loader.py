"""Load the external ``config.py`` used by BanBot.

Console scripts are executed from the virtual environment's ``bin`` directory,
so the service working directory is not guaranteed to be on ``sys.path``.
This loader resolves the operator configuration explicitly and registers it as
the traditional top-level ``config`` module before the rest of BanBot imports.
"""

from __future__ import annotations

import builtins
import importlib.util
import linecache
import os
from pathlib import Path
import sys
from types import ModuleType

CONFIG_ENV_VAR = "MUC_BANBOT_CONFIG"


def _config_candidates() -> tuple[Path, ...]:
    """Return config paths in precedence order without duplicates."""
    candidates: list[Path] = []

    configured_path = os.environ.get(CONFIG_ENV_VAR)
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        candidates.append(path)

    candidates.append(Path.cwd() / "config.py")
    candidates.append(Path(__file__).resolve().parents[1] / "config.py")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return tuple(unique)


def load_config_module() -> ModuleType:
    """Load and return the external config module.

    Existing test or embedding environments may pre-register ``config`` in
    ``sys.modules``. In that case the supplied module remains authoritative.
    """
    existing = sys.modules.get("config")
    if existing is not None:
        return existing

    builtins.true = True
    builtins.false = False

    candidates = _config_candidates()
    if os.environ.get(CONFIG_ENV_VAR):
        config_path = candidates[0] if candidates[0].is_file() else None
    else:
        config_path = next((path for path in candidates if path.is_file()), None)
    if config_path is None:
        attempted = ", ".join(str(path) for path in candidates)
        exc = ModuleNotFoundError(
            f"No module named 'config' (looked in: {attempted})"
        )
        exc.name = "config"
        raise exc

    spec = importlib.util.spec_from_file_location("config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load config module from {config_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["config"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get("config") is module:
            sys.modules.pop("config", None)
        raise
    return module


def format_config_import_error(exc: BaseException) -> str:
    """Return a helpful config.py import/reload error with source context."""
    filename = "config.py"
    lineno = None
    text = None

    if isinstance(exc, SyntaxError):
        filename = exc.filename or filename
        lineno = exc.lineno
        text = (exc.text or "").strip() or None
    else:
        candidate_paths = set(_config_candidates())
        tb = exc.__traceback__
        while tb:
            frame_filename = tb.tb_frame.f_code.co_filename
            frame_path = Path(frame_filename).resolve()
            if Path(frame_filename).name == "config.py" or frame_path in candidate_paths:
                filename = frame_filename
                lineno = tb.tb_lineno
                text = linecache.getline(frame_filename, lineno).strip() or None
            tb = tb.tb_next

    location = Path(filename).name
    if lineno:
        location += f":{lineno}"

    lines = [f"{location}: {exc.__class__.__name__}: {exc}"]
    if text:
        lines.append(f"    {text}")
        if isinstance(exc, SyntaxError) and exc.offset:
            lines.append("    " + " " * max(exc.offset - 1, 0) + "^")

    if isinstance(exc, NameError):
        lines.append("Hint: string values in config.py need quotes.")
        lines.append('Example: CONNECT_HOST = "myhost.com"')
        lines.append("For booleans, use True/False.")
        lines.append("This bot also accepts lowercase true/false.")

    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None) == "config":
        lines.append("Hint: config.py is missing from the working directory or source checkout.")
        lines.append("Create it from the sample config first:")
        lines.append("  cp config_sample.py config.py")
        lines.append("Then edit config.py and start the bot again.")
        lines.append(
            f"Optional override: {CONFIG_ENV_VAR}=/absolute/path/to/config.py."
        )

    return "\n".join(lines)
