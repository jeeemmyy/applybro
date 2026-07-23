"""Zero-credential mode for a PUBLIC Google Sheet.

Reads the companies via the sheet's CSV export endpoint (works for any sheet
shared as "anyone with the link"). Writing to the sheet itself requires OAuth,
so in this mode discovered jobs are appended to a LOCAL CSV (cfg.log_csv) with
the same columns as the OAuth log tab — importable into the sheet via
File > Import > Append rows, or upgradable to direct writes later by switching
config to auth_mode: oauth.
"""
from __future__ import annotations

import csv
import datetime
import io
import os
from typing import Dict, List, Set

import requests

from ..config import Config
from ..discovery.ats import UA
from ..discovery.jobs import Job
from ..paths import CONFIG_YAML
from .sheets import LOG_HEADER


class PublicSheet:
    def __init__(self, cfg: Config):
        if not cfg.spreadsheet_id:
            raise SystemExit(f"{CONFIG_YAML}: google.spreadsheet_id is empty.")
        self.cfg = cfg

    def read_companies(self) -> List[Dict[str, str]]:
        url = (f"https://docs.google.com/spreadsheets/d/"
               f"{self.cfg.spreadsheet_id}/export?format=csv")
        r = requests.get(url, headers=UA, timeout=self.cfg.request_timeout)
        if r.status_code != 200:
            raise SystemExit(
                f"Couldn't read the public sheet (HTTP {r.status_code}). "
                f"Is it still shared as 'anyone with the link'?")
        rows = list(csv.DictReader(io.StringIO(r.text)))
        name_c, url_c = self.cfg.company_name_column, self.cfg.career_url_column
        if rows and (name_c not in rows[0] or url_c not in rows[0]):
            raise SystemExit(
                f"Columns '{name_c}' / '{url_c}' not found in the sheet header: "
                f"{list(rows[0].keys())}")
        out = []
        for row in rows:
            name = (row.get(name_c) or "").strip()
            curl = (row.get(url_c) or "").strip()
            if name:
                out.append({"company": name, "career_url": curl})
        return out

    def existing_urls(self) -> Set[str]:
        if not os.path.exists(self.cfg.log_csv):
            return set()
        with open(self.cfg.log_csv, newline="") as f:
            return {(row.get("URL") or "").strip().lower()
                    for row in csv.DictReader(f) if row.get("URL")}

    def append_jobs(self, jobs: List[Job]) -> None:
        if not jobs:
            return
        new_file = not os.path.exists(self.cfg.log_csv)
        today = datetime.date.today().isoformat()
        with open(self.cfg.log_csv, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(LOG_HEADER)
            for j in jobs:
                w.writerow([j.company, j.title, j.location, j.source, j.url,
                            today, "discovered", (j.description or "")[:30000]])
