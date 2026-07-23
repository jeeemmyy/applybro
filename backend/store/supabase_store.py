"""Supabase backend for the store layer — plain PostgREST over `requests`
(no new SDK dependency, same reasoning as backend/ai/openai_provider.py).

Tables (see supabase/schema.sql): companies, jobs, kv, applications,
email_log, tailored_resumes. companies/jobs/kv carry the local files' JSON
shape in a `data` jsonb column, so this module is a transport, not a remodel.

Error policy: every helper raises RuntimeError with a short, key-free message
on failure. Callers that treat storage as best-effort (resume uploads) catch
it; core reads/writes let it propagate — a broken cloud store must be loud,
not silently fall back mid-flight and fork the data.
"""
from __future__ import annotations

import base64
import datetime
import os
import threading
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

from ..paths import CONFIG_YAML

_TIMEOUT = 25
# A single dropped connection used to destroy a whole extension scan — minutes
# of paid AI work thrown away because one PostgREST call hiccuped (live reports
# 2026-07-21). The cause there was a home router answering REFUSED to AAAA
# lookups, which makes getaddrinfo fail outright every so often; but ApplyBro
# ships to other people's networks, so the fix belongs HERE, not in anyone's
# DNS settings. Two layers:
#   1. one pooled Session — a warm connection needs no new DNS lookup at all,
#      so a scan's dozens of calls resolve the host once instead of per call;
#   2. urllib3 retries with backoff on connect/read failures (DNS included:
#      NameResolutionError is a connect error) and on 502/503/504.
_RETRIES = 3
_BACKOFF = 0.6

_SESSION: Optional["requests.Session"] = None
_SESSION_LOCK = threading.Lock()


def _session() -> "requests.Session":
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            s = requests.Session()
            retry = Retry(total=_RETRIES, connect=_RETRIES, read=_RETRIES,
                          backoff_factor=_BACKOFF,
                          status_forcelist=(502, 503, 504),
                          allowed_methods=None,   # POST/DELETE too: PostgREST
                          raise_on_status=False)  # writes here are replayable
            ad = HTTPAdapter(max_retries=retry, pool_connections=4,
                             pool_maxsize=8)
            s.mount("https://", ad)
            s.mount("http://", ad)
            _SESSION = s
        return _SESSION


def _conf() -> Dict[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if url and key:
        return {"url": url, "key": key}
    try:
        with open(CONFIG_YAML) as f:
            block = (yaml.safe_load(f) or {}).get("supabase", {}) or {}
    except OSError:
        block = {}
    return {"url": str(block.get("url", "") or "").strip(),
            "key": str(block.get("key", "") or "").strip()}


def enabled() -> bool:
    c = _conf()
    return bool(c["url"] and c["key"])


class StoreUnreachable(RuntimeError):
    """Supabase couldn't be reached (offline, DNS, timeout) — a transient
    environment problem, not a bug. server.py maps this to a clean 503."""


def _req(method: str, table: str, *, params: Optional[dict] = None,
         json_body=None, prefer: str = "") -> list:
    c = _conf()
    if not (c["url"] and c["key"]):
        raise RuntimeError("Supabase isn't configured (supabase: block in config.yaml).")
    # Data access is per-user (RLS keyed on auth.uid()), so every request
    # carries the signed-in user's JWT — the publishable key alone can
    # neither read nor write any row.
    from . import auth
    token = auth.access_token()
    if not token:
        raise RuntimeError("Not signed in — sign in to ApplyBro first.")
    headers = {"apikey": c["key"], "Authorization": f"Bearer {token}"}
    if prefer:
        headers["Prefer"] = prefer
    try:
        r = _session().request(method, f"{c['url']}/rest/v1/{table}",
                               params=params or {}, json=json_body,
                               headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as e:
        # Every retry above already failed. Name the likely cause rather than
        # blaming the user's internet — a resolver that intermittently refuses
        # a lookup looks exactly like this while everything else works.
        raise StoreUnreachable(
            f"Couldn't reach Supabase ({type(e).__name__}) after {_RETRIES} "
            "retries — this is usually a DNS or network hiccup between your "
            "machine and Supabase, not a problem with ApplyBro.")
    if r.status_code >= 400:
        # PostgREST errors are JSON with a message; never echo headers/key.
        try:
            msg = r.json().get("message", "")
        except ValueError:
            msg = ""
        hint = " — did you run supabase/schema.sql in the SQL editor?" \
            if r.status_code == 404 else ""
        raise RuntimeError(f"Supabase error {r.status_code} on {table}"
                           + (f": {msg}" if msg else "") + hint)
    if r.status_code == 204 or not r.content:
        return []
    try:
        out = r.json()
        return out if isinstance(out, list) else [out]
    except ValueError:
        return []


def _replace_all(table: str, pk: str, rows: List[dict]) -> None:
    """Mirror the JSON files' save() semantics (the file is rewritten whole,
    deletions included): clear the table, insert the new state. Two requests,
    not atomic — acceptable for a single-user tool with <1k rows."""
    _req("DELETE", table, params={pk: "not.is.null"})
    if rows:
        _req("POST", table, json_body=rows,
             prefer="resolution=merge-duplicates,return=minimal")


# ------------------------------------------------------------------ companies
def _company_key(item: dict) -> str:
    url = item.get("careers_url", "") or ""
    if url:
        try:
            host = urlparse(url).netloc.lower()
            if host:
                return host
        except ValueError:
            pass
    return (item.get("name", "") or "").strip().lower()


def companies_load() -> List[dict]:
    rows = _req("GET", "companies", params={"select": "data", "order": "key.asc"})
    return [r["data"] for r in rows]


def companies_save(items: List[dict]) -> None:
    _replace_all("companies", "key",
                 [{"key": _company_key(it), "data": it} for it in items
                  if _company_key(it)])


# ----------------------------------------------------------------------- jobs
def jobs_load() -> List[dict]:
    rows = _req("GET", "jobs", params={"select": "data", "order": "url.asc"})
    return [r["data"] for r in rows]


def jobs_save(items: List[dict]) -> None:
    _replace_all("jobs", "url",
                 [{"url": it["url"], "data": it} for it in items if it.get("url")])


# ------------------------------------------------------------------------- kv
def kv_get(key: str) -> dict:
    rows = _req("GET", "kv", params={"select": "data", "key": f"eq.{key}"})
    return rows[0]["data"] if rows else {}


def kv_set(key: str, data: dict) -> None:
    _req("POST", "kv", json_body={"key": key, "data": data},
         prefer="resolution=merge-duplicates,return=minimal")


# ------------------------------------------------------------------- ledger
def applications_read_all() -> List[Dict[str, str]]:
    """Same shape (and oldest-first order) autofill.applications.read_all
    returns from the sheet, so every consumer works unchanged."""
    rows = _req("GET", "applications", params={"order": "id.asc"})
    return [{
        "company": r.get("company", ""), "title": r.get("title", ""),
        "url": r.get("url", ""), "location": r.get("location", ""),
        "fit": r.get("fit", ""), "resume_file": r.get("resume_file", ""),
        "date_applied": r.get("date_applied", ""), "status": r.get("status", ""),
    } for r in rows]


def applications_log(row: Dict[str, str]) -> None:
    _req("POST", "applications", json_body={
        "company": row.get("company", ""), "title": row.get("title", ""),
        "url": row.get("url", ""), "location": row.get("location", ""),
        "fit": str(row.get("fit", "")), "resume_file": row.get("resume_file", ""),
        "date_applied": row.get("date_applied", ""),
        "status": row.get("status", "applied"),
    }, prefer="return=minimal")


# ----------------------------------------------------------------- email log
def email_log_rows() -> List[dict]:
    return _req("GET", "email_log", params={"order": "date.asc"})


def email_log_ids() -> set:
    rows = _req("GET", "email_log", params={"select": "gmail_id"})
    return {r["gmail_id"] for r in rows}


def email_log_append(entries: List[dict]) -> int:
    """Insert classified emails, ignoring gmail_ids already logged."""
    if not entries:
        return 0
    _req("POST", "email_log", json_body=entries,
         prefer="resolution=ignore-duplicates,return=minimal")
    return len(entries)


# ------------------------------------------- accounts / admin / AI policy
def profile_get() -> dict:
    """The signed-in user's account row: {status, subscribed, email}.
    Missing row (schema_v3 not run yet) => {} — callers treat that as
    'no gating configured' so pre-v3 databases keep working."""
    try:
        rows = _req("GET", "profiles",
                    params={"select": "email,status,subscribed"})
    except RuntimeError:
        return {}
    return rows[0] if rows else {}


def ai_policy_get() -> dict:
    """The ADMIN-set AI policy for the signed-in user (own row, read-only
    under RLS). {} when schema_v3 isn't applied yet."""
    try:
        rows = _req("GET", "ai_policy", params={"select": "*"})
    except RuntimeError:
        return {}
    return rows[0] if rows else {}


def is_admin() -> bool:
    try:
        rows = _req("GET", "admins", params={"select": "user_id"})
    except RuntimeError:
        return False
    return bool(rows)


def admin_list_users() -> List[dict]:
    """Every user's profile + policy, for the Admin page. RLS makes this
    return ONLY the caller's own rows unless they're an admin — the backend
    still checks is_admin() first for a clear 403."""
    profiles = _req("GET", "profiles",
                    params={"select": "user_id,email,status,subscribed,created_at",
                            "order": "created_at.asc"})
    policies = {p["user_id"]: p for p in _req("GET", "ai_policy",
                                              params={"select": "*"})}
    out = []
    for p in profiles:
        pol = dict(policies.get(p["user_id"], {}))
        # The key itself never leaves the server response to the UI —
        # only whether one is set.
        pol["has_openai_key"] = bool(pol.pop("openai_api_key", ""))
        out.append({**p, "policy": pol})
    return out


def admin_update_profile(user_id: str, patch: dict) -> None:
    allowed = {k: v for k, v in patch.items() if k in ("status", "subscribed")}
    if allowed:
        _req("PATCH", "profiles", params={"user_id": f"eq.{user_id}"},
             json_body=allowed, prefer="return=minimal")


def admin_update_policy(user_id: str, patch: dict) -> None:
    allowed = {k: v for k, v in patch.items()
               if k in ("allow_openai", "allow_claude_cli", "primary_provider",
                        "fallback_provider", "models", "openai_api_key")}
    if allowed:
        _req("PATCH", "ai_policy", params={"user_id": f"eq.{user_id}"},
             json_body=allowed, prefer="return=minimal")


def audit_append(action: str, target_user: str = "", detail: Optional[dict] = None) -> None:
    """Best-effort admin audit entry — an audit hiccup never blocks the
    action itself (it already happened under RLS)."""
    try:
        body = {"action": action, "detail": detail or {}}
        if target_user:
            body["target_user"] = target_user
        _req("POST", "audit_log", json_body=body, prefer="return=minimal")
    except RuntimeError:
        pass


# --------------------------------------------- Claude runner queue (SCAFFOLD)
# Activated at hosting time (schema_v5.sql + docs/DEPLOY.md). Untested until
# the backend is remote and the table exists; follows the same _req pattern
# as everything above.
def ai_job_enqueue(feature: str, model: str, prompt: str) -> int:
    from . import auth
    rows = _req("POST", "ai_jobs",
                json_body={"user_id": auth.user_id(), "feature": feature,
                           "model": model, "prompt": prompt},
                prefer="return=representation")
    return rows[0]["id"] if rows else 0


def ai_job_get(job_id: int) -> dict:
    rows = _req("GET", "ai_jobs", params={"id": f"eq.{job_id}", "select": "*"})
    return rows[0] if rows else {}


def ai_job_claim(claimed_by: str) -> Optional[dict]:
    """Claim the oldest queued job (admin/runner only). Best-effort atomicity
    via a status-guarded PATCH; a single runner needs no stronger locking."""
    q = _req("GET", "ai_jobs", params={"status": "eq.queued",
             "order": "created_at.asc", "limit": "1", "select": "*"})
    if not q:
        return None
    job = q[0]
    updated = _req("PATCH", "ai_jobs",
                   params={"id": f"eq.{job['id']}", "status": "eq.queued"},
                   json_body={"status": "claimed", "claimed_by": claimed_by,
                              "claimed_at": datetime.datetime.now(
                                  datetime.timezone.utc).isoformat()},
                   prefer="return=representation")
    return updated[0] if updated else None   # lost the race -> try again


def ai_job_complete(job_id: int, ok: bool, output: str) -> None:
    _req("PATCH", "ai_jobs", params={"id": f"eq.{job_id}"},
         json_body={"status": "done" if ok else "error",
                    "result": output if ok else "",
                    "error": "" if ok else output[:500],
                    "finished_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat()},
         prefer="return=minimal")


def runner_heartbeat(who: str, current_job: Optional[int] = None) -> None:
    _req("POST", "runner_status",
         json_body={"id": 1, "online": True, "note": who,
                    "current_job": current_job,
                    "last_beat": datetime.datetime.now(
                        datetime.timezone.utc).isoformat()},
         prefer="resolution=merge-duplicates,return=minimal")


def runner_offline(who: str) -> None:
    _req("PATCH", "runner_status", params={"id": "eq.1"},
         json_body={"online": False, "note": who}, prefer="return=minimal")


def runner_status() -> dict:
    try:
        rows = _req("GET", "runner_status", params={"id": "eq.1", "select": "*"})
    except RuntimeError:
        return {}
    return rows[0] if rows else {}


# ------------------------------------------------------- per-user settings
def user_settings_get() -> dict:
    """The signed-in user's app settings blob, e.g. {"ai": {...}}."""
    rows = _req("GET", "user_settings", params={"select": "data"})
    return rows[0]["data"] if rows else {}


def user_settings_set(data: dict) -> None:
    from . import auth
    _req("POST", "user_settings",
         json_body={"user_id": auth.user_id(), "data": data},
         prefer="resolution=merge-duplicates,return=minimal")


# -------------------------------------------------- resume master + profile
def resumes_get() -> dict:
    """{"content_yaml": ..., "profile_yaml": ...} for the signed-in user
    (empty strings when nothing has been uploaded yet)."""
    rows = _req("GET", "resumes",
                params={"select": "content_yaml,profile_yaml"})
    return rows[0] if rows else {}


def resumes_set(content_yaml: Optional[str] = None,
                profile_yaml: Optional[str] = None) -> None:
    """Upsert only the provided fields — PostgREST's merge-duplicates updates
    just the columns present in the payload, so pushing one file never
    clobbers the other."""
    from . import auth
    body: dict = {"user_id": auth.user_id()}
    if content_yaml is not None:
        body["content_yaml"] = content_yaml
    if profile_yaml is not None:
        body["profile_yaml"] = profile_yaml
    if len(body) == 1:
        return
    _req("POST", "resumes", json_body=body,
         prefer="resolution=merge-duplicates,return=minimal")


# ------------------------------------------------------- base resume (PDF)
def base_resume_set(filename: str, pdf_bytes: bytes) -> None:
    from . import auth
    _req("POST", "base_resumes", json_body={
        "user_id": auth.user_id(), "filename": filename,
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, prefer="resolution=merge-duplicates,return=minimal")


def base_resume_meta() -> dict:
    """{filename, uploaded_at} of the stored base resume (no PDF payload —
    the UI only needs to show what's on file)."""
    try:
        rows = _req("GET", "base_resumes",
                    params={"select": "filename,uploaded_at"})
    except RuntimeError:
        return {}   # schema_v4 not applied yet
    return rows[0] if rows else {}


def base_resume_pdf() -> bytes:
    rows = _req("GET", "base_resumes", params={"select": "pdf_base64"})
    if not rows:
        return b""
    return base64.b64decode(rows[0].get("pdf_base64", "") or "")


# ------------------------------------------------------------ tailored PDFs
def resume_upload(job_url: str, filename: str, pdf_bytes: bytes) -> None:
    """Store one job's tailored PDF (upsert), purging expired copies first —
    the lazy half of the 24h TTL (pg_cron is the scheduled half)."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=24)).isoformat()
    _req("DELETE", "tailored_resumes", params={"created_at": f"lt.{cutoff}"})
    _req("POST", "tailored_resumes", json_body={
        "job_url": job_url, "filename": filename,
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, prefer="resolution=merge-duplicates,return=minimal")
