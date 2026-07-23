"""Unified AI entry point for ApplyBro.

Every feature that calls an LLM goes through `complete()`, which picks the
provider (OpenAI API key by default, Claude CLI opt-in) and the per-feature
model from config, then dispatches. `abort_current()` cancels whichever call
is in flight, regardless of provider.

    ok, text = complete(prompt, feature="tailoring", timeout=600)

`feature` ∈ {"tailoring", "fit", "qa", "extraction"} — selects the model and
labels the call. On failure `text` is a human-readable message (safe to show
the user); on user-abort it is exactly `ABORTED`.
"""
from __future__ import annotations

import json as _json
from typing import List, Optional, Tuple

from . import settings
from ._abort import ABORTED, abort_current

__all__ = ["complete", "abort_current", "ABORTED", "settings", "exhausted",
           "load_json", "as_list"]

DEFAULT_TIMEOUT = 600

# Per-feature sampling. Every AI task ApplyBro runs wants REPRODUCIBLE output
# (a fit score that doesn't swing 20 points between identical runs, an
# extraction that doesn't reshuffle, a tailoring that's stable) — the OpenAI
# default temperature is 1.0, which was the single biggest source of the
# "AI is inaccurate and unstable" reports (2026-07-21). The three features
# that must return machine-readable JSON also request JSON mode; tailoring
# emits YAML and gets a little headroom so the rewrite isn't wooden.
_FEATURE_SAMPLING = {
    "extraction": {"temperature": 0.0, "json": True},
    "fit":        {"temperature": 0.0, "json": True},
    "qa":         {"temperature": 0.0, "json": True},
    "tailoring":  {"temperature": 0.3, "json": False},
}


def _slice(text: str, opener: str, closer: str) -> Optional[str]:
    s, e = text.find(opener), text.rfind(closer)
    return text[s:e + 1] if s != -1 and e > s else None


def load_json(text: str):
    """Best-effort parse of a model's JSON output. Tries, in order: the raw
    text (with any ```json fence stripped), the outermost {...} slice, then
    the outermost [...] slice. Returns the parsed value (dict/list/…) or
    None. Centralizes the fragile parsing every AI feature used to duplicate;
    JSON mode makes the first attempt succeed, this stays the safety net."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = _re.sub(r"^```[ \t]*\w*[ \t]*\n?", "", t)
        t = _re.sub(r"\n?```\s*$", "", t).strip()
    for candidate in (t, _slice(t, "{", "}"), _slice(t, "[", "]")):
        if not candidate:
            continue
        try:
            return _json.loads(candidate)
        except ValueError:
            continue
    return None


def as_list(val, *keys) -> Optional[list]:
    """Normalize a possibly JSON-mode-wrapped array back to a plain list: a
    bare list passes through; a dict yields the first present key in `keys`,
    else its first list-valued entry. Lets JSON mode (which requires a
    top-level object) coexist with prompts that conceptually return arrays."""
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for k in keys:
            if isinstance(val.get(k), list):
                return val[k]
        for v in val.values():
            if isinstance(v, list):
                return v
    return None

# Provider-exhaustion detection (user request 2026-07-16): when the Claude
# subscription or OpenAI quota runs out mid-workflow, every further call
# fails slowly one by one. Long loops (scans, discovery) poll exhausted()
# and stop the whole workflow instead of grinding through failures.
import re as _re
import threading as _threading

_EXH_LOCK = _threading.Lock()
_EXH_COUNT = 0
_EXHAUSTED_AFTER = 2
_LIMIT_RE = _re.compile(
    r"rate.?limit|usage limit|quota|too many requests|\b429\b|"
    r"insufficient_quota|out of credits|limit reached", _re.I)


def _note_result(ok: bool, msg: str) -> None:
    global _EXH_COUNT
    with _EXH_LOCK:
        if ok:
            _EXH_COUNT = 0
        elif _LIMIT_RE.search(msg or ""):
            _EXH_COUNT += 1


def exhausted() -> bool:
    """True after consecutive limit/quota failures — the provider is out of
    budget right now and further calls will just fail too."""
    with _EXH_LOCK:
        return _EXH_COUNT >= _EXHAUSTED_AFTER


def reset_exhausted() -> None:
    global _EXH_COUNT
    with _EXH_LOCK:
        _EXH_COUNT = 0


def check_access() -> Tuple[bool, str]:
    """The account gate (MVP-105): with the cloud store on, every AI request
    requires a signed-in, ACTIVE, SUBSCRIBED account. Pure-local dev (no
    supabase block) and pre-v3 databases (no profiles table) aren't gated —
    the gate can't invent state that doesn't exist yet."""
    from .. import store
    if not store.enabled():
        return True, ""
    if not store.logged_in():
        return False, "Sign in to ApplyBro first."
    prof = settings.profile()
    if not prof:
        return True, ""     # schema_v3 not applied yet
    status = str(prof.get("status") or "")
    if status == "pending":
        return False, ("Your account is awaiting approval — you'll be able "
                       "to use AI features once it's activated.")
    if status in ("suspended", "rejected"):
        return False, "Your account isn't active. Contact ApplyBro."
    if not prof.get("subscribed"):
        return False, ("Your account isn't subscribed yet — AI features are "
                       "enabled once your subscription is active.")
    return True, ""


def _route() -> Tuple[str, str]:
    """Pick the provider per the ADMIN policy, falling back when the primary
    is unavailable (no key / CLI missing) and the policy allows it.
    Returns (provider, "") or ("", user-facing error)."""
    allowed = settings.allowed_providers()
    if not allowed:
        return "", ("No AI provider is enabled for your account — "
                    "contact ApplyBro.")

    def usable(p: str) -> Tuple[bool, str]:
        if p == "openai":
            if settings.openai_api_key():
                return True, ""
            return False, "no OpenAI API key is configured for your account"
        from .claude_cli import find_claude
        if find_claude() is not None:
            return True, ""
        return False, "the Claude CLI isn't installed on this machine"

    primary = settings.provider()
    if primary not in allowed:
        primary = allowed[0]
    ok, why = usable(primary)
    if ok:
        return primary, ""
    fb = settings.fallback_provider()
    if fb and fb != primary and fb in allowed:
        ok2, _ = usable(fb)
        if ok2:
            return fb, ""
    return "", f"AI is unavailable: {why}. Contact ApplyBro."


def complete(prompt: str, *, feature: str, timeout: int = DEFAULT_TIMEOUT
             ) -> Tuple[bool, str]:
    ok, reason = check_access()
    if not ok:
        return False, reason
    from . import ratelimit
    if not ratelimit.check():
        return False, ("You've hit ApplyBro's AI rate limit for now — give it "
                       "a few minutes and try again.")
    prov, err = _route()
    if not prov:
        return False, err
    model = settings.model_for(feature)
    samp = _FEATURE_SAMPLING.get(feature, {"temperature": 0.3, "json": False})
    if prov == "openai":
        from . import openai_provider
        # Sampling controls are OpenAI-only — the Claude CLI exposes no
        # temperature flag, and Claude's default sampling is already far
        # steadier on these tasks than OpenAI's temperature=1.0 default.
        ok, out = openai_provider.run(prompt, model=model,
                                      api_key=settings.openai_api_key(),
                                      timeout=timeout,
                                      temperature=samp["temperature"],
                                      json_mode=samp["json"])
    else:
        from . import claude_cli
        ok, out = claude_cli.run(prompt, model=model, timeout=timeout)
    _note_result(ok, out)
    return ok, out
