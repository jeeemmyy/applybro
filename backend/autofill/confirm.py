"""Submission-confirmation detection — the Layer-3 business rule for
"does this page look like the application was actually submitted?"

Pure pattern matching over a page's URL and visible text. This NEVER means
this code submitted anything (nothing in ApplyBro does — submission is always
a human click); it only recognizes a thank-you / confirmation screen the user
reached themselves, so the Apply room can offer to auto-mark it applied.

The CDP plumbing that FINDS which browser tabs to check, and the
host-ambiguity safety guard for matching a tab to a specific pending job,
live in the app layer (app/apply_engine.check_confirmations) because they
operate on the live shared-window tab set — this module is the pure,
testable "is this text a confirmation?" decision they delegate to.
"""
from __future__ import annotations

import re

CONFIRMATION_PATTERNS = re.compile(
    r"thank you for (your |)appl|application (has been |)(received|submitted|"
    r"complete)|we('| a)ve received your application|application confirm|"
    r"successfully applied|your application (has been |)sent", re.I)


def looks_confirmed(page) -> bool:
    """True if this Playwright page's URL or body text matches a
    submission-confirmation pattern. Best-effort: any read error (tab
    closed/navigating) reads as 'not confirmed'."""
    try:
        if CONFIRMATION_PATTERNS.search(page.url):
            return True
        text = page.inner_text("body", timeout=800)
        return bool(CONFIRMATION_PATTERNS.search(text[:4000]))
    except Exception:
        return False
