# Changelog

All notable changes to ApplyBro are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/).

Development before `v0.4.0` happened in a private repository and is
summarised rather than itemised — see [README](README.md#project-status).

## [Unreleased]

### Fixed
- **Live scan progress.** The "reading job descriptions" count sat frozen at 0
  while descriptions were read in a background tab (the backend isn't polled in
  that phase, so only the background-tab counter was live); it now drives the
  row directly and drops the duplicate "— N of M" from the note. Title triage
  ran its chunks sequentially, so the count jumped only once every ~20s and
  looked stuck — the chunks now run in parallel (same AI cost), finishing ~3×
  faster and updating ~3× more often.
- Header "AB" mark and the funnel tick are flex-centred instead of relying on
  line-height, which sat both glyphs slightly high/low.

## [0.4.0] — 2026-07-23

The release that made a scan of a 460-job board trustworthy. Before it, a
ServiceNow scan reported 237 of 463 jobs and blamed the website; it now
collects all 466 across 24 pages, or says precisely why it couldn't.

### Added
- **Location and department targeting** (Settings → Targets). Locations are a
  real filter applied *after* the whole board is collected, so coverage
  checks still see the true total; a job whose location couldn't be read is
  always kept and counted. Departments are a hint that *widens* title triage
  and can never exclude a role.
- **Pre-walk confirmation** — a board of 80+ jobs or 4+ pages asks "this board
  has N jobs across about M pages" before a long walk, using the site's own
  stated numbers.
- **Pagination stall detection.** A page must raise the site's stated `from`
  or contribute a job URL not already collected; otherwise the walk backs off
  (5s, 15s) and stops honestly. This catches boards that silently re-serve
  page 1 — a failure invisible to any check that looks at one page alone.
- Job **locations are read from the rendered card** (only from elements that
  identify themselves as a location — never guessed).
- `scripts/publish.py`, `LICENSE` (Apache-2.0), `NOTICE`, `CONTRIBUTING.md`,
  `SECURITY.md`, issue/PR templates and CI.

### Fixed
- **Nine caps in the capture → candidates → description chain**, audited in
  one pass. The worst: the POST payload sliced 463 correctly-captured cards
  to 300, and `MAX_PENDING_PAGES = 40` *dropped* every unreadable job past the
  40th and then reported them as "couldn't be read" — blaming sites for pages
  ApplyBro never opened. Descriptions are now read in batches until the list
  is done; the one remaining ceiling reports its overflow as "never opened".
- Per-page limits on cards (300), anchors (2000) and ld+json (10/page, which
  discarded 19 of every 20 postings on boards that render one per card).
- AI rate limit raised from 80 calls / 5 min, which stopped scoring mid-board
  on any list with more than ~80 survivors.
- **Funnel honesty.** Capture showed a raw `<a href>` count ("links") instead
  of jobs; title triage showed an endless stripe through eight sequential AI
  calls. Both now report real numbers, including on chunks that fail open.
- Stop now interrupts title triage between chunks instead of grinding through
  the remaining calls.
- Removed the maintainer's email from tracked files.

### Changed
- The public repository now keeps **real, append-only commit history**.
  Previous releases were published as a single force-pushed snapshot.

[Unreleased]: https://github.com/jeeemmyy/applybro/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/jeeemmyy/applybro/releases/tag/v0.4.0
