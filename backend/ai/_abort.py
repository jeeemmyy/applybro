"""Shared abort registry for the in-flight AI call, whatever the provider.

Only one AI call runs at a time in ApplyBro (the apply batch is sequential,
discovery scores one job at a time), so a single-slot registry is enough.
The dashboard's Abort / Stop-batch buttons call ai.abort_current(), which
cancels whichever provider is currently running — a Claude subprocess
(killpg) or an OpenAI stream (a cancel flag the reader loop checks).
"""
from __future__ import annotations

import threading
from typing import Optional

# Sentinel returned by a provider when the call was cancelled by the user —
# callers compare against this to report "aborted" instead of an error.
ABORTED = "aborted by you"

_LOCK = threading.Lock()
_CURRENT = set()   # objects exposing .cancel() -> bool (scans score jobs in
                   # parallel now, so several calls can be in flight at once)


def register(canceller) -> None:
    with _LOCK:
        _CURRENT.add(canceller)


def unregister(canceller) -> None:
    with _LOCK:
        _CURRENT.discard(canceller)


def abort_current() -> bool:
    """Cancel EVERY in-flight AI call. Returns whether there was any."""
    with _LOCK:
        cs = list(_CURRENT)
    hit = False
    for c in cs:
        try:
            hit = bool(c.cancel()) or hit
        except Exception:
            pass
    return hit
