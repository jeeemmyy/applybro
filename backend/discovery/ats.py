"""ATS adapters + detection.

Each adapter takes (company, career_url, cfg) and returns a list[Job].
We prefer official public JSON endpoints (Greenhouse / Lever / Ashby / Workday)
because they're stable and give us clean descriptions. For anything else we fall
back to parsing schema.org JobPosting JSON-LD out of the page HTML.

Bespoke career sites that render jobs purely client-side and expose no JSON-LD
won't yield results here — those get reported as "no jobs found" so you can
handle them (a Playwright/agent step is a natural future extension).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional
import json
import re

import requests

from .jobs import Job, strip_html

# A real browser UA, not a self-identifying bot one: career sites answer
# 429/403 to anything that looks scripted (personio.com did exactly this),
# which blinded both the generic adapter and the ATS sniff step.
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, timeout: int, **kw) -> requests.Response:
    return requests.get(url, headers=UA, timeout=timeout, **kw)


def detect(url: str) -> str:
    """Route a career URL to an adapter name based on its host."""
    u = (url or "").lower()
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "myworkdayjobs.com" in u or "/wday/" in u:
        return "workday"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "jobs.personio.de" in u or "jobs.personio.com" in u:
        return "personio"
    if "recruitee.com" in u:
        return "recruitee"
    if "workable.com" in u:
        return "workable"
    return "generic"


def _first_match(url: str, patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- Greenhouse
def greenhouse(company: str, url: str, cfg) -> List[Job]:
    token = _first_match(url, [
        r"greenhouse\.io/embed/job_board\?for=([^&/?#]+)",
        r"greenhouse\.io/([^/?#]+)",
    ])
    if not token:
        return []
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = _get(api, cfg.request_timeout).json()
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            company=company, title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""), source="greenhouse",
            description=strip_html(j.get("content", "")), job_id=str(j.get("id", "")),
        ))
    return out


# --------------------------------------------------------------------- Lever
def lever(company: str, url: str, cfg) -> List[Job]:
    slug = _first_match(url, [r"lever\.co/([^/?#]+)"])
    if not slug:
        return []
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get(api, cfg.request_timeout).json()
    out = []
    for j in data:
        cat = j.get("categories", {}) or {}
        out.append(Job(
            company=company, title=j.get("text", ""),
            location=cat.get("location", ""), url=j.get("hostedUrl", ""),
            source="lever",
            description=j.get("descriptionPlain") or strip_html(j.get("description", "")),
            job_id=str(j.get("id", "")),
        ))
    return out


# --------------------------------------------------------------------- Ashby
def ashby(company: str, url: str, cfg) -> List[Job]:
    org = _first_match(url, [r"ashbyhq\.com/([^/?#]+)"])
    if not org:
        return []
    api = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=false"
    data = _get(api, cfg.request_timeout).json()
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            company=company, title=j.get("title", ""),
            location=j.get("location", ""),
            url=j.get("jobUrl") or j.get("applyUrl", ""), source="ashby",
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
            job_id=str(j.get("id", "")),
        ))
    return out


# ------------------------------------------------------------------- Workday
def workday(company: str, url: str, cfg) -> List[Job]:
    m = re.match(
        r"https?://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)",
        url, re.I,
    )
    if not m:
        return []
    tenant, dc, site = m.group(1), m.group(2), m.group(3)
    base = f"https://{tenant}.{dc}.myworkdayjobs.com"
    cxs = f"{base}/wday/cxs/{tenant}/{site}"
    out: List[Job] = []
    offset, limit, total = 0, 20, None
    while True:
        r = requests.post(
            f"{cxs}/jobs",
            headers={**UA, "Content-Type": "application/json"},
            json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
            timeout=cfg.request_timeout,
        )
        if r.status_code != 200:
            break
        d = r.json()
        total = d.get("total", total)
        posts = d.get("jobPostings", [])
        if not posts:
            break
        for p in posts:
            path = p.get("externalPath", "")
            job_url = path if path.startswith("http") else f"{base}/{site}{path}"
            out.append(Job(
                company=company, title=p.get("title", ""),
                location=p.get("locationsText", ""), url=job_url,
                source="workday", description="", job_id=path,
            ))
        offset += limit
        if len(out) >= cfg.max_jobs_per_company:
            break
        if total is not None and offset >= total:
            break
    # Best-effort description fetch (Workday exposes it per posting under cxs).
    for j in out[: cfg.max_jobs_per_company]:
        try:
            rr = requests.get(f"{cxs}{j.job_id}", headers=UA, timeout=cfg.request_timeout)
            if rr.status_code == 200:
                info = rr.json().get("jobPostingInfo", {}) or {}
                j.description = strip_html(info.get("jobDescription", ""))
                if info.get("externalUrl"):
                    j.url = info["externalUrl"]
        except Exception:
            pass
    return out


# ------------------------------------------------------------ SmartRecruiters
def smartrecruiters(company: str, url: str, cfg) -> List[Job]:
    """Public postings API — no key needed."""
    slug = _first_match(url, [r"smartrecruiters\.com/([^/?#]+)"])
    if not slug:
        return []
    out: List[Job] = []
    offset = 0
    while True:
        api = (f"https://api.smartrecruiters.com/v1/companies/{slug}"
               f"/postings?limit=100&offset={offset}")
        d = _get(api, cfg.request_timeout).json()
        posts = d.get("content", [])
        if not posts:
            break
        for p in posts:
            loc = p.get("location", {}) or {}
            loc_s = ", ".join([x for x in (loc.get("city"), loc.get("country")) if x])
            ref = p.get("ref", "")  # API detail URL; jobAd fetched separately below
            out.append(Job(
                company=company, title=p.get("name", ""), location=loc_s,
                url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id','')}",
                source="smartrecruiters", description="", job_id=str(p.get("id", "")),
            ))
        offset += 100
        if len(out) >= cfg.max_jobs_per_company or offset >= d.get("totalFound", 0):
            break
    return out


# ------------------------------------------------------------------ Personio
def personio(company: str, url: str, cfg) -> List[Job]:
    """Public XML feed: https://<slug>.jobs.personio.de/xml"""
    m = re.search(r"https?://([a-z0-9-]+)\.jobs\.personio\.(de|com)", url, re.I)
    if not m:
        return []
    slug, tld = m.group(1), m.group(2)
    base = f"https://{slug}.jobs.personio.{tld}"
    r = _get(f"{base}/xml", cfg.request_timeout)
    if r.status_code != 200:
        return []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out = []
    for pos in root.iter("position"):
        def t(tag):
            el = pos.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        desc = "\n\n".join(
            strip_html((d.findtext("value") or ""))
            for d in pos.iter("jobDescription"))
        jid = t("id")
        out.append(Job(
            company=company, title=t("name"), location=t("office"),
            url=f"{base}/job/{jid}" if jid else base,
            source="personio", description=desc.strip(), job_id=jid,
        ))
    return out


# ----------------------------------------------------------------- Recruitee
def recruitee(company: str, url: str, cfg) -> List[Job]:
    slug = _first_match(url, [r"https?://([a-z0-9-]+)\.recruitee\.com",
                              r"recruitee\.com/o/([a-z0-9-]+)"])
    if not slug:
        return []
    r = _get(f"https://{slug}.recruitee.com/api/offers/", cfg.request_timeout)
    if r.status_code != 200:
        return []
    out = []
    for o in r.json().get("offers", []):
        out.append(Job(
            company=company, title=o.get("title", ""),
            location=o.get("location", ""),
            url=o.get("careers_url", "") or f"https://{slug}.recruitee.com/o/{o.get('slug','')}",
            source="recruitee", description=strip_html(o.get("description", "")),
            job_id=str(o.get("id", "")),
        ))
    return out


# ------------------------------------------------------------------ Workable
def workable(company: str, url: str, cfg) -> List[Job]:
    """Workable is the ONE adapter here that truly paginates, so it's the one
    where asking the API to filter saves real work — the other seven return a
    whole board in a request or two and a query param would only add a way to
    silently miss jobs. Even here the filtered call is VERIFIED: if it comes
    back empty we re-ask unfiltered and let the backend's own location filter
    do the work, because Workable's location vocabulary is not the user's."""
    slug = _first_match(url, [r"apply\.workable\.com/(?:api/v\d/accounts/)?([a-z0-9-]+)",
                              r"([a-z0-9-]+)\.workable\.com"])
    if not slug:
        return []
    wanted = [str(x) for x in (getattr(cfg, "locations", None) or [])]
    jobs = _workable_page(company, slug, cfg, wanted)
    if wanted and not jobs:
        # The filter matched nothing. That is far more likely to be our terms
        # not matching their vocabulary than a board with no jobs — never
        # report an API filter miss as an empty board.
        jobs = _workable_page(company, slug, cfg, [])
    return jobs


def _workable_page(company: str, slug: str, cfg, wanted: List[str]) -> List[Job]:
    out: List[Job] = []
    token = None
    while True:
        body = {"query": "", "location": list(wanted), "department": [],
                "worktype": [], "remote": []}
        if token:
            body["token"] = token
        r = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            headers={**UA, "Content-Type": "application/json"},
            json=body, timeout=cfg.request_timeout)
        if r.status_code != 200:
            break
        d = r.json()
        for j in d.get("results", []):
            loc = j.get("location", {}) or {}
            out.append(Job(
                company=company, title=j.get("title", ""),
                location=", ".join([x for x in (loc.get("city"), loc.get("country")) if x]),
                url=f"https://apply.workable.com/{slug}/j/{j.get('shortcode','')}",
                source="workable", description=strip_html(j.get("description", "")),
                job_id=str(j.get("shortcode", "")),
            ))
        token = d.get("nextPage")
        if not token or len(out) >= cfg.max_jobs_per_company:
            break
    return out


# ------------------------------------------------------------------- Generic
def _jsonld_location(it: dict) -> str:
    loc = it.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, dict):
        addr = loc.get("address", {})
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"),
                     addr.get("addressCountry")]
            return ", ".join([p for p in parts if p])
    return ""


def generic(company: str, url: str, cfg) -> List[Job]:
    if not url:
        return []
    try:
        r = _get(url, cfg.request_timeout)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    out = []
    for m in re.finditer(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', r.text, re.S | re.I
    ):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                out.append(Job(
                    company=company, title=it.get("title", ""),
                    location=_jsonld_location(it), url=it.get("url", "") or url,
                    source="jsonld", description=strip_html(it.get("description", "")),
                    job_id=str(it.get("identifier", "")),
                ))
    return out


ADAPTERS: Dict[str, Callable[..., List[Job]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workday": workday,
    "smartrecruiters": smartrecruiters,
    "personio": personio,
    "recruitee": recruitee,
    "workable": workable,
    "generic": generic,
}


# ATS references we can sniff out of a career page's raw HTML. Order matters:
# first pattern that yields jobs wins.
SNIFF_PATTERNS = [
    (r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9-]+)",
     "https://boards.greenhouse.io/{}"),
    (r"jobs\.lever\.co/([a-z0-9-]+)", "https://jobs.lever.co/{}"),
    (r"jobs\.ashbyhq\.com/([a-z0-9-]+)", "https://jobs.ashbyhq.com/{}"),
    (r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[A-Za-z0-9_-]+)", "https://{}"),
    (r"smartrecruiters\.com/([A-Za-z0-9]+)", "https://careers.smartrecruiters.com/{}"),
    (r"([a-z0-9-]+)\.jobs\.personio\.de", "https://{}.jobs.personio.de"),
    (r"([a-z0-9-]+)\.jobs\.personio\.com", "https://{}.jobs.personio.com"),
    (r"([a-z0-9-]+)\.recruitee\.com", "https://{}.recruitee.com"),
    (r"apply\.workable\.com/([a-z0-9-]+)", "https://apply.workable.com/{}"),
]


def sniff_ats(page_html: str) -> List[str]:
    """Extract candidate ATS board URLs referenced inside a career page."""
    out: List[str] = []
    for pat, tmpl in SNIFF_PATTERNS:
        for m in re.finditer(pat, page_html, re.I):
            u = tmpl.format(m.group(1))
            if u not in out:
                out.append(u)
    return out


def fetch_jobs(company: str, url: str, cfg) -> List[Job]:
    """Dispatch to the right adapter. Returns [] on any adapter error."""
    fn = ADAPTERS.get(detect(url), generic)
    try:
        return fn(company, url, cfg)
    except Exception:
        return []
