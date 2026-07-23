#!/usr/bin/env python3
"""Guards the project's single most important rule (CLAUDE.md):

    NO CODE PATH MAY EVER SUBMIT AN APPLICATION.

This once regressed for real. A fallback pressed Enter inside a react-select
to pick an option from a virtualized list; Enter inside a form input is the
browser's *implicit submit* gesture, so when react-select didn't swallow it
the application was submitted. It is not enough to "remember not to" — this
script fails CI/commit if the dangerous gestures reappear.

Run:  python3 scripts/check_never_submits.py
Exit 0 = safe. Exit 1 = a submit-capable gesture is present.
"""
from __future__ import annotations

import os
import re
import sys

AUTOFILL_DIR = os.path.join("backend", "autofill")

# Gestures that can submit a form. Enter/Return keypresses are the big one.
BANNED = [
    (re.compile(r"""press\(\s*["']Enter["']""", re.I), "presses Enter (implicit form submit)"),
    (re.compile(r"""press\(\s*["']Return["']""", re.I), "presses Return (implicit form submit)"),
    (re.compile(r"""keyboard\.press\(\s*["'](Enter|Return)["']""", re.I), "keyboard Enter/Return"),
    (re.compile(r"""\.press_sequentially\([^)]*\\n""", re.I), "types a newline"),
    (re.compile(r"""\.submit\(\s*\)"""), "calls .submit()"),
    (re.compile(r"""requestSubmit\(\s*\)(?!\s*\{)"""), "calls requestSubmit()"),
    (re.compile(r"""click_submit|submit_button\.click"""), "clicks a submit button"),
]

# The guard installs stubs named exactly these; they are the cure, not the bug.
ALLOWED_CONTEXT = re.compile(r"HTMLFormElement\.prototype|g\.blocked\+\+|origSubmit|origRequest")

def _check_guard_wired(root: str) -> list:
    """Substring checks are too weak (renaming a def leaves its call sites
    behind), so import the real module and inspect fill_form's own source."""
    missing = []
    sys.path.insert(0, root)
    try:
        from backend.autofill import forms as F
    except Exception as e:  # pragma: no cover
        return [f"cannot import backend.autofill.forms ({e})"]
    import inspect

    for name in ("_guard_on", "_guard_off"):
        if not callable(getattr(F, name, None)):
            missing.append(f"forms.{name}() is gone — the submit guard is not installed")

    try:
        src = inspect.getsource(F.fill_form)
    except Exception:
        return missing + ["cannot read fill_form source"]
    if "_guard_on" not in src:
        missing.append("fill_form() never installs the submit guard")
    if "_guard_off" not in src:
        missing.append("fill_form() never removes the submit guard")
    if "finally" not in src:
        missing.append("fill_form() must remove the guard in a `finally` "
                       "(else a crash leaves the human unable to submit)")
    if "blocked_submits" not in src:
        missing.append("fill_form() must report blocked_submits")
    return missing


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(root, AUTOFILL_DIR)
    if not os.path.isdir(target):
        print(f"ERROR: {AUTOFILL_DIR} not found (run from the project root).")
        return 1

    problems = []
    for dirpath, _dirs, files in os.walk(target):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                stripped = line.lstrip()
                # Skip comments — Python (#) and the JS (//) inside injected
                # snippets, which legitimately *describe* form.submit().
                if (stripped.startswith("#") or stripped.startswith("//")
                        or ALLOWED_CONTEXT.search(line)):
                    continue
                for pat, why in BANNED:
                    if pat.search(line):
                        rel = os.path.relpath(path, root)
                        problems.append((rel, i, why, line.strip()[:80]))

    missing = _check_guard_wired(root)

    if problems or missing:
        print("UNSAFE — the autofiller could submit an application:\n")
        for rel, line, why, text in problems:
            print(f"  {rel}:{line}  {why}")
            print(f"      {text}")
        for why in missing:
            print(f"  MISSING: {why}")
        print("\nSee CLAUDE.md: submission is always a human act.")
        return 1

    print("SAFE — no submit-capable gesture in backend/autofill/, "
          "and the submit guard is in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
