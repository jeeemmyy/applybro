"""data/applications/queue.json — the staged candidate list produced by Run,
before the user chooses which jobs to actually pursue.

An entry here does NOT mean a workspace folder exists. Workspace creation
is a separate, explicit, lazy action (`tailor --url`, or the Review queue's
Tailor button in Phase P2) — see runner.py's module docstring for why this
matters (P1's runner used to auto-create a folder per fit job and spawned
dozens of throwaway directories per run).
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from ..tailoring.tailor import find_workspace_by_url
from ..paths import APPLICATIONS_DIR, CONTENT_YAML, QUEUE_JSON, QUEUE_META_JSON


def meta() -> dict:
    """Sidecar metadata about the queue itself — currently just when a Run
    last refreshed it. A separate file (not a schema change to queue.json's
    list, which several readers iterate directly) written ONLY by upsert():
    skip/unskip toggles rewrite queue.json too, and must not masquerade as
    a refresh."""
    from .. import store
    if store.enabled():
        return store.kv_get("queue_meta")
    if not os.path.exists(QUEUE_META_JSON):
        return {}
    try:
        with open(QUEUE_META_JSON) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(d: dict) -> None:
    from .. import store
    if store.enabled():
        store.kv_set("queue_meta", d)
        return
    with open(QUEUE_META_JSON, "w") as f:
        json.dump(d, f)


def load() -> List[dict]:
    from .. import store
    if store.enabled():
        return store.jobs_load()
    if not os.path.exists(QUEUE_JSON):
        return []
    try:
        with open(QUEUE_JSON) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save(items: List[dict]) -> None:
    from .. import store
    if store.enabled():
        store.jobs_save(items)
        return
    d = os.path.dirname(QUEUE_JSON)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(QUEUE_JSON, "w") as f:
        json.dump(items, f, indent=2)


# How long an applied job stays hidden from the Discover Jobs review queue
# even if a re-run finds it again. After this, it's treated as a fresh
# candidate (the listing may have been reposted, or is worth a second look).
APPLIED_HIDE_DAYS = 90


def _hide_expired(entry: dict) -> bool:
    """True once an applied-job hide (entry["applied_at"]) is older than
    APPLIED_HIDE_DAYS — a plain manual Skip has no applied_at and is never
    expired by this check."""
    applied_at = entry.get("applied_at")
    if not applied_at:
        return False
    import datetime
    try:
        d = datetime.date.fromisoformat(applied_at)
    except ValueError:
        return False   # unparsable date: keep the hide (conservative)
    return (datetime.date.today() - d).days > APPLIED_HIDE_DAYS


def upsert(new_items: List[dict], touch_meta: bool = True) -> int:
    """Add new entries / refresh existing ones, keyed by job URL. Returns
    how many were newly added (as opposed to just refreshed). Preserves an
    entry's "skipped" flag across a refresh (a re-run shouldn't un-skip
    something the user already dismissed) — UNLESS the skip was an
    applied-job hide (applied_at set) that's now older than
    APPLIED_HIDE_DAYS, in which case it's treated as a fresh candidate
    again and skipped resets to False.

    `touch_meta=False` is for single manually-added jobs: the meta file's
    last_refresh means "a Run refreshed the whole queue", and a hand-added
    entry must not masquerade as that."""
    existing = {it["url"]: it for it in load()}
    added = 0
    for it in new_items:
        prev = existing.get(it["url"])
        if prev is None:
            added += 1
        elif prev.get("skipped") and not _hide_expired(prev):
            it = {**it, "skipped": True}
            if prev.get("applied_at"):
                it["applied_at"] = prev["applied_at"]
        existing[it["url"]] = it
    save(list(existing.values()))
    if touch_meta:
        import datetime
        _save_meta({"last_refresh":
                    datetime.datetime.now().isoformat(timespec="seconds")})
    return added


# ------------------------------------------------- unified job lifecycle
# SaaS Phase 3: every job has ONE status + an append-only timeline instead
# of moving between product sections. Older entries carry only the legacy
# skipped/applied_at flags — job_status() maps those lazily, so no
# migration pass ever rewrites the store.
STATUSES = ("recommended", "saved", "applying", "applied", "interview",
            "rejected", "offer", "archived")
# Statuses that mean "the user is past reviewing this" — hidden from the
# review queue exactly like the old skipped flag.
DONE_STATUSES = ("applied", "interview", "rejected", "offer", "archived")


def job_status(entry: dict) -> str:
    s = entry.get("status")
    if s in STATUSES:
        return s
    if entry.get("applied_at"):
        return "applied"
    if entry.get("skipped"):
        return "archived"
    return "recommended"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def add_timeline(url: str, event: str, note: str = "") -> bool:
    items = load()
    for it in items:
        if it["url"] == url:
            it.setdefault("timeline", []).append(
                {"at": _now_iso(), "event": event, **({"note": note} if note else {})})
            save(items)
            return True
    return False


def set_status(url: str, status: str, note: str = "") -> bool:
    """Move a job to `status` + record it on the timeline. Keeps the legacy
    skipped flag in sync so every pre-Phase-3 reader (queue_with_status,
    upsert's skip-preservation, APPLIED_HIDE_DAYS) behaves identically."""
    if status not in STATUSES:
        return False
    items = load()
    for it in items:
        if it["url"] == url:
            it["status"] = status
            it["skipped"] = status in DONE_STATUSES
            if status == "applied" and not it.get("applied_at"):
                import datetime
                it["applied_at"] = datetime.date.today().isoformat()
            it.setdefault("timeline", []).append(
                {"at": _now_iso(), "event": status,
                 **({"note": note} if note else {})})
            save(items)
            return True
    return False


def all_jobs_with_status() -> List[dict]:
    """Every queue entry (including done ones) with its computed status —
    the unified Jobs tab's data. Sorted: active review first, then by score."""
    out = []
    for it in load():
        entry = dict(it)
        entry["status"] = job_status(it)
        out.append(entry)
    order = {s: i for i, s in enumerate(STATUSES)}
    out.sort(key=lambda e: (order.get(e["status"], 9), -(e.get("score") or 0)))
    return out


def update_entry(url: str, patch: dict) -> bool:
    """Merge `patch` into the queue entry for `url` (e.g. fit_before/fit_after
    after the Apply room re-assesses the tailored resume). Returns whether an
    entry was found."""
    items = load()
    for it in items:
        if it["url"] == url:
            it.update(patch)
            save(items)
            return True
    return False


def set_skipped(url: str, skipped: bool = True, applied_at: Optional[str] = None) -> bool:
    """applied_at (ISO date) marks WHY this is skipped — an applied-job hide,
    which expires after APPLIED_HIDE_DAYS (see upsert) — as opposed to a
    manual Skip, which has no applied_at and is preserved indefinitely."""
    items = load()
    for it in items:
        if it["url"] == url:
            it["skipped"] = skipped
            if applied_at is not None:
                it["applied_at"] = applied_at
            elif not skipped:
                it.pop("applied_at", None)
            save(items)
            return True
    return False


def workspace_status(d: str) -> Dict[str, bool]:
    """Reworded / verified / filled / Workday flags for a workspace,
    derived purely from its files — this is what drives the Review queue's
    per-card status chips."""
    ty = os.path.join(d, "content.tailored.yaml")
    pdf = os.path.join(d, "content.tailored.pdf")
    with open(CONTENT_YAML) as f:
        master = f.read()
    tailored = open(ty).read() if os.path.exists(ty) else ""
    jm = os.path.join(d, "job.md")
    url = ""
    if os.path.exists(jm):
        with open(jm) as f:
            m = re.search(r"^- URL: (\S+)", f.read(), re.M)
            url = m.group(1) if m else ""
    return {
        "reworded": bool(tailored) and tailored != master,
        "verified": (os.path.exists(pdf) and os.path.exists(ty)
                    and os.path.getmtime(pdf) >= os.path.getmtime(ty)),
        "filled": os.path.exists(os.path.join(d, "autofill_report.md")),
        "is_workday": "myworkdayjobs.com" in url,
    }


def queue_with_status(base_dir: str = APPLICATIONS_DIR) -> List[dict]:
    """Every non-skipped queue.json entry, enriched with its workspace path
    (if one has been created for that URL yet) and status flags."""
    out = []
    for it in load():
        if it.get("skipped"):
            continue
        entry = dict(it)
        d = find_workspace_by_url(it["url"], base_dir)
        if d:
            entry["workspace"] = d
            entry.update(workspace_status(d))
        else:
            entry["workspace"] = None
            entry.update({"reworded": False, "verified": False,
                          "filled": False, "is_workday": False})
        out.append(entry)
    out.sort(key=lambda e: e["score"], reverse=True)
    return out


def find_workspace(url: str, base_dir: str = APPLICATIONS_DIR) -> Optional[str]:
    return find_workspace_by_url(url, base_dir)


def create_workspace(url: str, cfg) -> Optional[str]:
    """Lazily create (or return the existing) workspace for one queued job —
    backs the Review queue's Tailor button with the same make_package/
    salary/history logic `cli.py`'s `tailor` command already uses."""
    entry = next((it for it in load() if it["url"] == url), None)
    if entry is None:
        return None

    from ..tailoring.tailor import make_package, upsert_auto_answer
    row = {
        "Company": entry["company"], "Title": entry["title"],
        "Location": entry["location"], "URL": entry["url"],
        "Fit %": entry.get("ai_score", entry["score"]),
        "AI Reason": entry.get("ai_reason", ""),
        "Matched Skills": ", ".join(entry.get("matched", [])),
        "Missing Skills": ", ".join(entry.get("missing", [])),
        "Req Years": entry.get("req_years") or "",
        "Meets Years": "yes" if entry.get("meets_years") else "NO",
        "Source": entry.get("source", ""),
        "Description": entry.get("description", ""),
    }
    d = make_package(row)

    try:
        from ..scoring.salary import estimate as est_salary
        req_years = row["Req Years"]
        sal = est_salary(row["Title"], row["Description"], row["Location"],
                         my_years=cfg.years_experience,
                         req_years=int(req_years) if str(req_years).isdigit() else 0)
        with open(os.path.join(d, "job.md"), "a") as f:
            f.write(f"\n## Salary estimate — {sal.render()}\n\n")
            for r in sal.reasoning:
                f.write(f"- {r}\n")
        for m in ("salary", "compensation"):
            upsert_auto_answer(d, m, sal.render())
    except Exception:
        pass

    if entry.get("has_history"):
        try:
            from .. import store
            from googleapiclient.discovery import build
            from ..tracking.insights import load_history, match_company_history, render_history
            if store.enabled():
                gmail = None
                if os.path.exists(getattr(cfg, "token_file", "")):
                    from ..google.sheets import get_credentials
                    gmail = build("gmail", "v1", credentials=get_credentials(cfg))
                hist_all = load_history(None, gmail)
            elif cfg.auth_mode == "oauth":
                from ..google.sheet_factory import get_sheet
                sheet = get_sheet(cfg)
                gmail = build("gmail", "v1", credentials=sheet.creds)
                hist_all = load_history(sheet, gmail)
            else:
                hist_all = {}
            hist = match_company_history(hist_all, entry["company"])
            if hist:
                with open(os.path.join(d, "job.md"), "a") as f:
                    f.write(render_history(hist, entry["company"]))
        except Exception:
            pass
    return d
