#!/usr/bin/env python3
"""Sensitive-data-in-logs guard (SaaS Phase 6 / MVP-703).

ApplyBro handles resumes, prompts, application answers and email content.
None of that may land in operational logs (log files, Sentry, analytics) —
only safe metadata: feature, model, status, counts, truncated error text.

This is a heuristic lint: it flags logging / print calls whose arguments
mention a variable that carries sensitive payloads. It won't catch every
possible leak, but it fails loudly if someone logs `prompt`, `resume`,
`answer`, `content_yaml`, an email `body`, or an api key.

Run:  python3 scripts/check_safe_logging.py   (exit 1 on a suspected leak)
"""
from __future__ import annotations

import os
import re
import sys

SCAN_DIRS = ["backend"]

# A logging/print sink...
_SINK = re.compile(r"\b(log(ger)?\.\w+|logging\.\w+|print)\s*\(")
# ...mentioning a sensitive payload token as a whole word.
_SENSITIVE = re.compile(
    r"\b(prompt|resume_text|content_yaml|profile_yaml|answers?|"
    r"api_key|openai_api_key|pdf_bytes|pdf_base64|message_body|email_body|"
    r"cover_letter)\b")

# Allowed: logging a LENGTH/count/flag derived from a payload, not the payload
# itself (e.g. len(prompt), bool(answer)); and lines that only name the token
# inside a quoted human string.
_SAFE_DERIVED = re.compile(r"len\(|\.length|count|bool\(|is not None|== ''")


def _string_spans(line: str):
    return [(m.start(), m.end()) for m in re.finditer(r"""(['"]).*?\1""", line)]


def _outside_strings(line: str, token_match) -> bool:
    s, e = token_match.span()
    return not any(a <= s and e <= b for a, b in _string_spans(line))


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems = []
    for base in SCAN_DIRS:
        for dp, _dirs, files in os.walk(os.path.join(root, base)):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8") as f:
                    for i, line in enumerate(f.readlines(), 1):
                        if line.lstrip().startswith("#"):
                            continue
                        if not _SINK.search(line):
                            continue
                        if _SAFE_DERIVED.search(line):
                            continue
                        m = _SENSITIVE.search(line)
                        if m and _outside_strings(line, m):
                            problems.append((os.path.relpath(path, root), i,
                                             line.strip()[:90]))

    if problems:
        print("UNSAFE LOGGING — sensitive payload may reach the logs:\n")
        for rel, ln, text in problems:
            print(f"  {rel}:{ln}\n      {text}")
        print("\nLog only safe metadata (feature, model, status, counts, "
              "truncated errors) — never the prompt/resume/answers/email/key.")
        return 1

    print("SAFE — no sensitive payload found in logging/print calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
