# Changelog

All notable changes to ApplyBro are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/).

Development before `v0.4.0` happened in a private repository and is
summarised rather than itemised — see [README](README.md#project-status).

## [Unreleased]

### Added
- **AI answers open-ended application questions without tailoring first.** The
  answerer previously required a tailoring *workspace*, so applying straight
  from a posting left every essay question ("Which of our values resonates with
  you?") blank. With no workspace it now grounds on the session's job
  description plus your master resume. Truth rules unchanged: an answer it
  can't support is still left blank, never invented.
- **Writing style is yours** — `writing_style` and `answer_length` in
  Settings → Profile control the VOICE of those answers (never the facts).
  Settings added to the profile template now surface automatically for
  existing profiles instead of needing a hand-edited YAML.
- **Autofill says why it skipped a field.** Each left-for-you field now
  carries a reason (self-ID, no profile answer matched the options, no profile
  field matches this label, the AI couldn't ground an answer), summarised in
  the panel — so a miss is diagnosable instead of just highlighted.

### Fixed
- The AI's echoed question key is matched back leniently (case, punctuation,
  truncation, or order), so a correctly-answered question is no longer dropped
  and then mislabelled "couldn't answer truthfully".

### Changed
- **Apply flow simplified to two buttons.** A posting used to show Save /
  Tailor / Autofill-this-step / attach-choice all at once. Now it offers
  **Apply with autofill** (saves the details, opens the form, and fills the
  first step on arrival) and **Tailor my resume first** (tailors, then does the
  same). Saving is implicit; "Autofill this step" only appears on later pages
  of a multi-step form. Never-submit is unchanged — the panel navigates and
  fills, you always submit by hand.

### Fixed
- **Apply flow reads the description from the ATS API** when a posting's
  scraped title/description come up thin (a client-rendered Greenhouse job was
  blank). Known boards only; generic URLs are unchanged.
- **The extension now has an icon.** The manifest declared none, so Chrome
  showed a gray placeholder that was easy to miss and never auto-pinned — the
  "icon doesn't show up" report. Added a proper **AB** icon (16/32/48/128) for
  the toolbar and extensions page. Pin it via the puzzle-piece menu; see the
  extension README.
- **Toolbar icon is now self-healing.** Clicking the ApplyBro icon did nothing
  when the content script was missing or orphaned — most often after reloading
  the unpacked extension, which disconnects the content script in every already
  open tab. The icon now injects the panel when none answers, and re-injection
  tears down a dead instance and builds a fresh one, so a working panel always
  results. General resilience, not a per-page fix.
- **Triage count moves more often.** Title triage chunked at 60, so a 130-job
  board stepped the "picking which jobs" number only ~3 times and looked
  frozen between. Chunks are now 30 with one more worker (~5 steps for 130) at
  negligible cost — triage prompts are titles only.

### Added
- **ARIA accordion expansion in scans.** Careers pages that hide every role
  inside collapsed team sections (fin.ai and other Base-UI/Radix disclosure
  widgets) rendered nothing until expanded — a scan of fin.ai's 130 roles
  collected 1. The capture pass now opens `aria-expanded="false"` disclosures
  outside nav/header while job links keep appearing (fin.ai 1 → 130; a plain
  Greenhouse board 50 → 50, no regression). General, not a per-site branch —
  see ADR 0001.
- **ADR 0001** (`docs/adr/`): a scan fix must improve the scanner generally and
  never regress other companies — no `if host == …` branches.

### Changed
- `python3 -m backend.cli start` no longer auto-opens a Chrome window and a
  localhost tab. It runs the backend the extension talks to and prints the URL;
  `--with-browser` restores the legacy dedicated-window/dashboard behaviour.

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
