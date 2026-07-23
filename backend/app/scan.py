"""Extension scan sessions (SaaS Phase 4) — jobs enter ApplyBro through the
Chrome extension now.

The extension captures the user's CURRENT rendered view of a careers page —
their own logged-in browser, their own filters, no bot walls — and posts
{url, title, jsonld, anchors, visibleText, ...} here. The backend then does
what it's already good at:

  1. extract job candidates (JSON-LD postings → job-link anchors → AI
     extraction over the rendered text as last resort),
  2. read each job's full description (known-ATS board APIs first, then a
     plain fetch; failures are COUNTED, never hidden — a scan can finish
     "partial", never falsely complete: blueprint coverage rule),
  3. AI-score every candidate against the resume (the same two-stage
     triage/assess pipeline discovery uses),
  4. stage relevant jobs in the unified lifecycle as "recommended", with a
     "found via extension scan" timeline note.

Sessions are in-memory (single-user local backend); the extension polls
GET /api/extension/scan/{id} for live found/read/scored/relevant/failed
counts. The rendered-tab fallback for unreadable descriptions (extension
opens one job tab at a time) is Phase 5 territory — for now those jobs are
reported as failed reads.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from ..config import Config
from ..discovery.jobs import Job, strip_html
from . import queue as queue_store

_LOCK = threading.Lock()
SCANS: Dict[str, dict] = {}
# Per-scan job objects (kept out of the polled status dict). Needed because a
# scan can now PAUSE for the extension: sites like Delivery Hero bot-block
# server-side fetches (a real scan failed 76/76 description reads), so
# unreadable jobs go back to the extension, which opens each in a background
# tab — the user's own browser — and posts the rendered text here.
_SCAN_JOBS: Dict[str, Dict[str, Job]] = {}
# How many unreadable job pages the extension is handed AT ONCE. This is a
# BATCH SIZE, not a budget: when the batch comes back the scan hands out the
# next one, and only scores once the whole list has been walked. It used to be
# MAX_PENDING_PAGES=40, a hard truncation — everything past the 40th job was
# dropped from reading and then counted as "couldn't be read", which is a lie
# about a job we never opened (cap audit 2026-07-22).
PENDING_BATCH = 25
# The one real ceiling on the rendered-tab fallback: every page here is a tab
# navigation in the user's own browser (~4s each), so 500 is already ~30
# minutes. Anything beyond it is reported as NOT ATTEMPTED, never as failed.
MAX_RENDERED_READS = 500

# Bounds the legacy ANCHOR fallback only (used when the structural card read
# found under 3 jobs). 150 was below the size of a real board, so the one path
# that runs when card reading fails had a truncation of its own waiting.
MAX_ANCHOR_JOBS = 1000
# An extension scan is the user standing on ONE page they chose, so
# max_jobs_per_company (a discovery-era budget) doesn't apply — it silently
# truncated Freshworks' 133 jobs to 100 (user report 2026-07-22). This bound
# exists only to keep one runaway page from exhausting memory.
MAX_SCAN_JOBS = 2000
# Anchor text that is navigation/boilerplate, never a job title.
_NOT_TITLE = re.compile(
    r"^(apply( now)?|learn more|find out more|read more|view( job| all)?|"
    r"see (all|more)|details|share|save|login|sign in|next|previous|back)$",
    re.I)
_JOB_HREF = re.compile(
    r"greenhouse\.io/.+/jobs/\d|lever\.co/[^/]+/[0-9a-f-]{8}|"
    r"ashbyhq\.com/[^/]+/[0-9a-f-]{8}|myworkdayjobs\.com/.+/job/|"
    r"smartrecruiters\.com/[^/]+/\d|jobs\.personio\.(de|com)/job/|"
    r"recruitee\.com/o/|workable\.com/.+/j/|"
    r"/(jobs?|careers?|positions?|vacanc\w+|openings?)/[^?#]*[\w-]{6,}", re.I)


def _coverage_note(found: int, page_total, hash_filtered: bool = False,
                   missed_rows: int = 0, challenged: str = "",
                   stalled_at: int = 0, pages: int = 1) -> str:
    """Compare what we extracted against the count the page advertises. Any
    real gap is reported to the user rather than swallowed — "0 relevant" is
    only trustworthy if we actually saw the jobs they're looking at (user
    report 2026-07-21: 100 candidates on a 15-job filtered list).

    The page's own total is the authority. If it matches what we collected the
    scan IS complete and says nothing — including when filters live in the
    URL fragment and only one page was captured, because "Show all" puts the
    whole filtered list on that one page (user report 2026-07-21: warning
    shown on a scan that had in fact seen all 15 of 15)."""
    try:
        total = int(page_total)
    except (TypeError, ValueError):
        total = 0
    if challenged:
        return ("The site showed a \"verify you are human\" check partway "
                "through, so the scan stopped there and kept what it had. "
                "Clear the check in the tab, then run the scan again.")
    # The site's pager stopped advancing. This one we MEASURED — the walk
    # compared each page against the previous page's stated range AND against
    # the job URLs already collected, and neither moved. Rule 4 forbids
    # claiming a cause we can't know; this is one we can, so name it plainly
    # instead of hedging with "may be a limit in ApplyBro".
    if stalled_at:
        # State only what was MEASURED. The walk saw later pages serve page
        # 1's jobs again; it does NOT know why, and this site paginates fine
        # by hand — so "the site is broken" would be the same unknowable
        # cause-claim as the ones rule 4 exists to stop, just pointed the
        # other way. Naming the likeliest trigger as a maybe is allowed;
        # asserting it is not.
        return (f"After page {stalled_at} this site kept serving the same "
                f"jobs as page 1 instead of the next page, so the scan "
                f"stopped there with {found} of {total or 'the advertised'} "
                f"job(s). Paging by hand usually still works — sites often do "
                f"this when a scan moves through them quickly. Narrow the "
                f"board with this page's own filters, or scan again in a few "
                f"minutes.")
    # A MEASURED shortfall beats an inferred one: the extension compared each
    # page against that page's own stated range and counted what it missed.
    if missed_rows > 0:
        return (f"{missed_rows} job(s) that this site said were on the pages "
                f"we walked never made it into the scan — ApplyBro couldn't "
                f"read them off the page in time. This scan is incomplete.")
    if total > 0:
        if found > total * 1.3:
            return (f"This page says it has {total} job(s) but {found} were "
                    f"collected — filters that live in the URL's # fragment "
                    f"are lost when paging, so this scan probably covered the "
                    f"UNFILTERED list. Trust it accordingly.")
        if found < total * 0.7:
            # Say WHAT is missing, never WHY: every time this blamed the site
            # it was wrong — the cause was an ApplyBro limit (page cap, link
            # budget, URL grouping, card cap), four times running.
            return (f"This page says it has {total} job(s) but only {found} "
                    f"were collected, so this scan did NOT cover all of them. "
                    f"That may be a limit in ApplyBro rather than the site."
                    + (" Try the page's own \"Show all\" first."
                       if hash_filtered else ""))
        return ""       # collected what the page advertises: complete
    if hash_filtered:
        return ("This site keeps your filters in the URL's # fragment, which "
                "is lost on navigation, so only the page you were looking at "
                "was scanned. Use the page's own \"Show all\" to put the "
                "whole filtered list on one page before scanning.")
    return ""


_JUNK = re.compile(
    r"cookie|consent|gtag|googletag|dataLayer|window\.|function\s*\(|"
    r"privacy policy|\{|\}|;", re.I)


def usable_description(text: str) -> bool:
    """Is this actually a job description, or the page's tracking/consent
    scripts? A SmartRecruiters scan scored a job 32% off a body that was
    "entirely tracking/consent-banner scripts" — the AI said so in its own
    reasoning and we scored it anyway (user report 2026-07-22). Scoring a job
    on its title alone and presenting it as a fit % is exactly the fake
    signal this project forbids: count it unread instead."""
    t = (text or "").strip()
    if len(t) < 400:
        return False
    words = t.split()
    if len(words) < 80:
        return False
    # Junk markers per 100 words — prose has few braces/semicolons/`window.`
    hits = len(_JUNK.findall(t))
    return (hits * 100.0 / max(len(words), 1)) < 8


def _clip(text: str, limit: int = 600) -> str:
    """Trim a reason for transport without slicing a word in half (the panel
    showed "...Jest, RTL, C" — user report 2026-07-21)."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit * 0.6 else cut).rstrip(" ,;:.") + "…"


def _update(sid: str, **kw) -> None:
    with _LOCK:
        if sid in SCANS:
            SCANS[sid].update(kw)


def get_scan(sid: str) -> Optional[dict]:
    with _LOCK:
        s = SCANS.get(sid)
        return dict(s) if s else None


def request_stop(sid: str) -> bool:
    """User pressed Stop in the extension: flag the scan and kill any
    in-flight AI call so it halts within seconds, not minutes."""
    with _LOCK:
        s = SCANS.get(sid)
        if s is None:
            return False
        s["cancel"] = True
    from .. import ai
    ai.abort_current()
    return True


def _stopped(sid: str) -> bool:
    with _LOCK:
        s = SCANS.get(sid)
        return bool(s and s.get("cancel"))


# ------------------------------------------------------------- extraction
def _iter_jsonld_postings(blobs: List[str]):
    for raw in blobs or []:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "JobPosting":
                    yield node
                else:
                    stack.extend(node.values())


def _ld_location(node: dict) -> str:
    """A JobPosting's location, incl. schema.org's remote marker. Shares the
    address reader with the ATS adapters rather than re-deriving it."""
    from ..discovery.ats import _jsonld_location
    place = _jsonld_location(node)
    if place:
        return place
    if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        return "Remote"
    return ""


def _org_name(node: dict) -> str:
    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        return str(org.get("name") or "").strip()
    if isinstance(org, str):
        return org.strip()
    return ""


def _host_company(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    parts = [p for p in host.split(".")
             if p not in ("www", "jobs", "careers", "boards", "job-boards", "apply")]
    return parts[0].title() if parts else ""


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def jobs_from_board_api(page_url: str, cfg: Config) -> List[Job]:
    """Ask the ATS for its jobs instead of reading them off the screen.

    Greenhouse, Lever, Ashby, SmartRecruiters and friends are job DATABASES
    with a public read endpoint; scraping their rendered page is guesswork by
    comparison. Measured on careers.smartrecruiters.com/Freshworks
    (2026-07-22): scraping produced mangled titles ("Principal Engineer
    Full-time") and 11 of 12 descriptions unreadable, while the API returns
    133 postings with clean titles and 4,371-char descriptions.

    Best-effort BY DESIGN: any failure returns [] and the caller falls back
    to the rendered-page reader, so an API outage or a site we don't know
    degrades instead of breaking (user requirement 2026-07-22)."""
    try:
        from ..discovery import ats
        kind = ats.detect(page_url)
        fn = ats.ADAPTERS.get(kind)
        if fn is None or kind == "generic":
            return []
        # The adapters page until cfg.max_jobs_per_company; an extension scan
        # wants the whole board, so hand them a scan-sized budget.
        class _ScanCfg:
            request_timeout = getattr(cfg, "request_timeout", 20)
            max_jobs_per_company = MAX_SCAN_JOBS
            # Adapters that can filter server-side (Workable) use this; the
            # rest ignore it and the backend filters after collecting.
            locations = list(getattr(cfg, "locations", None) or [])
        jobs = fn(_host_company(page_url), page_url, _ScanCfg()) or []
    except Exception:
        return []                      # fall back to reading the page
    for j in jobs:
        j.source = "extension"
    return jobs[:MAX_SCAN_JOBS]


def candidates_from_page(page: dict, cfg: Config) -> List[Job]:
    """Job candidates from one captured careers-page view, cheapest first.
    Dedup within the scan by normalized URL (MVP-405/406)."""
    page_url = page.get("url", "")
    company = _host_company(page_url)
    out: List[Job] = []
    seen = set()

    def add(job: Job) -> None:
        k = _norm_url(job.url).lower()
        if k and k not in seen:
            seen.add(k)
            out.append(job)

    # 0. The ATS's own API, when this is a board we know. Cleanest data by a
    #    wide margin; everything below is the fallback for when it isn't.
    for j in jobs_from_board_api(page_url, cfg):
        add(j)
    if len(out) >= 3:
        return out[:MAX_SCAN_JOBS]

    # 1. JSON-LD postings rendered into the page
    for node in _iter_jsonld_postings(page.get("jsonld", [])):
        title = str(node.get("title") or "").strip()
        if not title:
            continue
        add(Job(company=_org_name(node) or company, title=title,
                location=_ld_location(node),   # so the location filter can see it
                url=str(node.get("url") or "") or page_url,
                source="extension",
                description=strip_html(str(node.get("description") or ""))))

    # 2. job CARDS the extension read structurally from the rendered DOM.
    #    This is the trustworthy path: the extension can see that a link sits
    #    in a repeated job card and where that card's title element is, which
    #    no amount of URL pattern-matching here can recover. On HubSpot the
    #    anchor heuristic below produced 100 "jobs" that were marketing pages
    #    while missing all 15 real ones (user report 2026-07-21).
    # MAX_ANCHOR_JOBS bounds the ANCHOR fallback, not this. Applying it here
    # truncated 480 captured cards to 150 -> 123 after dedup, on a 462-job
    # board (user report 2026-07-22). Third cap found in this chain; the
    # coverage note below no longer claims to know WHY a gap exists.
    for c in (page.get("cards") or [])[:MAX_SCAN_JOBS]:
        href = urljoin(page_url, str(c.get("url") or ""))
        title = " ".join(str(c.get("title") or "").split())
        if not href.startswith("http") or not (4 <= len(title) <= 140):
            continue
        if _norm_url(href).lower() == _norm_url(page_url).lower():
            continue
        add(Job(company=company, title=title,
                # "" when the card didn't SAY where the job is. The location
                # filter treats that as unknown and keeps the job.
                location=" ".join(str(c.get("location") or "").split())[:80],
                url=href, source="extension", description=""))

    # 3. job-link anchors — the legacy fallback, used only when the structural
    #    pass found nothing (older extension build, or a page whose listing
    #    isn't built from links at all).
    for a in ([] if len(out) >= 3 else (page.get("anchors") or [])[:60000]):
        href = urljoin(page_url, str(a.get("href") or ""))
        text = " ".join(str(a.get("text") or "").split())
        if not href or not _JOB_HREF.search(href):
            continue
        if _norm_url(href).lower() == _norm_url(page_url).lower():
            continue
        if not (4 <= len(text) <= 140) or _NOT_TITLE.match(text):
            continue
        add(Job(company=company, title=text, location="", url=href,
                source="extension", description=""))
        if len(out) >= MAX_ANCHOR_JOBS:
            break

    # 4. AI extraction over the rendered text — last resort, same routine the
    #    server-side browser fallback uses.
    if len(out) < 3 and (page.get("visibleText") or "").strip():
        from ..discovery.browser_discover import _ai_extract
        links = [f"{(a.get('text') or '').strip()[:80]} -> {urljoin(page_url, a.get('href') or '')}"
                 for a in (page.get("anchors") or [])[:500] if a.get("text")]
        for j in _ai_extract(company, page_url,
                             page.get("visibleText", ""), links):
            j.source = "extension"
            add(j)

    return out[:MAX_SCAN_JOBS]


# --------------------------------------------------------------- filtering
# "Remote" is written a dozen ways and none of them name a city.
_REMOTE = re.compile(r"\bremote|work from home|wfh|anywhere|distributed|"
                     r"virtual|home[- ]based\b", re.I)


def _norm_place(s: str) -> str:
    """Casefold and flatten punctuation so 'Berlin, Germany' and
    'berlin (germany)' compare the same."""
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def location_matches(job_location: str, wanted: List[str]) -> Optional[bool]:
    """Does this job's location match any of the user's? Returns None when we
    don't KNOW the job's location — the caller must keep those.

    Deliberately generous in both directions: "Berlin" keeps "Berlin,
    Germany" and "Germany" keeps it too. A location filter that is too tight
    is indistinguishable from the undercount bugs this scan keeps producing,
    except that the user asked for it and so won't suspect it."""
    if not wanted:
        return True                       # no preference = everything matches
    loc = _norm_place(job_location)
    if not loc:
        return None                       # unknown — never a reason to drop
    for w in wanted:
        want = _norm_place(w)
        if not want:
            continue
        if _REMOTE.search(want) and _REMOTE.search(job_location or ""):
            return True
        # Substring either way: the user's term may be broader (a country) or
        # narrower (a city) than what the board prints.
        if want in loc or loc in want:
            return True
        # Multi-city strings ("Berlin, Munich, Remote") are one field on many
        # boards — match on any comma-separated part.
        if any(want in p or p in want
               for p in (x.strip() for x in loc.split(",")) if p):
            return True
    return False


def filter_by_location(jobs: List[Job], wanted: List[str]):
    """(kept, dropped, unknown_kept). Jobs whose location we couldn't read are
    KEPT and counted, so the funnel can say how much of the filter was
    guesswork rather than pretending the filter was exact."""
    if not wanted:
        return list(jobs), 0, 0
    kept, dropped, unknown = [], 0, 0
    for j in jobs:
        m = location_matches(j.location or "", wanted)
        if m is None:
            unknown += 1
            kept.append(j)
        elif m:
            kept.append(j)
        else:
            dropped += 1
    return kept, dropped, unknown


_SR_JOB = re.compile(r"smartrecruiters\.com/([^/?#]+)/(\d{6,})", re.I)


def _smartrecruiters_description(job: Job, cfg: Config) -> bool:
    """The posting's own jobAd sections. The generic ATS-board reader doesn't
    cover SmartRecruiters DETAIL urls, so scraping the rendered page was the
    only path — and that page serves consent-banner scripts to a fetch
    (user report 2026-07-22: 11 of 12 descriptions unreadable)."""
    m = _SR_JOB.search(job.url or "")
    if not m:
        return False
    slug, jid = m.group(1), m.group(2)
    try:
        from ..net import safe_get
        r = safe_get(f"https://api.smartrecruiters.com/v1/companies/{slug}"
                     f"/postings/{jid}", timeout=cfg.request_timeout)
        if r is None or r.status_code >= 400:
            return False
        d = r.json()
    except Exception:
        return False
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    parts = [strip_html(str((secs.get(k) or {}).get("text") or ""))
             for k in ("companyDescription", "jobDescription",
                       "qualifications", "additionalInformation")]
    text = " ".join(" ".join(parts).split())
    if len(text) < 200:
        return False
    job.description = text[:20000]
    job.title = d.get("name") or job.title
    return True


def _read_description(job: Job, cfg: Config) -> bool:
    """Fill job.description; True on success. Board APIs first (exact,
    instant for known ATS urls), then a plain fetch."""
    if len((job.description or "").strip()) > 200:
        return True
    if _smartrecruiters_description(job, cfg):
        return True
    from ..discovery.single_job import _job_from_ats_board, _job_from_ld
    try:
        hit = _job_from_ats_board(job.company, job.url, cfg)
    except Exception:
        hit = None
    if hit and (hit.description or "").strip():
        job.description = hit.description
        # The board API's title/location are canonical — anchor text often
        # smushes "Title Location (Hybrid)" into one string.
        job.title = hit.title or job.title
        job.location = hit.location or job.location
        return True
    try:
        from ..discovery.ats import UA
        from ..net import safe_get
        r = safe_get(job.url, headers=UA, timeout=cfg.request_timeout)
        if r is not None and r.status_code < 400:
            ld = _job_from_ld(job.company, job.url, r.text)
            if ld and (ld.description or "").strip():
                job.description = ld.description
                job.location = job.location or ld.location
                return True
            body = strip_html(r.text)
            if len(body) > 400:
                job.description = body[:20000]
                return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------ scan session
def start_scan(page: dict, cfg: Config, threshold: int) -> str:
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        SCANS[sid] = {"id": sid, "state": "extracting", "page_url": page.get("url", ""),
                      "found": 0, "read": 0, "failed": 0, "assessed": 0,
                      "to_assess": 0, "relevant": 0, "duplicates": 0,
                      "ai_errors": 0, "below_threshold": 0, "top_score": None,
                      "threshold": threshold, "cancel": False, "error": "",
                      # Live per-job verdicts, so the extension can show WHAT
                      # the AI judged and why while it's still judging.
                      "results": [], "skipped_by_title": 0,
                      "kept_after_triage": 0, "unusable": 0,
                      # Rendered-tab fallback progress, across ALL batches.
                      "pending": [], "pending_all": [], "pending_total": 0,
                      "pending_done": 0, "unattempted": 0,
                      # Location filter, reported as a measured number.
                      "location_skipped": 0, "location_unknown": 0,
                      "locations": [], "pages_scanned": 1,
                      # Triage is 1 AI call per 60 titles — on a 466-job board
                      # that's 8 sequential calls, so it reports per chunk.
                      "triage_done": 0, "triage_total": 0,
                      # What the PAGE said its own total was, and whether our
                      # coverage matches it. A scan that quietly reads a
                      # different set than the user is looking at must say so.
                      "page_total": page.get("pageTotal"), "coverage": ""}
    t = threading.Thread(target=_run_scan, args=(sid, page, cfg, threshold),
                         daemon=True)
    t.start()
    return sid


def _should_stop(sid: str) -> bool:
    # User's Stop button, or the AI provider ran out of budget —
    # keep whatever was finished, never grind through failures.
    from .. import ai
    return _stopped(sid) or ai.exhausted()


def _run_scan(sid: str, page: dict, cfg: Config, threshold: int) -> None:
    """Phase 1: extract candidates + try SERVER-side description reads.
    Unreadable jobs (bot-blocked sites — Delivery Hero failed 76/76 in real
    use) go back to the extension via state='need_pages'; it opens each in a
    background tab and posts the rendered text to supply_page(), then calls
    continue_scan() for phase 2 (scoring + staging)."""
    try:
        from ..scoring.ai_score import background_text, triage_titles

        jobs = candidates_from_page(page, cfg)
        existing = {_norm_url(it["url"]).lower() for it in queue_store.load()}
        fresh = [j for j in jobs if _norm_url(j.url).lower() not in existing]
        duplicates = len(jobs) - len(fresh)
        # Location filter runs HERE — after collecting the whole board, before
        # triage. Filtering first is what makes a 463-job board affordable; the
        # coverage check below still compares the FULL collection against the
        # page's own total, so filtering can never disguise an undercount.
        fresh, loc_dropped, loc_unknown = filter_by_location(fresh, cfg.locations)
        _update(sid, location_skipped=loc_dropped,
                location_unknown=loc_unknown, locations=list(cfg.locations),
                found=len(jobs), duplicates=duplicates,
                pages_scanned=int(page.get("pagesScanned") or 1),
                coverage=_coverage_note(len(jobs), page.get("pageTotal"),
                                        bool(page.get("hashFiltered")),
                                        int(page.get("missedRows") or 0),
                                        str(page.get("challenged") or ""),
                                        int(page.get("stalledAtPage") or 0),
                                        int(page.get("pagesScanned") or 1)),
                state="triaging")

        # Title triage runs BEFORE any description is fetched (user request
        # 2026-07-21). It used to run after reading all 75 pages — the extension
        # opened a background tab for every one of them, then the AI threw most
        # away on the title alone. Reading only what survives triage cuts the
        # slowest part of a scan by whatever share of the board is irrelevant.
        if fresh and not _should_stop(sid):
            company = fresh[0].company or _host_company(page.get("url", ""))

            def on_triage(done, total, kept_so_far):
                _update(sid, triage_done=done, triage_total=total,
                        kept_after_triage=kept_so_far)

            # Title AND location: the triage prompt asks for "title — location"
            # and was only ever given titles, so it judged remote-vs-onsite
            # blind. Departments go in as a WIDENING hint, never a filter.
            keep = triage_titles(
                company,
                [" — ".join(p for p in ((j.title or "").strip(),
                                        (j.location or "").strip()) if p)
                 for j in fresh],
                background_text(), cfg.role_keywords,
                departments=cfg.departments,
                on_progress=on_triage,
                should_skip=lambda: _should_stop(sid))
            kept = [fresh[i] for i in sorted(set(keep))] or []
        else:
            kept = fresh
        note = ""
        if loc_dropped and not fresh:
            note = (f"All {loc_dropped} job(s) collected were outside "
                    f"{', '.join(cfg.locations)}, so none were read. Widen "
                    f"the locations in Settings → Targets if that's too "
                    f"tight.")
        elif fresh and not kept:
            note = (f"None of the {len(fresh)} title(s) collected looked like "
                    f"roles for you, so nothing was read in full. If that "
                    f"seems wrong, this page's jobs probably weren't read "
                    f"correctly — check the titles it found.")
        # kept_after_triage is the TRIAGE result and is never overwritten;
        # to_assess later becomes the count that survived READING, which is
        # smaller. Reusing one field for both made the funnel contradict
        # itself: "88 skipped" beside "1 worth reading" out of 100.
        _update(sid, skipped_by_title=len(fresh) - len(kept),
                kept_after_triage=len(kept),
                to_assess=len(kept), state="reading",
                coverage=note or (get_scan(sid) or {}).get("coverage", ""))
        fresh = kept

        with _LOCK:
            _SCAN_JOBS[sid] = {_norm_url(j.url).lower(): j for j in fresh}

        read = 0
        pending: List[str] = []
        for j in fresh:
            if _should_stop(sid):
                break
            if _read_description(j, cfg):
                read += 1
            else:
                pending.append(j.url)
            _update(sid, read=read)

        if pending and not _should_stop(sid) and _begin_pending(sid, pending):
            # The user's browser can read what we can't — hand the URLs back,
            # a batch at a time until the list is exhausted.
            return
        _score_and_stage(sid, cfg, threshold)
    except Exception as e:
        _update(sid, state="error", error=str(e)[:300])


def _begin_pending(sid: str, pending: List[str]) -> bool:
    """Start the rendered-tab fallback over the whole pending list. Returns
    False if there is nothing to hand back (scoring should run now)."""
    unattempted = max(0, len(pending) - MAX_RENDERED_READS)
    with _LOCK:
        s = SCANS.get(sid)
        if s is None:
            return False
        s["pending_all"] = list(pending[:MAX_RENDERED_READS])
        s["pending_total"] = len(s["pending_all"])
        s["pending_done"] = 0
        # Jobs past the ceiling were never opened. _score_and_stage counts
        # unreadable jobs as "failed"; saying so here keeps "failed" meaning
        # "we tried and couldn't", which is the only way the number is useful.
        s["unattempted"] = unattempted
    return _next_pending_batch(sid)


def _next_pending_batch(sid: str) -> bool:
    """Hand the extension the next batch of unreadable job pages. True while
    batches remain; False once the list is walked."""
    with _LOCK:
        s = SCANS.get(sid)
        if s is None:
            return False
        rest = list(s.get("pending_all") or [])
        if not rest:
            s["pending"] = []
            return False
        s["pending"] = rest[:PENDING_BATCH]
        s["pending_all"] = rest[PENDING_BATCH:]
        s["state"] = "need_pages"
        return True


def supply_page(sid: str, url: str, jsonld: List[str], text: str) -> dict:
    """The extension read one pending job page in a background tab; take the
    best description we can get from it (JSON-LD first, rendered text else)."""
    with _LOCK:
        jobs = _SCAN_JOBS.get(sid)
        s = SCANS.get(sid)
    if jobs is None or s is None:
        raise KeyError("unknown scan id")
    j = jobs.get(_norm_url(url).lower())
    if j is not None:
        for node in _iter_jsonld_postings(jsonld or []):
            desc = strip_html(str(node.get("description") or ""))
            if len(desc) > 200:
                j.description = desc[:20000]
                break
        if len((j.description or "").strip()) <= 200 and len((text or "").strip()) > 400:
            j.description = text.strip()[:20000]
    with _LOCK:
        s = SCANS.get(sid)
        if s is None:
            raise KeyError("unknown scan id")
        pend = [u for u in s.get("pending", []) if _norm_url(u).lower()
                != _norm_url(url).lower()]
        got = j is not None and len((j.description or "").strip()) > 200
        s.update(pending=pend, read=s.get("read", 0) + (1 if got else 0),
                 # Whole-scan progress, not per-batch: the panel's bar would
                 # otherwise restart at 0 every 25 jobs.
                 pending_done=s.get("pending_done", 0) + 1)
        left = len(pend) + len(s.get("pending_all") or [])
    return {"remaining": len(pend), "remaining_total": left}


def continue_scan(sid: str, cfg: Config, threshold: int) -> None:
    """Phase 2 entry, called by the extension once pending pages are
    delivered (or it gave up / was stopped)."""
    # Flip the state atomically BEFORE starting the thread: the extension
    # retries this call if a backend hiccup swallowed the first one, and two
    # racing continues must never spawn two scoring threads (double AI spend).
    with _LOCK:
        s = SCANS.get(sid)
        if s is None:
            raise KeyError("unknown scan id")
        if s["state"] not in ("need_pages", "reading"):
            return   # already scoring/terminal — idempotent
        # More unreadable pages waiting? Hand back the next batch rather than
        # scoring: the rest of the list is not "read", it is UNOPENED. Each
        # batch shrinks pending_all, so this always terminates.
        more = bool(s.get("pending_all")) and not s.get("cancel")
        if not more:
            s["state"] = "scoring"
    if more:
        _next_pending_batch(sid)
        return
    t = threading.Thread(target=_score_and_stage, args=(sid, cfg, threshold),
                         daemon=True)
    t.start()


def _score_and_stage(sid: str, cfg: Config, threshold: int) -> None:
    try:
        from .. import ai
        with _LOCK:
            jobs = list(_SCAN_JOBS.get(sid, {}).values())
            page_url = SCANS.get(sid, {}).get("page_url", "")
        readable = [j for j in jobs if usable_description(j.description)]
        # "failed" must mean we OPENED it and still got nothing. Jobs past
        # MAX_RENDERED_READS were never opened, so they are reported as their
        # own number instead of being folded into failures.
        with _LOCK:
            unattempted = int((SCANS.get(sid) or {}).get("unattempted") or 0)
        failed = max(0, len(jobs) - len(readable) - unattempted)
        _update(sid, state="scoring", read=len(readable), failed=failed,
                unattempted=unattempted, pending=[], pending_all=[])

        from ..scoring.ai_score import ScorableJob, background_text, score_company_jobs
        company = readable[0].company if readable else _host_company(page_url)
        sjobs = [ScorableJob(j.url, (j.title or "").strip(), j.location,
                             j.description or "") for j in readable]
        by_url = {j.url: j for j in readable}

        def on_one(done, total, item=None):
            # One verdict landed: publish it immediately (title + score +
            # reason) so the panel shows the AI's reasoning as it works,
            # not a silent bar.
            entry = None
            if item is not None:
                url, fit = item
                src = by_url.get(url)
                entry = {"url": url,
                         "title": (src.title if src else "") or "This job",
                         "score": fit.score if fit else None,
                         "reason": _clip(fit.reason if fit else "")}
            with _LOCK:
                s = SCANS.get(sid)
                if s is None:
                    return
                s["assessed"] = done
                s["to_assess"] = total
                if entry:
                    s["results"] = (s.get("results") or []) + [entry]

        scored = {}
        if not _should_stop(sid):
            scored = score_company_jobs(
                company, sjobs, background_text(), cfg.role_keywords,
                should_skip=lambda: _should_stop(sid),
                on_progress=on_one, pretriaged=True)

        from .runner import _queue_entry
        entries = []
        ai_errors = below = 0
        top_score = None
        for j in readable:
            af = scored.get(j.url)
            if af is None or not af.worth_exploring:
                continue                      # triaged out — not attempted
            if af.score is None:
                ai_errors += 1                # assess call failed
                continue
            top_score = af.score if top_score is None else max(top_score, af.score)
            if af.score < threshold:
                below += 1                    # honestly scored, just not enough
                continue
            entry = _queue_entry(j.company, j, af, cfg, has_history=False)
            entry["status"] = "recommended"
            entry["timeline"] = [{
                "at": queue_store._now_iso(), "event": "recommended",
                "note": f"extension scan of {urlparse(page_url).netloc}"}]
            entries.append(entry)
        _update(sid, ai_errors=ai_errors, below_threshold=below,
                top_score=top_score, threshold=threshold)
        # A failed WRITE must not throw away a completed scan: the scoring
        # already happened (and cost real AI spend), and the per-job verdicts
        # are in the status for the user to read. Report the save failure
        # honestly instead of reporting the whole scan as failed — that's what
        # a Supabase blip did on 2026-07-21.
        save_error = ""
        if entries:
            try:
                queue_store.upsert(entries, touch_meta=False)
            except Exception as e:
                save_error = str(e)[:200]

        # Coverage honesty (blueprint rule): a stop keeps what finished and
        # says WHY; failures make the scan "partial", never silently
        # successful.
        if save_error:
            _update(sid, relevant=0, state="partial",
                    error=f"Scored everything, but the {len(entries)} match(es) "
                          f"couldn't be saved to your account: {save_error} "
                          "The scores below are still here — run the scan "
                          "again to save them.")
        elif ai.exhausted():
            _update(sid, relevant=len(entries), state="stopped",
                    error="AI limit reached — what finished so far was kept. "
                          "Try again when your provider's limit resets.")
        elif _stopped(sid):
            _update(sid, relevant=len(entries), state="stopped",
                    error="stopped by you — jobs scored so far were kept")
        elif unattempted:
            _update(sid, relevant=len(entries), state="partial",
                    error=f"{unattempted} job(s) were never opened — one scan "
                          f"reads at most {MAX_RENDERED_READS} descriptions in "
                          f"your browser. Narrow the board with a location or "
                          f"department filter and scan again.")
        else:
            _update(sid, relevant=len(entries),
                    state="partial" if failed else "complete")
    except Exception as e:
        _update(sid, state="error", error=str(e)[:300])
    finally:
        with _LOCK:
            _SCAN_JOBS.pop(sid, None)


# ------------------------------------------------------------- single job
def save_single_job(page: dict, cfg: Config) -> dict:
    """Path A: the user is ON one job posting and hits Save. Build the job
    from the RENDERED page (their browser already sees it — no bot walls),
    AI-assess it, stage it as 'saved' (they explicitly chose it)."""
    page_url = page.get("url", "")
    job: Optional[Job] = None
    for node in _iter_jsonld_postings(page.get("jsonld", [])):
        title = str(node.get("title") or "").strip()
        if title:
            job = Job(company=_org_name(node) or _host_company(page_url),
                      title=title, location="", url=page_url,
                      source="extension",
                      description=strip_html(str(node.get("description") or "")))
            break
    if job is None or len((job.description or "").strip()) < 200:
        text = (page.get("visibleText") or "").strip()
        if not text:
            raise ValueError("Couldn't read this page — try reloading it.")
        from ..discovery.single_job import _ai_fields
        fields = _ai_fields(page_url, text)
        title = str(fields.get("title") or "").strip() or (job.title if job else "")
        if not title:
            raise ValueError("This page doesn't look like a single job posting.")
        job = Job(company=str(fields.get("company") or "").strip()
                  or _host_company(page_url),
                  title=title,
                  location=str(fields.get("location") or "").strip(),
                  url=page_url, source="extension",
                  description=text[:20000])

    from ..scoring.ai_score import AiFit, assess_job, background_text
    af = assess_job(job.title, job.location, job.description or "",
                    background_text()) or AiFit(worth_exploring=True)
    from .runner import _queue_entry
    entry = _queue_entry(job.company, job, af, cfg, has_history=False)
    entry["status"] = "saved"
    entry["timeline"] = [{"at": queue_store._now_iso(), "event": "saved",
                          "note": "saved from the extension"}]
    queue_store.upsert([entry], touch_meta=False)
    return entry
