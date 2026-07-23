#!/usr/bin/env python3
"""Extension twin of check_never_submits.py — the never-submit rule extends to
the Chrome extension's page agent (SaaS Phase 5).

    NO EXTENSION CODE PATH MAY EVER SUBMIT AN APPLICATION.

The extension's apply.js fills forms in the page. It must never click a
submit control, never call form.submit()/requestSubmit(), never dispatch a
submit or Enter event, and it must install the capture-phase submit guard
while filling (removed in a `finally` so the human can submit).

Run:  python3 scripts/check_extension_never_submits.py
Exit 0 = safe. Exit 1 = a submit-capable gesture is present / guard missing.
"""
from __future__ import annotations

import os
import re
import sys

EXT_DIR = "extension"

BANNED = [
    (re.compile(r"\.submit\(\s*\)"), "calls .submit()"),
    (re.compile(r"\.requestSubmit\("), "calls requestSubmit()"),
    # .click() is banned everywhere EXCEPT background.js's expandListings(),
    # which reveals hidden jobs on a careers LIST during a scan. That function
    # refuses any control inside a <form>, any submit/image/reset input, and
    # anything whose text isn't a show-more phrase, and it installs the submit
    # guard for the whole pass (see _expand_click_is_guarded below).
    (re.compile(r"\.click\("), "clicks an element (could hit submit)"),
    (re.compile(r"new\s+KeyboardEvent"), "creates a keyboard event"),
    (re.compile(r"""key(Code)?\s*:\s*["']?Enter"""), "dispatches Enter"),
    (re.compile(r"""(keyCode|which)\s*:\s*13\b"""), "dispatches keyCode 13 (Enter)"),
    (re.compile(r"""new\s+Event\(\s*["']submit"""), "dispatches a submit event"),
    (re.compile(r"""dispatchEvent\([^)]*submit""", re.I), "dispatches a submit event"),
]

# Lines that legitimately NAME these tokens: the guard neuters the methods and
# blocks Enter — that is the cure, not the bug.
ALLOWED = re.compile(
    r"HTMLFormElement\.prototype|__applybroGuard|origSubmit|origRequest|"
    r"g\.blocked|preventDefault|stopImmediatePropagation|"
    r'e\.key === "Enter"')


EXPAND_FN = "async function expandListings()"


def _expand_span(src: str):
    """(start, end) of expandListings() in background.js, or None."""
    i = src.find(EXPAND_FN)
    if i < 0:
        return None
    j = src.find("\nfunction capturePage()", i)
    return (i, j if j > i else len(src))


def _expand_click_is_safe(src: str) -> list:
    """The one sanctioned click must keep every one of its rails."""
    span = _expand_span(src)
    if span is None:
        return []                      # function absent = nothing to allow
    body = src[span[0]:span[1]]
    problems = []
    for needle, why in (
        ('c.closest("form")', "expandListings no longer refuses controls inside a <form>"),
        ('t === "submit"', "expandListings no longer refuses submit inputs"),
        ("HTMLFormElement.prototype.submit = function", "expandListings doesn't neuter .submit()"),
        ("HTMLFormElement.prototype.requestSubmit = function", "expandListings doesn't neuter requestSubmit()"),
        ('addEventListener("submit", blocked, true)', "expandListings doesn't install the submit guard"),
        ("} finally {", "expandListings doesn't restore the page in a finally"),
        ("MORE.test(", "expandListings no longer checks the button's text"),
    ):
        if needle not in body:
            problems.append(why)
    if body.count(".click(") != 1:
        problems.append("expandListings must contain exactly one .click()")
    return problems


def _guard_wired(apply_js: str) -> list:
    missing = []
    src = ""
    try:
        with open(apply_js, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return ["extension/apply.js is missing"]
    for needle, why in (
        ("function guardOn", "guardOn() is gone — the submit guard isn't installed"),
        ("function guardOff", "guardOff() is gone — the guard can't be removed"),
        ("addEventListener(\"submit\"", "the guard never blocks the submit event"),
        ("window.__applybro", "apply.js never exposes its entry points"),
    ):
        if needle not in src:
            missing.append(why)
    # fill() must install the guard and remove it in a finally
    m = re.search(r"function fill\([^)]*\)\s*\{(.*?)\n  \}", src, re.S)
    body = m.group(1) if m else src
    if "guardOn(" not in body:
        missing.append("fill() never installs the submit guard")
    if "guardOff(" not in body:
        missing.append("fill() never removes the submit guard")
    if "finally" not in body:
        missing.append("fill() must remove the guard in a `finally`")
    return missing


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(root, EXT_DIR)
    if not os.path.isdir(target):
        print(f"ERROR: {EXT_DIR}/ not found (run from the project root).")
        return 1

    problems = []
    for fn in sorted(os.listdir(target)):
        if not fn.endswith(".js"):
            continue
        path = os.path.join(target, fn)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # The one sanctioned click lives in background.js's expandListings();
        # its own rails are checked separately and far more strictly.
        span = _expand_span(src) if fn == "background.js" else None
        for i, line in enumerate(src.splitlines(), 1):
            s = line.lstrip()
            if s.startswith("//") or ALLOWED.search(line):
                continue
            for pat, why in BANNED:
                if not pat.search(line):
                    continue
                if (span and why.startswith("clicks an element")
                        and span[0] <= src.find(line) <= span[1]):
                    continue        # inside the audited expand pass
                problems.append((f"{EXT_DIR}/{fn}", i, why, line.strip()[:80]))

    missing = _guard_wired(os.path.join(target, "apply.js"))
    with open(os.path.join(target, "background.js"), encoding="utf-8") as f:
        missing += _expand_click_is_safe(f.read())

    if problems or missing:
        print("UNSAFE — the extension could submit an application:\n")
        for rel, ln, why, text in problems:
            print(f"  {rel}:{ln}  {why}\n      {text}")
        for why in missing:
            print(f"  MISSING: {why}")
        print("\nSee CLAUDE.md: submission is always a human act.")
        return 1

    print("SAFE — no submit-capable gesture in extension/, and the guard is wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
