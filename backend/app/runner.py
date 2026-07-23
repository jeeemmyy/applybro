"""The 'Run' pipeline behind the dashboard's Run screen.

Fuses discover -> score -> stage-in-queue.json per company, so the UI shows
live per-company progress ("Celonis — 9 jobs found — 3 fit >= 60%") instead
of exposing discover/score as separate CLI steps.

IMPORTANT: this does NOT create tailoring workspace folders. A job scoring
>= threshold is staged as a candidate in data/applications/queue.json — nothing
more. A workspace directory (data/applications/<slug>/) is created only when the
user explicitly chooses to pursue a job (via `tailor --url`, or the Review
queue's Tailor button in Phase P2). An earlier version of this file called
make_package() for every fit job, which spawned dozens of throwaway folders
per run (one Celonis run created ~30 "Value Engineer" workspaces the user
never asked for). Folders under data/applications/ now mean exactly one thing:
the user chose to pursue that job.

This module contains NO new business logic — it orchestrates the existing
discover.py / score.py / salary.py / insights.py functions exactly as
cmd_discover / cmd_score already do, just fused per company and reporting
progress through a callback instead of print().

`on_event(dict)` is called for every event; the caller (server.py for the
dashboard, or a CLI command) decides what to do with it (store for SSE,
print, etc.). Events:
  {"type": "company_start", "company": str, "index": int, "total": int}
  {"type": "company_done", "company": str, "jobs_found": int, "new_jobs": int,
   "fit_jobs": [{"title","url","score","salary","has_history"}],
   "error": str|None}
  {"type": "run_done", "companies": int, "total_jobs": int, "total_fit": int,
   "queued": int}
"""
from __future__ import annotations

import datetime
import threading
from typing import Callable, Dict, List, Optional

from ..config import Config
from ..discovery.discover import discover_company
from ..scoring.score import required_years
from ..scoring.ai_score import (AiFit, ScorableJob, assess_job,
                                background_text, score_company_jobs)
from ..google.sheet_factory import get_sheet
from . import queue as queue_store

EventFn = Callable[[Dict], None]

# Per-company Skip and whole-run Stop for a discovery run (mirrors
# apply_engine's CANCEL). _SKIP abandons the CURRENT company (staging nothing
# for it); _STOP ends the run after the current company. Both also call
# ai.abort_current() so a long in-flight assess/triage call dies immediately
# instead of being waited out.
_SKIP = threading.Event()
_STOP = threading.Event()


def request_skip_company() -> bool:
    _SKIP.set()
    from .. import ai
    ai.abort_current()
    return True


def request_stop_run() -> bool:
    _STOP.set()
    from .. import ai
    ai.abort_current()
    return True


def _should_skip() -> bool:
    return _SKIP.is_set() or _STOP.is_set()


def parse_rows(spec: str, n: int) -> tuple:
    """1-based inclusive slice bounds, e.g. '10:25' -> (9, 25)."""
    a, _, b = spec.partition(":")
    lo = int(a) if a.strip() else 1
    hi = int(b) if b.strip() else n
    if lo < 1 or hi < lo:
        raise ValueError(f"rows {spec!r}: need 1 <= X <= Y")
    return lo - 1, hi


def _queue_entry(company: str, job, af: "AiFit", cfg: Config,
                 has_history: bool) -> dict:
    """Build one queue.json entry from the AI fit assessment (`af`). Fit is
    AI-only now — the `score` shown IS the AI's reasoned judgment. `matched`
    / `missing` mirror the AI's matched/gaps so existing readers (sheet log,
    Apply room) keep working. req_years is parsed from the JD (for salary +
    the experience note) — that's plain text extraction, not skill matching.
    Writes NOTHING to disk beyond the queue file (no workspace)."""
    req_years = required_years(job.description or "")
    meets_years = req_years == 0 or cfg.years_experience >= req_years
    entry = {
        "url": job.url,
        "company": company,
        "title": job.title,
        "location": job.location,
        "source": job.source,
        "score": af.score if af and af.score is not None else 0,
        "matched": (af.matched if af else []),
        "missing": (af.gaps if af else []),
        "req_years": req_years,
        "meets_years": meets_years,
        "has_history": has_history,
        "date_found": datetime.date.today().isoformat(),
        # Kept so a LATER lazy tailor can still write the "## Job description"
        # into job.md — the text the resume is reworded against.
        "description": job.description or "",
    }
    if af is not None:
        entry.update(af.to_dict())
    try:
        from ..scoring.salary import estimate as est_salary
        sal = est_salary(job.title, job.description or "", job.location,
                         my_years=cfg.years_experience, req_years=req_years)
        entry["salary"] = sal.render()
    except Exception:
        entry["salary"] = None
    return entry


def add_manual_job(url: str, cfg: Config) -> dict:
    """Stage ONE user-provided job URL (a friend's link, a posting found by
    hand) as a queue entry, exactly like a Run would have — fetched, scored,
    salary-estimated — so tailoring and autofill treat it identically.

    Unlike a Run there is NO fit-threshold gate: the user already chose this
    job, so it's staged regardless of score (the score is still computed and
    shown — an honest number, per the fit-scoring rules). Raises ValueError
    when the page can't be read as a job posting."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("That doesn't look like a URL.")
    from ..discovery.single_job import fetch_single_job
    job = fetch_single_job(url, cfg)
    if job is None:
        raise ValueError(
            "Couldn't read a job posting from that page. If it's behind a "
            "login or a bot wall, there's nothing to fetch — check the URL.")

    # AI-assess this single job against the resume (no threshold — the user
    # chose it). If the AI call fails, stage it anyway with no score rather
    # than block the user from applying to a job they're holding.
    af = assess_job(job.title, job.location, job.description or "",
                    background_text()) or AiFit(worth_exploring=True)
    entry = _queue_entry(job.company, job, af, cfg, has_history=False)
    entry["manual"] = True
    queue_store.upsert([entry], touch_meta=False)
    return entry


def run_pipeline(cfg: Config, rows: str, threshold: int, min_reqs: int,
                 on_event: EventFn,
                 only_companies: Optional[List[str]] = None) -> Dict:
    """Discover each company's jobs, AI-assess every one against the resume,
    and stage those whose AI fit meets `threshold`. Fit is AI-only now — no
    keyword skill matching anywhere (directive 2026-07-12). `min_reqs` is
    accepted for signature compatibility but no longer used.

    `only_companies` limits the run to those company names (case-insensitive)
    — the Companies table's per-company "Refresh jobs" path."""
    # Company list comes from the LOCAL store now (data/companies.json), not a
    # Google Sheet. Import the sheet once on first run if one was configured.
    from . import companies as company_store
    company_store.migrate_from_sheet_if_needed(cfg)
    companies = company_store.as_run_companies()
    if only_companies:
        wanted = {n.strip().lower() for n in only_companies if n.strip()}
        companies = [c for c in companies
                     if c["company"].strip().lower() in wanted]
    if rows:
        lo, hi = parse_rows(rows, len(companies))
        companies = companies[lo:hi]
    # Dedup / "new" flag is against the queue we already staged (queue.json is
    # the record of found jobs now — the Job Log sheet is retired).
    existing = {it["url"] for it in queue_store.load()}

    # Gmail-derived company history is OPTIONAL now (Google is only for
    # tracking). Loaded ONCE — never per job/company (an O(n) API storm that
    # once hung the first run for minutes). No Google configured => no history.
    history_by_company: Dict[str, list] = {}
    try:
        from .. import store
        from googleapiclient.discovery import build
        from ..tracking.insights import load_history
        if store.enabled():
            # Cloud email log; Gmail only needs the OAuth token (no sheet).
            import os as _os
            if _os.path.exists(getattr(cfg, "token_file", "")):
                from ..google.sheets import get_credentials
                gmail = build("gmail", "v1", credentials=get_credentials(cfg))
                history_by_company = load_history(None, gmail)
            else:
                history_by_company = load_history(None, None)
        elif getattr(cfg, "auth_mode", "") == "oauth" and getattr(cfg, "spreadsheet_id", ""):
            sheet = get_sheet(cfg)
            gmail = build("gmail", "v1", credentials=sheet.creds)
            history_by_company = load_history(sheet, gmail)
    except Exception:
        history_by_company = {}

    from ..tracking.insights import match_company_history

    # The candidate's background, loaded once — same resume text for every job.
    ai_background = background_text()

    _SKIP.clear()
    _STOP.clear()
    total_jobs = total_fit = 0
    new_entries: List[dict] = []
    for i, c in enumerate(companies, start=1):
        if _STOP.is_set():
            on_event({"type": "run_stopped", "company": c["company"]})
            break
        _SKIP.clear()   # a fresh skip budget for this company
        on_event({"type": "company_start", "company": c["company"],
                  "index": i, "total": len(companies)})
        res = discover_company(c["company"], c["career_url"], cfg,
                               should_skip=_should_skip)
        jobs = res["jobs"]
        if (res["error"] and not jobs) or (not jobs):
            on_event({"type": "company_done", "company": c["company"],
                      "jobs_found": 0, "new_jobs": 0, "fit_jobs": [],
                      "error": res["error"]})
            continue

        new_jobs = [j for j in jobs if j.key() not in existing]
        for j in new_jobs:
            existing.add(j.key())
        company_store.mark_run(c["company"], c["career_url"], len(jobs))

        history_entries = match_company_history(history_by_company, c["company"])
        has_history = bool(history_entries)

        # AI-only fit: triage titles, then reason about each worth-reading
        # job. Progress + skip flow through so the dashboard can show
        # "assessing 3/8" and bail mid-company.
        def _progress(done: int, total: int, item=None, company=c["company"]) -> None:
            on_event({"type": "company_progress", "company": company,
                      "assessed": done, "to_assess": total})

        sjobs = [ScorableJob(j.url, (j.title or "").strip(), j.location,
                             j.description or "") for j in jobs]
        try:
            ai_by_url = score_company_jobs(
                c["company"], sjobs, ai_background, cfg.role_keywords,
                should_skip=_should_skip, on_progress=_progress)
        except Exception:
            ai_by_url = {}

        # A skip mid-company stages nothing for it — a half-assessed company
        # would be a misleading partial result.
        if _SKIP.is_set() or _STOP.is_set():
            on_event({"type": "company_skipped", "company": c["company"]})
            continue

        fit_jobs: List[dict] = []
        for job in jobs:
            af = ai_by_url.get(job.url)
            if af is None or af.score is None or af.score < threshold:
                continue
            entry = _queue_entry(c["company"], job, af, cfg, has_history)
            fit_jobs.append(entry)
            new_entries.append(entry)

        total_jobs += len(jobs)
        total_fit += len(fit_jobs)
        on_event({"type": "company_done", "company": c["company"],
                  "jobs_found": len(jobs), "new_jobs": len(new_jobs),
                  "fit_jobs": fit_jobs, "error": None})

    queued = queue_store.upsert(new_entries)
    summary = {"type": "run_done", "companies": len(companies),
               "total_jobs": total_jobs, "total_fit": total_fit,
               "queued": queued}
    on_event(summary)
    return summary
