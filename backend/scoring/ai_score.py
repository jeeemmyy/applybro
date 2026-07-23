"""AI fit scoring — reasoning-based, NOT keyword overlap (Layer 2.5).

The keyword scorer in score.py counts taxonomy-term overlap. That number is
defensible and auditable, but it buries good matches: a "Fullstack Software
Engineer" role listing aws/azure/gcp/docker/kubernetes as separate line items
scored 36% against a full-stack resume that simply doesn't spell out every
cloud vendor — while a manager role whose boilerplate happened to overlap
scored 89%. Keyword counting can't tell that a full-stack dev IS a strong fit
for a full-stack role.

This module asks Claude (Sonnet 5 by default) to REASON about fit instead,
in a two-stage funnel that mirrors how a person reads a careers page:

  Stage 1 — triage: given the candidate's background + the roles they want
    and the full list of job TITLES, pick the ones worth actually reading.
    One cheap call per company. ("browse the whole page, decide what's worth
    opening")
  Stage 2 — assess: for each worth-reading job, read the full description and
    return a fit % WITH written reasoning, the requirements the candidate
    genuinely meets, and the real gaps. ("open the relevant ones, judge fit")

IMPORTANT — this is a SEPARATE, clearly-labelled signal shown ALONGSIDE the
keyword fit %, never replacing it (the keyword number stays the auditable one
CLAUDE.md defines). The AI score is a hiring-fit JUDGEMENT of how well the
background matches the role — it is NOT, and must never be shown as, a
probability of hearing back.

Truth rules (CLAUDE.md): the assessment is grounded ONLY in what the resume
actually contains. It must not credit the candidate with skills/experience
they don't have — "matched" means the resume genuinely evidences it, "gaps"
are real. The reasoning is for the user's own triage; nothing here is written
onto a resume or an application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

from ..paths import CONTENT_YAML
from .. import ai

log = logging.getLogger("applybro.scoring.ai")

STAGE1_TIMEOUT = 180
STAGE2_TIMEOUT = 240
# Titles per triage call. Smaller = the "picking which jobs" count moves more
# often (a 130-job board was 3 chunks of 60, so the number only stepped ~3
# times and looked frozen between — user report 2026-07-23). 30 makes it ~5
# steps for 130 without a meaningful cost change: triage prompts are just
# titles, so total tokens barely move; there are only a few more small calls.
TRIAGE_CHUNK = 30
# Chunks run in parallel. One more worker than the assess stage: triage calls
# are short, so they finish fast and the extra concurrency keeps the count
# visibly moving without fanning out near the rate limit.
TRIAGE_WORKERS = 4


@dataclass
class AiFit:
    """AI's reasoned assessment of one job. `score` is None when the job was
    triaged out (not worth reading) — the UI then shows only the keyword %."""
    worth_exploring: bool
    score: Optional[int] = None          # 0-100 hiring-fit judgement
    reason: str = ""
    matched: List[str] = field(default_factory=list)   # genuinely met reqs
    gaps: List[str] = field(default_factory=list)      # real missing reqs

    def to_dict(self) -> dict:
        return {"ai_worth_exploring": self.worth_exploring,
                "ai_score": self.score, "ai_reason": self.reason,
                "ai_matched": self.matched, "ai_gaps": self.gaps}


def _run(prompt: str, timeout: int) -> Optional[str]:
    """One fit-scoring call through the shared provider (OpenAI or Claude CLI,
    feature='fit' model). Returns the text, or None on any failure — scoring
    degrades to keyword-only / triage-fail-open rather than blocking a run."""
    ok, out = ai.complete(prompt, feature="fit", timeout=timeout)
    if not ok:
        log.warning("AI fit call failed: %s", (out or "")[:150])
        return None
    return out


# --------------------------------------------------------- background summary
def background_text(content_yaml: str = CONTENT_YAML,
                    max_chars: int = 6000) -> str:
    """The candidate's real background, for grounding the assessment — the
    resume content verbatim (summary, every bullet, skills)."""
    try:
        with open(content_yaml) as f:
            return f.read()[:max_chars]
    except OSError:
        return ""


# --------------------------------------------------------------- stage 1
_STAGE1_PROMPT = """You are triaging a company's open roles for one candidate,
deciding which are worth reading in full — exactly what a person scanning a
careers page does before opening any listing.

# Candidate background (their resume)

{background}

# Roles the candidate is targeting

{targets}
{departments}
# Open roles at {company} (title — location)

{titles}

Return ONLY a JSON object, no commentary or fences, shaped exactly:
  {{"keep": [1, 4, 5]}}
where the array holds the 1-based indices of the roles worth reading in full
for THIS candidate.

Be SELECTIVE — every kept role costs a full read, so keep only roles whose
CORE CRAFT is the candidate's own. A role qualifies when someone doing the
candidate's day-to-day job could step into it: the same discipline, adjacent
specialities within it, and a comparable level. Keep such roles even when the
title wording differs or some listed tools are unfamiliar — a full-stack
engineer, for instance, should keep frontend, backend, full-stack, software
engineer, product engineer, AI/ML engineering, forward-deployed engineer,
integration and automation engineering roles.

EXCLUDE, without exception:
- different job FUNCTIONS, however technical they sound — QA / test /
  test-automation, support, IT/helpdesk, security operations, data science and
  analytics (when the candidate isn't one), design, product management,
  project/program management,
- non-engineering functions entirely: HR, payroll, recruiting, finance,
  accounting, legal, sales, marketing, operations, logistics, customer service,
- people-management and executive roles (manager, head of, director, VP,
  principal/staff-level leadership) unless the candidate already holds one,
- roles requiring a core stack or domain the resume shows no trace of,
- internships and roles far below the candidate's level.

When in doubt about a role in the candidate's OWN discipline, keep it. When
the doubt is about whether it's even their discipline, drop it. If none fit,
return {{"keep": []}}.
"""


# Departments are a HINT THAT WIDENS and the prompt has to say so in those
# words. The same full-stack role is filed under Engineering at one company,
# Product at the next and Customer Success at the third, so treating this list
# as a filter would drop real matches for a taxonomy the candidate never sees.
_DEPARTMENTS_HINT = """
# Teams the candidate is open to (a HINT, never a filter)

{departments}

Treat these as areas the candidate would happily work in, so lean toward
KEEPING a role that sits in one of them. Never drop a role for being outside
this list — company department names are arbitrary and the same job is filed
differently at every company.
"""


def triage_titles(company: str, titles: List[str], background: str,
                  targets: List[str],
                  departments: Optional[List[str]] = None,
                  on_progress=None, should_skip=None) -> List[int]:
    """Indices (0-based) of the jobs worth reading in full. On any failure,
    returns ALL indices — degrade toward reading more, never toward hiding a
    job the way the keyword filter did.

    `on_progress(done, total, kept)` fires as each chunk lands. A 466-job
    board is 8 AI calls here; they run IN PARALLEL (same total cost as
    sequential, but ~3x faster and the count moves ~3x more often, so it
    stops looking frozen — user report 2026-07-23). The caller turns the
    numbers into a real fraction."""
    if not titles:
        return []
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    dept_block = (_DEPARTMENTS_HINT.format(departments=", ".join(departments))
                  if departments else "")
    bases = list(range(0, len(titles), TRIAGE_CHUNK))

    def judge(base: int) -> List[int]:
        chunk = titles[base:base + TRIAGE_CHUNK]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))
        out = _run(_STAGE1_PROMPT.format(
            background=background or "(not provided)",
            targets=", ".join(targets) or "(any relevant engineering role)",
            departments=dept_block,
            company=company, titles=numbered), STAGE1_TIMEOUT)
        idxs = ai.as_list(ai.load_json(out), "keep") if out is not None else None
        if not isinstance(idxs, list):
            return list(range(base, base + len(chunk)))   # fail open
        return [base + n - 1 for n in idxs
                if isinstance(n, int) and 1 <= n <= len(chunk)]

    keep: List[int] = []
    done_titles = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=TRIAGE_WORKERS) as ex:
        futures = {}
        for base in bases:
            if should_skip and should_skip():
                break        # Stop pressed — don't dispatch the rest.
            futures[ex.submit(judge, base)] = base
        for f in as_completed(futures):
            base = futures[f]
            try:
                got = f.result()
            except Exception:
                got = list(range(base, min(base + TRIAGE_CHUNK, len(titles))))
            with lock:
                keep.extend(got)
                done_titles += min(TRIAGE_CHUNK, len(titles) - base)
                # After EVERY chunk, including ones that failed open — a bar
                # that stalls on failures is how a working scan looks stuck.
                if on_progress:
                    on_progress(done_titles, len(titles), len(set(keep)))
    return sorted(set(keep))


# --------------------------------------------------------------- stage 2
_STAGE2_PROMPT = """You are assessing how well ONE candidate fits ONE job,
based only on their real resume and the job description.

# Candidate resume

{background}

# Job

Title: {title}
Location: {location}

Description:
{description}

Assess the fit HONESTLY, grounded ONLY in what the resume actually shows.
Never credit the candidate with a skill or experience the resume doesn't
evidence. But judge like a pragmatic hiring manager, not a checklist:

- What matters most is the CORE of the role — its primary function, level,
  and main technologies. A candidate whose day-to-day IS this role (e.g. a
  full-stack engineer for a full-stack role, on the same core stack) is a
  STRONG fit (think 75-95), even if some secondary or "nice to have" tools
  are missing. Do not let a handful of peripheral gaps (a specific cloud
  vendor, an infra tool, a niche library) drag down an otherwise-strong core
  match — real engineers pick those up.
- Transferable and equivalent experience counts fully (one cloud provider for
  another, related frameworks, comparable domains).
- Reserve low scores (below ~40) for genuine mismatches: a different function
  (a management, sales, or data-science role for a hands-on dev), a seniority
  gulf, or a core stack the candidate simply hasn't worked in.

Output ONLY a JSON object, no commentary or fences:
{{
  "score": <0-100 integer: how well this candidate's background matches the
            role's CORE requirements, judged as above. A fit JUDGEMENT, NOT a
            chance of getting an interview.>,
  "reason": "<2-4 sentences: why this score — the strongest alignments and the
             most important gaps, specific to this candidate and role>",
  "matched": ["<requirement the resume genuinely meets>", ...],
  "gaps": ["<important requirement the resume does NOT evidence>", ...]
}}
"""


def assess_job(title: str, location: str, description: str,
               background: str) -> Optional[AiFit]:
    if not (description or "").strip():
        return None
    out = _run(_STAGE2_PROMPT.format(
        background=background or "(not provided)", title=title,
        location=location or "(not stated)",
        description=(description or "")[:14000]), STAGE2_TIMEOUT)
    if out is None:
        return None
    obj = ai.load_json(out)
    if not isinstance(obj, dict):
        log.warning("AI assess: no JSON object for %r", title[:40])
        return None
    try:
        score = int(obj.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    matched = [str(x) for x in obj.get("matched", []) if str(x).strip()][:12]
    gaps = [str(x) for x in obj.get("gaps", []) if str(x).strip()][:12]
    return AiFit(worth_exploring=True, score=score,
                 reason=str(obj.get("reason") or "").strip(),
                 matched=matched, gaps=gaps)


# --------------------------------------------------------------- orchestration
@dataclass
class ScorableJob:
    """Minimal shape the scorer needs — a discovery Job or a queue entry both
    adapt to this (url, title, location, description)."""
    url: str
    title: str
    location: str
    description: str


ASSESS_WORKERS = 3   # parallel assess calls — a 20-job scan drops from
                     # ~10-15 min sequential to a few minutes (user report)


def score_company_jobs(company: str, jobs: List[ScorableJob],
                       background: str, targets: List[str],
                       should_skip=None, on_progress=None,
                       workers: int = ASSESS_WORKERS,
                       pretriaged: bool = False) -> Dict[str, AiFit]:
    """AI-score one company's jobs, url -> AiFit. Two-stage: triage titles,
    then assess the worth-reading ones IN PARALLEL. Jobs triaged out get an
    AiFit with worth_exploring=False and no score.

    `should_skip()` (optional) is polled before each assess task starts so
    Stop can bail out mid-company — ai.abort_current() from the button
    handler also kills every in-flight call (the abort registry is
    multi-slot now). `on_progress(done, total, (url, fit))` fires as each job
    finishes — the third arg carries that job's verdict so callers can show
    it live."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: Dict[str, AiFit] = {}
    if not jobs:
        return result
    if should_skip and should_skip():
        return result
    # pretriaged: the caller already ran stage 1 on the titles and is passing
    # only the survivors (the extension scan does this BEFORE fetching any
    # description, so it never reads a job the titles already ruled out).
    keep = list(range(len(jobs))) if pretriaged else sorted(set(
        triage_titles(company, [j.title for j in jobs], background, targets)))
    keep_set = set(keep)
    for i, j in enumerate(jobs):
        if i not in keep_set:
            result[j.url] = AiFit(worth_exploring=False)

    total = len(keep)
    done = 0
    lock = threading.Lock()

    def work(j: ScorableJob):
        if should_skip and should_skip():
            return j.url, None
        return j.url, assess_job(j.title, j.location, j.description, background)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = [ex.submit(work, jobs[i]) for i in keep]
        for f in as_completed(futures):
            try:
                url, fit = f.result()
            except Exception:
                continue
            fit = fit or AiFit(worth_exploring=True)
            result[url] = fit
            with lock:
                done += 1
                d = done
            if on_progress:
                on_progress(d, total, (url, fit))
    return result
