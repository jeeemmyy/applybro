"""Configuration loading for ApplyBro Layer 1.

All settings live in config.yaml (copy config.example.yaml). Secrets (the OAuth
client file and the stored token) are referenced by path, never inlined here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import os

import yaml

from .paths import CONFIG_EXAMPLE_YAML, CONFIG_YAML, CREDENTIALS_JSON, JOB_LOG_CSV, TOKEN_JSON

DEFAULT_ROLE_KEYWORDS = [
    "developer", "engineer", "full stack", "full-stack", "software",
    "backend", "back-end", "frontend", "front-end", "web",
]


@dataclass
class Config:
    # Google / Sheets
    auth_mode: str          # "public" (no credentials; log to local CSV) or "oauth"
    log_csv: str            # local log path used in public mode
    credentials_file: str
    token_file: str
    spreadsheet_id: str
    companies_tab: str
    company_name_column: str
    career_url_column: str
    log_tab: str
    # Discovery
    role_keywords: List[str]
    # Where the user will actually work. A REAL filter, applied after
    # collecting — but only to jobs whose location we genuinely read. Empty
    # list = no location filtering at all.
    locations: List[str]
    # Teams/functions the user is open to. Deliberately NOT a filter: the same
    # full-stack role sits under Engineering, Product, Technology, R&D or
    # Customer Success depending on the company, so this only ever WIDENS what
    # triage keeps. Never used to exclude.
    departments: List[str]
    max_jobs_per_company: int
    request_timeout: int
    # Drive a headless browser at career pages the HTTP adapters can't read
    # (client-rendered / bot-blocked / "Load more" lists). See
    # discovery/browser_discover.py.
    browser_fallback: bool
    # AI fit scoring (scoring/ai_score.py): reason about each job's fit with
    # Claude instead of keyword overlap, shown ALONGSIDE the keyword score.
    # Off by default — it adds a Claude call per relevant job to every Run.
    ai_fit: bool
    ai_model: str
    # Optional web search fallback for finding a broken/missing career page
    search_provider: str
    search_api_key: Optional[str]
    # Profile facts used by scoring (must stay true of the real resume)
    years_experience: float

    @staticmethod
    def load(path: str = CONFIG_YAML) -> "Config":
        if not os.path.exists(path):
            raise SystemExit(
                f"Config file '{path}' not found. Copy {CONFIG_EXAMPLE_YAML} to "
                f"{CONFIG_YAML} and fill in your spreadsheet_id and column names."
            )
        return Config._parse(path)

    @staticmethod
    def try_load(path: str = CONFIG_YAML) -> Optional["Config"]:
        """Same as load(), but returns None instead of raising SystemExit
        when the file is missing. For the dashboard server (backend.app),
        which must boot and respond cleanly (409, not a crash) even before
        the user has run through the /setup wizard — a raised SystemExit is
        a BaseException, not an Exception, so it would escape FastAPI's
        normal exception handling entirely if it were allowed into a route.
        The CLI keeps using load(), whose crash-with-a-message behavior is
        exactly right for a terminal tool."""
        if not os.path.exists(path):
            return None
        return Config._parse(path)

    @staticmethod
    def _parse(path: str) -> "Config":
        with open(path) as f:
            d = yaml.safe_load(f) or {}
        g = d.get("google", {}) or {}
        disc = d.get("discovery", {}) or {}
        s = d.get("search", {}) or {}
        kws = disc.get("role_keywords")
        if kws is None:
            kws = DEFAULT_ROLE_KEYWORDS
        return Config(
            auth_mode=str(g.get("auth_mode", "public")).lower(),
            log_csv=g.get("log_csv", JOB_LOG_CSV),
            credentials_file=g.get("credentials_file", CREDENTIALS_JSON),
            token_file=g.get("token_file", TOKEN_JSON),
            spreadsheet_id=g.get("spreadsheet_id", ""),
            companies_tab=g.get("companies_tab", "Companies"),
            company_name_column=g.get("company_name_column", "Company Name"),
            career_url_column=g.get("career_url_column", "careers_page"),
            log_tab=g.get("log_tab", "Job Log"),
            role_keywords=[str(k).lower() for k in kws],
            locations=[str(x).strip() for x in (disc.get("locations") or [])
                       if str(x).strip()],
            departments=[str(x).strip() for x in (disc.get("departments") or [])
                         if str(x).strip()],
            max_jobs_per_company=int(disc.get("max_jobs_per_company", 100)),
            request_timeout=int(disc.get("request_timeout", 20)),
            browser_fallback=bool(disc.get("browser_fallback", True)),
            ai_fit=bool(disc.get("ai_fit", False)),
            ai_model=str(disc.get("ai_model", "claude-sonnet-5")),
            search_provider=str(s.get("provider", "none")),
            search_api_key=os.environ.get("APPLYBRO_SEARCH_KEY") or s.get("api_key"),
            years_experience=float((d.get("profile", {}) or {}).get("years_experience", 5)),
        )
