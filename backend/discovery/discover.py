"""Per-company discovery orchestration (no fit scoring — that's Layer 2).

Cascade per company:
  1. fetch jobs straight from the sheet's career URL (ATS adapters / JSON-LD),
  2. if empty: download the page HTML and SNIFF embedded ATS board references
     (many marketing career pages embed Greenhouse/Lever/SmartRecruiters/...),
  3. if still empty: try heuristic ATS slug candidates + resolver fallbacks —
     but a GUESSED board is only tentative: the slug may belong to another
     company, or to a stale board (personio.jobs.personio.de "succeeded" with
     1 stale job while personio.com's real careers page listed 108),
  4. if nothing trusted yet: drive a real headless browser at the career page
     itself (browser_discover) — handles client-rendered lists, bot-blocked
     pages and "Load more" pagination, with AI extraction as its last tier.
     Whichever of (guess, browser) found more keyword-matched jobs wins,
  5. if still empty: flag the company for manual review.

`role_keywords` is only a COARSE title prefilter to avoid logging thousands of
unrelated postings from big boards. It is NOT a fit score and makes no claim
about how well a job matches the resume. Leave it empty to log every job.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import requests

from .ats import UA, fetch_jobs, sniff_ats
from .jobs import Job
from .resolve import candidates, resolve

# A guessed board with fewer keyword-matched jobs than this is suspect enough
# to double-check against the real career page in a browser.
WEAK_GUESS_THRESHOLD = 5


def matches_role(job: Job, keywords: List[str]) -> bool:
    if not keywords:
        return True
    title = (job.title or "").lower()
    return any(k in title for k in keywords)


def _try(company: str, url: str, cfg) -> List[Job]:
    jobs = fetch_jobs(company, url, cfg)
    return [j for j in jobs if matches_role(j, cfg.role_keywords)]


def discover_company(company: str, career_url: str, cfg,
                     should_skip=None) -> Dict[str, object]:
    """`should_skip()` (optional) is polled between cascade tiers so the
    dashboard's per-company Skip / global Stop can abandon a slow company
    (e.g. before the ~1-min browser tier) instead of waiting it out."""
    tried: List[str] = []

    def skipping() -> bool:
        return bool(should_skip and should_skip())

    def attempt(url: str) -> List[Job]:
        tried.append(url)
        return _try(company, url, cfg)

    # 1. the URL from the sheet, as-is
    jobs: List[Job] = attempt(career_url) if career_url else []
    used = career_url

    # 2. sniff ATS references out of the page HTML (SSRF-guarded — career_url
    #    can originate from a user-added company / the extension)
    if not jobs and career_url and not skipping():
        from ..net import safe_get
        r = safe_get(career_url, headers=UA, timeout=cfg.request_timeout)
        html = r.text if r is not None else ""
        for cand in sniff_ats(html)[:5]:
            jobs = attempt(cand)
            if jobs:
                used = cand
                break

    # 3. heuristic slug candidates, then the full resolver (handles broken
    #    URLs). These are GUESSES — kept tentative, they no longer end the
    #    cascade on their own (see module docstring: wrong-company slugs and
    #    stale boards produced convincing false positives).
    guessed: List[Job] = []
    guessed_url: Optional[str] = None
    if not jobs and not skipping():
        for cand in candidates(company, career_url):
            if cand in tried:
                continue
            guessed = attempt(cand)
            if guessed:
                guessed_url = cand
                break
        if not guessed:
            resolved: Optional[str] = resolve(company, career_url, cfg)
            if resolved and resolved not in tried:
                guessed = attempt(resolved)
                if guessed:
                    guessed_url = resolved

    # 4. real browser on the career page itself. Worth the ~1 min when the
    #    trusted tiers found nothing and the guess is absent or suspiciously
    #    thin — a genuinely small board stays accepted without a browser run.
    if (not jobs and career_url and cfg.browser_fallback and not skipping()
            and len(guessed) < WEAK_GUESS_THRESHOLD):
        from .browser_discover import discover_with_browser
        browser_jobs = [j for j in discover_with_browser(company, career_url, cfg)
                        if matches_role(j, cfg.role_keywords)]
        if len(browser_jobs) >= len(guessed) and browser_jobs:
            jobs, used = browser_jobs, career_url

    if not jobs and guessed:
        jobs, used = guessed, guessed_url

    jobs = jobs[: cfg.max_jobs_per_company]
    error = None if jobs else (
        "no jobs found (client-rendered page, unknown ATS, or none matched keywords)")
    return {"company": company, "career_url": career_url,
            "resolved_url": used if jobs else None, "jobs": jobs, "error": error}
