"""Per-user AI rate limiting (SaaS Phase 6 / MVP-704).

A sliding-window cap on AI calls per account, so one runaway client (a stuck
loop, an abusive user) can't burn the shared OpenAI budget or hammer the
Claude runner. In-process (single backend); when the backend is horizontally
scaled at hosting time this moves to a shared store (Redis) — noted in
docs/DEPLOY.md. The admin's own account is generous but still bounded.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict

WINDOW_SECONDS = 300      # 5 minutes
# Per account per window. This is an ABUSE bound, not a scan budget — but at
# 80 it was the last silent cap in the scan chain: scoring is one call per
# job, so any board with more than ~80 survivors stopped mid-scan and reported
# "AI limit reached" (cap audit 2026-07-22). The deliberate limits on AI spend
# are elsewhere and stay: scans are per-company and user-initiated (never
# "scan all"), and title triage drops most jobs before a description is read.
MAX_CALLS = 300

_LOCK = threading.Lock()
_HITS: Dict[str, Deque[float]] = {}


def _who() -> str:
    try:
        from .. import store
        if store.enabled() and store.logged_in():
            from ..store import auth
            return auth.user_id() or "anon"
    except Exception:
        pass
    return "local"


def check() -> bool:
    """True if this account may make another AI call now; False if it has hit
    the window cap."""
    key = _who()
    now = time.time()
    with _LOCK:
        dq = _HITS.setdefault(key, deque())
        while dq and now - dq[0] > WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= MAX_CALLS:
            return False
        dq.append(now)
        return True
