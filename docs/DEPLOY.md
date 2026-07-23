# ApplyBro — hosting the backend (SaaS Phase 6)

Phases 1–5 shipped the product; this is the path from "runs on the admin's
Mac" to "hosted multi-user beta". Some of it is code (done/scaffolded); the
rest is infra the operator runs. **Nothing here is deployed by ApplyBro
itself** — provisioning and secrets are the operator's actions.

## What already supports hosting

- **Supabase is already the hosted DB + auth** (per-user RLS, schemas v2–v5).
- **Config is env-first**: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`,
  `APPLYBRO_AI_PROVIDER`, `APPLYBRO_AI_MODEL_*` all override config.yaml, so a
  hosted process needs no config file — just env vars.
- The app is a standard ASGI app (`backend.app.server:app`).

## Step 1 — run the API as a server

    pip install -r requirements.txt
    uvicorn backend.app.server:app --host 0.0.0.0 --port 8000
    # or gunicorn -k uvicorn.workers.UvicornWorker backend.app.server:app

On Fly/Railway/Render: set the start command above, expose the port, and set
the env vars below. A Dockerfile is trivial (python:3.12-slim + requirements +
that command) — add one matching your host.

## Step 2 — server-side OpenAI key (retire per-user key custody)

Today each user's OpenAI key sits in their admin-set `ai_policy` row (a
transitional custody model that was fine while every backend was the user's
own machine). Hosted, use ONE server key:

1. Set `OPENAI_API_KEY` in the hosted environment (never in the repo/DB).
   `settings.openai_api_key()` already reads the env var first, so this wins
   over any per-user key automatically.
2. In the Admin → AI policy UI, clear the per-user keys (they're now unused).
3. Bill/limit per user via the existing per-account rate limit
   (`backend/ai/ratelimit.py`) — move its in-memory window to Redis if you run
   more than one API process (it's per-process today).

## Step 3 — CORS + the extension

- The extension's `host_permissions` currently list `127.0.0.1:8765`. For a
  hosted backend, add your API origin there and ship the updated extension.
- Add FastAPI CORS for the extension origin (`chrome-extension://<id>`) and
  the dashboard origin. The extension will also need to carry the user's
  Supabase JWT (Authorization header) instead of relying on the local
  session — wire it through the same `store.auth` token the dashboard uses.

## Step 4 — Claude Mac-runner (only if any user routes to claude_cli)

Hosted, the server can't shell out to Claude. Users whose policy uses
`claude_cli` have their AI jobs run on the admin's Mac:

1. Run `supabase/schema_v5.sql` (ai_jobs + runner_status).
2. Wire `ai.complete()`: when `provider == "claude_cli"` AND the process is
   the hosted server (not the Mac), `store.ai_job_enqueue(...)` and poll
   `store.ai_job_get(id)` for the result instead of calling `claude_cli.run`.
   (This switch is intentionally NOT wired yet — it's a no-op until hosting,
   and would only add latency to the local app. Do it as part of going live.)
3. On the Mac: `python3 scripts/claude_runner.py` (needs the app installed +
   `APPLYBRO_RUNNER_EMAIL/_PASSWORD` for the admin account). It claims jobs,
   runs Claude CLI in isolated temp dirs, posts results, and heartbeats.
4. Admin → runner health reads `GET /api/admin/runner`.

**Scaling warning (from the product blueprint):** one Mac runner is fine for
a controlled beta, not production. For many users you need redundant runners,
queue limits, monitoring, and eventually a hosted Claude API path.

## Step 5 — privacy + security before beta

See `docs/PRIVACY.md`. Already in code: OpenAI `store:false`, local resume
parsing, SSRF guard, safe logging, per-account rate limit. Operator actions
before a public claim: a Zero-Data-Retention agreement on the OpenAI account
(if you want to claim ZDR), TLS (the host provides it), and a review that the
`SUPABASE_KEY` in use is the publishable/anon key (RLS does the enforcement),
not the service-role key.

## Guards to keep green in CI

    python3 scripts/check_never_submits.py
    python3 scripts/check_extension_never_submits.py
    python3 scripts/check_safe_logging.py
