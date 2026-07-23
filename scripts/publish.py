#!/usr/bin/env python3
"""Publish the tracked tree to the public GitHub repo, keeping REAL history.

Why this exists
---------------
The local repository's history is not publishable: an early commit
(368a654) contains resume/content.yaml with a real name, phone number and
email. Git history is forever on a public repo — GitHub serves unreferenced
commits by SHA, and forks and caches make a mistake unrecallable — so raw
local history must never be pushed.

The old answer was to force-push a fresh single-commit snapshot every time,
which was safe but threw away all history: the public repo had exactly one
commit, replaced on every publish.

This script gives both. It keeps a persistent mirror clone whose FIRST commit
is the tracked tree as of the day publishing began, and every publish after
that adds ONE ordinary commit on top with its own message and diff. Nothing is
force-pushed, nothing is rewritten, and the private history never leaves this
machine.

    local repo   92 commits, some containing personal data   (never pushed)
    mirror       1. Initial commit — ApplyBro
                 2. Ask before walking a long board
                 3. ...                                       (pushed, append-only)

Usage
-----
    python3 scripts/publish.py                 # publish HEAD's commit message
    python3 scripts/publish.py -m "message"    # override the message
    python3 scripts/publish.py --dry-run       # stage and diff, push nothing
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRROR = os.path.expanduser("~/.applybro/public-mirror")
REMOTE = "https://github.com/jeeemmyy/applybro.git"

# Files that must never appear in the published tree, even if something goes
# wrong with .gitignore. Belt and braces: this is the last gate before a
# public push, and the cost of being wrong is permanent.
FORBIDDEN = [
    "config.yaml", "profile.yaml", "resume/content.yaml",
    "credentials.json", "token.json", "data/session.json",
]

# Any AI attribution. The user's work carries the user's name only.
# The trailing `.*$` matters: without it the match stops at the vendor word
# and leaves the rest of the address ("...\n.com>") behind in the message.
# `[ \t]*` rather than `\s*` so it can't eat the preceding blank line.
TRAILER = re.compile(
    r"^[ \t]*Co-authored-by:.*(?:claude|anthropic|copilot|gpt|codex).*$",
    re.I | re.M)


def run(cmd, cwd=None, capture=False, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True,
                          capture_output=capture)


def out(cmd, cwd=None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True,
                          capture_output=True).stdout.strip()


def die(msg: str):
    print(f"REFUSING TO PUBLISH: {msg}", file=sys.stderr)
    sys.exit(1)


def clean_message(msg: str) -> str:
    """Strip AI co-author trailers, then tidy the blank lines they leave."""
    msg = TRAILER.sub("", msg)
    return re.sub(r"\n{3,}", "\n\n", msg).strip() + "\n"


def _has_commits(path: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"],
                          cwd=path, capture_output=True).returncode == 0


def ensure_mirror() -> bool:
    """Prepare the mirror clone. Returns True when this is the FIRST publish —
    defined as "the mirror has no commits yet", not merely "no .git dir",
    because a --dry-run leaves an initialized-but-empty mirror behind and the
    next real publish must still recognise itself as the first."""
    if os.path.isdir(os.path.join(MIRROR, ".git")):
        return not _has_commits(MIRROR)
    os.makedirs(os.path.dirname(MIRROR), exist_ok=True)
    shutil.rmtree(MIRROR, ignore_errors=True)
    run(["git", "init", "-q", "-b", "main", MIRROR])
    run(["git", "remote", "add", "origin", REMOTE], cwd=MIRROR)
    return True


def export_tracked_tree(dest: str) -> None:
    """Replace dest's working tree with `git archive HEAD` — the TRACKED files
    only. Anything untracked or gitignored (your config, your resume, your
    tokens) cannot reach the mirror by construction."""
    for name in os.listdir(dest):
        if name == ".git":
            continue
        p = os.path.join(dest, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        subprocess.run(["git", "archive", "--format=tar", "HEAD"],
                       cwd=REPO, check=True, stdout=tf)
        tar = tf.name
    try:
        with tarfile.open(tar) as t:
            try:                      # Python 3.12+ wants an explicit filter
                t.extractall(dest, filter="data")
            except TypeError:
                t.extractall(dest)
    finally:
        os.unlink(tar)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-m", "--message", help="commit message (default: HEAD's)")
    ap.add_argument("--dry-run", action="store_true",
                    help="stage and show the diff, push nothing")
    ap.add_argument("--tag", help="also create and push an annotated tag "
                                  "(e.g. v0.4.0) at this commit")
    args = ap.parse_args()

    if out(["git", "status", "--porcelain"], cwd=REPO):
        die("the working tree has uncommitted changes — commit them first, "
            "so the published commit matches a real local one.")

    # Gate 1: no personal data in tracked files. This runs HERE, not in CI,
    # because it needs the maintainer's own profile/resume to compare against —
    # in CI it would pass while checking nothing.
    print("→ checking tracked files for personal data ...")
    r = run([sys.executable, "scripts/release_check.py"], cwd=REPO,
            capture=True, check=False)
    print("  " + (r.stdout or r.stderr).strip().splitlines()[0])
    if r.returncode != 0:
        die("release_check found personal data in tracked files (above).")

    # Gate 2: the never-submit rails.
    for check in ("check_never_submits.py", "check_extension_never_submits.py",
                  "check_safe_logging.py"):
        r = run([sys.executable, f"scripts/{check}"], cwd=REPO,
                capture=True, check=False)
        if r.returncode != 0:
            die(f"{check} failed:\n{r.stdout}{r.stderr}")
    print("→ safety checks pass")

    first = ensure_mirror()
    export_tracked_tree(MIRROR)

    # Gate 3: nothing forbidden made it into the export.
    for f in FORBIDDEN:
        if os.path.exists(os.path.join(MIRROR, f)):
            die(f"{f} is in the tracked tree and would be published.")

    msg = args.message or out(["git", "log", "-1", "--format=%B"], cwd=REPO)
    msg = clean_message(msg)
    if first:
        msg = ("Initial commit — ApplyBro\n\n"
               "Published from a clean export of the tracked tree. The "
               "project's development history predates this commit and is "
               "kept private: it contains personal data that must not be "
               "published, and git history on a public repository cannot be "
               "recalled. Every commit from here on is real, incremental "
               "history.\n")

    run(["git", "add", "-A"], cwd=MIRROR)
    if not out(["git", "status", "--porcelain"], cwd=MIRROR):
        print("→ nothing changed since the last publish; nothing to do.")
        return 0

    print("\n→ files changing in this publish:")
    print(out(["git", "diff", "--cached", "--stat"], cwd=MIRROR))
    print("\n→ commit message:\n" + "\n".join(
        "    " + line for line in msg.strip().splitlines()))

    if args.dry_run:
        print("\n--dry-run: staged in %s, nothing pushed." % MIRROR)
        return 0

    subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=MIRROR,
                   input=msg, text=True, check=True)
    if first:
        # ONE-TIME baseline reset. The public repo's existing commits are the
        # old force-pushed snapshots this tool replaces; the clean initial
        # commit becomes the new root, so this single push is a --force. Every
        # push AFTER this is a plain append (below) that fails loudly on
        # divergence rather than forcing — that's the whole point of the
        # switch away from snapshots.
        print("→ first publish: force-setting the clean initial commit as the "
              "new root (replaces the old force-pushed snapshot; append-only "
              "from here).")
        run(["git", "push", "--force", "origin", "main"], cwd=MIRROR)
    else:
        # A normal push. If this fails as non-fast-forward, someone changed the
        # public repo directly — investigate, never --force.
        run(["git", "push", "origin", "main"], cwd=MIRROR)
    print("\n→ published: " + out(["git", "log", "-1", "--oneline"], cwd=MIRROR))
    print("  public history is now %s commit(s)."
          % out(["git", "rev-list", "--count", "HEAD"], cwd=MIRROR))

    if args.tag:
        # Annotated (not lightweight) so the tag carries the release message
        # and a date — that's what GitHub turns into a Release. The tag name is
        # the release message's first line; the message is the changelog entry
        # the caller passed, else HEAD's.
        existing = out(["git", "tag", "--list", args.tag], cwd=MIRROR)
        if existing:
            print(f"  tag {args.tag} already exists on the mirror — skipping.")
        else:
            subprocess.run(["git", "tag", "-a", args.tag, "-F", "-"],
                           cwd=MIRROR, input=msg, text=True, check=True)
            run(["git", "push", "origin", args.tag], cwd=MIRROR)
            print(f"  tagged and pushed {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
