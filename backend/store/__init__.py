"""Cloud storage layer — Supabase (Postgres via PostgREST), opt-in.

Mirrors backend/ai/'s discipline: ONE switch, config-driven, with the local
JSON files as the always-working fallback. When config.yaml has a `supabase:`
block (url + key), the domain stores (companies, jobs/queue, ui_state, the
applications ledger, the email log, tailored resumes) read and write Supabase
instead of local files / the Google Sheet. Remove the block and everything
falls back to local — nothing else in the app knows which backend is live.

The key is read from gitignored config.yaml or the SUPABASE_URL /
SUPABASE_KEY env vars; it must never appear in code, logs, errors, or git
(same rule as the OpenAI key).
"""
from __future__ import annotations

from .auth import (
    logged_in,
    login,
    logout,
    session,
    signup,
    user_email,
)
from .supabase_store import (
    StoreUnreachable,
    admin_list_users,
    base_resume_meta,
    base_resume_pdf,
    base_resume_set,
    ai_job_enqueue,
    ai_job_get,
    ai_job_claim,
    ai_job_complete,
    runner_heartbeat,
    runner_offline,
    runner_status,
    admin_update_policy,
    admin_update_profile,
    ai_policy_get,
    applications_log,
    applications_read_all,
    audit_append,
    is_admin,
    profile_get,
    companies_load,
    companies_save,
    email_log_append,
    email_log_ids,
    email_log_rows,
    enabled,
    jobs_load,
    jobs_save,
    kv_get,
    kv_set,
    resume_upload,
    resumes_get,
    resumes_set,
    user_settings_get,
    user_settings_set,
)

__all__ = [
    "enabled", "StoreUnreachable",
    "logged_in", "login", "logout", "signup", "session", "user_email",
    "profile_get", "ai_policy_get", "is_admin",
    "admin_list_users", "admin_update_profile", "admin_update_policy",
    "base_resume_set", "base_resume_meta", "base_resume_pdf",
    "ai_job_enqueue", "ai_job_get", "ai_job_claim", "ai_job_complete",
    "runner_heartbeat", "runner_offline", "runner_status",
    "audit_append",
    "companies_load", "companies_save",
    "jobs_load", "jobs_save",
    "kv_get", "kv_set",
    "applications_read_all", "applications_log",
    "email_log_rows", "email_log_ids", "email_log_append",
    "resume_upload", "resumes_get", "resumes_set",
    "user_settings_get", "user_settings_set",
]
