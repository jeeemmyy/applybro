"""AI provider + per-feature model configuration.

Product decision (2026-07-12): every AI feature defaults to the user's own
OpenAI API key; the Claude Code CLI is the opt-in alternative. Config shape:

    ai:
      provider: openai          # or "claude_cli"
      openai_api_key: ""        # config.yaml is gitignored; OPENAI_API_KEY
                                # env overrides. NEVER commit a real key.
      models:
        openai:     {tailoring: ..., fit: ..., qa: ..., extraction: ...}
        claude_cli: {tailoring: ..., fit: ..., qa: ..., extraction: ...}

Models are nested by provider so switching providers never leaves an id that
belongs to the other one. Legacy keys (tailoring.model, discovery.ai_model)
are still read as a fallback so pre-existing configs keep working.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List

import yaml

from ..paths import CONFIG_YAML

# Pipeline order — reading pages comes first, then scoring, tailoring, QA.
# The Settings UI renders the dropdowns in this order.
FEATURES = ("extraction", "fit", "tailoring", "qa")
PROVIDERS = ("openai", "claude_cli")
DEFAULT_PROVIDER = "openai"

# Claude model ids ApplyBro offers for the CLI provider (these are known and
# stable). The OpenAI list is fetched live from the user's account
# (/v1/models) since ids change and shouldn't be guessed — these are only the
# out-of-the-box defaults, overridable in Settings.
CLAUDE_MODELS = [
    {"id": "claude-sonnet-5", "label": "Sonnet 5 (default — fast, high quality)"},
    {"id": "claude-opus-4-8", "label": "Opus 4.8 (slower, most thorough)"},
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5 (fastest, lighter)"},
]

_OPENAI_DEFAULT = "gpt-4o"

# Offered when the live /v1/models fetch fails (offline, DNS hiccup) so the
# dropdowns never collapse to "— no models —". Well-known chat-capable ids;
# the live list from the user's key always replaces these when reachable.
OPENAI_FALLBACK_MODELS = [
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "o3", "o3-mini", "o4-mini",
]

# Extraction decides WHICH jobs exist on a page; it feeds fit and everything
# downstream, so it no longer defaults to the cheapest model (gpt-4o-mini
# missed real postings — 2026-07-21). All four features default to gpt-4o;
# the admin can still dial extraction down per-user in the AI policy.
DEFAULT_MODELS: Dict[str, Dict[str, str]] = {
    "openai": {f: _OPENAI_DEFAULT for f in FEATURES},
    "claude_cli": {f: "claude-sonnet-5" for f in FEATURES},
}


def _cfg() -> dict:
    try:
        with open(CONFIG_YAML) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


# ADMIN-set per-user AI policy from Supabase (ai_policy row — users read
# their own row, only the admin writes it; decided 2026-07-14: users never
# choose providers or models). The signed-in user's profile (status/
# subscribed) rides in the same cache. Cached briefly so scoring/tailoring
# loops don't pay REST calls per AI request. Legacy user_settings.data["ai"]
# is still read AFTER the policy so pre-v3 setups keep working.
_USER_CACHE: Dict[str, object] = {"at": 0.0, "policy": None, "profile": None,
                                  "legacy": None}
_USER_TTL = 60.0


def _refresh_user_cache() -> None:
    from .. import store
    now = time.time()
    if _USER_CACHE["policy"] is not None and \
            now - float(_USER_CACHE["at"]) <= _USER_TTL:
        return
    _USER_CACHE["policy"] = store.ai_policy_get() or {}
    _USER_CACHE["profile"] = store.profile_get() or {}
    _USER_CACHE["legacy"] = (store.user_settings_get() or {}).get("ai", {}) or {}
    _USER_CACHE["at"] = now


def _policy() -> dict:
    try:
        from .. import store
        if not store.enabled() or not store.logged_in():
            return {}
        _refresh_user_cache()
        return dict(_USER_CACHE["policy"])  # type: ignore[arg-type]
    except Exception:
        return {}   # storage hiccup: fall back to config/env, don't break AI


def profile() -> dict:
    """The signed-in user's account row ({status, subscribed, ...}), cached.
    {} when the store is off or schema_v3 hasn't been applied."""
    try:
        from .. import store
        if not store.enabled() or not store.logged_in():
            return {}
        _refresh_user_cache()
        return dict(_USER_CACHE["profile"])  # type: ignore[arg-type]
    except Exception:
        return {}


def _legacy_user_ai() -> dict:
    try:
        from .. import store
        if not store.enabled() or not store.logged_in():
            return {}
        _refresh_user_cache()
        return dict(_USER_CACHE["legacy"])  # type: ignore[arg-type]
    except Exception:
        return {}


def invalidate_user_cache() -> None:
    _USER_CACHE["policy"] = None


def allowed_providers() -> List[str]:
    """Providers the admin permits this user. Without a policy row (local
    dev, pre-v3 DB) everything is allowed — the access gate handles the
    account-level blocking separately."""
    pol = _policy()
    if not pol:
        return list(PROVIDERS)
    out = []
    if pol.get("allow_openai"):
        out.append("openai")
    if pol.get("allow_claude_cli"):
        out.append("claude_cli")
    return out


def fallback_provider() -> str:
    fb = str(_policy().get("fallback_provider") or "").strip()
    return fb if fb in PROVIDERS else ""


def provider() -> str:
    env = os.environ.get("APPLYBRO_AI_PROVIDER", "").strip()
    if env in PROVIDERS:
        return env
    p = str(_policy().get("primary_provider", "")).strip()
    if p in PROVIDERS:
        return p
    p = str(_legacy_user_ai().get("provider", "")).strip()
    if p in PROVIDERS:
        return p
    p = str((_cfg().get("ai", {}) or {}).get("provider", "")).strip()
    return p if p in PROVIDERS else DEFAULT_PROVIDER


def openai_api_key() -> str:
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env:
        return env
    key = str(_policy().get("openai_api_key", "") or "").strip()
    if key:
        return key
    key = str(_legacy_user_ai().get("openai_api_key", "") or "").strip()
    if key:
        return key
    return str((_cfg().get("ai", {}) or {}).get("openai_api_key", "") or "").strip()


def _legacy_model(cfg: dict, feature: str) -> str:
    """Old single-model keys, honored for the Claude CLI provider so existing
    configs don't silently change behavior."""
    if feature == "tailoring":
        return str((cfg.get("tailoring", {}) or {}).get("model", "") or "").strip()
    if feature == "fit":
        return str((cfg.get("discovery", {}) or {}).get("ai_model", "") or "").strip()
    return ""


def model_for(feature: str) -> str:
    if feature not in FEATURES:
        feature = "tailoring"
    cfg = _cfg()
    prov = provider()
    env = os.environ.get(f"APPLYBRO_AI_MODEL_{feature.upper()}", "").strip()
    if env:
        return env
    policy_models = (_policy().get("models", {}) or {}).get(prov, {}) or {}
    if policy_models.get(feature):
        return str(policy_models[feature]).strip()
    legacy_models = (_legacy_user_ai().get("models", {}) or {}).get(prov, {}) or {}
    if legacy_models.get(feature):
        return str(legacy_models[feature]).strip()
    models = ((cfg.get("ai", {}) or {}).get("models", {}) or {}).get(prov, {}) or {}
    if models.get(feature):
        return str(models[feature]).strip()
    if prov == "claude_cli":
        legacy = _legacy_model(cfg, feature)
        if legacy:
            return legacy
    return DEFAULT_MODELS[prov][feature]


def all_models() -> Dict[str, str]:
    return {f: model_for(f) for f in FEATURES}
