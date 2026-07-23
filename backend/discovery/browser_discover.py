"""Browser-based discovery fallback (the cascade's last tier).

Bespoke career pages defeat the plain-HTTP adapters three ways: they render
jobs client-side, they block scripted requests outright, or they hide most
postings behind filters / "Load more" buttons. This module drives a real
headless Chromium via Playwright (already a dependency of the autofiller)
against the company's own career URL and harvests jobs cheapest-first:

  1. re-sniff the RENDERED html + iframe URLs for embedded ATS boards and
     pull those through the normal adapters (catches boards injected by JS);
  2. watch the page's own XHR/fetch traffic for JSON job arrays — most
     custom career sites feed their list from an internal endpoint
     (personio.com: /api/careers/jobs/list/ with 108 postings the HTTP
     adapters never saw);
  3. last resort: hand the rendered page text + links to the local Claude
     CLI (the user's own login, same as tailoring) and ask it to extract
     the postings as strict JSON. Titles are checked back against the page
     text so the model cannot invent postings.

Descriptions are then fetched through the same browser session, but only for
jobs that pass the role-keyword prefilter — visiting every posting of a big
company would take minutes for jobs we'd never score.

READ-ONLY by design: the only interaction is clicking "load more"-style
buttons on public listing pages. Nothing here ever touches a form field or
anything submit-like — the autofiller's never-submit doctrine applies doubly.
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import urljoin
import json
import logging
import re

from .jobs import Job, strip_html

# Everything in this module degrades silently by design (the cascade must
# survive any single site breaking) — the log is the only way to see WHY a
# tier came back empty. `applybro discover -v` / server logs pick these up.
log = logging.getLogger("applybro.discovery.browser")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# Keys that make a JSON dict smell like a job posting. TITLE alone is not
# enough (nav menus are arrays of {title,url} too) — we also require one of
# the JOBISH keys, which menus don't have.
TITLE_KEYS = ("title", "name", "jobTitle", "position", "text", "job_title")
LOC_KEYS = ("location", "office", "locationsText", "city",
            "locations", "offices", "allOffices", "otherOffices")
URL_KEYS = ("url", "absolute_url", "absoluteUrl", "hostedUrl", "jobUrl",
            "applyUrl", "careers_url", "link", "href")
ID_KEYS = ("id", "slug", "shortcode", "job_id", "jobId", "uuid")
JOBISH_KEYS = LOC_KEYS + ("department", "employmentType", "employment_type",
                          "createdAt", "created_at", "workplace", "remote",
                          "team", "categories")

LOAD_MORE_RE = re.compile(
    r"\b(load|show|view|see)\s+(all|more)\b|\bmore\s+(jobs|positions|roles|openings)\b"
    r"|\bmehr\s+(laden|anzeigen)\b|\bweitere\s+(stellen|jobs)\b", re.I)

# Cookie banners overlay the page and make every real click time out
# ("element intercepts pointer events"). Dismiss with the most
# privacy-preserving choice available — decline, never accept-all.
CONSENT_RE = re.compile(
    r"\breject all\b|\bdecline\b|\bdeny( all)?\b|\brefuse\b"
    r"|\bonly (necessary|essential|required)\b|\b(necessary|essential) only\b"
    r"|\balle ablehnen\b|\bablehnen\b|\bnur notwendige\b", re.I)

MAX_LOAD_MORE_CLICKS = 20
MAX_DESC_FETCHES = 40
# Extracting ~100 postings from a big page took claude -p 217s in testing —
# a tight timeout silently discards a perfectly good answer.
AI_TIMEOUT = 420
AI_PAGE_TEXT_CHARS = 12000
# Generous: a 108-job page carries ~360 links, and every truncated-away link
# is a posting the model can no longer attach a URL to.
AI_MAX_LINKS = 500


def _first_str(d: dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and k in ID_KEYS:
            return str(v)
    return ""


def _loc_str(d: dict) -> str:
    for k in LOC_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            parts = [x for x in v if isinstance(x, str) and x.strip()]
            if parts:
                return ", ".join(parts[:4])
        if isinstance(v, dict):
            for kk in ("name", "city", "label"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk].strip()
    return ""


def _looks_like_job(d: dict) -> bool:
    title = _first_str(d, TITLE_KEYS)
    if not (3 <= len(title) <= 200):
        return False
    return any(k in d for k in JOBISH_KEYS)


def _find_job_arrays(node, found: List[list], depth: int = 0) -> None:
    """Recursively collect lists that look like arrays of job postings."""
    if depth > 6:
        return
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if (len(dicts) >= 2
                and sum(1 for d in dicts if _looks_like_job(d))
                >= max(2, int(0.7 * len(dicts)))):
            found.append(dicts)
            return
        for x in node:
            _find_job_arrays(x, found, depth + 1)
    elif isinstance(node, dict):
        for v in node.values():
            _find_job_arrays(v, found, depth + 1)


def _job_url(d: dict, hrefs: List[str], base: str) -> str:
    u = _first_str(d, URL_KEYS)
    if u:
        return u if u.startswith("http") else urljoin(base, u)
    # No URL in the payload: find the page link that embeds this job's
    # id/slug (personio.com links are /careers/<id>/ while the JSON only
    # carries id + slug).
    for k in ID_KEYS:
        v = d.get(k)
        v = str(v).strip() if v is not None else ""
        if len(v) >= 6:
            for href in hrefs:
                if v.lower() in href.lower():
                    return href
    return ""


def _jobs_from_payloads(company: str, payloads: List[object],
                        hrefs: List[str], base: str) -> List[Job]:
    arrays: List[list] = []
    for p in payloads:
        _find_job_arrays(p, arrays)
    out: List[Job] = []
    for arr in arrays:
        for d in arr:
            title = _first_str(d, TITLE_KEYS)
            if not title:
                continue
            out.append(Job(
                company=company, title=title, location=_loc_str(d),
                url=_job_url(d, hrefs, base), source="browser",
                description="", job_id=_first_str(d, ID_KEYS),
            ))
    _fill_urls_from_template(out)
    return out


def _fill_urls_from_template(jobs: List[Job]) -> None:
    """Learn the detail-URL pattern from jobs whose id matched a page link
    and apply it to the rest. Pagination means most postings never render a
    link (personio.com shows 5 of 108 until "Load more" is exhausted), but
    the JSON carries every id — one matched example gives the template
    (…/careers/{id}/) for all of them."""
    template = None
    for j in jobs:
        if j.url and len(j.job_id) >= 6 and j.job_id in j.url:
            template = j.url.replace(j.job_id, "{id}")
            break
    if not template:
        return
    for j in jobs:
        if not j.url and len(j.job_id) >= 6:
            j.url = template.replace("{id}", j.job_id)


# ------------------------------------------------------------------ AI tier
AI_PROMPT = """You are extracting job postings from the rendered text of a company careers page.

Company: {company}
Page URL: {url}

PAGE TEXT (truncated):
<<<
{text}
>>>

PAGE LINKS, one "text -> href" per line (truncated):
{links}

Output ONLY a JSON object — no commentary, no markdown fences — shaped exactly:
  {{"jobs": [{{"title": "...", "location": "...", "url": "..."}}]}}
with one element per job posting actually listed in the page text.
Rules:
- Only real, currently-listed postings that appear in the page text. Never
  invent or infer postings. Navigation items, team pages, blog posts and
  benefit blurbs are not jobs.
- "url": the absolute href of the link belonging to that posting, or "" if
  you cannot find one. "location": "" if not shown.
- If the page lists no jobs, output {{"jobs": []}}.
"""


def _ai_extract(company: str, url: str, page_text: str,
                links: List[str]) -> List[Job]:
    from .. import ai
    prompt = AI_PROMPT.format(
        company=company, url=url, text=page_text[:AI_PAGE_TEXT_CHARS],
        links="\n".join(links[:AI_MAX_LINKS]))
    ok, out = ai.complete(prompt, feature="extraction", timeout=AI_TIMEOUT)
    if not ok:
        log.warning("AI extraction for %s failed: %s", company, (out or "")[:200])
        return []
    data = ai.as_list(ai.load_json(out), "jobs")
    if data is None:
        log.warning("AI extraction for %s: no JSON array in output: %s",
                    company, (out or "")[:200])
        return []
    haystack = page_text.lower()
    out: List[Job] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        title = str(d.get("title") or "").strip()
        # Anti-hallucination: the title must actually occur in the page text.
        if not title or title[:30].lower() not in haystack:
            continue
        out.append(Job(
            company=company, title=title,
            location=str(d.get("location") or "").strip(),
            url=str(d.get("url") or "").strip(), source="browser-ai",
        ))
    return out


# ------------------------------------------------------------- page driving
def _settle(page, ms: int = 1200) -> None:
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass


def _scroll_down(page, times: int = 4) -> None:
    for _ in range(times):
        try:
            page.mouse.wheel(0, 4000)
        except Exception:
            return
        _settle(page, 400)


def _dismiss_consent(page) -> None:
    try:
        btn = page.locator("button, [role='button']").filter(
            has_text=CONSENT_RE).first
        if btn.count() and btn.is_visible():
            btn.click(timeout=2500)
            _settle(page, 600)
    except Exception:
        pass


def _anchor_count(page) -> int:
    try:
        return page.eval_on_selector_all("a[href]", "els => els.length") or 0
    except Exception:
        return 0


def _click_load_more(page) -> None:
    """Click visible 'load more'-style buttons a bounded number of times.
    Never touches submit buttons or form fields (see module docstring)."""
    stale_clicks = 0
    for _ in range(MAX_LOAD_MORE_CLICKS):
        try:
            btn = page.locator(
                "button:not([type=submit]), a, [role='button']"
            ).filter(has_text=LOAD_MORE_RE).first
            if btn.count() == 0 or not btn.is_visible():
                return
            before = _anchor_count(page)
            try:
                btn.click(timeout=3000)
            except Exception:
                # Actionability timed out (sticky headers/overlays intercept
                # the pointer even after consent is dismissed) — a JS click
                # still reaches the handler.
                btn.evaluate("e => e.click()")
        except Exception:
            return
        _settle(page)
        # A button that no longer grows the page (disabled-but-visible end
        # state) would otherwise burn the full click budget.
        if _anchor_count(page) <= before:
            stale_clicks += 1
            if stale_clicks >= 2:
                return
        else:
            stale_clicks = 0


def _collect_hrefs(page) -> List[str]:
    try:
        return page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)") or []
    except Exception:
        return []


def _collect_link_lines(page) -> List[str]:
    """'text -> href' lines for the AI prompt."""
    try:
        pairs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => [e.innerText.trim().slice(0, 80), e.href])")
    except Exception:
        return []
    out, seen = [], set()
    for text, href in pairs or []:
        line = f"{text} -> {href}"
        if text and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _jsonld_description(html: str) -> str:
    for m in re.finditer(
            r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
            html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                desc = strip_html(it.get("description", ""))
                if desc:
                    return desc
    return ""


def _fetch_description(page, url: str) -> str:
    try:
        from ..net import validate_public_url
        validate_public_url(url)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        _settle(page, 800)
        desc = _jsonld_description(page.content())
        if desc:
            return desc[:20000]
        return (page.inner_text("body") or "")[:20000]
    except Exception:
        return ""


def _matches_role(job: Job, keywords: List[str]) -> bool:
    # Local copy of discover.matches_role (importing it would be circular).
    if not keywords:
        return True
    title = (job.title or "").lower()
    return any(k in title for k in keywords)


def _dedup(jobs: List[Job]) -> List[Job]:
    out: List[Job] = []
    seen = set()
    for j in jobs:
        k = j.key()
        if k not in seen:
            seen.add(k)
            out.append(j)
    return out


# --------------------------------------------------------------- entrypoint
def discover_with_browser(company: str, career_url: str, cfg) -> List[Job]:
    """Render the career page in headless Chromium and harvest jobs.
    Returns [] on any environment problem (Playwright/browser missing) so
    the caller's cascade can degrade gracefully."""
    if not career_url:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    from .ats import fetch_jobs, sniff_ats

    with sync_playwright() as p:
        # Installed Chrome first (same as the autofiller — always present on
        # this machine), bundled Chromium as fallback: `playwright install`
        # may never have been run.
        browser = None
        for kw in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(headless=True, **kw)
                break
            except Exception:
                continue
        if browser is None:
            log.warning("no launchable browser (installed Chrome or bundled "
                        "Chromium) — browser fallback skipped for %s", company)
            return []
        try:
            ctx = browser.new_context(
                user_agent=BROWSER_UA, locale="en-US",
                viewport={"width": 1440, "height": 900})
            page = ctx.new_page()

            # Buffer response objects only — no blocking calls inside the
            # event handler (deadlocks the sync API). Bodies are read after
            # the interaction phase, before any further navigation evicts
            # them.
            responses: List[object] = []
            page.on("response", lambda r: responses.append(r))

            try:
                from ..net import validate_public_url
                validate_public_url(career_url)
                page.goto(career_url, wait_until="domcontentloaded",
                          timeout=45000)
            except Exception as e:
                log.warning("browser goto %s failed: %s", career_url, str(e)[:200])
                return []
            _settle(page, 2500)
            _dismiss_consent(page)
            _scroll_down(page)
            _click_load_more(page)

            hrefs = _collect_hrefs(page)

            # Tier 1: JSON the page itself fetched — the ground truth for
            # THIS company's list. Checked before the ATS sniff because a
            # rendered marketing page can reference other boards entirely
            # (personio.com links customers' *.jobs.personio.de boards —
            # following one would report another company's jobs).
            payloads: List[object] = []
            for r in responses:
                try:
                    if "json" not in (r.headers.get("content-type") or ""):
                        continue
                    payloads.append(r.json())
                except Exception:
                    continue
            jobs = _jobs_from_payloads(company, payloads, hrefs, career_url)
            log.info("%s: %d JSON payloads -> %d jobs", company,
                     len(payloads), len(jobs))

            # Tier 2: ATS board references visible only after rendering
            # (JS-injected embeds, iframes).
            if not jobs:
                rendered = page.content()
                frame_urls = "\n".join(f.url for f in page.frames)
                for cand in sniff_ats(rendered + "\n" + frame_urls)[:5]:
                    got = fetch_jobs(company, cand, cfg)
                    if got:
                        return got

            # Tier 3: AI extraction from the rendered page.
            if not jobs:
                try:
                    page_text = page.inner_text("body") or ""
                except Exception:
                    page_text = ""
                if page_text.strip():
                    jobs = _ai_extract(company, career_url, page_text,
                                       _collect_link_lines(page))
                    log.info("%s: AI extraction -> %d jobs", company, len(jobs))

            jobs = _dedup(jobs)
            # Prefilter BEFORE fetching descriptions: visiting every posting
            # of a big company for roles we'd never score takes minutes.
            jobs = [j for j in jobs if _matches_role(j, cfg.role_keywords)]
            jobs = jobs[: cfg.max_jobs_per_company]
            for j in jobs[:MAX_DESC_FETCHES]:
                if j.url and not j.description:
                    j.description = _fetch_description(page, j.url)
            return jobs
        finally:
            try:
                browser.close()
            except Exception:
                pass
