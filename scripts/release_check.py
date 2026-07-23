#!/usr/bin/env python3
"""Release-readiness verifier — NOT a release.

Checks that no personal data has leaked from the user's local files into
the code/docs that would actually ship (backend/, frontend/src/, and the
tracked top-level docs). It reads the sensitive strings to look for — name,
email, phone, spreadsheet id — from the user's OWN local profile.yaml /
config.yaml / content.yaml at runtime; none of them are hardcoded here, so
this script contains no personal data of its own and works for any user.

This is explicitly NOT the release procedure. Actually stripping
content.yaml and rewriting git history (see CLAUDE.md's "Product direction"
section) is destructive, human-triggered, and out of scope for this script
— see RELEASING.md for that documented procedure. This script only tells
you whether tracked code/docs are clean right now; it changes nothing.

Usage:
    python3 scripts/release_check.py [--root PATH]

Exit 0 = clean (or nothing to check against). Exit 1 = personal data found
in tracked code/docs — printed as file:line so it can be fixed.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - requirements.txt always installs this
    yaml = None

# What actually ships to users — this is what must be clean.
SCAN_TARGETS = ["backend", "frontend/src", "scripts", "docs",
                "CLAUDE.md", "README.md"]
SCAN_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".yaml", ".yml")
SKIP_DIRS = {"__pycache__", "node_modules", "dist", ".git"}

# Only these profile.yaml keys are treated as identifying — deliberately a
# narrow list matching the spec (name/email/phone/spreadsheet id), not a
# blind dump of every field. Fields like current_title/location/linkedin
# are skipped on purpose: they're often generic enough words/phrases
# ("Remote", "Full Stack Developer") to flood false positives in a
# codebase that is itself ABOUT job applications.
PROFILE_KEYS = ["first_name", "last_name", "full_name", "email", "phone", "phone_national"]

# Generic template placeholders (from profile.example.yaml) that must never
# themselves trigger a "personal data" hit if profile.yaml still has them.
PLACEHOLDER_VALUES = {"jane", "doe", "jane doe", "you@example.com",
                       "+10000000000", "0000000000"}

MIN_LEN = 5  # skip trivially short values that would false-positive-flood


def _load_yaml(path: str) -> dict:
    if yaml is None or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _clean(s: object) -> str:
    return str(s).strip() if s is not None else ""


def collect_patterns(root: str) -> Dict[str, str]:
    """{personal_string: which local file it came from}. Reads three LOCAL,
    gitignored-or-personal files; never scans them (content.yaml legitimately
    contains personal facts by CLAUDE.md's design, profile.yaml/config.yaml
    are gitignored) — only pulls values OUT of them to check everything else
    against."""
    patterns: Dict[str, str] = {}

    profile = _load_yaml(os.path.join(root, "profile.yaml"))
    for key in PROFILE_KEYS:
        val = _clean(profile.get(key))
        if len(val) >= MIN_LEN and val.lower() not in PLACEHOLDER_VALUES:
            patterns.setdefault(val, "profile.yaml")

    cfg = _load_yaml(os.path.join(root, "config.yaml"))
    sid = _clean((cfg.get("google") or {}).get("spreadsheet_id"))
    if len(sid) >= MIN_LEN:
        patterns.setdefault(sid, "config.yaml")

    # content.yaml's `contact` block only (name + the info line items) —
    # NOT the whole file: skills/experience/summary are professional
    # content, not identifying data, and scanning them would just match
    # this codebase's own domain vocabulary everywhere.
    content = _load_yaml(os.path.join(root, "resume", "content.yaml"))
    contact = content.get("contact") or {}
    name = _clean(contact.get("name"))
    if len(name) >= MIN_LEN and name.lower() not in PLACEHOLDER_VALUES:
        patterns.setdefault(name, "content.yaml (contact.name)")
    for item in contact.get("info") or []:
        if not isinstance(item, dict):
            continue
        for field in ("text", "link", "href"):
            val = _clean(item.get(field))
            if len(val) >= MIN_LEN and val.lower() not in PLACEHOLDER_VALUES:
                patterns.setdefault(val, "content.yaml (contact.info)")

    return patterns


def _files_under(root: str, target: str) -> List[str]:
    target_path = os.path.join(root, target)
    if os.path.isfile(target_path):
        return [target_path]
    if not os.path.isdir(target_path):
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(target_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SCAN_EXTS):
                out.append(os.path.join(dirpath, fn))
    return out


def scan(root: str, patterns: Dict[str, str]) -> List[Tuple[str, int, str, str]]:
    """Returns (relative_path, line_no, line_text, source_file) per hit."""
    hits: List[Tuple[str, int, str, str]] = []
    if not patterns:
        return hits
    for target in SCAN_TARGETS:
        for fpath in _files_under(root, target):
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for i, line in enumerate(lines, start=1):
                for pat, src in patterns.items():
                    if pat and pat in line:
                        hits.append((os.path.relpath(fpath, root), i, line.rstrip(), src))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.getcwd(),
                    help="project root to check (default: cwd)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    patterns = collect_patterns(root)
    if not patterns:
        print("No profile.yaml/config.yaml/content.yaml (with identifying "
              "fields filled in) found under --root — nothing to check "
              "against. Run this from a real checkout with your data filled "
              "in before actually shipping.")
        return 0

    hits = scan(root, patterns)
    if not hits:
        print(f"Clean — checked {len(patterns)} personal string(s) from "
              f"profile.yaml/config.yaml/content.yaml against "
              f"{', '.join(SCAN_TARGETS)}. No hits.")
        return 0

    print(f"FOUND {len(hits)} personal-data hit(s) in tracked code/docs — "
          "fix before shipping:\n")
    for fpath, lineno, text, src in hits:
        print(f"  {fpath}:{lineno}  (matches a value from {src})")
        print(f"    {text.strip()[:160]}")
    print(f"\n{len(hits)} hit(s) across "
          f"{len(set(h[0] for h in hits))} file(s). This script does NOT fix "
          "anything — see RELEASING.md for the documented (destructive, "
          "human-run) content.yaml-strip + git-history-rewrite procedure.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
