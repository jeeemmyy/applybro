"""ApplyBro dashboard backend.

Wraps the existing backend modules (config/sheets/discover/score/tailor/...)
behind a small local HTTP API for the React frontend. This file contains NO
new business logic beyond orchestration + I/O for the dashboard itself —
the actual discovery/scoring/tailoring/autofill/tracking logic all lives in
the backend feature packages, untouched (see runner.py for the Run-screen fusion).

Run with:  python -m backend.cli start   (also opens the dedicated Chrome
window via launch.py) or, for API-only testing:
  uvicorn backend.app.server:app --port 8765
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import threading
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ui_state
from .setup import SetupRequiredError, require_config, safe_get_sheet
from ..config import Config
from ..google.columns import guess_columns
from ..paths import APPLICATIONS_DIR, CONFIG_YAML, PROFILE_YAML, TOKEN_JSON
from ..store import StoreUnreachable

CONFIG_PATH = CONFIG_YAML
APPLICATIONS_DIR_ABS = os.path.abspath(APPLICATIONS_DIR)
# backend/app/server.py -> up 3 dirnames = the project root, where
# frontend/ lives (NOT 2 — server.py moved one package level deeper than
# its original backend/server.py location; this is exactly the kind of
# path bug a mechanical file move creates if you don't recompute it).
FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dist")

app = FastAPI(title="ApplyBro")
app.state.browser_context = None  # set by launch.py once the dedicated
                                   # Chrome window exists; used by later phases


@app.exception_handler(SetupRequiredError)
async def _setup_required_handler(request, exc: SetupRequiredError) -> JSONResponse:
    """Every route that needs config.yaml / a Sheet connection / profile.yaml
    raises SetupRequiredError instead of letting a bare SystemExit or
    FileNotFoundError escape — this is the ONE place that turns it into the
    clean, structured 409 the frontend's /setup redirect logic looks for
    (never a 500 stack trace)."""
    return JSONResponse(status_code=409, content={
        "error": "setup_required", "missing": exc.missing, "detail": exc.message,
    })


@app.exception_handler(StoreUnreachable)
async def _store_unreachable_handler(request, exc: StoreUnreachable) -> JSONResponse:
    """Supabase being unreachable (wifi drop, DNS, VPN) is an environment
    problem, not a bug — one clean 503 with a readable message instead of a
    500 stack trace on every endpoint (2026-07-17: an offline stretch spammed
    dozens of tracebacks and the extension showed 'Internal Server Error')."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# State for the /api/setup/auth/* endpoints — the OAuth consent flow blocks
# (it runs a local redirect server and waits on the user's browser), so it's
# kicked off in a background thread like Run/Apply; this dict is polled
# instead of streamed since it's a one-shot yes/no, not a progress rail.
AUTH_STATE: Dict[str, Any] = {"running": False, "error": None, "done": False}


# --------------------------------------------------------------------- state
class RunState:
    """A single global event buffer covers every run for the process
    lifetime, so a naive after=0 subscription replays EVERY prior run's
    events too — not just the current one. `current_run_start_id` is the
    cursor right before the active run began; both starting a run (from the
    frontend, which has this value fresh) and loading the Run page mid-run
    (a page refresh, which does NOT have it — it must ask the server) use
    this same cursor so they see exactly this run's progress, nothing else."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.events: List[Dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.current_run_start_id = 0

    def begin(self) -> int:
        """Atomically claim the run slot and record its start cursor. Raises
        RuntimeError if a run is already in progress."""
        with self.lock:
            if self.running:
                raise RuntimeError("A run is already in progress.")
            self.running = True
            self.current_run_start_id = self.events[-1]["id"] if self.events else 0
            return self.current_run_start_id

    def push(self, event: Dict[str, Any]) -> None:
        with self.lock:
            event = dict(event)
            event["id"] = next(self._ids)
            self.events.append(event)
            if len(self.events) > 2000:
                self.events = self.events[-2000:]

    def since(self, after: int) -> List[Dict[str, Any]]:
        with self.lock:
            return [e for e in self.events if e["id"] > after]

    def max_id(self) -> int:
        with self.lock:
            return self.events[-1]["id"] if self.events else 0


RUN = RunState()
# A second, independent event buffer for the Apply room — kept separate from
# RUN so a discovery run and an apply batch can be watched/refreshed
# independently without either's events contaminating the other's stream.
APPLY = RunState()


def _sse_response(state: "RunState", after: int) -> StreamingResponse:
    async def gen():
        last = after
        while True:
            batch = state.since(last)
            for e in batch:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            if not batch:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ raw yaml
def _load_raw_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        # 409 setup_required, not a 500 — config.yaml simply doesn't exist
        # yet if /setup hasn't been completed.
        raise SetupRequiredError(
            ["config_yaml"],
            f"{CONFIG_PATH} not found — finish setup at /setup first.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def _save_raw_config(d: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(d, f, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------------- models
class SourceUpdate(BaseModel):
    tab: str
    company_name_column: str
    career_url_column: str


class RunRequest(BaseModel):
    rows: str = ""
    threshold: Optional[int] = None
    min_reqs: int = 4
    # Company names to scan (case-insensitive). Empty/None = all companies.
    # This is how the Companies table's per-company "Refresh jobs" works.
    companies: Optional[List[str]] = None


class UrlRequest(BaseModel):
    url: str


class CommentRequest(BaseModel):
    url: str
    text: str


class ApplyStartRequest(BaseModel):
    urls: List[str]


class MarkRequest(BaseModel):
    url: str
    status: str  # "applied" | "skipped"


# ----------------------------------------------------------------- /api/source
@app.get("/api/source")
def get_source() -> dict:
    cfg = require_config()
    state = ui_state.load()
    result = {
        "spreadsheet_id": cfg.spreadsheet_id,
        "auth_mode": cfg.auth_mode,
        "current": {
            "tab": cfg.companies_tab,
            "company_name_column": cfg.company_name_column,
            "career_url_column": cfg.career_url_column,
        },
        "tabs": [],
        "headers_by_tab": {},
        "company_count": 0,
        "last_range_end": state.get("last_range_end", 0),
        "fit_threshold": state.get("fit_threshold", 60),
    }
    try:
        # safe_get_sheet, not get_sheet directly: Sheet()'s constructor
        # raises a bare SystemExit for a missing credentials.json / empty
        # spreadsheet_id (it's written for the CLI) — that's a
        # BaseException, so the `except Exception` right below would NOT
        # have caught it, and it would have escaped this route as an
        # unhandled crash instead of the graceful partial result this
        # function is otherwise designed to return.
        sheet = safe_get_sheet(cfg)
        if cfg.auth_mode == "oauth":
            tabs = sheet._tab_names()
            result["tabs"] = tabs
            # header() fetches ONLY row 1 of each tab — sheet.values(t) would
            # pull the ENTIRE tab (for Job Log that's ~1,700 rows with up to
            # 30KB description cells) just to read column names.
            result["headers_by_tab"] = {t: sheet.header(t) for t in tabs}
        companies = sheet.read_companies()
        result["company_count"] = len(companies)
    except Exception as e:
        result["error"] = str(e)
    return result


@app.put("/api/source")
def put_source(body: SourceUpdate) -> dict:
    raw = _load_raw_config()
    g = raw.setdefault("google", {})
    g["companies_tab"] = body.tab
    g["company_name_column"] = body.company_name_column
    g["career_url_column"] = body.career_url_column
    _save_raw_config(raw)
    return {"ok": True}


@app.get("/api/source/guess-columns")
def guess_columns_for_tab(tab: str) -> dict:
    cfg = require_config()
    if cfg.auth_mode != "oauth":
        raise HTTPException(400, "Column auto-guess needs auth_mode: oauth")
    sheet = safe_get_sheet(cfg)
    headers = sheet.header(tab)
    return guess_columns(headers)


# ------------------------------------------------------------------- /api/run
@app.post("/api/run")
def start_run(body: RunRequest) -> dict:
    try:
        start_after = RUN.begin()
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    cfg = require_config()
    state = ui_state.load()
    threshold = body.threshold if body.threshold is not None else state.get("fit_threshold", 60)

    def worker() -> None:
        try:
            from .runner import run_pipeline
            run_pipeline(cfg, body.rows, threshold, body.min_reqs,
                         on_event=RUN.push, only_companies=body.companies)
            # remember where this range ended, for "continue from yesterday"
            if body.rows:
                try:
                    from .runner import parse_rows
                    from . import companies as company_store
                    n = len(company_store.load())
                    _, hi = parse_rows(body.rows, n)
                    ui_state.save({"last_range_end": hi})
                except Exception:
                    pass
        except Exception as e:
            RUN.push({"type": "run_error", "error": str(e)})
        finally:
            with RUN.lock:
                RUN.running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "after": start_after}


@app.post("/api/run/skip-company")
def skip_company() -> dict:
    """Abandon the company currently being discovered/assessed (stages
    nothing for it) and move to the next. Cancels any in-flight AI call."""
    from .runner import request_skip_company
    return {"ok": request_skip_company()}


@app.post("/api/run/stop")
def stop_run() -> dict:
    """Stop the whole discovery run after the current company."""
    from .runner import request_stop_run
    return {"ok": request_stop_run()}


@app.get("/api/run/status")
def run_status() -> dict:
    with RUN.lock:
        # If a run is active, `after` is that run's own start cursor (so a
        # mid-run page refresh replays exactly this run's progress, not the
        # whole session's history). If idle, `after` is simply "now" — a
        # fresh subscription should only show whatever runs next.
        after = RUN.current_run_start_id if RUN.running else (
            RUN.events[-1]["id"] if RUN.events else 0)
        return {"running": RUN.running, "after": after}


@app.get("/api/events/stream")
async def events_stream(after: int = 0):
    return _sse_response(RUN, after)


# --------------------------------------------------------------- /api/queue
@app.get("/api/queue")
def get_queue() -> dict:
    from . import queue as queue_store
    return {"jobs": queue_store.queue_with_status(),
            "last_refresh": queue_store.meta().get("last_refresh")}


class CompanyAdd(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None


class CompanyRemove(BaseModel):
    name: str
    careers_url: Optional[str] = ""


@app.get("/api/companies")
def list_companies() -> dict:
    """The company list, enriched with per-company queue stats so the
    Companies table can show relevant/applied counts without a second call.
    Counts come from the LOCAL queue.json only — no network."""
    from . import companies as company_store
    from . import queue as queue_store
    cfg = Config.try_load()
    if cfg is not None:
        company_store.migrate_from_sheet_if_needed(cfg)

    relevant: Dict[str, int] = {}
    applied: Dict[str, int] = {}
    for it in queue_store.load():
        key = (it.get("company") or "").strip().lower()
        if not key:
            continue
        if it.get("applied_at"):
            applied[key] = applied.get(key, 0) + 1
        elif not it.get("skipped"):
            relevant[key] = relevant.get(key, 0) + 1

    out = []
    for c in company_store.load():
        key = (c.get("name") or "").strip().lower()
        out.append({**c,
                    "relevant_jobs": relevant.get(key, 0),
                    "applied_jobs": applied.get(key, 0)})
    return {"companies": out}


@app.post("/api/companies")
def add_company(body: CompanyAdd) -> dict:
    """Add a company by name and/or a bare careers URL. A URL alone is enough
    — the name is resolved from the page (AI). Slow-ish when resolving, so the
    frontend shows a spinner."""
    from . import companies as company_store
    if not (body.name or body.url):
        raise HTTPException(400, "Give a company name or a URL.")
    cfg = require_config()
    resolved = company_store.resolve_company(body.name or "", body.url or "", cfg)
    if not resolved["name"] and not resolved["careers_url"]:
        raise HTTPException(400, "Couldn't make sense of that — try a name or a full URL.")
    entry = company_store.add(resolved["name"], resolved["careers_url"])
    return {"company": entry}


class CompanyEdit(BaseModel):
    old_name: str
    old_careers_url: Optional[str] = ""
    name: str
    careers_url: str


@app.put("/api/companies")
def edit_company(body: CompanyEdit) -> dict:
    """Edit a company's name / careers URL in place (no AI resolution — the
    user is correcting facts). A rename also updates the company field on its
    staged queue entries so they don't orphan under the old name."""
    from . import companies as company_store
    from . import queue as queue_store
    entry = company_store.update(body.old_name, body.old_careers_url or "",
                                 body.name, body.careers_url)
    if entry is None:
        raise HTTPException(404, "That company isn't in the list any more.")
    new_name = entry.get("name", "")
    if new_name and new_name.strip().lower() != body.old_name.strip().lower():
        items = queue_store.load()
        changed = False
        for it in items:
            if (it.get("company") or "").strip().lower() == body.old_name.strip().lower():
                it["company"] = new_name
                changed = True
        if changed:
            queue_store.save(items)
    return {"company": entry}


@app.delete("/api/companies")
def delete_company(body: CompanyRemove) -> dict:
    from . import companies as company_store
    return {"ok": company_store.remove(body.name, body.careers_url or "")}


@app.post("/api/queue/add")
def add_job(body: UrlRequest) -> dict:
    """Stage one hand-provided job URL (fetch -> extract -> score -> queue)
    so the Apply room can tailor + autofill it like any discovered job.
    Slow by nature (may render the page in a headless browser and call the
    local Claude CLI) — the frontend shows a loader."""
    from .runner import add_manual_job
    cfg = require_config()
    try:
        entry = add_manual_job(body.url, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": entry}


# ----------------------------------------------------------- /api/extension
# The Chrome extension (SaaS Phase 4) is the job intake now: it captures the
# user's rendered view of a page (their own browser — their filters, their
# logins, no bot walls) and posts it here. While the app runs locally the
# extension talks to this same local backend, which already holds the
# session; extension-side Supabase auth arrives with hosting (Phase 6).
class CapturedPage(BaseModel):
    url: str
    title: Optional[str] = ""
    jsonld: Optional[List[str]] = None       # raw ld+json script contents
    anchors: Optional[List[Dict[str, str]]] = None   # [{text, href}]
    cards: Optional[List[Dict[str, str]]] = None     # [{title, url}] — the
    # structural read of the rendered job cards; preferred over `anchors`.
    pageTotal: Optional[int] = None    # the count the page itself advertises
    hashFiltered: Optional[bool] = False   # filters live in the URL fragment
    missedRows: Optional[int] = 0   # rows the page claimed but capture missed
    challenged: Optional[str] = ""  # a bot check stopped the walk
    pagesScanned: Optional[int] = 1     # how many pages the walk covered
    stalledAtPage: Optional[int] = 0    # the site's pager stopped advancing
    visibleText: Optional[str] = ""


@app.post("/api/extension/job")
def extension_save_job(body: CapturedPage) -> dict:
    """Path A — save the single job posting the user is looking at."""
    from .scan import save_single_job
    cfg = require_config()
    try:
        entry = save_single_job(body.dict(), cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": {k: entry.get(k) for k in
                    ("url", "company", "title", "location", "score", "ai_reason")}}


@app.post("/api/extension/scan")
def extension_scan(body: CapturedPage) -> dict:
    """Path B — scan the careers-page listing the user has filtered/rendered.
    Returns a scan id; the extension polls /api/extension/scan/{id}."""
    from .scan import start_scan
    cfg = require_config()
    state = ui_state.load()
    threshold = int(state.get("fit_threshold", 60))
    return {"scan_id": start_scan(body.dict(), cfg, threshold)}


@app.get("/api/extension/scan/{sid}")
def extension_scan_status(sid: str) -> dict:
    from .scan import get_scan
    s = get_scan(sid)
    if s is None:
        raise HTTPException(404, "unknown scan id")
    return s


@app.post("/api/extension/scan/{sid}/stop")
def extension_scan_stop(sid: str) -> dict:
    from .scan import request_stop
    if not request_stop(sid):
        raise HTTPException(404, "unknown scan id")
    return {"ok": True}


class ScanPage(BaseModel):
    url: str
    jsonld: Optional[List[str]] = None
    visibleText: Optional[str] = ""


@app.post("/api/extension/scan/{sid}/page")
def extension_scan_page(sid: str, body: ScanPage) -> dict:
    """Rendered-tab fallback (MVP-408): the extension opened one job page the
    server couldn't fetch (bot-blocked) and delivers its rendered content."""
    from .scan import supply_page
    try:
        return supply_page(sid, body.url, body.jsonld or [], body.visibleText or "")
    except KeyError:
        raise HTTPException(404, "unknown scan id")


@app.post("/api/extension/scan/{sid}/continue")
def extension_scan_continue(sid: str) -> dict:
    """Pending pages delivered (or given up) — run scoring + staging."""
    from .scan import continue_scan
    cfg = require_config()
    threshold = int(ui_state.load().get("fit_threshold", 60))
    try:
        continue_scan(sid, cfg, threshold)
    except KeyError:
        raise HTTPException(404, "unknown scan id")
    return {"ok": True}


# ---------------------------------------------- /api/extension/apply (Phase 5)
# The extension autofill flow. The extension only reads the form and types
# what the backend resolves; every decision (field meaning, values, AI
# answers) is made here in the same code the Playwright filler uses. No code
# path submits — the human always submits (check_extension_never_submits.py).
class ApplyStart(BaseModel):
    url: str
    # What the FORM PAGE says about the job — the application record is built
    # from this now, not from a saved job (pivot 2026-07-22).
    pageTitle: Optional[str] = ""
    h1: Optional[str] = ""
    company: Optional[str] = ""
    title: Optional[str] = ""
    description: Optional[str] = ""
    jsonld: Optional[List[str]] = None


class ApplyResolve(BaseModel):
    url: str
    fields: List[Dict[str, Any]]      # [{id,label,kind,options?,maxlength?}]


class ApplyFinish(BaseModel):
    url: str
    outcome: str                       # "applied" | "cancelled"


@app.get("/api/extension/apply/session")
def apply_session_state() -> dict:
    from .apply_session import get_session
    return get_session()


@app.post("/api/extension/apply/start")
def apply_start(body: ApplyStart) -> dict:
    from .apply_session import start_session, SessionBusy
    cfg = require_config()
    threshold = int(ui_state.load().get("fit_threshold", 60))
    try:
        return start_session(body.url, cfg, threshold, page={
            "pageTitle": body.pageTitle, "h1": body.h1 or body.title,
            "company": body.company, "description": body.description,
            "jsonld": body.jsonld or []})
    except SessionBusy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/extension/apply/context")
def apply_set_context(body: ApplyStart) -> dict:
    """The user's own version of title/company/description for the
    application in progress. Theirs wins over anything scraped."""
    from .apply_session import set_context
    try:
        return set_context(body.url, body.title or "", body.company or "",
                           body.description or "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/extension/apply/tailor")
def apply_tailor(body: ApplyStart) -> dict:
    from .apply_session import tailor_for
    cfg = require_config()
    try:
        return tailor_for(body.url, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/extension/apply/resolve")
def apply_resolve(body: ApplyResolve) -> dict:
    """The extension found these controls on the current form step; the
    backend says what to put in each. Unresolved fields stay for the human."""
    from .apply_session import resolve_fields
    cfg = require_config()
    return resolve_fields(body.url, body.fields, cfg)


@app.get("/api/extension/apply/resume")
def apply_resume(url: str, which: str = "tailored"):
    """The resume PDF to attach, for the file inputs the resolve step
    flagged. which=tailored serves the job's tailored PDF (same file
    /api/pdf serves); which=master serves the uploaded base resume, for the
    user's explicit "just attach my current resume" choice in the panel."""
    if which == "master":
        from .. import store
        if store.enabled():
            pdf = store.base_resume_pdf()
            if pdf:
                return Response(content=pdf, media_type="application/pdf")
        raise HTTPException(404, "No base resume uploaded — add one in Settings.")
    # Tailored: the session's own workspace, not a queue lookup.
    from .apply_session import get_session
    d = (get_session() or {}).get("workspace")
    if d:
        for name in ("content.tailored.pdf", "resume.tailored.pdf", "resume.pdf"):
            path = os.path.join(d, name)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return Response(content=f.read(), media_type="application/pdf")
    raise HTTPException(404, "No tailored resume yet — tailor first.")


@app.post("/api/extension/apply/finish")
def apply_finish(body: ApplyFinish) -> dict:
    from .apply_session import finish_session
    cfg = require_config()
    return finish_session(body.url, body.outcome, cfg)


# ---------------------------------------------------------------- /api/jobs
# The unified Jobs view (SaaS Phase 3): every job the user has, one status
# lifecycle each — queue entries in all states, merged with applications-
# ledger rows the queue no longer holds (pruned/pre-Phase-3 applies).
@app.get("/api/jobs")
def unified_jobs() -> dict:
    from . import queue as queue_store
    jobs = queue_store.all_jobs_with_status()
    seen = {j["url"] for j in jobs}
    try:
        from ..autofill import applications
        from .. import store
        sheet = None
        if not store.enabled():
            cfg = require_config()
            from ..google.sheets import Sheet
            sheet = Sheet(cfg)
        for a in applications.read_all(sheet):
            if a.get("url") and a["url"] not in seen:
                jobs.append({
                    "url": a["url"], "company": a.get("company", ""),
                    "title": a.get("title", ""), "location": a.get("location", ""),
                    "score": None, "status": a.get("status") or "applied",
                    "applied_at": a.get("date_applied", ""),
                    "timeline": [], "from_ledger": True,
                })
                seen.add(a["url"])
    except Exception:
        pass   # no ledger configured — queue entries alone are the view
    return {"jobs": jobs, "statuses": list(queue_store.STATUSES)}


class JobStatusUpdate(BaseModel):
    url: str
    status: str


@app.post("/api/jobs/status")
def set_job_status(body: JobStatusUpdate) -> dict:
    """Manual status change from the Jobs tab (blueprint rule: the user can
    always correct/override — including undoing an automatic email match)."""
    from . import queue as queue_store
    if body.status not in queue_store.STATUSES:
        raise HTTPException(400, f"unknown status {body.status!r}")
    ok = queue_store.set_status(body.url, body.status, note="set manually")
    if not ok:
        raise HTTPException(404, "That job isn't in the queue store.")
    return {"ok": True, "status": body.status}


@app.post("/api/queue/tailor")
def tailor_job(body: UrlRequest) -> dict:
    from . import queue as queue_store
    cfg = require_config()
    d = queue_store.create_workspace(body.url, cfg)
    if d is None:
        raise HTTPException(404, "That URL isn't in the queue.")
    return {"workspace": d, **queue_store.workspace_status(d)}


@app.post("/api/queue/comment")
def comment_job(body: CommentRequest) -> dict:
    """Feedback on a tailored resume. Recorded in comments.md (which the AI
    tailoring prompt includes), and the tailored copy is reset to the master
    so the next apply/autofill-again re-tailors WITH this feedback — without
    the reset, `reworded` stays true and the comment would never be acted on."""
    from . import queue as queue_store
    d = queue_store.find_workspace(body.url)
    if d is None:
        raise HTTPException(404, "This job hasn't been tailored yet — comments "
                                 "apply to a tailored resume.")
    import datetime
    with open(os.path.join(d, "comments.md"), "a") as f:
        f.write(f"## {datetime.datetime.now():%Y-%m-%d %H:%M}\n{body.text}\n\n")
    from ..paths import CONTENT_YAML
    with open(CONTENT_YAML) as src, \
         open(os.path.join(d, "content.tailored.yaml"), "w") as dst:
        dst.write(src.read())
    return {"ok": True}


@app.post("/api/queue/skip")
def skip_job(body: UrlRequest) -> dict:
    from . import queue as queue_store
    return {"ok": queue_store.set_skipped(body.url, True)}


@app.post("/api/queue/unskip")
def unskip_job(body: UrlRequest) -> dict:
    from . import queue as queue_store
    return {"ok": queue_store.set_skipped(body.url, False)}


@app.post("/api/verify")
def verify_job(body: UrlRequest) -> dict:
    from . import queue as queue_store
    from ..tailoring.tailor import verify
    d = queue_store.find_workspace(body.url)
    if d is None:
        raise HTTPException(404, "This job hasn't been tailored yet.")
    ty = os.path.join(d, "content.tailored.yaml")
    ok, problems = verify(ty)
    if ok:
        # Cloud copy of the freshly built PDF (24h TTL) — best-effort only.
        from .. import store
        pdf = os.path.join(d, "content.tailored.pdf")
        if store.enabled() and os.path.isfile(pdf):
            try:
                with open(pdf, "rb") as f:
                    store.resume_upload(body.url, os.path.basename(pdf), f.read())
            except Exception:
                pass
    return {"ok": ok, "problems": problems, **queue_store.workspace_status(d)}


@app.get("/api/pdf")
def get_pdf(url: str, download: bool = False):
    """The tailored resume PDF for a job, by job URL. Serves the renamed
    upload copy ("<Role> - <Name> - Resume.pdf" — the exact file that went
    to the employer) when the job has been filled, else the workspace's
    content.tailored.pdf. `download=1` sets Content-Disposition: attachment
    with that filename instead of rendering inline."""
    from fastapi.responses import FileResponse
    from . import queue as queue_store
    d = queue_store.find_workspace(url)
    if d is None:
        raise HTTPException(404, "No tailored files for that job.")
    path = os.path.join(d, "content.tailored.pdf")
    for f in sorted(os.listdir(d)):
        if f.endswith(".pdf") and f != "content.tailored.pdf":
            path = os.path.join(d, f)
            break
    real_base = os.path.realpath(APPLICATIONS_DIR_ABS) + os.sep
    if not os.path.realpath(path).startswith(real_base) or not os.path.isfile(path):
        raise HTTPException(404, "Resume PDF not built yet — run verify first.")
    if download:
        return FileResponse(path, media_type="application/pdf",
                            filename=os.path.basename(path))
    return FileResponse(path, media_type="application/pdf")


# --------------------------------------------------------------- /api/apply
@app.post("/api/apply/start")
def start_apply(body: ApplyStartRequest) -> dict:
    try:
        start_after = APPLY.begin()
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    cfg = require_config()

    def worker() -> None:
        try:
            from .apply_engine import apply_batch
            apply_batch(body.urls, PROFILE_YAML, on_event=APPLY.push, cfg=cfg)
        except Exception as e:
            APPLY.push({"type": "batch_error", "error": str(e)})
        finally:
            with APPLY.lock:
                APPLY.running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "after": start_after}


class AiUpdate(BaseModel):
    provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    models: Optional[Dict[str, str]] = None   # {feature: model_id}


def _ai_state() -> dict:
    """Current AI settings for the UI. The API key is never returned in full —
    only whether one is set and its last 4 chars, so the field can show
    'saved' without re-exposing the secret."""
    from ..ai import settings as ai_settings
    key = ai_settings.openai_api_key()
    return {
        "provider": ai_settings.provider(),
        "has_key": bool(key),
        "key_hint": ("…" + key[-4:]) if len(key) >= 4 else "",
        "models": ai_settings.all_models(),
        "features": list(ai_settings.FEATURES),
        "claude_models": ai_settings.CLAUDE_MODELS,
    }


@app.get("/api/ai")
def get_ai() -> dict:
    return _ai_state()


def _apply_ai_patch(ai_block: dict, body: "AiUpdate") -> dict:
    """Merge an AiUpdate into an `ai` settings dict (shared by the config.yaml
    and per-user Supabase paths). Models are stored nested by provider so
    switching providers never leaves a mismatched id."""
    from ..ai import settings as ai_settings
    if body.provider is not None:
        if body.provider not in ai_settings.PROVIDERS:
            raise HTTPException(400, f"unknown provider {body.provider!r}")
        ai_block["provider"] = body.provider
    if body.openai_api_key is not None:
        # Only overwrite when a non-blank value is sent, so re-saving other
        # fields doesn't wipe a key the UI never re-shows.
        if body.openai_api_key.strip():
            ai_block["openai_api_key"] = body.openai_api_key.strip()
    if body.models:
        prov = ai_block.get("provider") or ai_settings.provider()
        models = ai_block.setdefault("models", {}).setdefault(prov, {})
        for feat, mid in body.models.items():
            if feat in ai_settings.FEATURES and str(mid).strip():
                models[feat] = str(mid).strip()
    return ai_block


@app.put("/api/ai")
def put_ai(body: AiUpdate) -> dict:
    """Persist AI provider / OpenAI key / per-feature models — to the signed-in
    user's Supabase settings when the cloud store is on, else to local
    config.yaml. ADMIN-ONLY when the store is on (decided 2026-07-14: users
    never pick providers/models — per-user policy lives in /api/admin; this
    endpoint remains for the admin's own machine)."""
    from .. import store
    from ..ai import settings as ai_settings
    if store.enabled():
        _require_admin()
        # Write the admin's OWN ai_policy row — the policy is what
        # settings.provider()/model_for() actually read (user_settings.ai is
        # legacy and gets shadowed by the policy, which is why the provider
        # radio used to snap back). Because every AI call — dashboard AND
        # extension — funnels through ai.complete() -> this policy, switching
        # here switches the extension too.
        from ..store import auth as store_auth
        me = store_auth.user_id()
        pol = store.ai_policy_get() or {}
        patch = {}
        if body.provider is not None:
            if body.provider not in ai_settings.PROVIDERS:
                raise HTTPException(400, f"unknown provider {body.provider!r}")
            patch["primary_provider"] = body.provider
            # Switching to a provider implies it's allowed for yourself.
            patch["allow_openai" if body.provider == "openai"
                  else "allow_claude_cli"] = True
        if body.openai_api_key is not None and body.openai_api_key.strip():
            patch["openai_api_key"] = body.openai_api_key.strip()
        if body.models:
            prov = patch.get("primary_provider") or ai_settings.provider()
            merged = dict(pol.get("models") or {})
            per = dict(merged.get(prov) or {})
            for feat, mid in body.models.items():
                if feat in ai_settings.FEATURES and str(mid).strip():
                    per[feat] = str(mid).strip()
            merged[prov] = per
            patch["models"] = merged
        if patch:
            store.admin_update_policy(me, patch)
            store.audit_append("policy_update", me,
                               {k: ("(set)" if k == "openai_api_key" else v)
                                for k, v in patch.items()})
        ai_settings.invalidate_user_cache()
        return _ai_state()
    d = _load_raw_config()
    _apply_ai_patch(d.setdefault("ai", {}), body)
    _save_raw_config(d)
    return _ai_state()


@app.get("/api/ai/models")
def get_ai_models(provider: str) -> dict:
    """Model ids to offer for a provider's dropdowns. Claude ids are known;
    OpenAI ids are fetched live from the user's key (we don't guess ids)."""
    from ..ai import settings as ai_settings
    if provider == "claude_cli":
        return {"models": [m["id"] for m in ai_settings.CLAUDE_MODELS],
                "labeled": ai_settings.CLAUDE_MODELS}
    if provider == "openai":
        key = ai_settings.openai_api_key()
        if not key:
            return {"models": [], "error": "Add your OpenAI API key first."}
        from ..ai.openai_provider import list_models
        ok, res = list_models(key)
        if not ok:
            # Never leave the dropdowns empty because of a network blip —
            # offer the well-known ids and say why they're a fallback.
            return {"models": ai_settings.OPENAI_FALLBACK_MODELS,
                    "fallback": True, "error": str(res)}
        return {"models": res}
    raise HTTPException(400, f"unknown provider {provider!r}")


@app.post("/api/apply/abort-tailoring")
def abort_tailoring() -> dict:
    """Kill the in-flight AI tailoring call (dashboard Abort button). The
    aborted job is skipped — never filled untailored — and the batch moves
    on to the next job."""
    from ..tailoring import reword
    return {"aborted": reword.abort_current()}


@app.post("/api/apply/stop")
def stop_apply_batch() -> dict:
    """Stop the whole running batch: kills an in-flight tailoring at once;
    an in-flight form fill finishes first, then every remaining job is
    marked cancelled. Already-filled tabs stay open for review."""
    from .apply_engine import stop_batch
    return {"stopping": stop_batch()}


@app.get("/api/apply/status")
def apply_status() -> dict:
    with APPLY.lock:
        after = APPLY.current_run_start_id if APPLY.running else (
            APPLY.events[-1]["id"] if APPLY.events else 0)
        return {"running": APPLY.running, "after": after}


@app.get("/api/apply/events/stream")
async def apply_events_stream(after: int = 0):
    return _sse_response(APPLY, after)


@app.get("/api/apply/jobs")
def apply_jobs() -> dict:
    """Current batch's per-job status — lets the Apply room show state
    immediately on load/refresh, not just from the live event stream."""
    from .apply_engine import BATCH
    with BATCH.lock:
        return {"jobs": dict(BATCH.jobs)}


@app.post("/api/apply/check")
def apply_check() -> dict:
    """Best-effort: look for submission-confirmation pages among the open
    apply tabs. Opportunistic only — Mark Applied is the reliable path."""
    from .apply_engine import check_confirmations
    cfg = require_config()
    return {"detected": check_confirmations(cfg)}


@app.post("/api/apply/mark")
def mark_apply(body: MarkRequest) -> dict:
    from .apply_engine import mark_applied, mark_skipped
    cfg = require_config()
    if body.status == "applied":
        mark_applied(cfg, body.url)
    elif body.status == "skipped":
        mark_skipped(body.url)
    else:
        raise HTTPException(400, "status must be 'applied' or 'skipped'")
    return {"ok": True}


# --------------------------------------------------------------- /api/tracking
@app.get("/api/applications")
def list_applications() -> dict:
    """Tracking, applications-first (pivot 2026-07-22): one row per job the
    user applied to through the extension, newest first, each carrying
    whatever Gmail has since said about it. Rejections are the only outcome
    matched for now; anything else stays 'no reply yet' rather than guessing.
    """
    from ..autofill import applications as apps
    from .. import store

    try:
        rows = apps.read_all(None if store.enabled() else safe_get_sheet())
    except Exception as e:
        raise HTTPException(503, f"Couldn't read your applications: {e}")

    # Gmail outcomes, keyed by company (the ledger has no message ids).
    outcomes: Dict[str, list] = {}
    if store.enabled():
        try:
            for e in store.email_log_rows():
                outcomes.setdefault(
                    (e.get("company") or "").strip().lower(), []).append(e)
        except Exception:
            pass        # tracking is additive — never block the list on it

    out = []
    for r in rows:
        co = (r.get("company") or "").strip().lower()
        mine = sorted(outcomes.get(co, []), key=lambda e: e.get("date", ""))
        # Only count mail that arrived AFTER the application went in.
        after = [e for e in mine
                 if (e.get("date") or "") >= (r.get("date_applied") or "")]
        rejected = next((e for e in after
                         if (e.get("classification") or "") == "rejection"), None)
        out.append({
            "company": r.get("company", ""),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "date_applied": r.get("date_applied", ""),
            "status": "rejected" if rejected else "applied",
            "rejected_on": (rejected or {}).get("date", ""),
            "last_email": (after[-1].get("date") if after else ""),
        })
    out.sort(key=lambda a: a["date_applied"], reverse=True)
    return {"applications": out,
            "counts": {"total": len(out),
                       "rejected": sum(1 for a in out if a["status"] == "rejected"),
                       "waiting": sum(1 for a in out if a["status"] == "applied")}}


@app.get("/api/tracking")
def get_tracking() -> dict:
    from .tracking import get_tracking as _get
    cfg = require_config()
    if cfg.auth_mode != "oauth":
        raise HTTPException(400, "Tracking needs auth_mode: oauth (Gmail read requires it).")
    return _get(cfg)


@app.post("/api/tracking/refresh")
def refresh_tracking() -> dict:
    from .tracking import refresh_tracking as _refresh
    cfg = require_config()
    if cfg.auth_mode != "oauth":
        raise HTTPException(400, "Tracking needs auth_mode: oauth (Gmail read requires it).")
    return _refresh(cfg)


# --------------------------------------------------------------------- /api/me
@app.get("/api/profile")
def get_profile() -> dict:
    from .me import get_profile as _get
    try:
        return _get()
    except FileNotFoundError:
        raise SetupRequiredError(
            ["profile_yaml"],
            f"{PROFILE_YAML} not found — finish setup at /setup first.")


@app.put("/api/profile")
def put_profile(body: Dict[str, str]) -> dict:
    from .me import put_profile as _put
    try:
        return _put(body)
    except FileNotFoundError:
        raise SetupRequiredError(
            ["profile_yaml"],
            f"{PROFILE_YAML} not found — finish setup at /setup first.")


@app.get("/api/content")
def get_content() -> dict:
    from .me import get_content_raw
    try:
        return {"content": get_content_raw()}
    except FileNotFoundError:
        raise SetupRequiredError(
            ["content_yaml"],
            "content.yaml not found — this is created by a Claude Code "
            "session, not the wizard (see /setup's final step).")


class ContentUpdate(BaseModel):
    content: str


@app.put("/api/content")
def put_content(body: ContentUpdate) -> dict:
    from .me import put_content_raw
    try:
        put_content_raw(body.content)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"Not saved — YAML error: {e}")
    return {"ok": True}


@app.post("/api/content/rebuild")
def rebuild_content() -> dict:
    from .me import rebuild_resume
    return rebuild_resume()


class ResumeUpload(BaseModel):
    filename: str
    pdf_base64: str


@app.post("/api/resume/upload")
def resume_upload_pdf(body: ResumeUpload) -> dict:
    """SaaS Phase 2: user uploads their resume PDF. The PDF is stored (cloud
    when signed in), its text parsed into candidate-facts YAML, and the
    PARSED RESULT IS RETURNED FOR REVIEW — nothing overwrites the user's
    existing facts until they hit Save (PUT /api/content). Parsing is an AI
    call, so it passes the account gate like every other AI feature."""
    import base64 as b64
    from ..tailoring.parse_resume import parse_pdf_resume
    try:
        pdf = b64.b64decode(body.pdf_base64 or "", validate=True)
    except Exception:
        raise HTTPException(400, "Couldn't read the uploaded file.")
    if len(pdf) > 10 * 1024 * 1024:
        raise HTTPException(400, "PDF is over 10 MB — export a smaller one.")
    ok, out = parse_pdf_resume(pdf)
    if not ok:
        raise HTTPException(400, out)
    # Keep the original on file (best-effort — parsing already succeeded).
    from .. import store
    stored = False
    if store.enabled() and store.logged_in():
        try:
            store.base_resume_set(body.filename or "resume.pdf", pdf)
            stored = True
        except RuntimeError:
            pass
    return {"parsed": True, "content": out, "stored": stored}


@app.get("/api/resume/base")
def resume_base_meta() -> dict:
    """What base resume is on file: {filename, uploaded_at} or {}."""
    from .. import store
    if not (store.enabled() and store.logged_in()):
        return {}
    try:
        return store.base_resume_meta()
    except Exception:
        return {}


@app.get("/api/resume/pdf")
def get_resume_pdf():
    from fastapi.responses import FileResponse
    from ..paths import RESUME_PDF
    if not os.path.isfile(RESUME_PDF):
        raise HTTPException(404, "resume.pdf not built yet — rebuild first.")
    return FileResponse(RESUME_PDF, media_type="application/pdf")


@app.get("/api/targets")
def get_targets() -> dict:
    cfg = require_config()
    return {"role_keywords": cfg.role_keywords,
            "locations": cfg.locations,
            "departments": cfg.departments}


class TargetsUpdate(BaseModel):
    role_keywords: List[str]
    # Optional so an older client (or the extension) can still PUT keywords
    # alone without wiping the two new lists.
    locations: Optional[List[str]] = None
    departments: Optional[List[str]] = None


@app.put("/api/targets")
def put_targets(body: TargetsUpdate) -> dict:
    raw = _load_raw_config()
    disc = raw.setdefault("discovery", {})
    disc["role_keywords"] = \
        [k.strip().lower() for k in body.role_keywords if k.strip()]
    # Locations keep their case — they're shown back to the user and go into
    # the ATS query params; matching lowercases them itself.
    if body.locations is not None:
        disc["locations"] = [x.strip() for x in body.locations if x.strip()]
    if body.departments is not None:
        disc["departments"] = [x.strip() for x in body.departments if x.strip()]
    _save_raw_config(raw)
    return {"ok": True}


# ----------------------------------------------------------------- /api/auth
# Supabase Auth, email + password. Signup sends ONE confirmation email
# (verify-before-use); sign-in works once confirmed. Only active when the
# Supabase store is configured — pure-local installs have no login. The
# password passes straight through to Supabase and is never stored or logged.
class AuthCredentials(BaseModel):
    email: str
    password: str


@app.get("/api/auth/status")
def auth_status() -> dict:
    from .. import store
    if not store.enabled():
        return {"supabase": False, "logged_in": False, "email": ""}
    out = {"supabase": True, "logged_in": store.logged_in(),
           "email": store.user_email(),
           "account_status": "", "subscribed": None, "is_admin": False}
    if out["logged_in"]:
        # Account gate state (schema_v3). Empty on a pre-v3 database —
        # the UI then shows no approval banner, matching the gate's behavior.
        try:
            prof = store.profile_get()
            out["account_status"] = str(prof.get("status") or "")
            out["subscribed"] = prof.get("subscribed")
            out["is_admin"] = store.is_admin()
        except Exception:
            pass
    return out


@app.post("/api/auth/signup")
def auth_signup(body: AuthCredentials) -> dict:
    from .. import store
    if not (body.email or "").strip():
        raise HTTPException(400, "Enter your email address.")
    try:
        res = store.signup(body.email, body.password)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    if res.get("needs_confirmation"):
        return {"needs_confirmation": True, **auth_status()}
    return auth_status()


@app.post("/api/auth/login")
def auth_login(body: AuthCredentials) -> dict:
    from .. import store
    try:
        store.login(body.email, body.password)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    from ..ai.settings import invalidate_user_cache
    invalidate_user_cache()
    # First sign-in on this machine: pull the user's resume/profile down so
    # typst + autofill have their local working copies.
    try:
        from .me import sync_down
        sync_down()
    except Exception:
        pass
    return auth_status()


@app.post("/api/auth/logout")
def auth_logout() -> dict:
    from .. import store
    store.logout()
    from ..ai.settings import invalidate_user_cache
    invalidate_user_cache()
    return {"ok": True}


# ----------------------------------------------------------------- /api/admin
# Admin-only account + AI-policy management (SaaS MVP Phase 1). RLS already
# hard-enforces all of this at the database — a non-admin JWT simply can't
# read or write other users' rows — these checks exist for clear 403s.
def _require_admin():
    from .. import store
    if not store.enabled() or not store.logged_in():
        raise HTTPException(403, "Sign in first.")
    if not store.is_admin():
        raise HTTPException(403, "Admins only.")


class AdminUserPatch(BaseModel):
    status: Optional[str] = None          # pending|active|suspended|rejected
    subscribed: Optional[bool] = None
    policy: Optional[Dict[str, Any]] = None   # ai_policy columns


@app.get("/api/admin/runner")
def admin_runner_status() -> dict:
    """Claude Mac-runner health for the admin panel (SaaS Phase 6). Empty
    until schema_v5 + a runner exist (hosting) — the panel then shows online/
    last-heartbeat/current-job."""
    from .. import store
    _require_admin()
    try:
        return {"runner": store.runner_status()}
    except Exception:
        return {"runner": {}}


@app.get("/api/admin/users")
def admin_users() -> dict:
    from .. import store
    _require_admin()
    try:
        return {"users": store.admin_list_users()}
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.patch("/api/admin/users/{user_id}")
def admin_patch_user(user_id: str, body: AdminUserPatch) -> dict:
    from .. import store
    _require_admin()
    # The admin cannot lock themselves out: no self-suspend/reject/
    # unsubscribe (user feedback 2026-07-15). Another admin could; you can't.
    from ..store import auth as store_auth
    if user_id == store_auth.user_id():
        if body.status in ("suspended", "rejected"):
            raise HTTPException(400, "You can't suspend your own account.")
        if body.subscribed is False:
            raise HTTPException(400, "You can't unsubscribe your own account.")
    try:
        prof_patch = {}
        if body.status is not None:
            if body.status not in ("pending", "active", "suspended", "rejected"):
                raise HTTPException(400, f"unknown status {body.status!r}")
            prof_patch["status"] = body.status
        if body.subscribed is not None:
            prof_patch["subscribed"] = bool(body.subscribed)
        if prof_patch:
            store.admin_update_profile(user_id, prof_patch)
            store.audit_append("profile_update", user_id, prof_patch)
        if body.policy:
            store.admin_update_policy(user_id, body.policy)
            # Never audit the key itself — only that it changed.
            safe = {k: ("(set)" if k == "openai_api_key" and v else v)
                    for k, v in body.policy.items()}
            store.audit_append("policy_update", user_id, safe)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    from ..ai.settings import invalidate_user_cache
    invalidate_user_cache()   # the admin may have edited their own row
    return {"ok": True}


# ------------------------------------------------------------------- /api/ui
@app.get("/api/ui-state")
def get_ui_state() -> dict:
    """Lightweight UI prefs (fit_threshold etc.) — lets pages read the saved
    threshold without GET /api/source's slow per-tab Google calls."""
    return ui_state.load()


@app.put("/api/ui-state")
def put_ui_state(body: Dict[str, Any]) -> dict:
    return ui_state.save(body)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ------------------------------------------------------------------ /api/setup
# First-run onboarding (P4). Every route here is safe to call on a totally
# fresh clone — none of them assume config.yaml/credentials.json/token.json/
# profile.yaml already exist; that's the whole point of a setup wizard.
@app.get("/api/setup/status")
def get_setup_status() -> dict:
    from .setup import status as _status
    return _status()


class SpreadsheetSetup(BaseModel):
    spreadsheet_id: str


@app.post("/api/setup/spreadsheet")
def setup_spreadsheet(body: SpreadsheetSetup) -> dict:
    """Wizard step 3: bootstraps config.yaml if this is the very first
    setup call to touch it, records the spreadsheet id, and returns the
    tab list + header row per tab so the frontend can reuse the same
    tab-picker/column-auto-guess UI the Run page's Source card already has
    (GET/PUT /api/source) to finish the column mapping."""
    from .setup import ensure_config_yaml
    if not os.path.exists(TOKEN_JSON):
        # Guard the step order server-side too: without a token, Sheet()
        # would launch the blocking OAuth consent flow from INSIDE this
        # request instead of step 2's background thread.
        raise HTTPException(409, "Connect your Google account first (step 2).")
    ensure_config_yaml()
    raw = _load_raw_config()
    g = raw.setdefault("google", {})
    g["spreadsheet_id"] = body.spreadsheet_id.strip()
    g["auth_mode"] = "oauth"
    _save_raw_config(raw)

    cfg = require_config()
    sheet = safe_get_sheet(cfg)
    try:
        tabs = sheet._tab_names()
        return {"tabs": tabs,
                "headers_by_tab": {t: sheet.header(t) for t in tabs}}
    except Exception:
        # A mistyped spreadsheet id is the most likely user error in the
        # whole wizard — the Sheets API raises HttpError(404/403) here,
        # which must come back as a friendly 400, not a 500 stack trace.
        # Roll the bad id back out of config.yaml so setup status doesn't
        # claim the spreadsheet step is done when it isn't.
        g["spreadsheet_id"] = ""
        _save_raw_config(raw)
        raise HTTPException(
            400, "Couldn't open that spreadsheet — double-check the id (the "
                 "long part of the sheet's URL) and that the Google account "
                 "you connected in step 2 can access it.")


@app.post("/api/setup/auth/start")
def setup_auth_start() -> dict:
    """Wizard step 2: kicks off the OAuth consent flow in a background
    thread — it's the exact same blocking flow `python -m backend.cli
    auth` runs (opens the user's default browser, waits on a local
    redirect), just triggered from the dashboard instead of a terminal.
    Requires credentials.json (step 1) to already be in place; bootstraps
    a bare config.yaml if step 3 hasn't run yet, since the OAuth flow
    itself never touches a specific spreadsheet."""
    from .setup import ensure_config_yaml
    ensure_config_yaml()
    cfg = require_config()
    if not os.path.exists(cfg.credentials_file):
        raise HTTPException(
            409, f"{cfg.credentials_file} not found yet — finish step 1 first.")
    if AUTH_STATE["running"]:
        return {"started": False, "already_running": True}
    AUTH_STATE.update({"running": True, "error": None, "done": False})

    def worker() -> None:
        try:
            # get_credentials, NOT Sheet(cfg): Sheet.__init__ raises
            # SystemExit for an empty spreadsheet_id — which is exactly the
            # state at this step (the spreadsheet is picked in step 3). The
            # OAuth consent itself never needs a spreadsheet.
            from ..google.sheets import get_credentials
            get_credentials(cfg)  # runs the flow; writes token.json
            AUTH_STATE["done"] = True
        except BaseException as e:  # noqa: BLE001 — SystemExit included: it
            # would otherwise kill this thread SILENTLY (threading swallows
            # it), leaving the wizard polling "waiting for consent…" forever
            # with no error to show.
            AUTH_STATE["error"] = str(e)
        finally:
            AUTH_STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True}


@app.get("/api/setup/auth/status")
def setup_auth_status() -> dict:
    return {**AUTH_STATE, "token_present": os.path.exists(TOKEN_JSON)}


@app.post("/api/setup/profile/init")
def setup_profile_init() -> dict:
    """Wizard step 4: creates profile.yaml from profile.example.yaml
    (structure only — placeholder values, no facts) if it doesn't exist
    yet, then returns it so the frontend's ProfileTab can edit it in place
    with the existing Me/Profile editor."""
    from .me import get_profile as _get
    from .setup import ensure_profile_yaml
    ensure_profile_yaml()
    return _get()


# --------------------------------------------------------------- static files
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
