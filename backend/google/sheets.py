"""Google Sheets I/O over OAuth.

Reads the companies tab (company name + career URL, located by header name) and
appends discovered jobs to the log tab (created with a header if missing).
The same spreadsheet is both the input and the log, per project design.
"""
from __future__ import annotations

from typing import Dict, List, Set
import datetime
import os

from ..config import Config
from ..discovery.jobs import Job
from ..paths import CONFIG_YAML

# One consent covers everything ApplyBro will ever need:
# sheet read/write (Layers 1-3 logging) + read-only Gmail (Layer 4 tracking).
# gmail.readonly can NEVER send, delete, or modify email.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
]
LOG_HEADER = ["Company", "Title", "Location", "Source", "URL",
              "Date Found", "Status", "Description"]


def get_credentials(cfg: Config):
    """OAuth credential acquisition, independent of any spreadsheet: loads/
    refreshes token.json, or runs the consent flow (opens the user's default
    browser, blocks until they consent) when no usable token exists yet.
    Standalone — NOT a Sheet method — because the /setup wizard's
    connect-account step runs BEFORE a spreadsheet id exists, so it must not
    go through Sheet.__init__'s spreadsheet_id requirement."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    tf = cfg.token_file
    if os.path.exists(tf):
        creds = Credentials.from_authorized_user_file(tf, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(cfg.credentials_file):
                raise SystemExit(
                    f"OAuth client file '{cfg.credentials_file}' not found. "
                    f"Create an OAuth 2.0 Client ID (Desktop app) in Google Cloud, "
                    f"enable the Sheets API, and download it to that path."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                cfg.credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(tf, "w") as f:
            f.write(creds.to_json())
    return creds


class Sheet:
    def __init__(self, cfg: Config):
        if not cfg.spreadsheet_id:
            raise SystemExit(f"{CONFIG_YAML}: google.spreadsheet_id is empty.")
        self.cfg = cfg
        self.svc = self._service()

    # -- auth ---------------------------------------------------------------
    def _service(self):
        from googleapiclient.discovery import build
        self.creds = get_credentials(self.cfg)  # exposed so Layer 4 can
        return build("sheets", "v4",            # build the gmail service
                     credentials=self.creds, cache_discovery=False)

    # -- helpers ------------------------------------------------------------
    def _values(self, rng: str) -> List[List[str]]:
        return (self.svc.spreadsheets().values()
                .get(spreadsheetId=self.cfg.spreadsheet_id, range=rng)
                .execute().get("values", []))

    def _tab_names(self) -> List[str]:
        meta = self.svc.spreadsheets().get(
            spreadsheetId=self.cfg.spreadsheet_id).execute()
        return [s["properties"]["title"] for s in meta["sheets"]]

    # -- read companies -----------------------------------------------------
    def read_companies(self, extra_cols: List[str] = ()) -> List[Dict[str, str]]:
        """Company + career URL per row; `extra_cols` adds other columns by
        header name (lowercased key), plus '_row' = 1-based sheet row number."""
        rows = self._values(f"'{self.cfg.companies_tab}'")
        if not rows:
            raise SystemExit(f"Companies tab '{self.cfg.companies_tab}' is empty.")
        header = [h.strip() for h in rows[0]]

        def col_index(name: str):
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
            return None

        ci = col_index(self.cfg.company_name_column)
        ui = col_index(self.cfg.career_url_column)
        if ci is None or ui is None:
            raise SystemExit(
                f"Couldn't find columns '{self.cfg.company_name_column}' and "
                f"'{self.cfg.career_url_column}' in tab '{self.cfg.companies_tab}'. "
                f"Header row is: {header}"
            )
        extras = {c: col_index(c) for c in extra_cols}
        out = []
        for rn, r in enumerate(rows[1:], start=2):
            name = r[ci].strip() if ci < len(r) else ""
            url = r[ui].strip() if ui < len(r) else ""
            if name:
                d = {"company": name, "career_url": url, "_row": rn}
                for c, idx in extras.items():
                    d[c.lower()] = (r[idx].strip()
                                    if idx is not None and idx < len(r) else "")
                out.append(d)
        return out

    # -- generic tab helpers --------------------------------------------------
    def values(self, tab: str) -> List[List[str]]:
        """All values of a tab ([] if the tab doesn't exist)."""
        if tab not in self._tab_names():
            return []
        return self._values(f"'{tab}'")

    def header(self, tab: str) -> List[str]:
        """Just row 1 of `tab` ([] if missing/empty). Use this instead of
        values(tab)[0] when all you need is the header row — values() pulls
        the ENTIRE tab, which for a log tab with thousands of rows and long
        description cells means megabytes fetched just to read column names."""
        if tab not in self._tab_names():
            return []
        rows = self._values(f"'{tab}'!1:1")
        return rows[0] if rows else []

    def ensure_tab(self, tab: str, header: List[str]) -> None:
        """Create `tab` with `header` if missing (idempotent)."""
        if tab not in self._tab_names():
            self.svc.spreadsheets().batchUpdate(
                spreadsheetId=self.cfg.spreadsheet_id,
                body={"requests": [{"addSheet": {
                    "properties": {"title": tab}}}]},
            ).execute()
        if not self._values(f"'{tab}'!1:1"):
            self.svc.spreadsheets().values().update(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=f"'{tab}'!A1",
                valueInputOption="RAW",
                body={"values": [header]},
            ).execute()

    # -- log ----------------------------------------------------------------
    def _ensure_log(self) -> None:
        self.ensure_tab(self.cfg.log_tab, LOG_HEADER)

    def existing_urls(self) -> Set[str]:
        """URLs already logged (column E), for de-duplication."""
        if self.cfg.log_tab not in self._tab_names():
            return set()
        rows = self._values(f"'{self.cfg.log_tab}'!E2:E")
        return {r[0].strip().lower() for r in rows if r and r[0].strip()}

    def append_jobs(self, jobs: List[Job]) -> None:
        if not jobs:
            return
        self._ensure_log()
        today = datetime.date.today().isoformat()
        values = [[j.company, j.title, j.location, j.source, j.url,
                   today, "discovered", (j.description or "")[:30000]] for j in jobs]
        self.svc.spreadsheets().values().append(
            spreadsheetId=self.cfg.spreadsheet_id,
            range=f"'{self.cfg.log_tab}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()

    # -- generic append/update (used by the Apply room's sheet logging) -----
    def append_row(self, tab: str, header: List[str], row: List) -> None:
        """Append one row to `tab`, creating it with `header` if missing."""
        self.ensure_tab(tab, header)
        self.svc.spreadsheets().values().append(
            spreadsheetId=self.cfg.spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    def update_company_columns(self, company: str, updates: Dict[str, str]) -> bool:
        """Best-effort: set `updates` (column-name -> value) on the companies
        tab's row for `company`, matching column names case-insensitively.
        Returns False (no-op) if the company or any requested column isn't
        found — the target-list tab schema varies (some tabs track
        applied/applied_date, some don't), so this must never raise."""
        rows = self._values(f"'{self.cfg.companies_tab}'")
        if not rows:
            return False
        header = [h.strip() for h in rows[0]]

        def col_index(name: str):
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
            return None

        ci = col_index(self.cfg.company_name_column)
        if ci is None:
            return False
        row_idx = next((rn for rn, r in enumerate(rows[1:], start=2)
                        if ci < len(r) and r[ci].strip().lower() == company.strip().lower()),
                       None)
        if row_idx is None:
            return False

        col_indices = {k: col_index(k) for k in updates}
        if any(v is None for v in col_indices.values()):
            return False  # a requested column doesn't exist in this tab

        data = [{"range": f"'{self.cfg.companies_tab}'!"
                          f"{self._col_letter(idx)}{row_idx}",
                "values": [[updates[k]]]}
               for k, idx in col_indices.items()]
        self.svc.spreadsheets().values().batchUpdate(
            spreadsheetId=self.cfg.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        return True

    @staticmethod
    def _col_letter(index: int) -> str:
        """0-based column index -> spreadsheet column letters (0 -> A)."""
        letters = ""
        n = index + 1
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return letters
