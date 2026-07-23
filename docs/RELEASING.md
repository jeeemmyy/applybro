# Releasing ApplyBro (procedure — do not run casually)

ApplyBro is currently a **local-only, single-user repo**. `resume/content.yaml`
(your resume master) is tracked in git on purpose, per `CLAUDE.md`'s
"Product direction" decision — versioning your own rewordings over time is
useful while you're the only user. That decision comes with a matching
promise: **strip it and rewrite git history before this repo is ever made
public or shipped to another person.**

This document is that procedure. It is destructive (history rewrite) and
irreversible on any clone/fork/remote you don't control, so:

- It is **not automated** and **nothing in this project runs it for you** —
  not `scripts/release_check.py`, not the `/setup` wizard, nothing.
- Only run it when you have actually decided to publish/ship the repo.
- Do it on a fresh clone, never your working copy, until you're sure.

## 1. Verify first — `scripts/release_check.py`

Before touching history, confirm no personal data has ALSO leaked into
`applybro/`, `frontend/src/`, or the tracked docs (separate problem from
`content.yaml` — those files should be generic regardless of whether you
ever publish):

```bash
python3 scripts/release_check.py
```

It reads identifying strings (name/email/phone/spreadsheet id) out of your
own local `profile.yaml` / `config.yaml` / `resume/content.yaml` and greps
tracked code/docs for them. Fix every hit it reports, re-run until clean.
This step is safe to run anytime and changes nothing.

## 2. The actual strip + history rewrite (the destructive part)

On a **fresh clone** you're prepared to discard if something goes wrong:

1. Replace the tracked `resume/content.yaml` with a genuinely generic sample (or
   delete it and rely on `content.example.yaml` if one exists by then —
   there isn't one today; create it as part of this step).
2. Rewrite git history so the real content.yaml never existed in any
   commit, using `git filter-repo` (preferred over the deprecated
   `filter-branch`):
   ```bash
   pip install git-filter-repo
   # BOTH paths: the file lived at the repo root before the 2026-07-10
   # restructure moved it to resume/ — history contains both.
   git filter-repo --path content.yaml --path resume/content.yaml --invert-paths
   ```
   This rewrites every commit; all commit hashes change.
3. Double check nothing else slipped through: re-run
   `scripts/release_check.py`, and also grep the full history (not just
   the working tree) for your name/email/phone as a last resort:
   ```bash
   git log -p --all | grep -i "<your name or email>"
   ```
4. Confirm `.gitignore` already excludes `config.yaml`, `credentials.json`,
   `token.json`, `profile.yaml`, `data/` (workspaces/queue/ui-state),
   `resume/Master Resume/`, `resume/assets/photo.png` (it does) — these
   were never committed, so history rewriting isn't needed for them.
   (Their pre-restructure root-level names were gitignored then too.)
5. Force-push the rewritten history to wherever you're actually
   publishing (a NEW remote, not your personal working remote, unless
   you're fully replacing it) — `git push --force` rewrites remote
   history for everyone else who has a clone, so treat this as a
   point-of-no-return step.

## 3. After publishing

Anyone who clones the published repo from this point on:

- Gets a generic placeholder content file instead of your real
  resume content.
- Follows `scripts/install.sh` + the `/setup` wizard (see `README.md`) to
  bring their own Google credential, spreadsheet, profile, and — per the
  wizard's step 5 — their own Claude Code session to turn their own resume
  PDF into their own `content.yaml`.
- Never sees your name, email, phone, or spreadsheet id — that's what
  step 1 and 2 above exist to guarantee.
