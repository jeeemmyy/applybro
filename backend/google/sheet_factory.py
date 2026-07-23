"""Picks the Sheet vs PublicSheet backend from config. Shared by the CLI and
the server so there's exactly one place that makes this decision."""
from __future__ import annotations

from ..config import Config


def get_sheet(cfg: Config):
    if cfg.auth_mode == "oauth":
        from .sheets import Sheet
        return Sheet(cfg)
    from .public_sheet import PublicSheet
    return PublicSheet(cfg)
