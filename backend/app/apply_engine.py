"""Apply room ORCHESTRATION — sequential form-filling in the dashboard's
shared Chrome window (see autofill.forms._acquire_page / constants.CDP_PORT
for how tabs land beside the dashboard tab instead of a separate browser).

This module owns no domain rules; it wires them together and holds the
in-memory batch state the dashboard polls. The actual decisions live in the
autofill package:
  - what a submission-confirmation page looks like → autofill.confirm
  - the applications-ledger schema, the already-applied rule, how a record
    is written → autofill.applications

For each selected job: create the job's folder on demand, AI-tailor the
resume to that job (tailoring.reword — the user's local Claude Code CLI),
verify the no-fabrication rules + compile the PDF, then fill the form with
wait_for_close=False so the tab stays open for review WHILE the next job's
fill starts immediately (the "lookahead"). A job whose resume could not be
tailored is NOT filled — an untailored application is just mass-applying,
which defeats the product's goal (interview calls, not submission counts).
Submission is always a human act: no code path here or in autofill.forms
clicks a submit-like control.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from ..autofill import applications, confirm
from ..config import Config
from ..constants import CDP_PORT
from . import queue as queue_store

EventFn = Callable[[Dict], None]


class BatchState:
    """Tracks the jobs in the most recent apply batch so a later
    'check for confirmations' pass and manual mark-applied know which
    workspace/company a given URL belongs to."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: Dict[str, dict] = {}   # url -> {company, title, workspace, status}

    def start_batch(self, entries: List[dict]) -> None:
        with self.lock:
            for e in entries:
                self.jobs[e["url"]] = {
                    "company": e["company"], "title": e["title"],
                    "workspace": e["workspace"], "status": "queued",
                }

    def set_status(self, url: str, status: str) -> None:
        with self.lock:
            if url in self.jobs:
                self.jobs[url]["status"] = status

    def set_workspace(self, url: str, workspace: str) -> None:
        with self.lock:
            if url in self.jobs:
                self.jobs[url]["workspace"] = workspace

    def get(self, url: str) -> Optional[dict]:
        with self.lock:
            return dict(self.jobs[url]) if url in self.jobs else None

    def pending_urls(self) -> List[str]:
        with self.lock:
            return [u for u, j in self.jobs.items() if j["status"] in ("queued", "filled")]


BATCH = BatchState()

# Set by the dashboard's "Stop batch" button (server /api/apply/stop).
# Checked between jobs — an in-flight AI tailoring is killed immediately
# (reword.abort_current), an in-flight form fill finishes first (killing
# Playwright mid-fill would leave a half-mangled tab).
CANCEL = threading.Event()


def stop_batch() -> bool:
    """Request the running batch to stop. Returns whether anything was
    actually in flight to stop."""
    from ..tailoring import reword
    CANCEL.set()
    aborted_tailor = reword.abort_current()
    return aborted_tailor or bool(BATCH.pending_urls())


def _workspace_ready(url: str) -> Optional[dict]:
    """A job can only be applied to once tailored + verified. Returns the
    workspace status dict, or None if no workspace exists yet."""
    d = queue_store.find_workspace(url)
    if d is None:
        return None
    status = queue_store.workspace_status(d)
    status["workspace"] = d
    return status


def _already_applied_urls(cfg: Optional[Config]) -> set:
    """Best-effort read of the applications ledger for the pre-fill dedupe
    guard. Error POLICY lives here (an app concern): any failure — no oauth,
    tab missing, offline — means 'proceed, nothing looks applied yet', since
    the user reviews every filled tab before submitting regardless."""
    if cfg is None or cfg.auth_mode != "oauth":
        return set()
    try:
        from ..google.sheet_factory import get_sheet
        return applications.applied_urls(get_sheet(cfg))
    except Exception:
        return set()


def _reassess_fit(url: str, workspace: str, on_event: EventFn) -> None:
    """After a job is tailored+verified, measure whether tailoring raised the
    fit. Assesses the MASTER and the TAILORED resume BACK-TO-BACK (same model,
    same moment) so the before->after delta reflects the tailoring, not the
    run-to-run variance that would swamp it if we reused discovery's score.
    Best-effort — a scoring hiccup never blocks the fill."""
    import os as _os
    import re as _re
    entry = next((e for e in queue_store.load() if e["url"] == url), None)
    if entry is None:
        return
    tailored_path = _os.path.join(workspace, "content.tailored.yaml")
    if not _os.path.exists(tailored_path):
        return
    desc = entry.get("description", "")
    if not desc:
        jm = _os.path.join(workspace, "job.md")
        if _os.path.exists(jm):
            with open(jm) as f:
                m = _re.search(r"## Job description\s+(.*)", f.read(), _re.S)
                desc = m.group(1) if m else ""
    try:
        from ..scoring.ai_score import assess_job, background_text
        title, loc = entry.get("title", ""), entry.get("location", "")
        before = assess_job(title, loc, desc, background_text())          # master
        after = assess_job(title, loc, desc, background_text(tailored_path))
    except Exception:
        before = after = None
    if after is None or after.score is None:
        return
    fit_before = before.score if (before and before.score is not None) else \
        entry.get("ai_score", entry.get("score"))
    queue_store.update_entry(url, {
        "fit_before": fit_before, "fit_after": after.score,
        "ai_score": after.score, "ai_reason": after.reason,
        "ai_matched": after.matched, "ai_gaps": after.gaps,
    })
    on_event({"type": "job_fit_change", "url": url,
              "fit_before": fit_before, "fit_after": after.score})


def _make_answer_generator(url: str, workdir: str, on_event: EventFn):
    """The Apply room's answer_generator for autofill(): AI-write grounded
    answers (tailoring.qa) for the open-ended questions the form still has
    after the normal pass, with batch status + SSE so the dashboard shows
    what the wait is. A generation failure is reported but never blocks the
    fill — those fields just stay 'left for you'."""
    def generate(questions: List[dict]) -> List[dict]:
        from ..tailoring import qa
        BATCH.set_status(url, "answering")
        on_event({"type": "job_answering", "url": url,
                  "count": len(questions)})
        try:
            ok, res = qa.answer_questions(workdir, questions)
        except Exception as e:
            ok, res = False, str(e)
        BATCH.set_status(url, "filling")
        if not ok:
            on_event({"type": "job_answers_failed", "url": url,
                      "error": str(res)[:300]})
            return []
        on_event({"type": "job_answered", "url": url, "count": len(res)})
        return res
    return generate


def apply_batch(urls: List[str], profile_path: str, on_event: EventFn,
                cfg: Optional[Config] = None) -> dict:
    """Sequentially fill each job's form. Never blocks waiting for the user
    to close/submit a tab — each fill returns immediately so the next job
    starts right away, leaving every filled tab open for review."""
    from ..autofill.forms import autofill

    entries = []
    for url in urls:
        ws = _workspace_ready(url)
        entries.append({"url": url, "company": "", "title": "",
                        "workspace": ws["workspace"] if ws else None})
    q = {e["url"]: e for e in queue_store.load()}
    for e in entries:
        qe = q.get(e["url"])
        if qe:
            e["company"], e["title"] = qe["company"], qe["title"]
    BATCH.start_batch(entries)

    applied_already = _already_applied_urls(cfg)
    filled = skipped_not_ready = 0
    CANCEL.clear()
    for i, url in enumerate(urls, start=1):
        if CANCEL.is_set():
            # Stop batch: everything not yet processed is marked cancelled —
            # nothing half-done, nothing silently dropped.
            BATCH.set_status(url, "cancelled")
            on_event({"type": "job_cancelled", "url": url})
            continue
        ws = _workspace_ready(url)
        on_event({"type": "job_start", "url": url, "index": i, "total": len(urls)})
        if url in applied_already:
            # Never fill a job already in the ledger — a second submission is
            # at best noise, at worst looks careless to the employer. The job
            # URL stays in the Apply room's history for a manual re-apply.
            BATCH.set_status(url, "already_applied")
            on_event({"type": "job_already_applied", "url": url})
            skipped_not_ready += 1
            continue
        # Unified lifecycle (Phase 3): the moment work starts on a job it
        # moves to "applying" — the Jobs tab shows it there with a timeline.
        queue_store.set_status(url, "applying")
        # The Apply room owns tailoring now (the queue's Tailor button is
        # gone): create the workspace on demand, then make sure a verified
        # PDF exists — verify() both enforces the no-fabrication rules and
        # compiles the exact PDF that gets uploaded.
        if ws is None:
            if cfg is None:
                BATCH.set_status(url, "not_tailored")
                on_event({"type": "job_error", "url": url,
                          "error": "No workspace yet, and no config to tailor with."})
                skipped_not_ready += 1
                continue
            BATCH.set_status(url, "tailoring")
            on_event({"type": "job_tailoring", "url": url})
            try:
                d = queue_store.create_workspace(url, cfg)
            except Exception as e:
                d = None
                on_event({"type": "job_error", "url": url,
                          "error": f"Tailoring failed: {e}"})
            if d is None:
                BATCH.set_status(url, "not_tailored")
                if queue_store.find_workspace(url) is None:
                    on_event({"type": "job_error", "url": url,
                              "error": "Couldn't tailor — this URL isn't in the queue."})
                skipped_not_ready += 1
                continue
            BATCH.set_workspace(url, d)
            ws = _workspace_ready(url)
        # AI tailoring: any job whose tailored copy still reads like the
        # master gets rewritten for THIS job by the local Claude Code CLI,
        # then mechanically verified (no invented skills/metrics/identity
        # changes) — reword_workspace does both. Without this step autofill
        # would just mass-apply the same resume, so a failure here skips the
        # fill instead of proceeding untailored.
        if ws and not ws["reworded"]:
            BATCH.set_status(url, "tailoring")
            on_event({"type": "job_tailoring", "url": url})
            from ..tailoring import reword
            try:
                ok, msg = reword.reword_workspace(ws["workspace"])
            except Exception as e:
                ok, msg = False, str(e)
            if not ok:
                if msg == reword.ABORTED:
                    # User's choice, not a failure — no error banner.
                    BATCH.set_status(url, "tailor_aborted")
                    on_event({"type": "job_tailor_aborted", "url": url})
                else:
                    BATCH.set_status(url, "not_tailored")
                    on_event({"type": "job_error", "url": url,
                              "error": f"AI tailoring failed: {msg}"})
                skipped_not_ready += 1
                continue
            ws = _workspace_ready(url)
        if ws and not ws["verified"]:
            from ..tailoring.tailor import verify as verify_rules
            ty = os.path.join(ws["workspace"], "content.tailored.yaml")
            ok, problems = verify_rules(ty)
            ws = _workspace_ready(url)   # verify compiled the PDF -> re-check
            if not ok or not ws["verified"]:
                BATCH.set_status(url, "not_verified")
                on_event({"type": "job_error", "url": url,
                          "error": "Verify failed: "
                                   + (problems[0] if problems else "PDF didn't compile.")})
                skipped_not_ready += 1
                continue
        # Re-score the now-tailored resume: did tailoring raise the fit? Shown
        # in the Apply room as "X% -> Y%". Only meaningful right after a fresh
        # tailoring, so gate on ws having just been (re)worded in this pass.
        if ws and ws.get("reworded"):
            BATCH.set_status(url, "scoring")
            on_event({"type": "job_rescoring", "url": url})
            _reassess_fit(url, ws["workspace"], on_event)
        try:
            BATCH.set_status(url, "filling")
            result = autofill(ws["workspace"], profile_path,
                              headed=True, wait_for_close=False,
                              answer_generator=_make_answer_generator(
                                  url, ws["workspace"], on_event))
            BATCH.set_status(url, "filled")
            queue_store.add_timeline(
                url, "form filled",
                f"{len(result['filled'])} fields — awaiting your review")
            on_event({"type": "job_filled", "url": url,
                      "filled": result["filled"], "uploaded": result["uploaded"],
                      "skipped": result["skipped"]})
            filled += 1
        except Exception as e:
            BATCH.set_status(url, "fill_error")
            on_event({"type": "job_error", "url": url, "error": str(e)})

    summary = {"type": "batch_done", "total": len(urls), "filled": filled,
               "not_ready": skipped_not_ready}
    on_event(summary)
    return summary


def check_confirmations(cfg: Config) -> List[str]:
    """Best-effort: reconnect to the shared window via CDP and check each
    still-pending job's tab for a confirmation page (delegating the "is this
    a confirmation?" decision to autofill.confirm). Returns the URLs newly
    detected as applied (also logs them). Opportunistic — never blocks the
    UI; an undetected job just stays available for the manual Mark Applied
    button.

    Host-ambiguity guard (P3.1): matching is by hostname, but a batch can
    contain 2+ still-pending jobs at the SAME company. If a confirmation
    page's host matches more than one pending job, there's no reliable way to
    tell which it confirms, so NONE are auto-marked — otherwise a false
    "applied" row would be written for the others. This guard is app-level on
    purpose: it reasons about the live open-tab set, not a pure text rule."""
    pending = BATCH.pending_urls()
    if not pending:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []

    by_host: Dict[str, List[str]] = {}
    for url in pending:
        by_host.setdefault(urlparse(url).netloc, []).append(url)

    detected: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{CDP_PORT}", timeout=1500)
            pages = browser.contexts[0].pages
            for host, urls in by_host.items():
                if len(urls) != 1:
                    continue  # ambiguous — see docstring
                match = next((pg for pg in pages
                             if urlparse(pg.url).netloc == host
                             and confirm.looks_confirmed(pg)), None)
                if match:
                    detected.append(urls[0])
    except Exception:
        return []

    for url in detected:
        BATCH.set_status(url, "applied")
        mark_applied(cfg, url)
    return detected


def mark_applied(cfg: Config, url: str) -> None:
    """Record a job as applied: update the in-memory batch + drop it from the
    review queue (both app state), then write it to the applications ledger
    (autofill.applications owns the schema)."""
    import datetime
    entry = {e["url"]: e for e in queue_store.load()}.get(url, {})
    BATCH.set_status(url, "applied")
    # An applied job leaves the review queue for APPLIED_HIDE_DAYS (queue.py)
    # — its record from here on is the ledger (Apply room history). Past
    # that window a re-run treats it as a fresh candidate again, in case the
    # listing was reposted or is worth a second look.
    today = datetime.date.today().isoformat()
    queue_store.set_skipped(url, True, applied_at=today)
    queue_store.set_status(url, "applied")   # unified lifecycle + timeline

    from .. import store
    if not store.enabled() and cfg.auth_mode != "oauth":
        return   # no ledger configured at all

    d = queue_store.find_workspace(url)
    resume_name = ""
    resume_path = ""
    if d:
        for f in os.listdir(d):
            if f.endswith(".pdf") and f != "content.tailored.pdf":
                resume_name = f
                resume_path = os.path.join(d, f)
                break

    if store.enabled():
        applications.log(
            None,
            company=entry.get("company", ""), title=entry.get("title", ""),
            url=url, location=entry.get("location", ""),
            fit=str(entry.get("score", "")), resume_file=resume_name,
            date=today)
        # Cloud copy of the exact PDF that went to the employer — expires
        # after 24h (schema TTL). Best-effort: a failed upload must never
        # block the applied mark itself.
        if resume_path:
            try:
                with open(resume_path, "rb") as f:
                    store.resume_upload(url, resume_name, f.read())
            except Exception:
                pass
        return

    from ..google.sheet_factory import get_sheet
    applications.log(
        get_sheet(cfg),
        company=entry.get("company", ""), title=entry.get("title", ""),
        url=url, location=entry.get("location", ""),
        fit=str(entry.get("score", "")), resume_file=resume_name,
        date=today)


def mark_skipped(url: str) -> None:
    """Manual fallback for 'I decided not to submit this one after all'."""
    BATCH.set_status(url, "skipped")
    queue_store.set_status(url, "archived", note="decided not to submit")
