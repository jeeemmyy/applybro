# ApplyBro SaaS MVP — implementation plan (2026-07-14)

Source of truth for scope: the user's MVP CSV (68 items, ~1620h) + User
Journeys blueprint + three decisions from chat (2026-07-14):
1. AI provider/model selection is **admin-only**. Users never pick.
2. **Claude CLI is allow-listed per user** — for now ONLY the admin
   (the admin account). Everyone else: OpenAI only.
3. Users upload **PDF resumes only**. No typst/YAML exposure. ApplyBro
   parses the PDF; tailored resumes are rendered by ApplyBro.

Decision: **evolve this repo** (Supabase auth+RLS, backend/ai/, AI scoring,
tailor-to-fit, autofill, Gmail tracking all exist and are live-tested).
Keep the Vite React dashboard — no Next.js rewrite; nothing in the journeys
needs SSR.

## Standing architecture decisions (read before any phase)

- **One AI choke point stays**: every gate (ACTIVE, SUBSCRIBED, provider
  allowed) wraps `backend/ai/complete()`. Nothing calls a model any other way.
- **Transitional key custody**: while the backend runs on each user's
  machine, the OpenAI key lives in the ADMIN-writable `ai_policy` row that
  the user's own app reads (RLS: user reads own row, only admin writes).
  True server-side key custody arrives when the backend is hosted (Phase 6).
  Never claim "your key never leaves our servers" until then.
- **content.yaml schema = the universal candidate-facts model.** A user's
  uploaded PDF is parsed (text extraction + AI) INTO that schema; scoring,
  tailoring, verify() and the typst renderer then work unchanged. The typst
  template becomes ApplyBro's standard output template for tailored PDFs
  (make photo/optional fields degrade gracefully for generic users).
- **Intake becomes extension-only** (blueprint rule). The Companies tab,
  manual add-job box, and server-side career-page *scanning* retire when the
  extension lands (Phase 4) — server keeps *description reading* + scoring.
- **Hosting is blocked on Phase 5** (extension autofill). Until the
  extension can fill forms, autofill needs the local Python/Playwright app,
  so beta users run the app locally. Host the backend only after Phase 5,
  then move the OpenAI key server-side and stand up the Claude runner queue.
- Python 3.9. `py_compile` everything. Never test on real data (scratchpad
  paths; byte-identical restore of anything real). User's live server on
  8765 — test on 8766. `npm run build` after frontend edits.
  `scripts/check_never_submits.py` after touching backend/autofill/.
  Schema changes ship as `supabase/schema_v3.sql` etc. — the ADMIN runs them
  in the Supabase SQL editor by hand; code must degrade gracefully before
  the SQL has been run.

## Phase 1 — Accounts, admin, AI policy gate  [MVP-101..106, 501..503; decisions 1+2]

Schema (`supabase/schema_v3.sql`, additive):
- `profiles` (user_id PK, email, status pending|active|suspended|rejected,
  subscribed bool, created_at) — filled by a security-definer trigger on
  auth.users insert; new users = pending + unsubscribed (MVP-102).
- `admins` (user_id PK). Seed by the admin's email (set at deploy time). Helper
  `is_admin()` (security definer) for policies.
- `ai_policy` (user_id PK, allow_openai bool default true, allow_claude_cli
  bool default false, primary_provider text default 'openai',
  fallback_provider text null, models jsonb, openai_api_key text default '')
  — created by the same trigger. RLS: owner SELECT; admin ALL.
- `audit_log` (id, admin_id, target_user, action, detail jsonb, at) —
  admin INSERT/SELECT only (MVP-106).
- `profiles` RLS: owner SELECT; admin SELECT/UPDATE. Status/subscribed are
  therefore admin-only by construction.
- Admin seeding also sets the admin active+subscribed+claude allowed.

Backend:
- store: `profile_get`, `ai_policy_get`, `admin_list_users`,
  `admin_update_profile`, `admin_update_policy`, `audit_append`, `is_admin`.
- `backend/ai/gate.py`: `check_access(feature) -> (ok, reason)` — store
  enabled ⇒ require logged_in + ACTIVE + SUBSCRIBED; called at the top of
  `ai.complete()` (MVP-105). Store disabled (pure-local dev) ⇒ allow.
- `ai/settings.py`: provider()/model_for()/openai_api_key() read `ai_policy`
  (admin-set, cached ~60s) ahead of config/env; enforce allow_openai /
  allow_claude_cli; fallback_provider honored when primary unavailable
  (MVP-506). user_settings.ai demoted to legacy fallback.
- `/api/admin/users` GET, `/api/admin/users/{id}` PATCH (status, subscribed,
  policy incl. models + key) — every change audited. `/api/me` gains
  {email, status, subscribed, is_admin}.

Frontend:
- New Admin page (sidebar entry only when is_admin): user table with
  approve/suspend/subscribe toggles, provider allow toggles, primary/
  fallback, model matrix per task (OpenAI list via /api/ai/models), per-user
  OpenAI key field (write-only).
- Settings → General: REMOVE the user-facing AI provider card; show a
  read-only "AI is managed by ApplyBro" note (+ provider label).
- Non-active users: full-page "pending approval" banner; AI errors from the
  gate render friendly.

## Phase 2 — PDF resume upload + parsing  [MVP-201..205; decision 3]

- Storage: `base_resumes` table (user_id PK, filename, pdf_base64,
  parsed_content_yaml, uploaded_at) — mirrors tailored_resumes pattern.
- Parse pipeline: pypdf text extraction → `ai.complete(feature="extraction")`
  with a strict prompt mapping to the content.yaml schema → validate
  (yaml parses, required sections, NO invented facts — instruct model to
  omit what the PDF doesn't state) → store as the user's candidate facts
  (`resumes.content_yaml`). Show a review screen: "here's what we read —
  edit anything wrong" (plain-language editor, not YAML).
- Settings → Resume becomes: upload/replace PDF, pick default, list
  tailored versions per job with fit before/after (MVP-205 already stores
  tailored PDFs per job).
- template.typ: make photo + any owner-specific fields optional so the
  standard template renders any parsed resume. The admin's own pixel-perfect
  setup is unaffected (his content.yaml is already in Supabase).
- Profile tab (MVP-201/202) already exists (profile.yaml editor) — reframe
  copy; reusable answers already flow into autofill.

## Phase 3 — Unified Jobs tab  [MVP-301..305]

- Job entry (jobs.data jsonb) gains `status`
  (recommended|saved|applying|applied|interview|rejected|offer|archived) and
  `timeline` (append-only events: found, scored, tailored, fill started,
  applied, email updates). Migrate: queue entries → recommended/saved;
  ledger rows → applied.
- Dashboard IA: one Jobs page = status filter chips + job cards (current
  score, original score, source) + detail drawer (description, evidence,
  gaps, resume version, timeline) + actions (open, apply, tailor, status
  change, archive) [MVP-303/304]. Apply room's batch UI folds in as the
  "Applying" state; Tracking page folds into timelines + a stats strip.
  SSE already gives live progress (MVP-305).
- Gmail classifier writes status+timeline onto the SAME job rows
  (MVP-706), with manual undo/correct (blueprint's user-control rule).

## Phase 4 — Chrome extension: capture + scan  [MVP-401..409]

- `extension/` (Manifest V3, side panel, service worker). Auth: Supabase JS
  session sharing with the dashboard (same project; PKCE).
- Single-job capture (MVP-403): TS port of single_job.py's ladder — JSON-LD
  → DOM heuristics → visible text; POST /api/extension/job → backend AI
  extraction fallback, scoring, save to jobs (status recommended/saved).
- Career-page scan (MVP-404): capture the user's CURRENT filtered rendered
  view — job cards, links, pagination/load-more state. Normalization +
  in-scan dedup (MVP-405/406: canonical URL, external ID). Coverage checks
  (MVP-407): visible-count vs extracted-count; scans can end "Partially
  completed", never falsely complete.
- Description reading (MVP-408): backend fetch first (ats.py adapters +
  requests); unreadable jobs are sent back to the extension to open ONE
  rendered tab at a time and return the text.
- Scan session = rows in a `scans` table + SSE progress (found/read/scored/
  relevant/duplicate/failed — MVP-409). Per-job error status, retry failed
  reads; no "0 matches" after a crash (blueprint rule).
- RETIRE: Companies tab, dashboard add-job box, run-over-companies pipeline
  (delete UI, keep discovery/ats.py + single_job.py as the server reading
  layer). CLAUDE.md update.

## Phase 5 — Extension autofill  [MVP-604..610]

- Port the forms.py DOCTRINE to TS content scripts (the code doesn't port;
  the landmine list does — react-select re-render staleness, Greenhouse
  iframe + resume-parse remount, exact-match-first options, intl-tel
  national numbers, NEVER Enter-in-input, submit guard as capture-phase
  listeners removed on completion). Read backend/autofill/* as the spec.
- `application_sessions` table: one ACTIVE session per user (MVP-604/605,
  server-enforced). Current-step loop: detect → classify → resolve answers
  (profile, parsed resume, saved answers, ai qa feature) → fill current step
  only → highlight uncertain fields → user clicks Next themselves → repeat
  (MVP-608). Workday + Greenhouse adapters (MVP-609).
- Low-score warning before starting (admin threshold, MVP-601) → tailor
  first (existing reword+verify+rescore loop, fit before/after preserved —
  MVP-602/603 are already built server-side).
- The user always submits manually (MVP-610). `check_never_submits.py`
  grows an extension twin (lint the content scripts for submit()/click on
  submit selectors/Enter dispatch).
- Python/Playwright autofill stays as the admin's local fallback until the
  extension reaches parity; then it's retired from the user product.

## Phase 6 — Hosting, Claude runner, privacy, launch  [MVP-002/004/005, 505, 701..708]

- Host FastAPI (Fly/Railway) + move the OpenAI key server-side (admin env),
  delete per-user key custody. Extension + dashboard point at the hosted URL.
- Claude runner (MVP-505): `ai_jobs` queue table (claimed_by, heartbeat,
  encrypted payload); a small runner script on the admin's Mac wraps the
  existing backend/ai/claude_cli.py; admin health panel (online, heartbeat,
  queue depth, pause). Fallback to OpenAI per policy when offline (MVP-506).
  Scaling warning from the blueprint stands: one Mac = beta only.
- Privacy (MVP-701..703): OpenAI calls stateless (`store: false` in the
  request body; no Assistants/Files — resume PDFs are parsed server-side
  already), Claude runner uses per-job temp dirs deleted after upload,
  logging audit (no prompts/resumes/emails in logs — only feature, tokens,
  status, request id). Security basics (MVP-704): URL validation/SSRF guard
  on everything the extension can make the backend fetch, rate limits.
- E2E journey tests + controlled beta (MVP-707/708).

## Sequencing note

1 → 2 → 3 are dashboard/backend-only and shippable to the current local
install. 4 → 5 are the extension arc. 6 last. Each phase = its own commits,
its own live verification, CLAUDE.md updated whenever a product rule changes
(intake, provider custody, resume model).
