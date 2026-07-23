# ADR 0001 — A scan fix must improve the scanner, never patch one company

Status: accepted (2026-07-23)

## Context

Scans are reported broken one company at a time: "fin.ai found 1 job of 130",
"ServiceNow reported 237 of 463". The tempting fix each time is a branch keyed
to that site — `if host == "fin.ai": expand the accordions`. That path leads
to a scanner made of dozens of brittle per-site rules, where fixing company A
silently breaks company B, and no one notices until B is reported too.

The user stated the rule directly (2026-07-23): when I report a scan problem
for one company, fixing it must **improve the scanner in general** and must not
*khraab* (ruin) the scan for other companies. Every reported bug is a prompt to
make the scanner *more capable*, not to special-case the site that surfaced it.

This is the same lesson the [scan doctrine](../../CLAUDE.md) already learned
about coverage ("fixing sites one at a time does not generalise; self-checking
does"); this ADR makes it binding for every future scan change.

## Decision

**Every scan fix must be a general capability, verified not to regress other
companies. Company-specific branches in scan code are prohibited.**

Concretely, a scan change must satisfy all of:

1. **General mechanism, not a site match.** The fix keys off a structural fact
   any site can present — an ARIA pattern, an ATS URL shape, a stated range, a
   JSON-LD block — never off a hostname, and never off a string only one
   company emits. If you cannot describe the fix without naming the company,
   it is not ready.

2. **Self-verifying, so it degrades to a no-op elsewhere.** The fix must carry
   its own check that it actually helped *on this page*, and do nothing when it
   didn't. Example: the accordion-expansion pass keeps clicking disclosure
   toggles only while each round adds job links; on a board with no such
   toggles it opens a filter or two, sees no new links, and stops. The site
   that needed the fix and the site that didn't both come out correct.

3. **Additive, never subtractive.** A fix may reveal more jobs, read more
   descriptions, or report a shortfall more honestly. It may not remove or
   reorder a path other companies already rely on. New behaviour goes *beside*
   the old, gated by the self-check in (2).

4. **Regression-checked against a corpus before shipping.** Run the change
   against at least: the company that reported it, one clean ATS board
   (Greenhouse/Ashby/Lever) where jobs are already anchors, and one paginated
   or client-rendered board. Record the before/after job counts in the commit.
   "Fixed X, and Y/Z unchanged" is the minimum evidence.

5. **Honest when it still can't.** If a site genuinely can't be covered, the
   fix makes the scan *say so as a measured number* (doctrine rule 4/8) — it
   never leaves a silent undercount, and never claims a cause it didn't
   measure.

## Consequences

- Scan code has no `if host == …` branches. The knowledge lives in general
  readers (ATS adapters keyed by URL shape, structural card grouping, ARIA
  expansion, per-page and cross-page range checks), each of which no-ops when
  it doesn't apply.
- Every scan bug makes the scanner permanently better at a *class* of sites,
  not just the one reported. fin.ai's accordion report fixed every ARIA
  disclosure careers page at once.
- Fixes cost more up front: each needs a corpus run and its numbers in the
  commit. That is the price of not shipping a regression to companies no one
  re-tested.

## Worked example — fin.ai (2026-07-23)

fin.ai advertised 130 roles; the scan collected 1. Cause: the roles live inside
collapsed Base-UI accordions and aren't in the DOM until each team is expanded.

- **Rejected:** a fin.ai branch that clicks its team headers.
- **Accepted:** a general second pass in `expandListings()` that opens any
  `aria-expanded="false"` disclosure outside `nav`/`header`, continuing only
  while job links keep appearing.
- **Verified:** fin.ai 1 → 130 (matches the advertised 130); a plain Greenhouse
  board 50 → 50 (two filter toggles clicked, no new links, stopped after one
  round — no regression). Numbers recorded in the commit.
