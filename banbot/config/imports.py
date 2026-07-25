"""Configuration import helpers."""

from __future__ import annotations

import logging

import config

from ..config_loader import format_config_import_error

log = logging.getLogger(__name__)


def get_config_resource() -> str | None:
    """Return RESOURCE with backwards-compatible support for legacy RESSOURCE."""
    resource = getattr(config, "RESOURCE", None)
    if resource is not None:
        return resource
    return getattr(config, "RESSOURCE", None)

