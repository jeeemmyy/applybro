"""Tiny persisted UI state (last row range used, etc.) — separate from
config.yaml so the dashboard's ephemeral preferences never collide with the
connection settings schema Config.load() parses strictly."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from ..paths import UI_STATE_JSON as STATE_FILE

_DEFAULTS: Dict[str, Any] = {
    "last_range_end": 0,
    "fit_threshold": 60,
}


def load() -> Dict[str, Any]:
    from .. import store
    if store.enabled():
        d = store.kv_get("ui_state")
    elif not os.path.exists(STATE_FILE):
        d = {}
    else:
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            d = {}
    merged = dict(_DEFAULTS)
    merged.update(d)
    return merged


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    from .. import store
    d = load()
    d.update(patch)
    if store.enabled():
        store.kv_set("ui_state", d)
        return d
    with open(STATE_FILE, "w") as f:
        json.dump(d, f, indent=2)
    return d
