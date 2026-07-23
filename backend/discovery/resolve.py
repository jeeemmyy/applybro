"""Career-page resolution.

If the Sheet's career URL is missing or broken, try to find the right one:
  1. use the given URL if it loads,
  2. try heuristic ATS/careers candidates derived from the company name/domain,
  3. optionally fall back to a web search (SerpAPI) if configured.
Returns the resolved URL, or None (so the company is flagged for manual review).
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlparse
import re

import requests

from .ats import UA


def verify(url: str, timeout: int) -> bool:
    if not url:
        return False
    from ..net import safe_get
    r = safe_get(url, headers=UA, timeout=timeout)
    return r is not None and r.status_code < 400


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def candidates(company: str, known_url: str) -> List[str]:
    slug = _slug(company)
    cands: List[str] = []
    if slug:
        cands += [
            f"https://boards.greenhouse.io/{slug}",
            f"https://jobs.lever.co/{slug}",
            f"https://jobs.ashbyhq.com/{slug}",
            f"https://careers.smartrecruiters.com/{slug}",
            f"https://{slug}.jobs.personio.de",
            f"https://{slug}.recruitee.com",
            f"https://apply.workable.com/{slug}",
        ]
    if known_url:
        try:
            dom = urlparse(known_url).netloc
        except Exception:
            dom = ""
        if dom:
            cands += [f"https://{dom}/careers", f"https://{dom}/jobs"]
    return cands


def web_search_careers(company: str, cfg) -> Optional[str]:
    if cfg.search_provider == "serpapi" and cfg.search_api_key:
        try:
            r = requests.get(
                "https://serpapi.com/search.json",
                params={"q": f"{company} careers jobs", "engine": "google",
                        "api_key": cfg.search_api_key},
                timeout=cfg.request_timeout,
            )
            for res in r.json().get("organic_results", []):
                link = res.get("link", "")
                if any(k in link.lower() for k in
                       ("career", "jobs", "greenhouse", "lever", "ashby", "workday")):
                    return link
        except Exception:
            return None
    return None


def resolve(company: str, known_url: str, cfg) -> Optional[str]:
    if known_url and verify(known_url, cfg.request_timeout):
        return known_url
    for c in candidates(company, known_url):
        if c != known_url and verify(c, cfg.request_timeout):
            return c
    hit = web_search_careers(company, cfg)
    if hit and verify(hit, cfg.request_timeout):
        return hit
    return None
