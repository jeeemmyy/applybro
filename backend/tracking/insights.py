"""Rejection-aware application insights.

Turns the Email Log (built by `track`) into guidance for the NEXT application:

  - per-company outcome history (what happened, when, how fast),
  - stated rejection reasons, extracted from the actual email bodies when the
    employer gave one (most don't — that is reported honestly as 'no reason
    stated', never invented),
  - patterns: reason distribution, companies to pause, what got interviews.

`tailor` consults this so every new application workspace carries the history
for that company (see company_history / render_history).
"""
from __future__ import annotations

import base64
import datetime
import re
from collections import defaultdict
from typing import Dict, List, Optional

# Stated-reason patterns searched in rejection email bodies. Conservative:
# a reason is only reported when the employer's own words match.
REASONS = [
    ("visa / sponsorship", [
        "sponsor", "sponsorship", "work authorization", "work permit",
        "right to work", "visa",
    ]),
    ("experience level", [
        "more experience", "level of experience", "seniority", "more senior",
        "experience required", "years of experience",
    ]),
    ("location / onsite", [
        "location", "relocat", "on-site", "onsite", "time zone", "timezone",
        "based in", "local candidates",
    ]),
    ("role closed / filled", [
        "position has been filled", "no longer open", "role has been closed",
        "decided not to fill", "put this role on hold", "position on hold",
    ]),
    ("competition (their wording)", [
        "other candidates", "more closely match", "closely aligned",
        "stronger match", "high volume of applications", "many qualified",
    ]),
]


# Supabase email_log column order matching the sheet's Email Log row layout
# (Date, Company, Classification, From, Subject, Matched By, Gmail ID).
LOG_ROW_KEYS = ("date", "company", "classification", "sender", "subject",
                "matched_by", "gmail_id")


def stated_reason(body: str) -> str:
    t = " ".join((body or "").lower().split())
    hits = [name for name, kws in REASONS if any(k in t for k in kws)]
    return "; ".join(hits) if hits else "no reason stated"


def _gmail_body(gmail, gmail_id: str) -> str:
    try:
        msg = gmail.users().messages().get(
            userId="me", id=gmail_id, format="full").execute()
        from .track import _body_text
        return _body_text(msg.get("payload", {}))
    except Exception:
        return ""


def load_history(sheet=None, gmail=None) -> Dict[str, List[dict]]:
    """Email Log -> {company: [entries oldest->newest]}, with stated reasons
    extracted from rejection bodies when a gmail client is provided. Reads
    the Supabase email_log table when the cloud store is configured (sheet
    may be None then), else the sheet's Email Log tab."""
    from .. import store
    if store.enabled():
        raw = store.email_log_rows()
        rows = [list(LOG_ROW_KEYS)] + [
            [r.get(k, "") for k in LOG_ROW_KEYS] for r in raw]
    elif sheet is not None:
        rows = sheet.values("Email Log")
    else:
        rows = []
    hist: Dict[str, List[dict]] = defaultdict(list)
    for r in rows[1:]:
        r = r + [""] * (7 - len(r))
        date, company, cls, sender, subject, matched_by, gid = r[:7]
        e = {"date": date, "classification": cls, "subject": subject, "gmail_id": gid}
        if gmail is not None and cls == "rejection" and gid:
            e["reason"] = stated_reason(_gmail_body(gmail, gid))
        hist[company].append(e)
    for c in hist:
        hist[c].sort(key=lambda e: e["date"])
    return dict(hist)


def match_company_history(hist: Dict[str, List[dict]], company: str) -> List[dict]:
    """Look up `company` in an already-loaded history dict (no I/O). Use
    this instead of company_history() whenever load_history() was already
    called for the run — load_history hits the Gmail API once per rejection
    row, so recomputing it per-job/per-company is expensive and pointless."""
    key = company.strip().lower()
    for c, entries in hist.items():
        if c.strip().lower() == key:
            return entries
    return []


def company_history(sheet, company: str, gmail=None) -> List[dict]:
    hist = load_history(sheet, gmail)
    key = company.strip().lower()
    for c, entries in hist.items():
        if c.strip().lower() == key:
            return entries
    return []


def render_history(entries: List[dict], company: str) -> str:
    """Markdown block for a tailoring workspace's job.md."""
    if not entries:
        return ""
    lines = [f"\n## Past outcomes with {company} (from your Gmail)\n"]
    for e in entries:
        reason = f" — stated reason: {e['reason']}" if e.get("reason") else ""
        lines.append(f"- {e['date']}: **{e['classification']}**"
                     f" ({e['subject'][:70]}){reason}")
    lines.append(
        "\nUse this: address a stated reason if fixable (e.g. lead with the "
        "skills they doubted); if rejected very recently for a similar role, "
        "consider pausing this company instead of re-applying.")
    return "\n".join(lines) + "\n"


# What counts as a real response (vs. an "application received" auto-ack or
# a needs-review unknown) — shared by the aggregate stats here and by the
# Tracking page's awaiting-reply computation in app/tracking.py.
RESPONSE_CLASSES = {"interview", "next steps", "offer", "rejection"}


def _aggregate(hist: Dict[str, List[dict]]) -> dict:
    """Shared computation behind both report() (markdown) and
    structured_report() (JSON for the dashboard) — one source of truth for
    what counts as a response, a pause candidate, etc."""
    counts: Dict[str, int] = defaultdict(int)
    reasons: Dict[str, int] = defaultdict(int)
    interviews, recent_rejections = [], []
    response_days: List[int] = []

    for company, entries in sorted(hist.items()):
        for e in entries:
            counts[e["classification"]] += 1
            if e["classification"] == "interview":
                interviews.append((company, e))
            if e["classification"] == "rejection":
                if e.get("reason"):
                    reasons[e["reason"]] += 1
                recent_rejections.append((company, e))
        # Days between first contact and the first MEANINGFUL response
        # (interview / next steps / offer / rejection — not just another
        # "application received" auto-ack). Only counted when: there is a
        # first dated entry, a later dated entry whose classification is
        # actually in RESPONSE_CLASSES exists, and both dates parse
        # cleanly. If the very first entry is already a response-class
        # entry there is no meaningful gap to measure, so it's skipped too
        # (matches the docstring/comment — this used to just diff the
        # first two dated entries regardless of classification, which
        # counted e.g. two auto-acks as a "response").
        dated = [e for e in entries if e.get("date")]
        if dated and dated[0]["classification"] not in RESPONSE_CLASSES:
            d0_entry = dated[0]
            response_entry = next(
                (e for e in dated[1:] if e["classification"] in RESPONSE_CLASSES),
                None)
            if response_entry is not None:
                try:
                    d0 = datetime.date.fromisoformat(d0_entry["date"])
                    d1 = datetime.date.fromisoformat(response_entry["date"])
                    response_days.append((d1 - d0).days)
                except ValueError:
                    pass

    cutoff = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    paused = sorted({c for c, e in recent_rejections if e["date"] >= cutoff})

    total_companies = len(hist)
    responded_companies = len({c for c, entries in hist.items()
                               if any(e["classification"] in RESPONSE_CLASSES
                                     for e in entries)})
    median_days = None
    if response_days:
        s = sorted(response_days)
        n = len(s)
        median_days = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    return {
        "counts": dict(counts), "reasons": dict(reasons),
        "interviews": interviews, "paused": paused,
        "total_companies": total_companies,
        "responded_companies": responded_companies,
        "response_rate_pct": (round(100 * responded_companies / total_companies)
                              if total_companies else None),
        "median_days_to_response": median_days,
        "median_days_sample_size": len(response_days),
    }


def structured_report(sheet, gmail=None) -> dict:
    """Same data as report(), as JSON-friendly structures for the
    dashboard's Tracking page instead of a markdown document."""
    hist = load_history(sheet, gmail)
    agg = _aggregate(hist)
    return {
        "companies": [
            {"company": c, "entries": entries}
            for c, entries in sorted(hist.items())
        ],
        **agg,
    }


def report(sheet, gmail=None, applied_dates: Optional[Dict[str, str]] = None) -> str:
    """Full insights report as markdown."""
    hist = load_history(sheet, gmail)
    agg = _aggregate(hist)
    today = datetime.date.today().isoformat()
    out = [f"# Application insights — {today}\n"]

    out.append("## Totals\n")
    for cls, n in sorted(agg["counts"].items(), key=lambda x: -x[1]):
        out.append(f"- {cls}: {n}")
    if agg["response_rate_pct"] is not None:
        out.append(f"\nResponse rate: {agg['responded_companies']}/"
                   f"{agg['total_companies']} companies logged got an "
                   f"interview, next-steps, offer, or rejection "
                   f"({agg['response_rate_pct']}%).")
    if agg["median_days_to_response"] is not None:
        out.append(f"Median days between first and next contact: "
                   f"{agg['median_days_to_response']:.0f} "
                   f"({agg['median_days_sample_size']} companies with enough data).")

    if agg["reasons"]:
        out.append("\n## Stated rejection reasons (their words, not guesses)\n")
        for r, n in sorted(agg["reasons"].items(), key=lambda x: -x[1]):
            out.append(f"- {r}: {n}")

    if agg["interviews"]:
        out.append("\n## What got interviews — do more of this\n")
        for company, e in agg["interviews"]:
            out.append(f"- {company} ({e['date']}): {e['subject'][:70]}")

    out.append("\n## Per-company history\n")
    for company, entries in sorted(hist.items()):
        arc = " → ".join(f"{e['classification']} ({e['date']})" for e in entries)
        out.append(f"- **{company}**: {arc}")
        if applied_dates and company in applied_dates and entries:
            out.append(f"  - applied {applied_dates[company]}")

    out.append("\n## Suggested pauses (rejected in the last 60 days)\n")
    if agg["paused"]:
        for c in agg["paused"]:
            out.append(f"- {c} — wait ~60-90 days or apply only with a referral "
                       f"or a materially different role")
    else:
        out.append("- none")
    return "\n".join(out) + "\n"
