"""Layer 4 — application-outcome tracking from Gmail. READ-ONLY.

Uses the gmail.readonly scope: this module can never send, delete, modify,
or reply to email. It reads recent messages, matches them to companies the
user actually applied to (applied=Yes in the companies tab), classifies the
outcome with transparent keyword rules, and logs results to an append-only
"Email Log" tab in the same spreadsheet.

Safety rules encoded here:
- Anything ambiguous is classified "needs review", never guessed.
- The company row's response cell is only updated when EMPTY — the user's
  hand-written notes are never overwritten.
- The Email Log is deduplicated by Gmail message id, so re-runs are safe.
"""
from __future__ import annotations

import base64
import datetime
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# Classification rules, checked in priority order. First hit wins; a message
# hitting rules in MORE THAN ONE category -> needs review (conflict).
RULES: List[Tuple[str, List[str]]] = [
    ("offer", [
        "pleased to offer", "offer letter", "offer of employment",
        "extend an offer", "formal offer", "employment contract",
    ]),
    ("rejection", [
        "unfortunately", "not to move forward", "not moving forward",
        "other candidates", "will not be proceeding", "regret to inform",
        "decided not to", "not been successful", "pursue other applicants",
        "position has been filled", "no longer under consideration",
        "not selected", "won't be moving", "not be progressing",
    ]),
    ("interview", [
        "schedule an interview", "interview invitation", "invite you to interview",
        "phone screen", "technical interview", "meet the team", "book a call",
        "schedule a call", "your availability", "calendly.com", "recruiter call",
        "first-round", "screening call", "video interview",
    ]),
    ("next steps", [
        "next steps", "next stage", "next round", "coding challenge",
        "take-home", "assessment", "online test", "questionnaire",
        "move forward with", "progressing to",
    ]),
    ("application received", [
        "received your application", "thank you for applying",
        "application has been received", "thank you for your application",
        "application was sent", "successfully submitted",
    ]),
]

# Sender domains that indicate recruiting mail even without a company match.
ATS_DOMAINS = ("greenhouse", "lever.co", "ashbyhq", "myworkday", "workday",
               "smartrecruiters", "successfactors", "icims", "personio",
               "recruitee", "workable", "teamtailor", "jobvite", "bamboohr")

LOG_TAB = "Email Log"
LOG_HEADER = ["Date", "Company", "Classification", "From", "Subject",
              "Matched By", "Gmail ID"]


@dataclass
class TrackedEmail:
    gmail_id: str
    date: str
    sender: str
    subject: str
    company: str          # "" when unmatched
    matched_by: str       # domain / from-name / subject / (unmatched)
    classification: str   # offer/rejection/interview/next steps/application received/needs review


# ------------------------------------------------------------ classification
def classify(text: str) -> str:
    t = " ".join((text or "").lower().split())
    hits = [cat for cat, kws in RULES if any(k in t for k in kws)]
    if not hits:
        return "needs review"
    if len(hits) == 1:
        return hits[0]
    # rejection wording often quotes interview/next-step phrases; trust the
    # highest-priority category only when it is offer/rejection, else review.
    return hits[0] if hits[0] in ("offer", "rejection") else "needs review"


# ------------------------------------------------------------------ matching
def company_domain(career_url: str) -> str:
    """contentful.com from https://www.contentful.com/careers/ ..."""
    net = urlparse(career_url or "").netloc.lower()
    net = re.sub(r"^(www|careers|jobs|apply|boards)\.", "", net)
    parts = net.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else net


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def match_company(sender: str, subject: str,
                  companies: List[Dict[str, str]]) -> Tuple[str, str]:
    """Returns (company, matched_by) or ("", "")."""
    sender_l = sender.lower()
    m = re.search(r"@([a-z0-9.-]+)", sender_l)
    sdom = m.group(1) if m else ""
    # 1. sender domain == company site domain (strongest signal). Exception:
    # when the company IS an ATS brand (e.g. applying to Personio itself),
    # everyone's mail comes from that domain — then the company name must
    # also appear in the subject or from-name to count.
    _ats = {"personio", "greenhouse", "lever", "workable", "recruitee",
            "ashby", "workday", "smartrecruiters", "teamtailor",
            "icims", "jobvite", "bamboohr"}
    for c in companies:
        dom = c.get("_domain", "")
        if dom and (sdom == dom or sdom.endswith("." + dom)):
            n = _norm(c["company"])
            # display name only — the address itself always contains the brand
            display = sender.split("<")[0]
            if n in _ats and n not in _norm(display + " " + subject):
                continue
            return c["company"], "sender domain"
    # 2/3. company name as a whole word in the From display name, then subject.
    # Companies that ARE ATS brands (e.g. applying to Personio itself) are
    # excluded here: other employers' mail arrives "via Personio" and would
    # misattribute — for those, only the sender-domain match above counts.
    ats_brands = {"personio", "greenhouse", "lever", "workable", "recruitee",
                  "ashby", "workday", "smartrecruiters", "teamtailor",
                  "icims", "jobvite", "bamboohr"}
    for field, label in ((sender_l, "from name"), (subject.lower(), "subject")):
        fn = _norm(field)
        cands = [c["company"] for c in companies
                 if re.search(rf"(?<![a-z0-9]){re.escape(_norm(c['company']))}(?![a-z0-9])", fn)
                 and len(_norm(c["company"])) >= 4
                 and _norm(c["company"]) not in ats_brands]
        if len(cands) == 1:
            return cands[0], label
        if len(cands) > 1:
            return "", f"ambiguous ({', '.join(cands[:3])})"
    return "", ""


def looks_recruiting(sender: str, subject: str) -> bool:
    s = (sender + " " + subject).lower()
    return (any(d in s for d in ATS_DOMAINS)
            or re.search(r"applicat|interview|recruit|talent|careers|hiring|"
                         r"position|job|candidate", s) is not None)


# ------------------------------------------------------------------- gmail IO
def _body_text(payload: dict) -> str:
    """Best-effort plain text from a Gmail message payload."""
    out: List[str] = []

    def walk(part):
        mt = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mt in ("text/plain", "text/html"):
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
            if mt == "text/html":
                from ..discovery.jobs import strip_html
                raw = strip_html(raw)
            out.append(raw)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(out)[:20000]


def fetch_recruiting_emails(gmail, days: int, max_results: int = 200) -> List[dict]:
    """List + fetch recent messages that look recruiting-related."""
    after = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y/%m/%d")
    # note: no category: filter — ATS mail often lands outside Primary
    q = (f"after:{after} "
         "(application OR interview OR recruiting OR careers OR candidate "
         "OR position OR opportunity)")
    resp = gmail.users().messages().list(
        userId="me", q=q, maxResults=min(max_results, 500)).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    msgs = []
    for mid in ids:
        msgs.append(gmail.users().messages().get(
            userId="me", id=mid, format="full").execute())
    return msgs


def analyze(gmail, companies: List[Dict[str, str]], days: int,
            max_results: int = 200) -> List[TrackedEmail]:
    """The read-only pipeline: fetch, match, classify."""
    applied = [dict(c, _domain=company_domain(c.get("career_url", "")))
               for c in companies]
    out: List[TrackedEmail] = []
    for msg in fetch_recruiting_emails(gmail, days, max_results):
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        if not looks_recruiting(sender, subject):
            continue
        company, how = match_company(sender, subject, applied)
        body = _body_text(msg.get("payload", {}))
        cls = classify(subject + "\n" + body)
        ts = int(msg.get("internalDate", "0")) / 1000
        date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        out.append(TrackedEmail(
            gmail_id=msg["id"], date=date, sender=sender[:120],
            subject=subject[:160], company=company,
            matched_by=how or "(unmatched)",
            classification=cls if company else "needs review",
        ))
    return out


# ------------------------------------------------------------------ log write
def write_log(sheet, emails: List[TrackedEmail]) -> int:
    """Append new classified emails to the log (dedup by Gmail ID) — the
    Supabase email_log table when the cloud store is configured (sheet may
    be None then), else the sheet's Email Log tab, where it also updates a
    company row's response cell when that cell is EMPTY."""
    from .. import store
    if store.enabled():
        existing = store.email_log_ids()
        new = [e for e in emails if e.gmail_id not in existing and e.company]
        return store.email_log_append([{
            "gmail_id": e.gmail_id, "date": e.date, "company": e.company,
            "classification": e.classification, "sender": e.sender,
            "subject": e.subject, "matched_by": e.matched_by,
        } for e in new])
    svc, cfg = sheet.svc, sheet.cfg
    sheet.ensure_tab(LOG_TAB, LOG_HEADER)
    existing = {r[6] for r in sheet.values(LOG_TAB)[1:] if len(r) > 6}
    new = [e for e in emails if e.gmail_id not in existing and e.company]
    if new:
        svc.spreadsheets().values().append(
            spreadsheetId=cfg.spreadsheet_id, range=f"'{LOG_TAB}'",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [[e.date, e.company, e.classification, e.sender,
                              e.subject, e.matched_by, e.gmail_id] for e in new]},
        ).execute()
    return len(new)
