# ApplyBro UI/UX Overhaul — Implementation Plan (handoff to Sonnet 5)

Decided with the user 2026-07-09. This document is the spec; implement it in
the phases at the bottom. Read CLAUDE.md first — its rules (never auto-submit,
no-fabrication tailoring, layout/content separation) override everything here.

## 0. Decisions already made (do not re-litigate)
- **Shell:** local web app — FastAPI backend wrapping the existing `applybro/`
  modules + a React (Vite) frontend built to static files, served at
  127.0.0.1:8765. Local-first, no external services, no telemetry.
- **Browser:** ApplyBro owns a dedicated Chrome window via Playwright
  persistent context (`user_data_dir=~/.applybro/chrome-profile`,
  `channel="chrome"`). Tab 1 = the dashboard. Application tabs open in the
  SAME window. Profile persists ATS logins (Workday accounts etc.).
- **Applied detection:** auto-detect submission confirmation (URL patterns +
  DOM text like "thank you for applying" / "application submitted" /
  Greenhouse-Lever confirmation markers) with a manual **Mark applied**
  fallback button on every job card.
- **Batch apply:** sequential with lookahead — one tab under user review while
  the next fills in the background. (A per-batch "open all" toggle is NOT in
  v1.)
- **Sheet logging:** on applied → append one row to a new **"Applications"**
  tab (Company | Title | URL | Location | Fit % | Resume file | Date applied |
  Status) AND update the company's `applied` / `applied_date` columns in the
  target-list tab.
- v1 defaults: light mode only, English only, name stays "ApplyBro".

## 1. The product story (what the user does daily)
1. Open ApplyBro (one command → dedicated Chrome window, dashboard tab).
2. **Run** screen: sheet tab + row range (e.g. 46:76, ~20 companies/day),
   press Run. Watches live progress per company.
3. **Review queue**: job cards with fit evidence appear; user selects jobs
   (per-card, or "select all ≥ 80%"), requests resume rewords where needed.
4. Reword happens via Claude Code (see §5) — cards flip to `reworded ✓`,
   verify runs automatically after reword.
5. **Apply room**: selected jobs fill sequentially in tabs beside the
   dashboard; user reviews each, submits manually; app detects/records it.
6. Everything logs to the Google Sheet; **Tracking** shows email outcomes and
   feeds the next tailoring round (rejection context is already wired into
   `tailor`).

## 2. Information architecture — left sidebar, 5 items
### 2.1 Run (home)
- Source card: Google account indicator, spreadsheet tab dropdown (live from
  Sheets API), column mapping (auto-guessed, editable), row-range input that
  live-previews the company names included, "continue from yesterday" chip
  (remembers last range end).
- Run button → per-company progress rail (streamed via SSE):
  `Celonis ── 9 jobs found ── 3 fit ≥60% ✓` / failures shown inline with
  reason ("client-rendered page — needs browser pass").
- Under the hood: discover → score → auto-create workspaces (tailor) for jobs
  ≥ threshold (setting, default 60%). No CSV/pipeline jargon anywhere in UI.

### 2.2 Review queue
- Job cards grouped by company. Card contents:
  - Title, location, fit % with matched-skill chips (from scores), salary
    estimate line (from job.md), link to job page.
  - ⚠ history badge when Email Log has outcomes for this company (hover:
    timeline, e.g. "rejected 2026-06-04 — stated reason: location/onsite").
  - Resume status chip: `needs reword` → `reworded ✓` → `verified ✓`.
  - Actions: checkbox (batch), Apply now, View resume (PDF inline),
    comment box ("say what to improve — Claude revises"), Skip (archives card).
- Bulk bar (sticky): "n selected · Apply to selected · select all ≥80% ·
  clear". Reword-queue banner when any selected card needs rewording
  (see §5).

### 2.3 Apply room
- Live batch strip: each job = a row with state machine
  `queued → filling → awaiting your review (tab #) → applied ✓ / skipped`.
- Autofill report inline per job (what was filled / uploaded / left for you).
- Mark applied (manual fallback) + Open tab buttons.
- On applied: sheet logging (§0) + workspace status update; card leaves the
  Review queue.

### 2.4 Tracking
- Per-company outcome timelines (Email Log data): applied → received →
  interview → rejection/offer, with dates; stated-reason chips.
- Summary tiles: totals by classification, response rate, median
  days-to-response; pause list ("rejected < 60 days ago").
- "Refresh from Gmail" button (runs track), Insights report rendered as a
  page (not a raw md dump).

### 2.5 Me
- Profile: application answers (all profile.yaml fields incl. phone/
  phone_national, standing dropdown answers, self-ID) with the field help
  text that exists today.
- Resume: content.yaml editor (YAML-validated) + live PDF preview + rebuild;
  the re-skin instructions block (exists today — keep the wording).
- Targets: role keywords editor.
- Settings: fit threshold, batch size, Chrome profile reset, sheet re-pick.

## 3. Design system ("quiet luxury", Apple-adjacent)
- Font stack: `-apple-system, "SF Pro Text", Inter, system-ui, sans-serif`.
  Three sizes only: 13px meta, 15px body, 20px section titles (600 weight).
  Line-height ≥ 1.5.
- Palette:
  - Paper `#FAFAF8` (app background), surface white `#FFFFFF`.
  - Ink `#1A1A1E` (text), muted `#6E6E73`.
  - Accent navy `#1F4E79` (continuity with the resume design) for primary
    actions and active nav.
  - Champagne `#B08D4A` ONLY for success/applied states, sparingly.
  - Danger muted red `#B4423A`. Hairline borders `#E6E4DF`.
- Components: Card (1px hairline, 12px radius, NO drop shadows beyond 1px),
  Chip (skill/status), ProgressRail, QueueRow, Sidebar (icons + labels,
  active = navy text + hairline pill), Toast for confirmations.
- Whitespace is the aesthetic: max content width ~960px, 24/16/8 spacing
  scale. Empty states teach: "No jobs yet — run rows 46:76 on the Run tab."
- Every long action streams progress; nothing ever looks frozen.

## 4. Backend (FastAPI) — API surface
Wrap existing modules; do NOT rewrite discover/score/tailor/verify/autofill/
track/insights logic. New `applybro/server.py` + `applybro/browser.py`.

- `GET  /api/source` / `PUT /api/source` — spreadsheet tabs, column mapping,
  saved range position.
- `POST /api/run {rows}` → job id; `GET /api/events` (SSE) streams progress
  for all long tasks (run, apply, track).
- `GET  /api/queue` — job cards (workspace status derived as in webui
  `_app_status` + scores + salary + history badge).
- `POST /api/queue/{id}/comment`, `POST /api/queue/{id}/skip`.
- `POST /api/apply {ids}` — sequential-lookahead engine (§6).
- `POST /api/applied/{id}` — manual mark-applied.
- `GET  /api/tracking` — Email Log aggregates; `POST /api/track/refresh`.
- `GET/PUT /api/profile`, `GET/PUT /api/content` (YAML-validated),
  `POST /api/content/rebuild`, `GET /api/pdf?path=` (sandboxed to
  applications/ + resume.pdf as today).
- Keep the old `python -m applybro.cli` intact — the API calls the same
  functions, CLI remains for debugging/power use.

## 5. The reword queue (Claude constraint — be explicit in UI)
There is no Claude API key (user decision: no extra costs). Rewording resumes
is done by Claude Code sessions. UX contract:
- Any workspace with `needs reword` joins a visible **Reword queue**.
- The queue banner shows ONE copyable command:
  *"Open Claude Code here and say: `Process the reword queue`"*.
- Convention for Claude Code: every workspace dir where content.tailored.yaml
  is identical to master (or has pending comments.md) is "queued". Claude
  rewords per CLAUDE.md BE-WATER doctrine, runs verify, statuses flip.
- After reword, the UI (which polls/streams status) updates automatically.

## 6. Apply engine (sequential with lookahead)
- One persistent Playwright context (the dedicated window). Queue of N jobs.
- Job k: open tab, run existing autofill logic (frame-aware fill, dropdowns,
  essay-selects, answers.yaml, renamed resume upload). When filled → state
  "awaiting review", focus stays on user's current tab; job k+1 starts
  filling in a background tab.
- Submission watcher per tab: URL/DOM confirmation heuristics → applied ✓ →
  sheet log → toast. Tab close without detection → card prompts "Did you
  submit? [Applied] [Skipped]".
- NEVER click submit-like controls (SUBMIT_PATTERNS guard stays absolute).
- Workday jobs: the interactive wizard needs the terminal today; v1 rule —
  Workday jobs get a "guided fill" card state with page-by-page Next
  confirmation moved into the UI (buttons in the job row replace terminal
  input()), since the browser session is now owned by the server.

## 7. Build phases (each independently shippable; verify before moving on)
1. **P1 — Backend + shell: ✅ DONE (commit cf2a00b).** Reviewed 2026-07-09;
   solid overall, but has known defects — fix them in P1.1 before P2.
2. **P1.1 — Review fixes: ✅ DONE (2026-07-09).** All items in §9 fixed and
   live-verified. Acceptance met: two consecutive runs in one session show a
   clean, uncontaminated progress rail (verified with a 1-company run
   followed immediately by a 20-company run — the second correctly
   subscribed at `after=6`, not `after=0`); Run page's source card loads in
   under a second (header-only fetch); a mid-run page reload re-attaches at
   the run's own start cursor via `run_status()`'s new `after` field, not the
   whole session's history. The product flaw (#9.6) is also fixed: a
   20-company real run staged 14 new candidates into
   `applications/queue.json` with **zero new workspace folders** — verified
   `applications/` still contains exactly the 2 real ones
   (`celonis--senior-ai-integration-automation-engineer`,
   `sumup--senior-full-stack-engineer-superapp`) before and after.
   **Correction to §9.6:** the workspace-naming hash-suffix collision fix is
   **deferred to P2**, not bundled into this phase — removing auto-creation
   already eliminates the collision's main trigger (Run no longer touches
   `make_package` at all), and changing the naming scheme now would have
   risked orphaning the 2 real in-progress workspaces above. P2 owns this
   properly, with the full Review-queue/URL-identity picture in view.
3. **R — Repo restructure: ✅ DONE (2026-07-09).** `applybro/` split into
   `engine/` (jobs, ats, resolve, discover, score, taxonomy, salary, tailor,
   autofill, workday, track, insights — zero Google/HTTP dependencies),
   `google/` (sheets, public_sheet, sheet_factory, columns — the only place
   Google APIs are touched), `app/` (server, runner, launch, ui_state, queue,
   webui — dashboard orchestration only). New `applybro/paths.py` centralizes
   every user-data filename. `cli.py`/`config.py` stay at `applybro/` root.
   All ~24 modules moved via `git mv` (history preserved); every cross-module
   import updated (no `sys.path` hacks, no compatibility shims). One
   mechanical-move bug caught and fixed during verification: `server.py`'s
   `FRONTEND_DIST` path needed a third `dirname()` call after moving one
   package level deeper. Acceptance met: `python -m applybro.cli --help` and
   `verify` both work unchanged; the dashboard starts and runs correctly
   through `.claude/launch.json` (updated to `applybro.app.server:app`);
   `git status` clean.
4. **P2 — Review queue + Apply room: ✅ DONE (2026-07-09).** Both screens
   fully functional, live-verified against real data and a real job form.
   - **URL identity fix (§9.4), solved properly:** `engine/tailor.py` gained
     `find_workspace_by_url()` (scans each workspace's job.md for its
     recorded URL — no schema change needed) and `url_hash()`.
     `make_package()` now finds-or-creates by URL: an existing workspace for
     that exact job is always reused; a genuinely new one gets a
     `company--title-<hash>` name. This fixes the same-title collision AND
     guarantees old-style workspace names (the 2 real ones from before this
     phase) are found and reused, never orphaned. Verified: calling tailor
     twice on the same URL returns the identical directory both times.
   - **Shared-window Apply engine — the hard architectural problem, solved
     and proven live.** The dashboard runs on ASYNC Playwright (its
     persistent Chrome context); the proven, tested autofill logic is SYNC
     Playwright. Rather than rewrite ~500 lines of working DOM-manipulation
     code to async, the dashboard's Chrome now exposes a CDP debugging port
     (`applybro/constants.py: CDP_PORT`, wired into `launch.py`), and
     `engine/autofill.py` connects to it (`connect_over_cdp`) to open new
     tabs in the SAME window — falling back to a standalone launch only when
     the dashboard isn't running. Empirically verified before writing any
     integration code: a CDP-attached tab survives even after the attaching
     sync Playwright client fully disconnects, so "fill and leave it open
     for review" works exactly as needed. **Live end-to-end proof:** started
     a real apply batch through the actual UI against a live Celonis
     Greenhouse form; a CDP introspection of the real running dashboard
     window showed 2 tabs — the dashboard and the filled job application,
     side by side in one window — with the exact same 17/17 fields correct
     as every prior autofill validation. Nothing regressed by sharing the
     window.
   - **Backend:** `app/queue.py` (queue_with_status, create_workspace i.e.
     lazy tailor, skip/unskip, workspace_status), `app/apply_engine.py`
     (sequential batch fill via `autofill(wait_for_close=False)`, so job k+1
     starts filling immediately while job k's tab awaits review — the
     "lookahead"; best-effort submission-confirmation detection via a
     second CDP reconnect checking open tabs' URL/DOM against confirmation
     phrases; manual Mark Applied/Skipped as the reliable fallback, always
     available), `google/sheets.py` gained generic `append_row()` and
     best-effort `update_company_columns()` (verified it gracefully no-ops
     when a target-list tab lacks applied/applied_date columns — confirmed
     "Updated Target List" is exactly such a tab — rather than crash). 21
     API routes total; a second independent SSE buffer (`APPLY`) mirrors
     `RUN`'s cursor-safety design so a run and an apply batch never
     cross-contaminate each other's progress streams.
   - **Frontend:** `Queue.jsx` (cards grouped by company, fit/matched-skill/
     salary/history-badge/status chips, checkbox multi-select, Tailor/
     Verify/Skip/comment actions, sticky bulk bar, reword-queue banner with
     one-click copy of the exact Claude Code phrase from §5) and `Apply.jsx`
     (per-job status rows, inline fill report, Mark applied/skipped). One
     design-system violation caught and fixed before commit: matched-skill
     chips were using the champagne "success" style, which the spec reserves
     for success/applied states only — changed to the neutral navy chip.
   - **Known minor gap, deferred to P3 polish:** a batch's per-field fill
     report (which exact fields were filled) doesn't survive a fresh page
     reload once the batch has finished — only the status chip does (it's
     read from a persisted snapshot, not the ephemeral event stream). Not a
     P2 acceptance blocker; noted for later.
   - Sheet-logging code path (`append_row`, `update_company_columns`) tested
     with a clearly-synthetic row (`__APPLYBRO_TEST__`), confirmed written
     correctly, then deleted — the user's real Applications tab was never
     given a false "applied" record from this testing.
5. **P3 — Tracking + Me + polish: ✅ DONE (2026-07-09).** Both remaining
   screens fully functional, live-verified; legacy code and stale artifacts
   removed.
   - **Tracking page:** `engine/insights.py` refactored to share one
     `_aggregate(hist)` computation between the existing CLI markdown
     `report()` and a new `structured_report(sheet, gmail)` that returns JSON
     (classification counts, response rate %, median days-to-response, stated
     rejection reasons, paused-company list). `app/tracking.py` sources
     applied-companies from the **Applications** tab (not the target list's
     possibly-absent `applied` column) so it works regardless of whether
     `update_company_columns` found matching columns. `Tracking.jsx`: summary
     chips, response-rate/median-days stats, danger-styled pause-list card,
     per-company timeline cards, "Refresh from Gmail" button
     (`refresh_tracking` re-runs `analyze`+`write_log`).
   - **Me page:** one route, four sub-tabs. The important fix here is
     **comment-preserving profile edits** — the legacy webui.py's plain
     `yaml.safe_dump` silently stripped every inline comment in
     `profile.yaml` on save. `app/me.py` uses `ruamel.yaml.YAML()`
     (`preserve_quotes=True`, wide line width) and only updates keys that
     already exist; verified in isolation before wiring up the UI: a no-op
     save produced a byte-identical file, and a real field edit changed
     exactly one line, comments intact. `content.yaml` is handled differently
     — validated via `yaml.safe_load` then written back **verbatim** (no
     re-dump at all), so a malformed paste is rejected without touching the
     file on disk. `ProfileTab` (structured inputs, the same field-help
     hints ported verbatim from webui.py), `ResumeTab` (raw textarea, Save &
     rebuild via `bash build.sh`, Open PDF link, re-skin instructions ported
     verbatim), `TargetsTab` (keywords), `SettingsTab` (fit threshold; Chrome
     profile reset shown as a **documented CLI command**
     (`rm -rf ~/.applybro/chrome-profile`), deliberately not a clickable
     button since it deletes saved ATS logins/cookies).
   - **Polish:** `Toast.jsx` (`ToastProvider`/`useToast()`, auto-dismiss
     2.5s) wired into Queue/Apply/Me actions (tailor, verify, skip, comment,
     mark applied/skipped, profile save). Keyboard nav on the Review queue:
     `j`/`k` move a focused-card outline, `space` toggles that card's
     selection, both ignored while typing in an input/textarea. One
     design-system bug fixed while touching Queue.jsx: matched-skill chips
     were still using the champagne "success" style caught in P2 for a
     *different* chip — corrected to the plain navy chip here too.
   - **Cleanup (acceptance criteria):** `applybro/app/webui.py` deleted
     (`git rm`), the `ui` CLI subcommand removed from `cli.py` (confirmed via
     `--help`: `{auth, discover, score, tailor, verify, apply, start,
     insights, track}`), stale `job_log.csv`/`job_scores.csv`/
     `discover_run.log` (~9.2MB, gitignored, never read by the new pipeline)
     deleted from disk, `README.md` written at the repo root (quickstart,
     5-screen walkthrough, tailoring-safety explanation, directory diagram,
     CLI reference, links to CLAUDE.md/UIUX_PLAN.md).
   - **Known minor gap carried from P2, not yet closed:** a finished batch's
     per-field fill report still doesn't survive a page reload — only the
     status chip persists. Cosmetic; not a blocker for any phase so far.

All of P1 → P3 are complete. A Fable review pass of P1–P3 (2026-07-09)
approved the work with two real findings — fix them in P3.1 before P4.
6. **P3.1 — Review fixes: ✅ DONE (2026-07-09).** Both real findings from the
   Fable review fixed and live-verified; the optional keyboard-order polish
   also done (it was cheap).
   - **Fix 1 (real defect — could write FALSE rows to the user's sheet).**
     `app/apply_engine.py check_confirmations()` matched a pending job to an
     open tab by hostname alone. Root cause: with 2+ still-pending jobs at
     the same company (Celonis alone had 4 similar postings queued at once),
     one submission-confirmation tab's host matches every pending URL for
     that company, so all of them would get silently auto-marked "applied"
     — writing false rows to the real Applications tab for jobs the user
     never actually submitted. Fix: pending URLs are now grouped by host
     first; a host is only eligible for auto-mark when **exactly one**
     pending job shares it. Ambiguous hosts (2+ pending jobs, same host) are
     skipped entirely and fall back to the manual Mark Applied button, which
     is unambiguous by construction. **Verified in isolation** (no live
     browser needed): stubbed `playwright.sync_api` with fake pages/contexts,
     started a batch with 2 pending Celonis (same-host) jobs + 1 pending
     SumUp (unique-host) job, put a confirmation-looking page on each host —
     asserted the unique-host job was auto-detected and marked, and BOTH
     ambiguous same-host jobs were left untouched (zero false marks).
   - **Fix 2 (stat honesty — no-fake-statistics doctrine).**
     `engine/insights.py _aggregate()`'s median-days-to-response calculation
     measured `dated[1]["date"] - dated[0]["date"]` unconditionally — the
     surrounding comment claimed it only counted a *meaningful* response,
     but the code never checked classification, so e.g. two
     "application received" auto-acks in a row would count as a 2-day
     "response". Fixed to match the comment: `d0` = a company's first dated
     entry (skipped entirely if that entry is itself already a
     response-class entry — no meaningful "time to respond" exists to
     measure); `d1` = the first *later* dated entry whose classification is
     in `RESPONSE_CLASSES` (interview/next steps/offer/rejection); a company
     contributes nothing to the median if no such entry exists.
     `Tracking.jsx`'s label updated to "Median days from first contact to
     first real response" with a note that auto-acks don't count. **Verified
     two ways:** (1) a crafted history dict covering exactly the three cases
     named in the spec — ack→rejection (counted, 14 days), ack→ack (not
     counted), rejection-only single entry (not counted) — all passed; (2)
     against the **real** Email Log via the live dashboard's `/api/tracking`
     — every one of the 7 real companies logged there has a *response-class*
     entry as its very first dated touch (rejection, or in Bunq's case
     "next steps"), so under the corrected semantics none of them have a
     measurable pre-response gap and `median_days_sample_size` is honestly
     `0`/`null` rather than fabricating a number from same-classification
     re-touches (which is exactly what the old code would have done for
     Bunq: two "next steps" emails 2 days apart, wrongly counted as a
     "response time").
   - **Optional polish — done.** Review queue's `j`/`k` used to walk the
     raw score-sorted `jobs` array while cards render grouped by company, so
     the focus outline could jump between company groups. `Queue.jsx` now
     derives `byCompany`/`visualOrder` once via `useMemo` and both the
     keyboard handler and the render use that same flattened, grouped
     order. **Live-verified** against the real Review queue (57 real staged
     jobs across many companies, heavily interleaved by score): dispatched
     synthetic `keydown` events at the running dashboard and read the
     outlined card's DOM position directly — repeated `j`/`k` presses moved
     the focus exactly one DOM position per keypress (0 → 45 after 45 `j`s,
     → 43 after 2 more `k`s), and `space` toggled the checkbox of that exact
     visually-outlined card (confirmed by reading the card's own checkbox
     state, not just the selection counter). No stray selection state was
     left behind afterward.
   - Frontend rebuilt (`npm run build`) after every source change — the
     dashboard serves committed `frontend/dist/`, not live `src/`; the live
     verification above ran against the freshly built bundle, confirmed by
     matching the served JS asset hash to the just-built one.
   Acceptance met: both fixes verified as specified above, build re-run,
   committed.
7. **P4 — Packaging (product): ✅ DONE (2026-07-10).** Implemented largely
   by a Sonnet session (which hit its usage limit mid-phase), then reviewed,
   bug-fixed, verified, and landed by Fable. What shipped, against the spec
   below:
   - **`scripts/install.sh`** — python3≥3.9 check, pip install, Chrome
     detection (macOS/Linux paths; warns rather than fails on unknown OS),
     typst check, exact next-steps output. Each check reports itself and
     the script tallies (no blind `set -e` abort). Run live: all checks
     pass on this machine.
   - **`/setup` wizard** — new `app/setup.py` (setup status, config/profile
     bootstrap from the tracked .example files, `SetupRequiredError`),
     a FastAPI exception handler turning it into a structured 409, and
     `Config.try_load()` so the server never hits `Config.load()`'s
     CLI-appropriate `SystemExit`. Frontend: `Setup.jsx` (5 steps per spec;
     step 4 reuses the exported `ProfileTab` component; step 5 honestly
     presents the content.yaml task as a Claude Code step with a copyable
     phrase), and a `SetupGate` in `App.jsx` that redirects every route to
     `/setup` until setup status says ready — rendering nothing until the
     decision is made, so no 409 console noise from a one-frame dashboard
     mount. `launch.py` needed no changes: `start` never loads config
     itself.
   - **`scripts/release_check.py` + `RELEASING.md`** — verifier reads
     identifying strings from the user's own local files at runtime
     (nothing hardcoded), scans applybro/ + frontend/src + tracked docs;
     RELEASING.md documents the (un-run, destructive) strip/filter-repo
     procedure. As a bonus the Sonnet session's own scan caught a REAL
     leak: the user's actual phone number sat in an autofill.py comment as
     an example — replaced with a generic number.
   - **Two real bugs found in Fable's review of the handed-off code, fixed
     before landing:** (1) wizard step 2 ran the OAuth flow via
     `Sheet(cfg)`, whose `__init__` raises `SystemExit` on an empty
     spreadsheet_id — always empty at step 2, and a `SystemExit` in a
     worker thread dies silently (threading swallows BaseException), so
     the wizard would spin on "waiting for consent…" forever. Credential
     acquisition was extracted from `Sheet._service` into a standalone
     `google/sheets.get_credentials(cfg)` that needs no spreadsheet, and
     the worker now catches BaseException into the polled error state.
     (2) a mistyped spreadsheet id — the likeliest user error in the whole
     wizard — escaped `setup_spreadsheet` as a raw 500 (googleapiclient
     HttpError) and left the bad id saved in config.yaml, marking the step
     falsely complete; now a friendly 400 with the id rolled back to "".
     Also added a server-side step-order guard: posting a spreadsheet
     before a token exists would have launched the blocking consent flow
     inside the request.
   - **Verified live (2026-07-10):** fresh temp-dir clone (working-tree
     code overlaid; no user files present) → every config-needing route
     returned structured 409s, zero 500s; headless-Chrome UI test showed
     `/` redirecting to `#/setup` rendering step 1 with the sidebar hidden
     and no console errors (bar the known favicon 404); wizard steps 2–4
     exercised end-to-end in the clone with the live credential copied in
     (auth start→done on an empty-spreadsheet config, bogus sheet id →
     friendly 400 + config rollback, real sheet id → 9 tabs listed,
     profile init → 24-key example template with comments intact, final
     status ready:true, restored state renders the normal dashboard and
     `/setup` stays reachable but unforced). Real-repo regression: 9 tabs
     + 76 companies read through the refactored auth path, 59-job queue
     intact. release_check.py: clean on the real tree (17 personal strings
     checked), and exit 1 correctly flagging a deliberately planted name in
     a .py comment. Clone (containing credential copies) deleted after
     testing; test servers shut down.

   Original P4 spec (for reference — all scope decisions held). Goal: a
   stranger who clones the repo, bringing their own free Google credential,
   reaches a working dashboard without reading code. Scope decisions (made
   2026-07-09, do not re-litigate):
   - **Distribution v1 = git clone + `scripts/install.sh`.** No pip
     packaging / homebrew / installers yet. The script: checks python3
     ≥ 3.9, runs `pip install -r requirements.txt`, verifies Google Chrome
     is installed (playwright uses channel=chrome — NEVER download a
     playwright browser, cdn.playwright.dev is unreliable here) and that
     `typst` is on PATH (build.sh needs it), then prints exact next steps.
     Idempotent; every failure prints an actionable message, not a
     traceback.
   - **First-run onboarding wizard** as a React route (`/setup`) in the
     EXISTING frontend — not a separate app. New `GET /api/setup/status`
     reports what's missing (config.yaml / credentials.json / OAuth token /
     profile.yaml / content.yaml). When config.yaml is missing, `start`
     must still boot cleanly and the frontend routes to /setup; every
     existing /api/* endpoint that needs config must return a clean
     structured "setup required" error (409), never a 500 stack trace.
     Wizard steps, in order: (1) credentials.json walkthrough (Google
     Cloud console, free tier, link + checklist) with a file-presence
     check; (2) OAuth consent — reuse the existing auth flow, show
     connected-account status; (3) spreadsheet ID + tab picker + column
     auto-guess (reuse the /api/source machinery); (4) profile.yaml
     created from profile.example.yaml, edited with the existing
     Me/Profile editor component; (5) resume master: creating content.yaml
     from the user's current resume PDF is a CLAUDE JUDGMENT TASK (Layer 0)
     — the wizard's final step states exactly that and shows the phrase to
     paste into Claude Code; it must NOT pretend to be a button. There is
     NO Claude-API-key step: nothing in the tool calls the API — rewording
     happens in Claude Code sessions.
   - **Release-readiness verifier, NOT a release.**
     `scripts/release_check.py` scans applybro/, frontend/src/, and
     tracked docs for personal data (name / email / phone / spreadsheet id
     patterns read from the LOCAL config/profile/content files at runtime,
     never hardcoded) and fails loudly on any hit. The actual content.yaml
     strip + git-history rewrite from CLAUDE.md is EXPLICITLY OUT OF
     SCOPE: document the release procedure (RELEASING.md or a README
     section) but NEVER run it — it is destructive and happens only when
     the user decides to publish. Do NOT rewrite git history in P4.
   - **Testing constraint:** never move/rename/delete the user's real
     config.yaml, profile.yaml, content.yaml, credentials.json, or
     token.json — not even "temporarily". Fresh-clone behavior is tested
     in a temp-dir clone (`git clone . /tmp/...`), which naturally lacks
     all gitignored user files; wizard steps needing a live credential may
     copy token.json/credentials.json INTO the temp clone for the test.
   Acceptance: in a fresh temp-dir clone, install.sh succeeds and `start`
   boots to the wizard with zero 500s across every route; the wizard flow
   is demonstrated end-to-end in the temp clone (live credential copied in
   for steps 2–4); release_check.py catches a deliberately planted
   personal string in a test file AND passes clean on the actual tracked
   code; README updated with the install path; committed.
8. **P5 — IA restructure + apply history + tracking redesign: ✅ DONE
   (2026-07-10, Fable, from four direct user change requests).**
   - **New sidebar IA (4 items):** Jobs / Apply room / Tracking / Settings.
     The old Run page is gone: its "Rows to run today" strip (row range,
     threshold, Run button, SSE progress rail) folded into the top of the
     **Jobs** page (the renamed Review queue — you run and review in one
     place), and its Source card became **Settings → Source**. The old Me
     page IS Settings now (gear icon, new briefcase icon for Jobs), with
     sub-tabs Source / Profile / Resume / Targets / General. Queue.jsx →
     Jobs.jsx and Me.jsx → Settings.jsx via git mv (history preserved);
     Run.jsx deleted.
   - **Source picker can switch spreadsheet FILES now**, not just tabs:
     paste a Sheets URL or bare id (frontend extracts the id from either),
     Load file → validated via the same POST /api/setup/spreadsheet the
     wizard uses (bad id → friendly 400 + config rollback), then pick the
     tab + columns with auto-guess. Deliberate scope decision: this is a
     paste-the-URL picker, NOT a Drive file browser — listing the user's
     Drive files would require a new OAuth scope (drive.metadata.readonly),
     re-consent, and scope creep against the "Sheets + read-only Gmail
     only" promise. Revisit only if the user asks for a real browser.
   - **Queue freshness:** `applications/queue.meta.json` sidecar written
     ONLY by queue.upsert() (i.e. by a Run — skip/unskip toggles must not
     masquerade as a refresh); GET /api/queue returns last_refresh and the
     Jobs page renders it as "Refreshed just now (12:50 AM)" / "N min ago
     (…)" with the absolute clock time always present, re-rendered every
     30s so it stays honest.
   - **Apply room = Today + History sub-tabs.** Default (Today) shows the
     current batch (if any) plus jobs applied TODAY in the user's local
     timezone (toLocaleDateString("en-CA"), not toISOString — UTC would
     flip "today" in the evening for timezones ahead of it). History =
     every Applications-tab row, newest first, filterable by free-text
     company/title search and from/to date pickers. Each row: job page ↗
     (kept on purpose for deliberate manual re-applies), view resume ↗,
     download PDF ↓ — GET /api/pdf now prefers the RENAMED upload copy
     ("<Role> - <Name> - Resume.pdf", the literal file the employer got)
     and takes `download=1` for a Content-Disposition attachment
     (verified: correct filename in the header). has_pdf flag per row so
     rows whose workspace was pruned say "resume PDF unavailable" instead
     of a dead link.
   - **Can't apply twice:** apply_batch() now takes cfg and pre-loads the
     Applications tab's URLs (best-effort, one read per batch); an
     already-applied URL is never filled — status chip "already applied —
     see history", new job_already_applied SSE event. mark_applied() also
     set_skipped()s the queue entry, so an applied job leaves the Jobs
     queue permanently (the plan's §2.3 "card leaves the Review queue",
     finally implemented properly). One-time migration run for the 1
     pre-existing applied URL (queue 167 → 166).
   - **Tracking redesigned for sense-making** (user: "couldn't make sense
     of it"): leads with an at-a-glance stat-tile row — Applications
     (from the Applications log) / Awaiting reply (applied companies with
     no response-class email yet — computed server-side in
     app/tracking.py against insights.RESPONSE_CLASSES, now a module-level
     constant) / Interviews (+next-steps count) / Offers (only when >0) /
     Rejections. Then the response-rate and median-days sentences in plain
     language, a "needs review" honesty note, "Why they said no" with
     count chips, the danger-styled pause list, and per-company timelines
     sorted by LATEST activity (newest first, latest-outcome chip on the
     card header, collapsed to 8 companies with "Show all"). Stat-tile
     values wear ink text tokens per the design system — status color
     stays in the chips.
   - **Verified live (2026-07-10)** against the real dashboard + real
     sheet: all 4 pages walked headless (zero console errors); Apply
     Today showed the user's real same-day Celonis application with
     working view/download links (download header carries the real renamed
     filename); History search filters to 0 on garbage and finds
     "celonis"; Tracking tiles computed honestly (1 application, 1
     awaiting, 71% response rate over 7 email-log companies); Settings →
     Source prefilled with the current sheet id and listed its 9 tabs;
     refresh label rendered from a temporary meta file (deleted after the
     test — the real one appears at the next Run); already-applied guard
     returned the 1 real applied URL. Screenshots reviewed for layout.
   Known gap carried forward: a finished batch's per-field fill report
   still doesn't survive reload (P2 note) — the new Today/History rows
   make this matter less (the durable record is the sheet row + PDF).
9. **R2 — Architecture & repo-layout restructure: ✅ DONE (2026-07-10,
   Fable, from the user's "engine is vague / root is a mess / why do I
   still have applications?" feedback).**
   - **`applybro/engine/` split into five feature packages**, one per
     product layer, all moved via git mv with imports rewritten
     project-wide (no shims): `discovery/` (discover, resolve, ats, jobs —
     L1), `scoring/` (score, taxonomy, salary — L2), `tailoring/` (tailor
     — L2.5), `autofill/` (forms ← renamed from autofill.py, workday —
     L3), `tracking/` (track, insights — L4). Each package's __init__
     docstring states its layer AND the standing invariant inherited from
     engine/: pure logic, no Google imports, no app-server imports.
     Dependency rule now documented in the README: features import each
     other + paths/config only; google/ is the single Google-API
     integration point; app/ orchestrates but owns no business logic.
   - **Root directory de-cluttered** from ~17 loose files to docs +
     config surface only. New homes: `resume/` (template.typ,
     content.yaml, build.sh, assets/photo, Master Resume/, built
     resume.pdf — the whole Layer-0 system as one unit), `data/`
     (gitignored wholesale: applications/ workspaces + queue.json +
     queue.meta.json + ui_state.json + legacy CSVs — this answers "why do
     I still have applications?": those are LIVE tailoring workspaces
     incl. real submitted applications and the queue, not cruft; they
     just didn't belong at root), `docs/` (UIUX_PLAN.md, RELEASING.md).
     Kept at root deliberately: CLAUDE.md (tooling reads it there),
     README.md, requirements.txt, and the config surface
     (config/profile + examples, credentials.json, token.json) — the
     wizard tells users to place credentials at the root, and examples
     must sit next to the files they template.
   - **paths.py absorbed the whole move** (it exists precisely for this):
     every Python consumer got new locations for free. The exceptions
     that needed real fixes: (1) typst compile in tailoring/verify —
     typst resolves the `--input content=` path relative to template.typ
     (now in resume/) and refuses paths escaping its root, so workspace
     compiles now pass `--root <project>` + a root-relative `/data/...`
     path; build.sh needed nothing (it cd's to its own dir and everything
     it touches lives beside it). (2) me.py's rebuild now runs BUILD_SH
     from paths. (3) release_check.py scan targets + content.yaml
     location. (4) RELEASING.md's filter-repo command now strips BOTH
     content.yaml paths — the file lived at root for most of history.
   - Also fixed while verifying: /api/applications swallowed EVERY sheet
     error as "no applications yet" (a transient Sheets blip rendered as
     an empty history, i.e. lying) — now only a genuinely missing
     Applications tab is empty; anything else is a 502 with the message.
   - **Verified live:** master resume compiles in its new home (photo
     path intact); `verify --dir data/applications/<real workspace>`
     passes incl. the V6 cross-directory typst compile; server smoke —
     queue (166 jobs), applications history (3/3 consistent calls),
     workspace PDF and master PDF both serve 200 application/pdf, setup
     status ready; release_check clean over the new layout; CLI help
     unchanged. git status shows only renames (R) + content edits — no
     history lost.
10. **R3 — apply_engine decisions extracted + package renamed to backend/:
    ✅ DONE (2026-07-10, Fable, from user feedback).**
    - **Business decisions pulled out of the app layer.**
      `app/apply_engine.py` was holding real Layer-3 domain rules; those
      moved into the autofill package so app/ is orchestration-only as the
      architecture claims: (a) `autofill/confirm.py` — the
      submission-confirmation patterns + `looks_confirmed(page)` (the pure
      "does this page look submitted?" decision); (b)
      `autofill/applications.py` — the applications-ledger TAB name + HEADER
      schema + `read_all`/`applied_urls`/`log` (the record of what you
      applied to, written at apply time, read to dedupe re-applies AND by
      Layer-4 tracking). Both take the Google `sheet`/`page` as a parameter
      — the autofill package still imports no Google libs (invariant held,
      verified by grep). apply_engine now keeps only the CDP plumbing,
      in-memory BatchState, the orchestration loop, and error POLICY (which
      is legitimately an app concern and differs per caller). Bonus: this
      collapsed FOUR duplicated "Applications" tab readers (apply_engine,
      /api/applications, two in app/tracking.py) onto the one ledger module.
    - **`applybro/` package renamed to `backend/`** (git mv, 36 renames,
      history preserved) since it only holds backend code and the repo root
      is already "ApplyBro". Relative imports (the vast majority) needed no
      change; fixed the entry points and references: `.claude/launch.json`
      + all docstrings/help to `backend.app.server:app` /
      `python3 -m backend.cli`, argparse prog, release_check SCAN_TARGETS,
      README architecture tree, CLAUDE.md. **Deliberately NOT renamed:** the
      runtime data home `~/.applybro/chrome-profile` (renaming it would
      orphan saved ATS/Workday logins — it's the product-branded home, not
      the package; noted in launch.py), the `applybro-frontend` npm name,
      and a temp filename.
    - **Verified live:** `python3 -m backend.cli --help` + `verify --dir`
      (real workspace, cross-dir typst) pass; server boots via
      `uvicorn backend.app.server:app` — health/queue/applications/source/
      setup-status all green; launch.json valid + retargeted; release_check
      clean scanning `backend/`; the four secrets (config/profile.yaml,
      credentials/token.json) confirmed gitignored AND untracked.
11. **P6 — Discover Jobs polish + apply-room tailoring: ✅ DONE (2026-07-10,
    Fable, from user screenshots + review of the interrupted Opus session).**
    - **Review of Opus's autofill work: PASS after fixes.** The sequential
      top-to-bottom fill (`_fill_in_order`: tag every control with its DOM
      index, dispatch per kind — text/combo/native-select/checkbox — in one
      ordered sweep instead of four separate page-scrolling passes), the
      combobox COMBO-MAP-before-city-autocomplete ordering fix, and the
      Apply-room Autofill-again/download-PDF/UNCONFIRMED-résumé-warning UI
      were all correct. Live-verified on the real Stripe form since Opus's
      own verification run had died on a DNS flake mid-session: 41 controls
      filled in document order (the one "inversion" is Country+Phone sharing
      a visual row), all dropdown values correct, 19 countries ticked,
      residence → "Other", 0 submit attempts, check_never_submits SAFE.
      Two fixes made during review: a missing module-level `os` import in
      apply_engine (the new auto-tailor path would have NameError'd at
      runtime) and a cosmetic `if True:` indentation wrapper left in
      `_do_text` (dedented).
    - **Tailoring moved into the Apply room** (user: the queue's Tailor
      button was confusing — every job needs tailoring anyway).
      `apply_batch` now tailors on demand: no workspace → `create_workspace`
      (status chip "tailoring resume…", new `job_tailoring` SSE event) →
      `verify()` (enforces the no-fabrication rules AND compiles the exact
      PDF that gets uploaded) → fill. Verify failure surfaces as "verify
      failed" with the first problem; queue entries that vanished surface
      honestly. Pipeline exercised on a real queue entry end-to-end
      (create → verify ok → verified), test workspace deleted after.
    - **Discover Jobs page** (renamed from "Jobs", sidebar + h1): keyboard
      j/k+space selection REMOVED (user request); company headings
      collapsible (chevron + always-visible "N relevant jobs" count,
      collapsed hides that company's cards); selection checkbox grown to a
      real 22×22 navy target (`.job-check`); job titles are now links that
      open the posting in a new tab (`.job-title-link`, quiet ink → navy
      underline on hover) replacing the removed "job page ↗" link; the
      tailor-status chip and Tailor/Verify buttons are gone (Skip + comment
      box remain); cards show "N+ yrs experience required" from the queue's
      req_years (danger-tinted when meets_years is false); "Apply to
      selected" no longer gates on tailored+verified (the Apply room does
      that work now); the Discover-new-jobs strip layout fixed (flex-wrap —
      threshold/Run no longer overflow the card).
    - **Apply room**: batch-card and history-row titles are links (new
      tab), "job page ↗" removed, `tailoring resume…` status label added.
    - **Settings → Source**: explicit loader ("one Google API call per tab —
      takes a few seconds") with input disabled while loading; the slowness
      is the per-tab header fetch, now stated in the UI instead of looking
      frozen.
    - **Verified live** (headless against the real dashboard): rename,
      no j/k hint, no Tailor buttons, no job-page links, 22×22 checkbox,
      title links target=_blank, collapse N26 hides its 2 cards and shows
      the count (expand restores), bare selection enables Apply, Source
      loader appears then resolves to 9 tabs, zero console errors.

## 9. P1 code review findings (2026-07-09, review-only pass — fix in P1.1)
1. **BUG — SSE replay corrupts the second run of a session.** RunState.events
   is a global append-only buffer and `subscribeEvents()` defaults to
   `after=0`, so starting run #2 replays run #1's events — including its
   `run_done`, which flips the UI to "done" while run #2 is still going.
   Fix: `POST /api/run` returns the current max event id; the frontend
   subscribes with `after=<that id>`. (Or: clear the buffer on run start —
   but the cursor approach also fixes multi-tab viewing.)
2. **BUG — /api/source fetches every tab's FULL contents just to read
   headers.** `sheet.values(t)` pulls the whole tab — for Job Log that is
   ~1,700 rows × up-to-30KB descriptions on every Run-page load, megabytes
   over the Sheets API to render one dropdown. Fix: fetch range `'{tab}'!1:1`
   only (add a `Sheet.header(tab)` helper).
3. **GAP — refresh mid-run loses the progress rail.** Run.jsx only
   subscribes inside startRun(); if the page mounts while `running=true`
   it should immediately subscribe (after=0 is correct in THAT case).
4. **DESIGN FLAW (inherited, solve in P2) — workspace slug collisions.**
   `applications/<company>--<title>` collides when a company posts several
   jobs with the same title (Celonis has 4 distinct "Lead Value Engineer"
   postings → one workspace, three silently skipped as `already_existed`).
   Queue/apply identity must be the job URL; workspace dirs need a
   disambiguator (e.g. short hash of the URL) for new collisions.
5. Nits (fix opportunistically): `parseRange` renders "NaN companies
   selected" on non-numeric input; `parse_rows` is duplicated between
   cli.py and runner.py (cli should import from runner); per-company import
   inside runner's loop.
6. **PRODUCT FLAW (fix in P1.1) — runner auto-creates a workspace folder for
   EVERY job >= threshold.** One Celonis run spawned ~30 folders of generic
   "Value Engineer" noise (user had to bulk-delete them). Change: run_pipeline
   writes a lightweight `applications/queue.json` index instead
   ({url, company, title, location, score, matched, missing, salary_line,
   history_flag, date}); a workspace directory is created LAZILY, only when
   the user selects the job in the Review queue (or via `tailor --url`).
   Folders in applications/ then mean exactly "user chose to pursue this".
   Workspace naming gains a short URL-hash suffix to fix the same-title
   collision (§9.4).

## 10. Target repo structure (execute as phase R)
Current layout is close; the moves are grouping, not rewriting. Rules:
update all imports/paths properly (no sys.path hacks), keep the `applybro`
package name (CLI stays `python3 -m applybro.cli`), and centralize file
paths FIRST in `applybro/paths.py` (single place that resolves config.yaml,
content.yaml, profile.yaml, applications/, ui_state.json) so later moves of
user data are one-line changes.

    ApplyBro/
    ├── CLAUDE.md  UIUX_PLAN.md  README.md(P3)     # docs at root
    ├── requirements.txt  config.example.yaml  profile.example.yaml
    ├── config.yaml  profile.yaml  credentials.json  token.json  ui_state.json
    │                                              # user data (gitignored)
    ├── content.yaml  template.typ  build.sh  resume.pdf  assets/photo.png
    │                                              # resume master (unmoved)
    ├── Master Resume/                             # reference PDF (re-skin input)
    ├── applications/                              # queue.json + SELECTED-job
    │   └── <company>--<title>-<urlhash>/          # workspaces only (see §9.6)
    ├── applybro/                                  # ── BACKEND (python pkg) ──
    │   ├── cli.py  config.py  paths.py            # paths.py built FIRST
    │   ├── engine/      # pure logic, no HTTP/Google: jobs, ats, resolve,
    │   │                # discover, score, taxonomy, salary, tailor,
    │   │                # autofill, workday, track, insights
    │   ├── google/      # ONLY place Google APIs are touched: sheets,
    │   │                # public_sheet, sheet_factory, columns
    │   └── app/         # dashboard layer: server (API only), runner,
    │                    # launch, ui_state (+ legacy webui until P3)
    └── frontend/                                  # ── FRONTEND (React) ──
        ├── src/         # main, App, api.js (only fetch() site), styles.css
        │                # (design tokens), components/, pages/
        ├── dist/        # committed build — `start` needs no npm at runtime
        └── package.json  vite.config.js  index.html

Separation contract: frontend never touches files or Google — it speaks only
to /api/* on 127.0.0.1; the API boundary IS the frontend/backend boundary.
engine/ must remain importable and fully functional with no server running
(the CLI proves it).

Explicitly NOT moving: CLAUDE.md (must stay at root), the Typst files
(build.sh has relative paths users may have memorized), user data files
(breaking a working personal setup for tidiness is a bad trade — revisit at
P4 packaging when an installer can migrate them).

## 8. Non-negotiables (from CLAUDE.md + user)
- No code path may submit an application. Ever.
- Tailoring guardrails (verify V1-V6) and BE-WATER doctrine untouched.
- No personal data in code; everything user-specific stays in config.yaml /
  profile.yaml / content.yaml / workspaces.
- Local-first: frontend makes requests to 127.0.0.1 only.
- Keep applybro core modules API-stable; UI is a layer, not a rewrite.
