"""Tracking page backend — thin wrapper around tracking.insights + tracking.track.

Computing the Tracking view (GET) uses whatever is already logged in the
Email Log tab. Refreshing (POST) re-scans Gmail for new outcome emails and
logs them, exactly like `cli.py track` — except the list of "companies
you've applied to" comes from the Applications tab (built by the Apply
room in P2), not a possibly-absent applied/applied_date column on the
target-list tab (several of the user's real tabs don't have one — this
mirrors the same graceful-degradation reasoning behind
Sheet.update_company_columns).
"""
from __future__ import annotations

from typing import Dict, List

from ..config import Config


def _applied_companies(sheet) -> List[Dict[str, str]]:
    """Unique companies from the applications ledger, shaped for
    tracking.track.analyze() (needs 'company' + 'career_url' for sender-
    domain matching — a specific job posting URL works fine for that;
    company_domain() just needs *a* URL on that company's domain)."""
    from ..autofill import applications
    seen: Dict[str, str] = {}
    for a in applications.read_all(sheet):
        if a["company"]:
            seen.setdefault(a["company"], a["url"])
    return [{"company": c, "career_url": u} for c, u in seen.items()]


def _applications_summary(sheet, report: dict) -> dict:
    """The 'applied' side of the funnel, from the applications ledger: how
    many applications, at how many companies, when the last one was — plus
    how many applied companies have had NO real response yet (awaiting),
    matched against the email-log companies in the already-computed report."""
    from ..autofill import applications
    from ..tracking.insights import RESPONSE_CLASSES
    try:
        apps = applications.read_all(sheet)
    except Exception:
        apps = []
    if not apps:
        return {"total": 0, "companies": 0, "last_date": None, "awaiting": 0}

    companies = {a["company"] for a in apps if a["company"]}
    dates = sorted(a["date_applied"] for a in apps if a["date_applied"])
    responded = {c["company"].strip().lower() for c in report["companies"]
                 if any(e["classification"] in RESPONSE_CLASSES
                        for e in c["entries"])}
    awaiting = sum(1 for c in companies if c.lower() not in responded)
    return {"total": len(apps), "companies": len(companies),
            "last_date": dates[-1] if dates else None, "awaiting": awaiting}


def _gmail_and_sheet(cfg: Config):
    """Gmail client + (sheet or None). With the Supabase store configured,
    the Google Sheet is out of the loop entirely — OAuth is still needed,
    but only for the gmail.readonly scope."""
    from googleapiclient.discovery import build
    from .. import store
    if store.enabled():
        from ..google.sheets import get_credentials
        return build("gmail", "v1", credentials=get_credentials(cfg)), None
    from ..google.sheets import Sheet
    sheet = Sheet(cfg)
    return build("gmail", "v1", credentials=sheet.creds), sheet


def get_tracking(cfg: Config) -> dict:
    from ..tracking.insights import structured_report
    gmail, sheet = _gmail_and_sheet(cfg)
    report = structured_report(sheet, gmail)
    report["applications"] = _applications_summary(sheet, report)
    return report


# Email classification -> unified job status (SaaS Phase 3). "needs review"
# and "application received" deliberately change nothing.
_STATUS_FROM_CLASS = {"rejection": "rejected", "interview": "interview",
                      "next steps": "interview", "offer": "offer"}


def _update_job_statuses(emails) -> int:
    """Write email outcomes onto the SAME job records the Jobs tab shows:
    for each newly classified outcome email, move that company's most recent
    in-flight job (applying/applied/interview) to the matching status. Best
    effort by design — a wrong match is one manual status change away
    (the Jobs tab always allows overriding)."""
    from . import queue as queue_store
    updated = 0
    jobs = queue_store.all_jobs_with_status()
    for e in emails:
        status = _STATUS_FROM_CLASS.get(getattr(e, "classification", ""))
        company = (getattr(e, "company", "") or "").strip().lower()
        if not status or not company:
            continue
        candidates = [j for j in jobs
                      if (j.get("company", "") or "").strip().lower() == company
                      and j["status"] in ("applying", "applied", "interview")
                      and j["status"] != status]
        if not candidates:
            continue
        candidates.sort(key=lambda j: j.get("applied_at") or j.get("date_found") or "",
                        reverse=True)
        target = candidates[0]
        if queue_store.set_status(target["url"], status,
                                  note=f"email from {getattr(e, 'sender', '')[:60]}"):
            target["status"] = status   # keep the local view consistent
            updated += 1
    return updated


def refresh_tracking(cfg: Config, days: int = 30) -> dict:
    """Re-scan Gmail, log any new outcome emails, update the matching jobs'
    lifecycle status, and return the refreshed structured report."""
    from ..tracking.track import analyze, write_log
    gmail, sheet = _gmail_and_sheet(cfg)
    applied = _applied_companies(sheet)
    emails = analyze(gmail, applied, days, max_results=200)
    n = write_log(sheet, emails)
    jobs_updated = _update_job_statuses(emails) if n else 0
    return {"new_logged": n, "jobs_updated": jobs_updated, **get_tracking(cfg)}
