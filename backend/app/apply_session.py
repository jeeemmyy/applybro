"""Extension application sessions (SaaS Phase 5).

One application at a time (MVP-604/605): starting an application takes a
per-user lock (Supabase kv when the store is on, module-local otherwise);
a second Start is refused until the first is finished or cancelled.

The DIVISION OF LABOR is the safety design: the extension's content script
only READS the form and TYPES values — every decision about what a field
means and what goes in it happens here, in the same battle-tested code the
Playwright autofiller uses (forms.FIELD_MAP/_answer_for, the workspace's
answers.yaml, qa.answer_questions for open questions). A field the backend
can't resolve stays EMPTY and is highlighted for the human — never guessed
(CLAUDE.md: a wrong answer on a real application is far worse than a blank).

Submission is always the human's act. The extension has no code path that
clicks, submits, or presses Enter — scripts/check_extension_never_submits.py
enforces that mechanically, like check_never_submits.py does for Playwright.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml

from ..config import Config

SESSION_KEY = "application_session"
_LOCAL_LOCK: Dict[str, dict] = {}   # pure-local dev fallback


def _store():
    from .. import store
    return store if (store.enabled() and store.logged_in()) else None


def get_session() -> dict:
    st = _store()
    if st is not None:
        try:
            return st.kv_get(SESSION_KEY) or {}
        except RuntimeError:
            return {}
    return dict(_LOCAL_LOCK)


def _set_session(data: dict) -> None:
    st = _store()
    if st is not None:
        st.kv_set(SESSION_KEY, data)
        return
    _LOCAL_LOCK.clear()
    _LOCAL_LOCK.update(data)


class SessionBusy(Exception):
    pass


# "Job Application for X at Y" / "Apply for X" — strip the boilerplate lead-in
# before splitting, or the split eats the title and leaves you with "Job".
_TITLE_LEAD = re.compile(
    r"^\s*(?:job\s+)?(?:application|apply)\s+(?:for|to)\s+", re.I)
_TITLE_SPLIT = re.compile(r"\s+(?:at|@)\s+|\s*[-–—|·]\s*", re.I)
# On an ATS host the company is the FIRST PATH SEGMENT, not the hostname:
# job-boards.greenhouse.io/contentful/jobs/791 is Contentful's, not
# Greenhouse's.
_ATS_HOST = re.compile(
    r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|"
    r"recruitee\.com|workable\.com|teamtailor\.com|personio\.(de|com)|"
    r"myworkdayjobs\.com|jobvite\.com|breezy\.hr", re.I)


def _page_identity(url: str, page: Optional[dict]) -> Dict[str, str]:
    """Who is this application for? Read from the FORM PAGE the user is on —
    there is no saved job to look it up in any more (pivot 2026-07-22).
    JSON-LD first (ATS forms often carry a JobPosting), then the page's own
    <h1>/<title>, then the host. Never guesses beyond what the page says."""
    page = page or {}
    for raw in (page.get("jsonld") or []):
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
                if node.get("@type") == "JobPosting" and node.get("title"):
                    org = node.get("hiringOrganization")
                    company = (org.get("name") if isinstance(org, dict)
                               else org if isinstance(org, str) else "") or ""
                    return {"title": str(node["title"]).strip(),
                            "company": str(company).strip() or _host_company(url)}
                stack.extend(node.values())

    company = " ".join(str(page.get("company") or "").split())
    title = " ".join(str(page.get("h1") or "").split())
    if not title:
        raw = _TITLE_LEAD.sub(
            "", " ".join(str(page.get("pageTitle") or "").split()))
        parts = [p.strip() for p in _TITLE_SPLIT.split(raw) if p.strip()]
        if parts:
            title = parts[0]
            if len(parts) > 1 and not company:
                company = parts[-1]      # "… at Contentful"
    return {"title": title[:160], "company": (company or _host_company(url))[:120]}


def _host_company(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower().split(":")[0]
    except ValueError:
        return ""
    if _ATS_HOST.search(host):
        seg = [x for x in p.path.split("/") if x]
        if seg and seg[0].lower() not in ("jobs", "job", "apply", "embed"):
            return seg[0].replace("-", " ").replace("_", " ").title()
    parts = [x for x in host.split(".")
             if x not in ("www", "jobs", "careers", "boards", "job-boards",
                          "apply", "my", "com", "io", "co", "net", "org")]
    return parts[0].title() if parts else ""


def _norm_url(u: str) -> str:
    """Compare postings by identity, not by tracking junk."""
    try:
        p = urlparse((u or "").strip())
        return (p.netloc.lower().lstrip("www.") + p.path.rstrip("/")).lower()
    except ValueError:
        return (u or "").strip().lower()


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).strip()


DUPLICATE_DAYS = 90


def recent_duplicate(url: str, company: str, title: str) -> Optional[dict]:
    """Did the user already apply to THIS job in the last 90 days? Matched by
    posting URL first, then company + closely-matching title — the same job
    is routinely applied to via the posting one time and the ATS form the
    next, so URL alone misses real duplicates (decision 2026-07-22).

    Advisory only: the panel warns, the user applies anyway if they want.
    """
    from ..autofill import applications
    try:
        rows = applications.read_all(_store())
    except Exception:
        return None            # tracking unavailable is not a reason to block
    cutoff = (datetime.date.today() -
              datetime.timedelta(days=DUPLICATE_DAYS)).isoformat()
    want_url, want_co, want_t = _norm_url(url), (company or "").strip().lower(), _norm_title(title)
    best = None
    for r in rows:
        when = (r.get("date_applied") or "")[:10]
        if when < cutoff:
            continue
        same_url = want_url and _norm_url(r.get("url", "")) == want_url
        rt = _norm_title(r.get("title", ""))
        same_job = (want_co and want_t and
                    (r.get("company", "").strip().lower() == want_co) and
                    (rt == want_t or (len(want_t) > 8 and
                                      (want_t in rt or rt in want_t))))
        if same_url or same_job:
            if best is None or when > best["date_applied"]:
                best = {"company": r.get("company", ""), "title": r.get("title", ""),
                        "date_applied": when, "url": r.get("url", "")}
    return best


def start_session(url: str, cfg: Config, threshold: int,
                  page: Optional[dict] = None) -> dict:
    """Take the one-application lock for the form the user is standing on.

    No saved job is involved any more: the extension is the product, the
    dashboard records APPLICATIONS (user decision 2026-07-22). Nothing is
    written to the database here — only finish_session('applied') writes.
    """
    cur = get_session()
    if cur.get("url") and cur["url"] != url:
        raise SessionBusy(
            f"You already have an application in progress ({cur.get('title') or cur['url']}). "
            "Finish or cancel it first — one at a time keeps quality up.")
    who = _page_identity(url, page)
    # The description comes from wherever the extension read it — the POSTING
    # when the user started there, the form when the ATS embeds one. It is
    # what tailoring reads, so it is also what the user may edit.
    desc = " ".join(str((page or {}).get("description") or "").split())[:20000]
    if not desc:
        for raw in ((page or {}).get("jsonld") or []):
            try:
                blob = json.loads(raw)
            except (ValueError, TypeError):
                continue
            stack = [blob]
            while stack:
                node = stack.pop()
                if isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, dict):
                    if node.get("@type") == "JobPosting" and node.get("description"):
                        # JSON-LD descriptions are HTML — tailoring reads this
                        # as prose, so strip the tags here too (the extension
                        # already does on its side).
                        from ..discovery.jobs import strip_html
                        desc = " ".join(
                            strip_html(str(node["description"])).split())[:20000]
                        stack = []
                        break
                    stack.extend(node.values())
            if desc:
                break
    # Still thin, or the page never rendered a title (client-rendered ATS
    # postings often haven't hydrated when the panel reads them — a Greenhouse
    # job showed an empty title and description, user report 2026-07-24)?
    # If this posting lives on a KNOWN ATS board, ask its public API — clean
    # title/company/description, the same "ATS API first" rule scans follow.
    # Only the fast API path runs here (generic URLs return None instantly), so
    # starting a session never blocks on a full browser fetch. General to any
    # Greenhouse/Lever/Ashby/… posting, never a per-site branch.
    if len(desc) < 200 or not who["title"]:
        try:
            from ..discovery.single_job import _job_from_ats_board, _company_fallback
            j = _job_from_ats_board(_company_fallback(url), url, cfg)
        except Exception:
            j = None
        if j:
            if len((j.description or "").strip()) > len(desc):
                desc = " ".join((j.description or "").split())[:20000]
            if not who["title"] and (j.title or "").strip():
                who["title"] = j.title.strip()[:160]
            if not who["company"] and (j.company or "").strip():
                who["company"] = j.company.strip()[:120]
    _set_session({"url": url,
                  "title": " — ".join([x for x in (who["title"], who["company"]) if x]),
                  "company": who["company"], "job_title": who["title"],
                  "description": desc,
                  "started_at": datetime.datetime.now().isoformat(timespec="seconds")})
    return {"url": url, "title": who["title"], "company": who["company"],
            "description": desc,
            "duplicate": recent_duplicate(url, who["company"], who["title"]),
            "duplicate_days": DUPLICATE_DAYS}


def set_context(url: str, title: str, company: str, description: str) -> dict:
    """The user reviewed what was read off the page and corrected it. Their
    version wins over anything scraped — it is what gets tailored against and
    what lands in Tracking (user decision 2026-07-22)."""
    cur = get_session()
    if not cur.get("url"):
        raise ValueError("No application in progress.")
    cur.update({
        "job_title": " ".join((title or "").split())[:160],
        "company": " ".join((company or "").split())[:120],
        "description": " ".join((description or "").split())[:20000],
    })
    cur["title"] = " — ".join([x for x in (cur["job_title"], cur["company"]) if x])
    _set_session(cur)
    return {"ok": True, "title": cur["job_title"], "company": cur["company"],
            "has_description": bool(cur["description"])}


def _workspace_from_session(cur: dict, cfg: Config) -> str:
    """Build a tailoring workspace straight from the session — the job the
    user is applying to, as they reviewed it. There is no queue entry to read
    any more (pivot 2026-07-22), so the same `row` create_workspace used to
    assemble from a saved job is assembled from the session's title/company/
    description instead."""
    from ..tailoring.tailor import make_package
    row = {
        "Company": cur.get("company", ""),
        "Title": cur.get("job_title", ""),
        "Location": "",
        "URL": cur.get("url", ""),
        "Fit %": "",
        "AI Reason": "",
        "Matched Skills": "",
        "Missing Skills": "",
        "Req Years": "",
        "Meets Years": "",
        "Source": "extension",
        "Description": cur.get("description", ""),
    }
    return make_package(row)


def tailor_for(url: str, cfg: Config) -> dict:
    """Tailor the resume for the job in the CURRENT session. Reword against
    the description the user reviewed, verify (no-fabrication + PDF), then
    assess master vs tailored back-to-back so the before->after delta
    reflects the tailoring, not run-to-run variance."""
    cur = get_session()
    if not cur.get("url"):
        raise ValueError("No application in progress.")
    desc = (cur.get("description") or "").strip()
    if len(desc) < 120:
        # Truth rule: never tailor against nothing. A resume reworded for a
        # job we can't read would be guesswork, which is exactly what this
        # project forbids.
        raise ValueError(
            "There's no job description to tailor against. Paste it into the "
            "Job description box above and save, then try again.")

    d = _workspace_from_session(cur, cfg)
    from ..tailoring import reword
    ok, msg = reword.reword_workspace(d)
    if not ok:
        raise ValueError(f"Tailoring failed: {msg}")

    tailored_path = os.path.join(d, "content.tailored.yaml")
    fit_before = fit_after = None
    try:
        from ..scoring.ai_score import assess_job, background_text
        title = cur.get("job_title", "")
        before = assess_job(title, "", desc, background_text())
        after = assess_job(title, "", desc, background_text(tailored_path))
        fit_before = before.score if before else None
        fit_after = after.score if after else None
    except Exception:
        pass        # a scoring hiccup never invalidates a good tailoring

    cur["tailored"] = True
    cur["workspace"] = d
    _set_session(cur)
    return {"fit_before": fit_before, "fit_after": fit_after, "tailored": True}


def finish_session(url: str, outcome: str, cfg: Config) -> dict:
    """Release the lock. 'applied' writes the ONE record this product keeps —
    an application, built from the form page the user was on. 'cancelled'
    writes nothing at all (pivot 2026-07-22: we track applications, not jobs)."""
    cur = get_session()
    _set_session({})
    if outcome != "applied":
        return {"ok": True, "logged": False}
    from ..autofill import applications
    row = {
        "company": cur.get("company", "") or _host_company(url),
        "title": cur.get("job_title", ""),
        "url": url,
        "location": "",
        "fit": "",
        "resume_file": "",
        "date_applied": datetime.date.today().isoformat(),
        "status": "applied",
    }
    applications.log(row, _store())
    return {"ok": True, "logged": True, "application": row}


# ------------------------------------------------------------ field resolve
_CONSENT = re.compile(r"privacy|terms|consent|policy|agree", re.I)
_QUESTIONISH = re.compile(r"\?|^(why|what|how|describe|tell|share|explain)\b", re.I)


def _pick_option(candidates: List[str], options: List[str]) -> Optional[str]:
    """Exact match first, then substring — the Playwright filler's rule
    ('No' must not prefix-match a longer disability option)."""
    lo = [o.strip() for o in options]
    for c in candidates:
        for o in lo:
            if o.lower() == str(c).strip().lower():
                return o
    for c in candidates:
        cs = str(c).strip().lower()
        for o in lo:
            if cs and cs in o.lower():
                return o
    return None


_GENERIC_UPLOAD = re.compile(r"choose a file|drop (it|files?|your)|drag|upload|attach", re.I)
_NOT_RESUME_FILE = re.compile(r"cover|letter|portfolio|photo|picture|avatar|transcript|certificate", re.I)


def resolve_fields(url: str, fields: List[dict], cfg: Config) -> dict:
    """The extension found these controls on the current form step; decide
    what goes in each. Returns {values: {id: {...}}, unresolved: [...],
    attach_resume_to: [...]} — unresolved fields stay for the human."""
    from ..autofill.forms import (DEMOGRAPHIC, RESUME_HINT, _answer_for,
                                  _value_for, load_profile)
    profile = load_profile()
    # The session owns the workspace now — there is no queue entry to look it
    # up from (pivot 2026-07-22).
    d = get_session().get("workspace") or None
    answers: List[dict] = []
    if d and os.path.exists(os.path.join(d, "answers.yaml")):
        with open(os.path.join(d, "answers.yaml")) as f:
            answers = yaml.safe_load(f) or []

    values: Dict[str, dict] = {}
    unresolved: List[str] = []
    attach: List[str] = []
    questions: List[dict] = []
    file_fields: List[tuple] = []

    for f in fields:
        fid = str(f.get("id"))
        label = " ".join(str(f.get("label") or "").split())
        desc = label.lower()
        kind = f.get("kind") or "text"

        if kind == "file":
            file_fields.append((fid, desc))
            if not desc or RESUME_HINT.search(desc):
                attach.append(fid)
            continue
        if not desc:
            continue
        if DEMOGRAPHIC.search(desc):
            unresolved.append(fid)   # self-ID stays human-only
            continue

        matched = next((str(a.get("answer") or "") for a in answers
                        if str(a.get("match") or "").lower().strip()
                        and str(a.get("match")).lower().strip() in desc), None)

        if kind in ("select", "combobox", "radio"):
            opts = [str(o) for o in (f.get("options") or []) if str(o).strip()]
            cands = _answer_for(label, profile) or ([matched] if matched else None)
            pick = _pick_option([str(c) for c in cands], opts) if (cands and opts) else None
            if pick:
                values[fid] = {"option": pick, "source": "profile"}
            else:
                unresolved.append(fid)
        elif kind == "checkbox":
            if _CONSENT.search(desc) and str(profile.get("accept_terms", "")).lower() in ("yes", "true"):
                values[fid] = {"checked": True, "source": "profile"}
            else:
                unresolved.append(fid)
        else:   # text-like / textarea
            if matched:
                values[fid] = {"value": matched, "source": "answers"}
                continue
            kv = _value_for(desc, profile)
            if kv is not None:
                key, val = kv
                if key == "phone":
                    val = re.sub(r"[\s-]+", "", val)
                values[fid] = {"value": val, "source": "profile"}
            elif kind == "textarea" or _QUESTIONISH.search(label) or len(label) >= 30:
                questions.append({"match": desc[:60], "question": label,
                                  "kind": kind, "maxlength": f.get("maxlength"),
                                  "_fid": fid})
            else:
                unresolved.append(fid)

    # Open-ended questions -> the same grounded AI answerer the Playwright
    # flow uses (needs the workspace for job.md context; without one they
    # stay unresolved rather than answered blind).
    if questions and d:
        from ..tailoring import qa
        ok, res = qa.answer_questions(
            d, [{k: q[k] for k in ("match", "question", "kind", "maxlength")}
                for q in questions])
        if ok:
            by_match = {a["match"]: a["answer"] for a in res}
            for q in questions:
                if q["match"] in by_match:
                    values[q["_fid"]] = {"value": by_match[q["match"]],
                                         "source": "ai"}
                else:
                    unresolved.append(q["_fid"])
        else:
            unresolved.extend(q["_fid"] for q in questions)
    else:
        unresolved.extend(q["_fid"] for q in questions)

    # No explicitly resume-labeled file field, but the form has a generic
    # upload slot ("Choose a file or drop it here" — SmartRecruiters' Easy
    # Apply dropzone never says "resume", so nothing was ever attached; user
    # report 2026-07-19). The FIRST generic slot on such a form is the resume;
    # anything naming another document kind is left alone.
    if not attach:
        for fid, desc in file_fields:
            if (_GENERIC_UPLOAD.search(desc)
                    and not _NOT_RESUME_FILE.search(desc)):
                attach.append(fid)
                break

    return {"values": values, "unresolved": unresolved,
            "attach_resume_to": attach}
