"""The applications ledger — the record of jobs the user has applied to,
kept in the "Applications" tab of their Google Sheet.

This is a Layer-3 domain concern: writing a record is the conclusion of the
apply step, and the same record is read both to stop a job being applied to
twice AND by Layer-4 tracking to know which companies to match Gmail
outcomes against. Keeping the tab name, column schema, and row parsing HERE
(one place) is why apply_engine, the /api/applications route, and the
tracking wrappers no longer each re-implement them.

The Google `sheet` client is passed IN — this module imports no Google
libraries itself, and it sets no error policy: it lets the sheet client's
exceptions propagate so each caller can decide (the apply guard treats any
failure as "nothing applied yet"; the history API distinguishes a missing
tab from a real outage).
"""
from __future__ import annotations

from typing import Dict, List

TAB = "Applications"
HEADER = ["Company", "Title", "URL", "Location", "Fit %", "Resume file",
          "Date applied", "Status"]


def read_all(sheet=None) -> List[Dict[str, str]]:
    """Every logged application as clean-keyed dicts, oldest first. Reads
    Supabase when the cloud store is configured (sheet may be None then);
    otherwise the Google Sheet tab. Keys are normalized (`fit`,
    `resume_file`, `date_applied`) regardless of exact header casing."""
    from .. import store
    if store.enabled():
        return store.applications_read_all()
    if sheet is None:
        return []
    rows = sheet.values(TAB)
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]

    def col(r: List[str], name: str) -> str:
        i = header.index(name) if name in header else -1
        return r[i].strip() if 0 <= i < len(r) else ""

    return [{
        "company": col(r, "company"), "title": col(r, "title"),
        "url": col(r, "url"), "location": col(r, "location"),
        "fit": col(r, "fit %"), "resume_file": col(r, "resume file"),
        "date_applied": col(r, "date applied"), "status": col(r, "status"),
    } for r in rows[1:]]


def applied_urls(sheet=None) -> set:
    """Set of job URLs already in the ledger — the record of truth for
    'don't fill this one again'."""
    return {a["url"] for a in read_all(sheet) if a["url"]}


def log(sheet=None, *, company: str, title: str, url: str, location: str,
        fit: str, resume_file: str, date: str, status: str = "applied") -> None:
    """Append one application row. Supabase when configured (no sheet
    involved); otherwise the sheet tab, plus a best-effort applied mark on
    the target-list tab (a no-op if that tab lacks applied/applied_date
    columns — schemas vary, and update_company_columns degrades gracefully)."""
    from .. import store
    if store.enabled():
        store.applications_log({
            "company": company, "title": title, "url": url,
            "location": location, "fit": fit, "resume_file": resume_file,
            "date_applied": date, "status": status,
        })
        return
    if sheet is None:
        raise RuntimeError("No application ledger configured (Supabase or Google Sheet).")
    sheet.append_row(TAB, HEADER, [company, title, url, location, fit,
                                   resume_file, date, status])
    sheet.update_company_columns(company, {"applied": "Yes", "applied_date": date})
