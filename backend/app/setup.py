"""First-run onboarding: setup status + bootstrap helpers for the /setup
wizard (P4 packaging).

Nothing here invents or guesses personal data. config.yaml and profile.yaml
are bootstrapped from their tracked .example.yaml templates (structure only,
no facts filled in) so the wizard's later steps have somewhere to write real
values. content.yaml is NEVER auto-generated here — turning a resume PDF
into content.yaml is a Claude Code judgment task (Layer 0, see CLAUDE.md);
the wizard's last step only shows the phrase to say, never a button that
pretends to do it automatically.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

import yaml

from ..config import Config
from ..paths import (CONFIG_EXAMPLE_YAML, CONFIG_YAML, CONTENT_YAML,
                     CREDENTIALS_JSON, PROFILE_EXAMPLE_YAML, PROFILE_YAML,
                     TOKEN_JSON)

# Wizard step order — also the order `missing` is reported in, and the order
# status()'s items dict is meant to be read in.
_ORDER = ["credentials_json", "token_json", "config_yaml",
          "spreadsheet_configured", "profile_yaml", "content_yaml"]


class SetupRequiredError(Exception):
    """Raised by API routes that need config.yaml / a working Sheet
    connection / profile.yaml that isn't there yet. server.py's exception
    handler turns this into a clean 409 JSON body — never a 500 stack
    trace, and (more importantly) never an escaped SystemExit: SystemExit
    is a BaseException, not an Exception, so Starlette's exception
    middleware — which only catches Exception — would NOT stop one raised
    from inside a route; it would propagate straight past FastAPI's error
    handling. Every call in this module that might hit a SystemExit-raising
    CLI helper (Config.load's callers, google.sheets.Sheet) converts it to
    this instead."""

    def __init__(self, missing: List[str], message: str = ""):
        self.missing = missing
        self.message = message or f"Setup required: {', '.join(missing)}"
        super().__init__(self.message)


def status() -> Dict[str, Any]:
    """What's present/missing, in wizard order, plus a `ready` gate.

    `ready` deliberately excludes content_yaml — creating it is a Claude
    Code task the user does outside the browser, on their own schedule, so
    the dashboard must be usable (thin — scoring/tailoring just won't have
    a profile to match against yet) without hard-blocking on that step."""
    cfg = Config.try_load(CONFIG_YAML)
    items = {
        "credentials_json": os.path.exists(CREDENTIALS_JSON),
        "token_json": os.path.exists(TOKEN_JSON),
        "config_yaml": cfg is not None,
        "spreadsheet_configured": bool(cfg and cfg.spreadsheet_id),
        "profile_yaml": os.path.exists(PROFILE_YAML),
        "content_yaml": os.path.exists(CONTENT_YAML),
    }
    missing = [k for k in _ORDER if not items[k]]
    ready = all(items[k] for k in _ORDER if k != "content_yaml")
    return {**items, "missing": missing, "ready": ready}


def require_config(path: str = CONFIG_YAML) -> Config:
    """Config.load(), but raising SetupRequiredError (a plain Exception)
    instead of SystemExit when config.yaml is absent."""
    cfg = Config.try_load(path)
    if cfg is None:
        raise SetupRequiredError(
            ["config_yaml"],
            f"{path} not found — finish setup at /setup first.")
    return cfg


def safe_get_sheet(cfg: Config):
    """get_sheet(cfg), but converts the SystemExit that google/sheets.py or
    google/public_sheet.py raise for a CLI user (missing credentials.json,
    empty spreadsheet_id, ...) into a SetupRequiredError so it can become a
    clean 409 instead of escaping FastAPI's exception handling."""
    from ..google.sheet_factory import get_sheet
    try:
        return get_sheet(cfg)
    except SystemExit as e:
        raise SetupRequiredError(["credentials_json"], str(e))


def ensure_config_yaml() -> None:
    """Idempotent: create config.yaml from config.example.yaml (structure
    only — no personal facts) if it doesn't exist yet, forcing auth_mode:
    oauth (the packaged product's only supported mode; config.example.yaml
    itself intentionally has no default so a bare CLI checkout still means
    'public' unless the user opts in). Never overwrites an existing file."""
    if os.path.exists(CONFIG_YAML):
        return
    with open(CONFIG_EXAMPLE_YAML) as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("google", {})["auth_mode"] = "oauth"
    with open(CONFIG_YAML, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False, allow_unicode=True)


def ensure_profile_yaml() -> None:
    """Idempotent: create profile.yaml from profile.example.yaml verbatim
    (byte-for-byte, comments intact) if it doesn't exist yet. Never
    overwrites an existing file."""
    if os.path.exists(PROFILE_YAML):
        return
    shutil.copyfile(PROFILE_EXAMPLE_YAML, PROFILE_YAML)
