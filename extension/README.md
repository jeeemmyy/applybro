# ApplyBro Chrome extension (Phase 4 — capture & scan)

Jobs enter ApplyBro through this extension: it captures the page YOUR
browser has rendered (your logins, your filters — no bot walls), and the
locally running ApplyBro backend does the extraction, description reading,
AI scoring and staging.

## Install (unpacked, during beta)

1. Open `chrome://extensions`, turn on **Developer mode** (top right).
2. **Load unpacked** → pick this `extension/` folder.
3. Open any job-related page — a floating **A** bubble appears on the right;
   click it to expand the ApplyBro panel inside the page (JobRight-style).
   On other pages, the toolbar icon toggles the panel.
4. Keep the ApplyBro dashboard app running (`python3 -m backend.cli start`);
   the panel shows "Connected as <you>" when it can reach it.

The panel lives IN the page (the Chrome side panel is gone since 0.2.0) — a
full-height drawer on the right that minimizes to a small icon rather than
closing, so a running scan is always one click away.
It disappears during navigations and reopens by itself while a scan or an
apply session is running — the service worker carries the state across
pages, so a mid-scan pagination hop or the posting→form jump loses nothing.

## Use

The panel shows **exactly one action**, chosen by the page it's on — a
careers list offers nothing but *Find Relevant Jobs*.

- **On a company careers list** (apply your filters first) → *Find Relevant
  Jobs*. If you're deep in the pager (`?page=7`), the scan **rewinds to page 1
  first** so the whole filtered list is covered. Then, in order:
  1. collect the jobs on the page — read STRUCTURALLY from the rendered job
     cards (repeated job-URL templates + the card's own title element), not
     guessed from raw links,
  2. **triage the titles** — roles outside your discipline (QA, HR, payroll,
     management…) are dropped here, before anything is fetched,
  3. read the survivors' descriptions in full,
  4. score each against your resume.

  The panel shows this as ONE funnel — each stage a row with its own number,
  ticked when done, ringed while running, and carrying **its own progress
  bar** (a real fraction where there's a total, a moving stripe where the step
  is a single AI call). Every number in it is a number about JOBS: collection
  counts jobs found and pages walked, and title triage — one AI call per 60
  titles, so eight sequential calls on a 466-job board — reports after each
  chunk as "180 of 466" rather than a stripe that moves for minutes. Under it, **every verdict as it lands**: title, score,
  and the AI's reasoning, clamped to three lines with *Read more*. Titles open
  in a new tab so you can overrule the AI yourself. A finished scan adds no
  prose summary — the funnel already says what happened. While it runs, the start button is replaced by
  *Stop Scan* — there's nothing else to press. Anything at or above your fit bar
  lands as **Recommended**. A scan with unreadable descriptions finishes
  **partially completed** — failures are counted, never hidden.

  **Coverage honesty.** The scan reads the count the page advertises
  ("Showing 13–15 of 15") and compares it to what it actually collected. The
  page's own total is the authority: match it and the scan is complete and
  says nothing. Disagree — too many (filters in the URL `#` fragment are lost
  when paging, so HubSpot's filtered 15 becomes the full 155), too few, or
  triage keeping nothing at all — and the panel says so in a warning band. A
  "no relevant jobs" result is only ever shown when it can be trusted.

  Each page is also checked against its OWN stated range ("Displaying 241 to
  260 of 463"): short pages are retried, and whatever is still missing is
  counted and reported as a measured fact. A bot check ("verify you are
  human") stops the walk and says so instead of recording empty pages.

  **And each page is checked against the PREVIOUS one**, because a page that
  doesn't advance passes every check that only looks at one page. A site
  re-serving page 1 still says "Displaying 1 to 20 of 466" and still delivers
  its 20 rows, so the range check ticks green while the walk collects the same
  jobs over and over — that is exactly how a ServiceNow scan reported 160 of
  464 with nothing missing (2026-07-22). The walk now requires the stated
  `from` to increase, or the page to contribute at least one job URL it hasn't
  already got. If neither happens it backs off (5s, then 15s) and only then
  stops, reporting the page it stalled on. Sites tend to do this when a scan
  moves through them quickly, so the message says what was observed and
  suggests narrowing or retrying — it never asserts the site is broken.

  **ATS boards are asked directly.** Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday, Personio, Recruitee and Workable have public
  endpoints; the scan uses those first (clean titles, true totals, full
  descriptions) and falls back to reading the page when they fail or answer
  thinly. Both paths always exist.

  **A long walk asks first.** Once page 1 is captured the scan knows the
  board's own stated size, so a big board stops and says "this board has 463
  jobs across about 24 pages" with *Scan all 463 jobs* / *Cancel*. The numbers
  are the site's, never an estimate — a board that doesn't state a total just
  runs. Small boards are never interrupted (the prompt needs 80+ jobs or 4+
  pages), and cancelling keeps nothing: the gate sits before the first page is
  aggregated, so a cancelled scan can't quietly become a one-page scan.

  On a `#`-filtered site, use the page's own *Show all* first: that puts the
  whole filtered list on one page, and the scan then covers it completely
  (and drops the warning).
- **On a single job posting** → *Apply to this job*. There is no "Save":
  the dashboard records APPLICATIONS, not jobs. Apply reads the JD off the
  posting and shows title / company / description as **editable fields** you
  own — whatever you save is what tailoring reads and what lands in Tracking.
  If you applied to the same job inside 90 days you get an advisory warning;
  it never blocks.
- **On an application form** → the same card, with *Autofill this step*.
  Nothing is recorded until you click **I've applied**.

An apply session in progress on a page that isn't its form is never mentioned
at all — cancel it from the apply card on the form.

**Never-submit doctrine.** The extension fills forms and leaves them for you
to submit. `apply.js` may not click ANYTHING. The single exception in the
whole extension is `expandListings()` in background.js, which clicks "Show
more" on a careers LIST during a scan: it refuses any control inside a
`<form>`, any submit/image/reset input, and anything whose text isn't a
show-more phrase, and it installs the capture-phase submit guard for the
whole pass. `scripts/check_extension_never_submits.py` enforces exactly that
split — it fails if a rail is removed, and if any other file clicks.

## After updating the extension code

Go to `chrome://extensions` and hit the reload (circular arrow) on ApplyBro.
The 2026-07-16 update expanded permissions to "all sites" — required so the
panel's buttons can read and fill pages (activeTab alone only worked at the
moment the toolbar icon was clicked, which is why Save/Scan/Autofill did
nothing). If Chrome disables the extension after reload, re-enable it to
accept the new permission.

## Known gaps (2026-07-22)

- Tailoring needs a description: with none, it refuses rather than rewording
  against nothing.
- A scan cancelled at the "proceed?" prompt, or interrupted mid-capture by
  Chrome killing the service worker, has to be started again from scratch —
  only the backend phase is resumable.

## Narrowing a scan (Settings → Targets)

A 460-job board is mostly jobs you'd never take, and reading them is the
expensive part. Two preferences narrow it, and they work differently on
purpose:

**Locations** are a real filter. The scan collects the WHOLE board first
(so the coverage check still compares against the site's own total), then
drops what's outside your list before anything is read. Matching is generous
in both directions — `Berlin` keeps "Berlin, Germany", `Germany` keeps it
too, `Remote` keeps "Remote — EMEA" and "Work from home". A job whose
location ApplyBro couldn't actually read is **kept**, and the funnel says how
many those were: an unreadable location must never look like a decision.
Empty list = no location filtering.

**Departments are a hint that widens, never a filter.** The same full-stack
role is filed under Engineering, Product, Technology, R&D or Customer Success
depending on the company, so these only push title triage toward keeping a
role. Nothing is ever dropped for being outside the list.

Where an ATS can filter server-side it is asked to — but only where that's
verifiable. Workable is the one adapter that truly paginates, so its API gets
the location list, and if the filtered call comes back empty the scan re-asks
UNFILTERED rather than reporting an empty board: their location vocabulary
isn't yours. The other seven return a whole board in a request or two, so a
query param there would buy nothing and add a way to miss jobs silently. Site
filter UIs are never driven.

## Cap audit (2026-07-22)

Every scan bug so far has been a silent undercount, so the numeric bounds in
the capture → candidates → description chain were swept in ONE pass rather
than one per failed scan. What was found and removed:

| Cap | Was | Now |
| --- | --- | --- |
| Payload `cards` slice | 300 | `MAX_SCAN_CARDS` (2000) |
| Cards per captured page | 300 | 2000 |
| Anchors per captured page | 2000 | 10000 |
| ld+json blobs kept per page | 10 | all (aggregate bounded at 3000) |
| "Show more" click rounds | 40 | 300, or a 4-minute budget |
| List-settle wait | 8s | 16s (still exits on stability) |
| `MAX_ANCHOR_JOBS` (fallback path) | 150 | 1000 |
| `MAX_PENDING_PAGES` | 40, **dropped the rest** | `PENDING_BATCH=25`, batched until the list is walked |
| AI calls per 5 min | 80 | 300 |

The payload slice is the one that cost ServiceNow: 24 pages captured 463
cards correctly and 300 were posted. `MAX_PENDING_PAGES` was the worse bug —
jobs past the 40th were never opened and were then reported as "couldn't be
read", blaming the site for ApplyBro's own ceiling. Descriptions are now read
a batch at a time until the whole list is done, and the one remaining ceiling
(`MAX_RENDERED_READS = 500`, ~30 minutes of tab navigation) reports its
overflow as **"never opened"** — a separate number from "couldn't be read".
