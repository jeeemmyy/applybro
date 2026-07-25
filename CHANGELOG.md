# Changelog

All notable changes to ApplyBro are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/).

Development before `v0.4.0` happened in a private repository and is
summarised rather than itemised — see [README](README.md#project-status).

## [Unreleased]

### Fixed
- **Autofill-on-arrival works again when the form opens at a different URL.** A
  previous fix gated it behind an exact URL match, so when the ATS apply link
  jumped cross-domain or the ATS redirected (Delivery Hero's Apply →
  `/Workflow` → jobs.smartrecruiters.com), the form opened but was never
  filled — the extension "lost" the job. The `autofillPending` flag, not the
  URL, now drives it: whichever page carries the real form (same tab, a new
  tab, or after any number of redirects) claims the session and fills. An
  intermediate redirect page with no form passes the flag on instead of
  swallowing it.
- **A leftover apply session no longer hijacks other jobs' pages.** A session
  bound to job X used to show X's title/company/description — and an "I've
  applied / Cancel" — on ANY posting or form, so a stale Intercom session
  filled the panel while you stood on a Delivery Hero form. A session now
  "owns" only its own posting and the form it navigated to (tracked
  explicitly); on any other job's page it stays silent and the card offers a
  single "Apply to this job", which clears the leftover session and starts
  fresh here. The confusing "cancels the one in progress" switch button is
  gone.
- **A job posting with a "similar jobs" rail is no longer mistaken for a
  careers list.** Almost every ATS posting has 3+ related-job cards (Delivery
  Hero's has 20), which tripped the structural list detector and offered
  *Find Relevant Jobs* instead of *Apply to this job*. A page carrying a
  JobPosting schema — which a careers list never has — is now classified as
  that one job regardless of the related-jobs rail. Verified across eight page
  shapes with no list/form regressions.
- **An expired session crashed the scan with a 500 instead of asking you to
  sign in.** Scanning reads the fit threshold via `ui_state.load()`, which
  called Supabase and rethrew "Not signed in" — an unhandled 500 ASGI
  traceback. Reading an ephemeral preference now degrades to its default, and
  the scan / save-job / apply-start endpoints fail FAST with a clean 401
  ("Your ApplyBro session has expired — sign in again") rather than running
  the whole AI pipeline and then failing to save.
- **The resume now actually attaches.** `which=master` only served a resume
  *uploaded* to your account, so it 404'd for anyone whose resume is built from
  `content.yaml` — and the panel then blamed the ATS ("the upload field may not
  accept scripted files") for a file it never fetched. It now falls back to the
  built `resume/resume.pdf`, and a failed attach reports the real reason.
- **Country fills from your location.** Forms often ask for country as its own
  field (Greenhouse puts one beside the phone number) and no profile key
  answered it. There's a `country` key now, derived from the tail of your
  "City, Country" location when blank — no retyping.
- **Fields being worked on show a real spinner.** The in-progress state
  animated a box-shadow, invisible at 15px, so a field mid-fill looked
  identical to one still waiting. Waiting, spinning and done are now three
  clearly different marks.
- Upload slots ApplyBro deliberately leaves alone (a cover letter) say so,
  instead of being reported as "not reached".
- **The apply card could render with no buttons at all.** With a session
  active, "Apply to this job" is hidden by design — but if binding that
  session's UI hadn't finished (or failed), its body stayed hidden too,
  leaving nothing to press and no way to cancel. Re-binding now says
  "Restoring the application in progress…" while it works, falls back to an
  explicit escape hatch on failure, and a hard invariant guarantees the card
  always offers at least one action.
- **Re-attaching an application no longer refetches the ATS.** The panel
  re-binds on every page load by sending just the URL, so the description was
  pulled from the board's API each time — ~2s per load, and a failed fetch
  could overwrite a description already in hand. The stored one now wins,
  which is also the correct precedence: what you reviewed and saved beats
  anything refetched.
- **Autofill counted 29 fields where the form has 22.** react-select renders a
  proxy `<input class="…requiredInput">` beside every dropdown — no id, no
  name, no label, no placeholder — purely to trigger native validation.
  Greenhouse's form has seven, and each became a phantom "(unlabelled field)"
  row. A control that cannot be *named* is not a question and is no longer
  detected; the backend already ignored them, so no fillable field is lost.
  Measured on the live form: 29 → 22, every real field kept.
- **The resume attaches instead of asking.** After filling, the flow stopped to
  ask "current resume, or tailor first?" — but that choice *is* the two buttons
  now, so the prompt was a dead-end extra click and the resume never went in.
  "Apply with autofill" attaches your current resume, "Tailor my resume first"
  attaches the tailored one, and the checklist row says which.
- **Every row shows when it's being worked on**, rather than sitting inert
  until it flips to done.

### Added
- **Live autofill checklist.** Autofill now shows every field the form asks
  for, grouped **Required / Optional** exactly as the form presents them, each
  ticking as it lands — ✓ filled, – skipped (with the reason inline), and a
  pulse while the AI writes an answer — under a real percentage and progress
  bar. To make that progress genuine rather than a fake bar, resolving runs in
  two passes: profile-backed fields land in a second or two, then the open
  questions are written by AI and land after.

### Fixed
- **The resume is attached again.** Modern upload widgets (Greenhouse's
  Attach / Dropbox / Drive row) hide the native `<input type="file">` behind
  styled buttons, and detection skipped every invisible control — so the
  resume slot was never even found. File inputs, the one control kind that is
  hidden *by design*, are now detected regardless of visibility. Which slot
  receives it is decided conservatively: a resume/CV-labelled input wins,
  else the first upload not named as another document kind, and never more
  than one — a hidden Cover Letter input must not get the resume.
- **Autofill reports progress.** Writing open-ended answers is a real AI call,
  so the card sat on one frozen line for ~30s and looked hung. It now reports
  each stage (reading → deciding, with a time hint when there are questions to
  write → filling) and the button says it's working.
- **The apply card's buttons follow the flow.** "I've applied" no longer
  appears before you have done anything, and the card switches to
  "Autofill this step" + "I've applied" once a fill has run — previously it
  keyed off the URL alone, which never changed on Greenhouse because the
  application form lives on the posting's own page.
- **The panel appearing only "sometimes" was an uncaught TypeError.** Reloading
  the unpacked extension orphans the content script in every open tab, and its
  `chrome.*` APIs go away — so `chrome.runtime.onMessage.addListener(...)` threw
  `Cannot read properties of undefined (reading 'onMessage')`. That call sits
  near the END of content.js, so the throw aborted everything after it,
  including the block that re-attaches the panel and resumes a scan or apply
  session. Every `chrome.*` use is now behind a liveness check: an orphaned
  instance stops cleanly and a live re-injection (the toolbar icon) replaces it.

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
