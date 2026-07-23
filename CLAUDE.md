# Job Application Automation — Project Rules

## What this project does
Automates tailoring my resume to specific jobs and filling application forms. Built in layers:
- Layer 0 (current): Typst resume template with content separated from layout.
- Layer 1: discover jobs from company career pages listed in a Google Sheet.
- Layer 2: score job fit and reword my resume per matched job.
- Layer 3: autofill application forms (Playwright for standard ATS; Claude in Chrome for Workday).
- Layer 4: track application outcomes from Gmail (read-only): classify incoming email as
  rejection / interview / next steps / offer, match it to the company/job applied to, and
  update the tracking columns in the same Google Sheet. Uses the free Google OAuth flow
  (gmail.readonly + spreadsheets scopes). Never sends, deletes, or modifies email; never
  auto-replies. Uncertain classifications are marked "needs review", not guessed.

## AI provider (decided 2026-07-12)
All AI in ApplyBro goes through ONE abstraction: `backend/ai/complete(prompt,
feature=...)`. Two providers: OpenAI (the user's own API key — the DEFAULT)
and the Claude Code CLI (opt-in). Every FUTURE AI feature must default to the
API-key provider and route through `backend/ai/` — never shell out to a model
directly. The OpenAI key lives only in gitignored config or the OPENAI_API_KEY
env var; it must never appear in code, logs, errors, or git. Per-feature
models are chosen in Settings → General.

## Product direction (updated 2026-07-22 — THE EXTENSION IS THE PRODUCT)
The Chrome extension is the app. Finding, scoring, tailoring and applying all
happen in the in-page panel; the dashboard is only what the panel can't be.
- Dashboard = **Tracking + Settings**, nothing else. Companies, Jobs and the
  Apply room are DELETED (pages, routes, sidebar). Do not reintroduce a jobs
  view: we track APPLICATIONS, not jobs.
- Scan results are ephemeral — they live in the panel's verdict list. Nothing
  is written to the database until the user clicks "I've applied".
- Apply sessions are UNBOUND: no saved job, no queue. The panel reads the JD
  off the posting (JSON-LD → h1 → main text), shows title/company/description
  as EDITABLE fields, and whatever the user saves is what tailoring reads and
  what lands in Tracking. `backend/app/apply_session.py` imports no queue.
- "Save this job" is gone. A posting page offers Apply.
- Duplicate guard: warn if the same job was applied to within 90 days (URL
  first, then company + close title). ADVISORY — never blocks.
- Admin lives at its own surface, `#/admin` (Users + Settings), never a tab in
  the user dashboard. AI provider + model-per-task is ADMIN-ONLY and appears
  in exactly one place; users see a read-only note in Settings.
- Publishing to GitHub is done FOR the user (credentials are in the macOS
  keychain) and goes through **`python3 scripts/publish.py`** — never a
  manual push, never `--force`. It keeps a mirror clone whose history is
  REAL and append-only: one ordinary commit per publish, each with its own
  message and diff. It refuses to publish if the working tree is dirty, if
  `release_check.py` finds personal data, or if a never-submit rail fails.
  Raw local history is still NEVER pushed: commit 368a654 contains the
  user's resume (name, phone, email), and a public repo serves unreferenced
  commits by SHA forever. The private history stays private; the public
  history starts from the first `publish.py` run.
- **NEVER put `Co-Authored-By: Claude` — or any AI attribution — in a commit
  message, PR, or anywhere in the repo** (user instruction 2026-07-23; it had
  silently accumulated across 78 commits and shows Claude as a contributor on
  a public repo). `publish.py` strips such trailers as a backstop, but the
  right fix is not to write them. Real human co-authors are preserved.

## Scan doctrine (2026-07-22 — earned the hard way)
Every scan bug so far has been a silent UNDERCOUNT that blamed the website.
Order of preference, and the rules that keep it honest:
1. **ATS board API first** (`jobs_from_board_api`): Greenhouse, Lever, Ashby,
   SmartRecruiters, Workday, Personio, Recruitee, Workable. Clean titles,
   true totals, full descriptions. Any failure or a thin answer (<3 jobs)
   falls through — the two paths must both exist.
2. **Structural card reading**: group links by URL template with ids blanked
   AND truncated at the first id (`/jobs/#`), keep templates repeating ≥3
   times, take titles from the card. Never from raw anchors alone.
3. **Verify every page against the site's own stated range** ("Displaying 241
   to 260 of 463") and retry short pages. Fixing sites one at a time does not
   generalise; self-checking does.
4. **Never claim a cause you can't know.** The coverage note says WHAT is
   missing and that it may be an ApplyBro limit — four times it blamed the
   site and was wrong every time.
5. A description that is consent/tracking script noise is NOT a description
   (`usable_description`): count it unread rather than scoring a title.
6. A bot check ("verify you are human") stops the walk and says so.
7. **Caps are audited as a CHAIN, in one pass** — never one per failed scan.
   The 2026-07-22 audit found five more after the first four: the payload
   sliced 463 captured cards to 300, per-page limits on cards (300), anchors
   (2000) and ld+json (10/page), MAX_PENDING_PAGES=40 silently DROPPED every
   unreadable job past the 40th and then counted them as "couldn't be read",
   and the AI rate limit (80 calls/5min) stopped scoring mid-board. A cap that
   truncates must either not exist or be reported as a measured number.
   Current bounds, all deliberate: MAX_SCAN_CARDS=2000, MAX_SCAN_JOBS=2000,
   MAX_LINKS=60000, MAX_RENDERED_READS=500 (reported as "never opened", never
   as "failed"), PENDING_BATCH=25 (a batch size, not a budget).
8. **Every page-level check is blind to a page that doesn't ADVANCE.** Rule 3
   compares a page to its own stated range, which a site re-serving page 1
   passes perfectly ("Displaying 1 to 20 of 466", expected 20, got 20 ✓) —
   measured on careers.servicenow.com 2026-07-22, where the walk collected
   page 1 eight times and reported 160 of 464 with missedRows=0. So the walk
   ALSO checks across pages: the stated `from` must increase, or the page must
   contribute a job URL not already collected. Neither → back off (5s, 15s),
   then stop and say so. Sites throttle fast walks; paging by hand often still
   works, so the note says what was measured and never that the site is broken.
9. **Narrowing (Settings → Targets) never hides a job silently.** LOCATIONS
   are a real filter, applied AFTER collecting the whole board (so the
   coverage check still sees the true total) and only to jobs whose location
   was actually read — an unknown location is always KEPT and counted.
   DEPARTMENTS are a hint that WIDENS triage and must never exclude: the same
   full-stack role is filed under Engineering, Product, Technology, R&D or
   Customer Success depending on the company. ATS query params are used only
   where verifiable (Workable paginates; an empty filtered result re-asks
   unfiltered). Never drive a site's own filter UI.

## Earlier product direction (2026-07-14 — SaaS groundwork, still true)
ApplyBro is becoming a multi-user product: **Supabase is the database and the
auth** (project https://ectuhcoyivbrwmaibsfx.supabase.co), while the app itself
still runs on the user's machine (the autofill layer must drive THEIR browser,
and their OpenAI key never touches our servers). Facts that new sessions need:
- GitHub repo: **https://github.com/jeeemmyy/ApplyBro** (public!). The local
  working copy pushes CLEAN SNAPSHOT commits there (git archive of the tracked
  tree) — never raw local history, which once contained personal data.
- Storage: backend/store/ — one switch. With a `supabase:` block in config.yaml
  (url + publishable key), ALL data is per-user rows in Supabase Postgres under
  row-level security; without it, local JSON files. Google Sheets is fully
  retired. Schema: supabase/schema_v2.sql (multi-user, RLS, run in SQL editor).
- Auth: Supabase email + password, NO confirmation emails — "Confirm email"
  is disabled in the Supabase dashboard (user decision 2026-07-14; both OTP
  codes and signup-confirmation emails hit the built-in mailer's rate limit).
  Signup signs the user straight in. Never add auth flows that send email
  unless the user asks. Sign-in is REQUIRED when Supabase is configured — companies,
  review queue, applications, tracking, AI keys, resume and profile all
  belong to the signed-in user (user_id + RLS). Session tokens live in
  gitignored data/session.json; passwords are never stored.
- Per-user AI scans only — never add "scan all companies" buttons; AI runs are
  expensive and must stay deliberate and scoped.
- No personal facts in code OR in tracked files. The user's resume/profile live
  in the Supabase `resumes` table; resume/content.yaml and profile.yaml are
  gitignored local caches of it (typst and the autofiller read the files).
- config.example.yaml / resume/content.example.yaml are the tracked templates.

## The most important rule: layout vs content are separate
- `resume/template.typ` = layout only. Fonts, margins, spacing, structure. NEVER put resume content here.
- `resume/content.yaml` = content only. My actual experience, bullets, skills. NEVER put formatting here.
- Every generated PDF must look pixel-identical to the original resume. The whole point of this
  separation is that rewording the content can never change the appearance. If a change would
  alter the visual output's layout, it belongs in resume/template.typ and must be intentional.

## Resume tailoring rules (CRITICAL — these protect me from lying on applications)
Tailoring doctrine: BE WATER (decided 2026-07-08). The resume should reshape to
fill each job's container — headline, summary, skill-line labels/order, and
bullet emphasis should all mirror the job's language and priorities as strongly
as the truth allows. Only the FACTS are rigid. A tailored resume that reads
almost identical to the master is a tailoring failure, not a safety success.
E.g. for an "AI Integration & Automation Engineer" role, the headline should
say that (I genuinely do Make/Zapier automation, API integrations, and AI
workflows), not stay "Full Stack Developer".

When tailoring my resume for a specific job, you may:
- Rewrite the headline (contact.title) to mirror the target role, as long as it
  describes work I genuinely do. Employment titles at real companies never change.
- Reword existing bullets to emphasize relevant skills using language from the job description.
- Reorder bullets, skills lines, and items within lines to put the most relevant first;
  relabel skill groupings (e.g. "Delivery" -> "Automation & Integrations") when truthful.
- Rewrite the summary to foreground the experience this job cares about.

You may NEVER:
- Invent experience, skills, tools, employers, dates, or achievements I don't have.
- Add a skill to my resume just because the job asks for it, unless it's already true of me.
- Inflate scope, seniority, metrics, or responsibilities beyond what content.yaml states.
- Change dates, titles, or company names.
If a job requires something I genuinely lack, leave it out — do not fabricate it. It is better
to be a weaker match honestly than a strong match dishonestly.

## Autofill safety: NOTHING may ever submit an application
The autofiller fills forms and leaves them open for me to review and submit.
This rule was once broken for real, so it is now enforced mechanically:
- NEVER press Enter/Return in a form field. Enter inside an input is the
  browser's *implicit submit* gesture — it submitted a real application when
  a react-select didn't swallow the keypress. Use clicks/scrolling instead.
- Never call `form.submit()` / `requestSubmit()`, never click a submit button.
- While the filler touches the page it installs a submit guard (blocks the
  submit event, Enter, and the `submit`/`requestSubmit` methods, which fire no
  event), removed in a `finally` so I can still submit by hand.
- `python3 scripts/check_never_submits.py` fails if any of that regresses.
  Run it after touching backend/autofill/.
- The Chrome extension (extension/apply.js) fills forms too and obeys the SAME
  doctrine: same capture-phase guard, no .submit()/.requestSubmit()/.click()/
  Enter, guard removed in a `finally`. `python3
  scripts/check_extension_never_submits.py` enforces it — run it after touching
  extension/. Proven live: when a test page's own onchange tried requestSubmit()
  mid-fill, the guard blocked it.
Also: never fill a field with a guessed value. A wrong answer on a real
application is far worse than an empty one — leave it and report it.

## Fit scoring rules (AI-only, decided 2026-07-12)
- Fit scoring is AI reasoning, NOT keyword matching. backend/scoring/ai_score.py asks the
  model (via backend/ai) to judge how well my real background fits a role, in two stages:
  triage the job titles worth reading, then assess each one's fit % with written reasoning.
  Keyword/taxonomy overlap is NO LONGER used for fit anywhere (it buried good matches — a
  full-stack role listing every cloud vendor separately scored 36% while a manager role I'm
  a weak fit for scored 89%).
- The fit % is a hiring-fit JUDGEMENT. NEVER present it as a "chance of hearing back" or any
  predicted callback probability — that's unknowable and I don't want fake statistics.
- The assessment MUST be grounded only in what my resume actually shows — never credit me
  with a skill or experience I don't have. "matched" = requirements the resume genuinely
  evidences; "gaps" = real missing requirements.
- The taxonomy (backend/scoring/taxonomy.py) and required_years() survive only for the
  salary estimate and the "N+ years required" note — NOT for fit.

## Re-skinning the resume (supported workflow)
When I provide a new reference PDF whose layout I prefer, re-run the Layer-0
process against it: analyze its fonts/sizes/colors/spacing, rewrite template.typ
to render the EXISTING content.yaml in that new look, and iterate against a
numeric span-comparison harness until pixel-faithful. content.yaml is never
modified by a re-skin (extend its schema if the new design needs it — never
drop data). This is a Claude judgment task, not a dashboard button.

## Keep one page (unless my original is longer)
Match the length of my original resume. If tailoring makes content overflow, tighten wording —
do not shrink fonts or margins to cram more in.

## Style
- Prefer editing existing files over creating new ones.
- Explain what you changed and why, concisely.
- When unsure whether something counts as fabrication vs. rewording, ask me.