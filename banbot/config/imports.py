"""Configuration mixin helpers."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import linecache
import logging
import os
import builtins
import pprint
import pathlib
import sys
from typing import Any

import config

from .common import RUNTIME_WRITABLE_CONFIG_KEYS, SECRET_CONFIG_KEYS, STARTUP_ONLY_CONFIG_KEYS

builtins.true = True
builtins.false = False

log = logging.getLogger(__name__)

def format_config_import_error(exc: BaseException) -> str:
    """Return a helpful config.py import/reload error with file line and source text."""
    filename = "config.py"
    lineno = None
    text = None

    if isinstance(exc, SyntaxError):
        filename = exc.filename or filename
        lineno = exc.lineno
        text = (exc.text or "").strip() or None
    else:
        tb = exc.__traceback__
        while tb:
            frame_filename = tb.tb_frame.f_code.co_filename
            if frame_filename.endswith("config.py") or os.path.basename(frame_filename) == "config.py":
                filename = frame_filename
                lineno = tb.tb_lineno
                text = linecache.getline(frame_filename, lineno).strip() or None
            tb = tb.tb_next

    location = f"{os.path.basename(filename)}"
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
        lines.append("Hint: config.py is missing.")
        lines.append("Create it from the sample config first:")
        lines.append("  cp config_sample.py config.py")
        lines.append("Then edit config.py and start the bot again.")

    return "\n".join(lines)

def get_config_resource() -> str | None:
    """Return RESOURCE with backwards-compatible support for legacy RESSOURCE."""
    resource = getattr(config, "RESOURCE", None)
    if resource is not None:
        return resource
    return getattr(config, "RESSOURCE", None)

