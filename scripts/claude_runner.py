#!/usr/bin/env python3
"""ApplyBro Claude Mac-runner (SaaS Phase 6, MVP-505) — SCAFFOLD.

Activated at HOSTING time (docs/DEPLOY.md). When the ApplyBro backend is
hosted it can't run the Claude CLI; this small process runs on the admin's
Mac instead. It signs in as the admin, claims queued Claude AI jobs from
Supabase (ai_jobs), runs each through backend.ai.claude_cli in an isolated
temp workspace, posts the structured result back, and heartbeats so the admin
health panel can show it online.

Privacy (MVP-702): each job runs in its own temp dir, deleted after upload;
no prompt/result is written to disk beyond that dir or into logs.

Run on the Mac (with the app installed):
    APPLYBRO_RUNNER_EMAIL=you@example.com \
    APPLYBRO_RUNNER_PASSWORD=... \
    python3 scripts/claude_runner.py

This is intentionally a thin skeleton: the queue store helpers
(store.ai_job_*) and claude_cli.run() it composes are the tested parts; the
loop below is glue that only matters once the backend is remote. Do not wire
ai.complete() to enqueue here until the backend is actually hosted — until
then claude_cli runs in-process and this queue stays empty.
"""
from __future__ import annotations

import os
import socket
import time

POLL_SECONDS = 3
HEARTBEAT_SECONDS = 20


def main() -> int:
    from backend import store
    from backend.ai import claude_cli
    from backend.ai import settings as ai_settings

    email = os.environ.get("APPLYBRO_RUNNER_EMAIL", "")
    password = os.environ.get("APPLYBRO_RUNNER_PASSWORD", "")
    if not (store.enabled() and email and password):
        print("Runner needs a Supabase-configured backend and "
              "APPLYBRO_RUNNER_EMAIL / _PASSWORD (the admin account).")
        return 1
    store.login(email, password)
    if not store.is_admin():
        print("That account isn't an admin — the runner claims jobs across "
              "users and must be the admin.")
        return 1

    who = f"{socket.gethostname()}:{os.getpid()}"
    print(f"ApplyBro Claude runner online as {who}. Ctrl-C to stop.")
    last_beat = 0.0
    try:
        while True:
            now = time.time()
            if now - last_beat > HEARTBEAT_SECONDS:
                store.runner_heartbeat(who)          # scaffold store helper
                last_beat = now

            job = store.ai_job_claim(who)            # None when queue empty
            if job is None:
                time.sleep(POLL_SECONDS)
                continue

            # claude_cli.run already isolates cwd to a temp dir and never
            # persists transcripts; nothing else touches disk.
            ok, out = claude_cli.run(job["prompt"], model=job.get("model")
                                     or ai_settings.DEFAULT_MODELS["claude_cli"]["fit"],
                                     timeout=600)
            store.ai_job_complete(job["id"], ok, out)
    except KeyboardInterrupt:
        store.runner_offline(who)
        print("Runner stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
