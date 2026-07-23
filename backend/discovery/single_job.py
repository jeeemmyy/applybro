"""Fetch ONE job posting by URL — for jobs the user found themselves (a
friend's link, a random posting) and adds to the Apply room by hand, rather
than via a Run over the companies sheet.

Extraction, cheapest first:
  1. known-ATS job URL -> fetch that board via its public API (the same
     adapters discovery uses) and match this posting by URL/id — exact
     title/location/description, no scraping (Greenhouse job pages embed
     no JSON-LD at all, verified live),
  2. plain GET + schema.org JobPosting JSON-LD (most job-detail pages),
  3. headless browser render, then JSON-LD again (client-rendered pages),
  4. AI (local Claude CLI): company/title/location from the rendered text,
     with the page text itself as the description.

Returns a discovery Job like any adapter would, so scoring/tailoring/
autofill treat a hand-added job exactly like a discovered one.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

import requests

from .ats import UA
from .jobs import Job, strip_html

log = logging.getLogger("applybro.discovery.single_job")

_LD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                    re.S | re.I)


def _iter_ld(html: str):
    for m in _LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict):
                yield it
            elif isinstance(it, list):
                for x in it:
                    if isinstance(x, dict):
                        yield x


def _ld_location(it: dict) -> str:
    loc = it.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, dict):
        addr = loc.get("address", {})
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"),
                     addr.get("addressCountry")]
            return ", ".join([str(p) for p in parts if p])
    return ""


def _company_fallback(url: str) -> str:
    """Best-effort company name from the URL when nothing better exists —
    'acme' from jobs.lever.co/acme/..., else the domain's base word."""
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path.strip("/")
    for ats in ("greenhouse.io", "lever.co", "ashbyhq.com",
                "smartrecruiters.com", "workable.com", "recruitee.com"):
        if ats in host and path:
            seg = path.split("/")[0]
            if seg and seg not in ("embed", "api", "o", "j"):
                return seg.replace("-", " ").title()
    parts = host.split(".")
    base = parts[-2] if len(parts) >= 2 else host
    return base.title()


def _job_from_ld(company_hint: str, url: str, html: str) -> Optional[Job]:
    for it in _iter_ld(html):
        if it.get("@type") != "JobPosting":
            continue
        org = it.get("hiringOrganization")
        company = ""
        if isinstance(org, dict):
            company = str(org.get("name") or "").strip()
        elif isinstance(org, str):
            company = org.strip()
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        return Job(
            company=company or company_hint, title=title,
            location=_ld_location(it), url=url, source="manual",
            description=strip_html(str(it.get("description") or "")),
            job_id=str(it.get("identifier") or ""),
        )
    return None


_AI_PROMPT = """This is the rendered text of a single job posting's page.

Page URL: {url}

PAGE TEXT (truncated):
<<<
{text}
>>>

Output ONLY a JSON object, no commentary or fences, shaped exactly:
  {{"company": "...", "title": "...", "location": "..."}}
Rules: values must appear in the page text (location "" if not shown).
If this page is clearly NOT a job posting, output {{"title": ""}}.
"""


def _ai_fields(url: str, page_text: str) -> dict:
    from .. import ai
    prompt = _AI_PROMPT.format(url=url, text=page_text[:15000])
    ok, out = ai.complete(prompt, feature="extraction", timeout=180)
    if not ok:
        log.warning("AI job-field extraction failed: %s", (out or "")[:200])
        return {}
    d = ai.load_json(out)
    return d if isinstance(d, dict) else {}


def _browser_fetch(url: str) -> Optional[dict]:
    """Render the page; returns {"html": ..., "text": ...} or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    from .browser_discover import BROWSER_UA
    with sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(headless=True, **kw)
                break
            except Exception:
                continue
        if browser is None:
            return None
        try:
            from ..net import validate_public_url
            validate_public_url(url)   # SSRF: don't point the browser inward
            ctx = browser.new_context(user_agent=BROWSER_UA, locale="en-US",
                                      viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            return {"html": page.content(),
                    "text": page.inner_text("body") or ""}
        except Exception as e:
            log.warning("browser fetch of %s failed: %s", url, str(e)[:200])
            return None
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _job_from_ats_board(company_hint: str, url: str, cfg) -> Optional[Job]:
    """When the URL belongs to a known ATS, pull the whole board through the
    normal adapter (public JSON API) and pick out THIS posting."""
    from .ats import detect, fetch_jobs
    if detect(url) == "generic":
        return None
    try:
        jobs = fetch_jobs(company_hint, url, cfg)
    except Exception:
        return None
    target = url.rstrip("/").lower()
    for j in jobs:
        if j.url and j.url.rstrip("/").lower() == target:
            return j
    for j in jobs:   # URL styles differ (job-boards. vs boards.) — match by id
        jid = str(j.job_id or "")
        if len(jid) >= 5 and jid in url:
            return j
    return None


def fetch_single_job(url: str, cfg) -> Optional[Job]:
    company_hint = _company_fallback(url)

    # 1. known ATS -> board API
    job = _job_from_ats_board(company_hint, url, cfg)
    if job:
        job.source = "manual"
        return job

    # 2. plain GET + JSON-LD (SSRF-guarded — this url came from the user)
    html = ""
    from ..net import safe_get
    r = safe_get(url, headers=UA, timeout=cfg.request_timeout)
    if r is not None and r.status_code < 400:
        html = r.text
    if html:
        job = _job_from_ld(company_hint, url, html)
        if job:
            return job

    # 3. rendered page + JSON-LD
    rendered = _browser_fetch(url)
    if rendered:
        job = _job_from_ld(company_hint, url, rendered["html"])
        if job:
            return job

    # 4. AI field extraction from whatever text we have
    page_text = (rendered or {}).get("text") or strip_html(html)
    if not page_text.strip():
        return None
    fields = _ai_fields(url, page_text)
    title = str(fields.get("title") or "").strip()
    if not title:
        return None
    return Job(
        company=str(fields.get("company") or "").strip() or company_hint,
        title=title, location=str(fields.get("location") or "").strip(),
        url=url, source="manual",
        description=page_text[:20000],
    )
