# AI-First Plan (2026-07-12) — handoff doc for Opus

User directives, verbatim intent (decided 2026-07-12, chat with Fable):
1. Settings option per AI use: Claude CLI (+model) OR an OpenAI API key
   (+model). **OpenAI API key is the DEFAULT**, and the default for every
   future AI feature.
2. Per-company **abort** while a discovery run is in progress.
3. **AI-only fit** from now on. No keyword matching for job fit or resume
   fit anywhere. (Keyword scorer retires from the pipeline.)
4. Discovery AI gathers relevant jobs per company; the Apply room's
   tailoring goal becomes **raising that job's fit %** (e.g. 80% → as close
   to 100% as the truth allows), re-assessed after tailoring.
5. Rename "Discover Jobs" → **"Companies"**. Add companies by name OR bare
   URL (AI resolves the name). **Google Sheets is dropped as the company
   source**; keep Sheets only where Tracking still needs it (Email Log +
   Applications ledger).

Implementation order: A → B → C → D. Each phase is shippable alone.
Read CLAUDE.md first. Rules that bind every phase are at the bottom.

---

## Phase A — Provider abstraction (foundation; everything else sits on it)

**New package `backend/ai/`** with one entry point:

    complete(prompt: str, *, feature: str, timeout: int) -> Tuple[bool, str]

- `feature` ∈ {"tailoring", "qa", "fit", "extraction"} — used to pick the
  per-feature model from config and for log lines.
- Providers: `openai.py` (HTTP via `requests` to the Chat Completions API —
  no new SDK dependency) and `claude_cli.py` (move the existing subprocess
  logic out of tailoring/reword.py, PRESERVING the abort machinery:
  `_PROC`/`_kill_tree`/`abort_current` — the dashboard Abort button and
  Stop-batch depend on it).
- OpenAI abort: honest best-effort. A cancel flag makes the caller discard
  the result and move on; the HTTP request itself isn't reclaimable. Say so
  in the docstring; don't fake it.
- Retry: keep the 2×/4s-backoff pattern from ai_score._run (transient
  fast-exit-1 with empty stderr is real; seen 2026-07-12).

**Call sites to migrate** (all currently shell to `claude -p`):
- tailoring/reword.py (resume rewrite)
- tailoring/qa.py (open-question answers)
- scoring/ai_score.py (fit triage + assess)
- discovery/browser_discover.py `_ai_extract`
- discovery/single_job.py `_ai_fields`

**Config** (config.yaml + example + config.py):

    ai:
      provider: openai          # DEFAULT. "claude_cli" = old behavior.
      openai_api_key: ""        # config.yaml is gitignored; env
                                # OPENAI_API_KEY overrides. NEVER commit.
      models:
        tailoring: <model>
        fit: <model>
        qa: <model>
        extraction: <model>

- Migrate/alias the existing `tailoring.model` and `discovery.ai_model`
  keys (read old keys as fallback so existing configs keep working).
- **Do NOT hardcode OpenAI model ids from memory.** At implementation time
  check platform.openai.com/docs (or GET /v1/models with the user's key)
  and build the dropdown list from that. Wrong guessed ids = every call 500s.
- No key configured + provider=openai → clear, actionable error pointing at
  Settings ("Add your OpenAI API key in Settings → General"). NO silent
  fallback to Claude CLI (surprises the user's billing expectations).

**Settings → General UI**: one "AI provider" card — provider radio
(OpenAI API key [default] / Claude Code CLI), masked key input (show last 4
chars once saved), then per-feature model dropdowns whose options depend on
the chosen provider. Replaces/absorbs the two existing model cards
(tailoring model, AI-fit model). Endpoints: GET/PUT /api/ai (supersedes
/api/tailoring + /api/ai-fit; keep old routes as thin shims or delete and
update api.js — grep frontend for both).

**CLAUDE.md**: add a standing product rule — "every future AI feature
defaults to the user's API key (provider abstraction in backend/ai/); the
Claude CLI path is the opt-in alternative."

## Phase B — AI-only discovery fit + per-company abort

**Fit (directive 3):**
- runner.run_pipeline: delete the score_job gate. Pipeline becomes:
  discover_company → ai_score.score_company_jobs (triage → assess) → stage
  every job with ai_score ≥ threshold. `ai_fit` config flag dies (it's
  always on now); the keyword scorer (scoring/score.py) leaves the pipeline.
- KEEP scoring/taxonomy.py + salary.py (salary estimate) and the verify()
  V4 no-invented-skills check — those are safety/estimation, not fit.
- AI triage also replaces the role_keywords title prefilter (AI-only means
  AI-only). Exception allowed purely as cost control: boards with >100
  titles may be truncated before triage — that's pagination, not scoring.
- Queue entries: ai_score/ai_reason/ai_matched/ai_gaps become the primary
  fields; keep writing the old keys where the UI/sheet code still reads
  them until those readers are updated in the same phase.
- UI (Jobs page): single "N% fit" chip (the AI one), reasoning line,
  ai_matched chips + ai_gaps count. Delete the keyword chip.
- **CLAUDE.md fit-scoring section must be rewritten** (user has explicitly
  overridden the keyword-% doctrine): the fit score is now Claude's reasoned
  judgment, grounded only in the real resume, still NEVER presented as a
  callback probability.
- **Cost/latency reality check to surface in the UI**: one triage call per
  company + one assess call per surviving job. A 76-company run is hundreds
  of calls. Show per-company progress ("assessing 4/9 jobs…") via run
  events; that's also what makes per-company abort meaningful.

**Per-company abort (directive 2):**
- runner.py gets `SKIP = threading.Event()` + `CANCEL = threading.Event()`
  (mirror apply_engine's pattern). A `should_skip()` callable is threaded
  into discover_company (checked between cascade tiers), browser_discover
  (between load-more clicks / before AI extraction), and score_company_jobs
  (between assess calls). Skipping mid-company stages nothing for that
  company and emits `company_skipped`.
- Endpoints: POST /api/run/skip-company (skip current), POST /api/run/stop
  (cancel rest). UI: "Skip" button on the active company row in the
  progress rail + a global Stop. An in-flight OpenAI call can't be killed —
  the flag makes the result get discarded (see Phase A).

## Phase C — Tailor-to-raise-fit loop (directive 4)

- Queue entry's discovery ai_score = `fit_before`.
- apply_engine, after reword_workspace + verify pass: re-run
  ai_score.assess_job with the TAILORED resume text (content.tailored.yaml)
  instead of the master → `fit_after` (+ new reason/gaps). Store both on
  the queue entry; SSE event `job_fit_change`.
- reword prompt additions: include the discovery assessment's ai_gaps and
  an explicit goal — "reword/reorder/emphasize so the fit judgment rises as
  far as the TRUTH allows; close listed gaps ONLY where the master content
  genuinely evidences them; a gap the resume can't truthfully cover stays a
  gap." The no-fabrication rules and verify() stay exactly as they are —
  100% is NOT always reachable and must never be reached by lying.
- Optional single retry: if fit_after ≤ fit_before, one more reword attempt
  with the new assessment fed back (mirror the existing verify-retry
  pattern; cap at 1 to bound cost).
- Apply room UI: "fit 80% → 92%" chip on the job card after tailoring, with
  the new reasoning on hover/expand.

## Phase D — Companies tab, Sheets retired as source (directive 5)

- **Store**: data/companies.json (gitignored with data/): entries
  {name, careers_url, added (iso date), last_run, last_jobs_found, notes}.
  New backend/app/companies.py (load/save/add/remove/update — follow
  queue.py's style).
- **Add by name or URL**: POST /api/companies {name?, url?}. Bare URL → AI
  resolves the company name (small extraction call, feature="extraction")
  from the page title/content; name only → resolve.py heuristics +
  browser/AI find the careers URL (candidates() already exists). Show what
  was resolved; let the user edit both fields.
- **One-time migration**: on first load, if companies.json is missing and
  the sheet is configured, import the current companies tab into
  companies.json (keep a `migrated_from_sheet` marker). Print/log clearly.
- **runner.run_pipeline** reads companies.json instead of
  sheet.read_companies(). Row-range selection becomes company checkboxes /
  "run all" / per-company "Run now".
- **Drop**: Job Log sheet appends from discovery + add_manual_job
  (queue.json is the record now); `google.companies_tab/column` config;
  Settings → Source tab (or repurpose it into the Companies management UI).
- **Keep Sheets ONLY for**: Applications ledger (autofill/applications.py)
  and Tracking (Email Log, insights). Setup wizard: spreadsheet + OAuth
  steps become OPTIONAL ("connect Google for tracking — skip if you don't
  want it"); the app must boot and discover/apply fine with no Google at
  all. Grep app/tracking.py + server /api/applications for hard sheet
  assumptions and make them degrade to a friendly empty state.
- **UI**: sidebar "Discover Jobs" → "Companies" (Jobs.jsx rename or new
  Companies.jsx; the review queue of jobs stays on this page below the
  company list, as today). Update README + Settings copy that mention the
  sheet as the source.

---

## Standing rules for the implementing session (learned the hard way)

- Python 3.9 syntax only. `python3 -m py_compile` every touched file.
- NEVER test against real data: monkeypatch queue/companies paths into the
  scratchpad; back up + byte-compare-restore any real file a UI test must
  touch (queue.json, config.yaml pattern from 2026-07-11/12).
- The user's live dashboard runs on port 8765 — never kill it; test on 8766
  via .claude/launch.json `applybro-backend-alt`.
- `cd frontend && npm run build` after ANY frontend edit (dist/ is served).
- `python3 scripts/check_never_submits.py` after touching backend/autofill/.
- config.yaml is gitignored — commit config.example.yaml only.
- OpenAI API key: gitignored config or env only; never in code, logs,
  git, or error messages.
- Remind the user to restart the dashboard after landing changes.
- Commit per phase with the established message style.
