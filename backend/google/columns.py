"""Column-name guessing for the Target-list picker UI.

Pure UI convenience — not part of the core discovery engine. Given a sheet
tab's header row, guesses which column holds the company name and which
holds the careers URL, so switching tabs in the dashboard doesn't require
the user to know exact header text.
"""
from __future__ import annotations

from typing import List, Optional

COMPANY_HINTS = ["company"]
URL_HINTS = ["careers", "career", "url", "site"]


def guess_column(headers: List[str], hints: List[str]) -> Optional[str]:
    for hint in hints:
        for h in headers:
            if hint in h.lower():
                return h
    return headers[0] if headers else None


def guess_columns(headers: List[str]) -> dict:
    return {
        "company_name_column": guess_column(headers, COMPANY_HINTS),
        "career_url_column": guess_column(headers, URL_HINTS),
    }
