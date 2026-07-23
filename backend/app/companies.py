"""data/companies.json — the company list ApplyBro discovers jobs from.

This REPLACES the Google Sheet as the source of companies (decided
2026-07-12). Google is now optional and used only for Tracking (Gmail) and
the Applications ledger. A company is {name, careers_url, added, last_run,
last_jobs_found, notes}. Add one by name or by a bare URL — a URL alone is
enough; the company name is resolved from the page.

On first use, if there's no companies.json yet and a Google Sheet is still
configured, the existing companies tab is imported once so nothing is lost.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..paths import COMPANIES_JSON


def _now() -> str:
    return datetime.date.today().isoformat()


def load() -> List[dict]:
    from .. import store
    if store.enabled():
        return store.companies_load()
    if not os.path.exists(COMPANIES_JSON):
        return []
    try:
        with open(COMPANIES_JSON) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save(items: List[dict]) -> None:
    from .. import store
    if store.enabled():
        store.companies_save(items)
        return
    d = os.path.dirname(COMPANIES_JSON)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(COMPANIES_JSON, "w") as f:
        json.dump(items, f, indent=2)


def _key(name: str, url: str) -> str:
    """De-dupe key: careers host if present, else the lowercased name."""
    if url:
        try:
            host = urlparse(url).netloc.lower()
            if host:
                return host
        except ValueError:
            pass
    return name.strip().lower()


def add(name: str, careers_url: str, notes: str = "") -> dict:
    """Insert or update a company. Keyed by careers host / name so re-adding
    the same one edits it rather than duplicating."""
    name = (name or "").strip()
    careers_url = (careers_url or "").strip()
    items = load()
    k = _key(name, careers_url)
    for it in items:
        if _key(it.get("name", ""), it.get("careers_url", "")) == k:
            if name:
                it["name"] = name
            if careers_url:
                it["careers_url"] = careers_url
            if notes:
                it["notes"] = notes
            save(items)
            return it
    entry = {"name": name, "careers_url": careers_url, "notes": notes,
             "added": _now(), "last_run": None, "last_jobs_found": None}
    items.append(entry)
    save(items)
    return entry


def update(old_name: str, old_url: str, name: str, careers_url: str) -> Optional[dict]:
    """Edit a company in place, found by its OLD identity (the row the user
    clicked edit on). Both new values are taken as typed — no AI resolution;
    editing is for corrections, e.g. a careers URL that moved. Returns the
    updated entry, or None if the old identity isn't in the list."""
    name = (name or "").strip()
    careers_url = (careers_url or "").strip()
    if careers_url and not careers_url.startswith(("http://", "https://")):
        careers_url = "https://" + careers_url
    items = load()
    k = _key(old_name, old_url)
    for it in items:
        if _key(it.get("name", ""), it.get("careers_url", "")) == k:
            if name:
                it["name"] = name
            it["careers_url"] = careers_url
            save(items)
            return it
    return None


def remove(name: str, careers_url: str = "") -> bool:
    items = load()
    k = _key(name, careers_url)
    kept = [it for it in items
            if _key(it.get("name", ""), it.get("careers_url", "")) != k]
    if len(kept) == len(items):
        return False
    save(kept)
    return True


def mark_run(name: str, careers_url: str, jobs_found: int) -> None:
    items = load()
    k = _key(name, careers_url)
    for it in items:
        if _key(it.get("name", ""), it.get("careers_url", "")) == k:
            it["last_run"] = _now()
            it["last_jobs_found"] = jobs_found
            save(items)
            return


def as_run_companies() -> List[Dict[str, str]]:
    """Shape run_pipeline expects: [{company, career_url}]."""
    return [{"company": it.get("name", ""),
             "career_url": it.get("careers_url", "")}
            for it in load() if it.get("name") or it.get("careers_url")]


# ------------------------------------------------------------ URL -> name
def resolve_company(name: str, url: str, cfg) -> dict:
    """Turn whatever the user typed into {name, careers_url}. A bare URL is
    enough — the company name is read from the page (AI/site title). A name
    alone gets careers-URL candidates from resolve.py heuristics."""
    name = (name or "").strip()
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    if url and not name:
        name = _name_from_url(url, cfg)
    elif name and not url:
        url = _careers_from_name(name, cfg)
    return {"name": name or _host_word(url), "careers_url": url}


def _host_word(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    parts = [p for p in host.split(".") if p not in ("www", "jobs", "careers")]
    return parts[0].title() if parts else ""


def _name_from_url(url: str, cfg) -> str:
    """Company name from a careers URL: try the page's OG/site name via a
    small AI extraction, else fall back to the host word."""
    from ..discovery.ats import UA
    from ..net import safe_get
    r = safe_get(url, headers=UA, timeout=cfg.request_timeout)
    html = r.text if (r is not None and r.status_code < 400) else ""
    if html:
        from .. import ai
        prompt = (
            "From this careers/jobs page HTML, output ONLY the employer's "
            "company name — no other words. If unclear, output UNKNOWN.\n\n"
            f"URL: {url}\n\n{html[:6000]}")
        ok, out = ai.complete(prompt, feature="extraction", timeout=60)
        if ok:
            guess = (out or "").strip().splitlines()[0].strip() if out else ""
            if guess and guess.upper() != "UNKNOWN" and len(guess) <= 60:
                return guess
    return _host_word(url)


def _careers_from_name(name: str, cfg) -> str:
    """Best careers URL for a company name: the resolver's heuristic
    candidates (ATS slugs + domain guesses), first that loads."""
    from ..discovery.resolve import candidates, verify
    for cand in candidates(name, ""):
        if verify(cand, cfg.request_timeout):
            return cand
    return ""


# ------------------------------------------------------- one-time migration
def migrate_from_sheet_if_needed(cfg) -> Optional[int]:
    """If companies.json doesn't exist yet and a Google Sheet is configured,
    import its companies tab ONCE. Returns how many were imported, or None if
    nothing to do. Never overwrites an existing companies.json."""
    from .. import store
    if store.enabled():
        return None   # cloud store: seeded by `python -m backend.cli migrate-supabase`
    if os.path.exists(COMPANIES_JSON):
        return None
    if getattr(cfg, "auth_mode", "") != "oauth" or not getattr(cfg, "spreadsheet_id", ""):
        save([])   # create an empty file so we don't retry the sheet every boot
        return 0
    try:
        from ..google.sheet_factory import get_sheet
        rows = get_sheet(cfg).read_companies()
    except Exception:
        return None   # sheet hiccup: try again next boot rather than lose data
    items = []
    for r in rows:
        items.append({"name": r.get("company", ""),
                      "careers_url": r.get("career_url", ""), "notes": "",
                      "added": _now(), "last_run": None,
                      "last_jobs_found": None, "migrated_from_sheet": True})
    save(items)
    return len(items)
