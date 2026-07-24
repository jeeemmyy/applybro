"""AI answers for open-ended application-form questions (JobRight-style).

The autofiller's normal pass fills facts from profile.yaml and prepared
answers.yaml entries. What remains are the prose questions — "Why do you
want to work here?", "Describe your experience with X", cover-letter boxes.
This module writes those answers with the user's local Claude Code CLI
(same login the resume tailoring uses), grounded in the SAME material the
resume itself came from:

  - job.md            (the job description, fit evidence, salary estimate)
  - content.tailored.yaml  (the resume version being submitted)
  - profile.yaml      (standing facts: location, sponsorship, notice, ...)

TRUTH RULES (CLAUDE.md): every claim must be supported by that material.
A question that can't be answered truthfully from it gets "" — and stays
"left for you" in the report — rather than a plausible invention. A wrong
or fabricated answer on a real application is far worse than a blank.

Answers are persisted to the workspace's answers.yaml via
tailor.upsert_auto_answer, so a re-fill types them again without another
AI call, and hand-written entries are never clobbered.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

import yaml

from ..paths import PROFILE_YAML
from .. import ai
from .tailor import upsert_auto_answer

# Generous: answering several open questions grounded in a long JD + resume
# can take a while on either provider.
AI_TIMEOUT = 420

# Keep the model honest about output size: an answer box rarely wants an
# essay, and giant answers read as AI slop to a recruiter.
_DEFAULT_GUIDANCE = "2-5 sentences unless the question clearly asks for more"

_PROMPT = """You are answering questions on a job application form on behalf
of the candidate, in the candidate's first-person voice.

# The job (description, fit evidence, salary estimate if present)

{job_md}

# The resume being submitted with this application (YAML)

```yaml
{resume_yaml}
```

# Candidate facts (profile)

```yaml
{profile_yaml}
```

# Prepared answers already on file (context — their questions are NOT yours)

```yaml
{answers_yaml}
```

# The questions still unanswered on the live form

{questions}

Rules — these protect the candidate from lying on a real application:
- TRUTH ONLY. Every claim must be supported by the resume or the candidate
  facts above. Never invent experience, skills, tools, numbers, employers,
  or preferences. Never inflate scope or seniority.
- If a question cannot be answered truthfully from the material — it asks
  about something the candidate doesn't have, or personal data not present
  (an ID, a reference's contact, a certification) — answer "" for it.
- Be specific and concrete: {guidance}. Mirror the job's own vocabulary
  where truthful. No filler openings like "I am writing to express...".
- Respect any "max N chars" limit on a question — stay comfortably under it.
- Salary questions: use the salary estimate from the job info verbatim if
  one is present, else "".

Output ONLY a JSON object, no commentary, no markdown fences, shaped exactly:
  {{"answers": [{{"match": "<the question's match key, copied exactly>", "answer": "..."}}]}}
with one element per question, in order.
"""


def _norm_key(s: str) -> str:
    """Compare match keys ignoring case, punctuation and spacing."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def _format_questions(questions: List[dict]) -> str:
    lines = []
    for i, q in enumerate(questions, start=1):
        limit = f" (max {q['maxlength']} chars)" if q.get("maxlength") else ""
        lines.append(f'{i}. match key: "{q["match"]}"\n'
                     f'   question: {q["question"]}{limit}')
    return "\n".join(lines)


def _style_guidance(profile_path: str) -> str:
    """How the answers should READ, from the user's own profile. Their voice,
    not a house style — a recruiter reads these, and generic AI prose is worse
    than none. Falls back to the default sizing guidance."""
    try:
        with open(profile_path) as f:
            prof = yaml.safe_load(f) or {}
    except (OSError, ValueError):
        return _DEFAULT_GUIDANCE
    length = str(prof.get("answer_length") or "").strip() or _DEFAULT_GUIDANCE
    style = str(prof.get("writing_style") or "").strip()
    return f"{length}. Write in this voice: {style}" if style else length


def answer_questions(workdir: Optional[str], questions: List[dict],
                     profile_path: str = PROFILE_YAML,
                     job_text: str = "", resume_yaml: str = "") -> Tuple[bool, object]:
    """Generate grounded answers for the given open questions. On success
    returns (True, [{match, answer}, ...]) — persisted into the workspace's
    answers.yaml when there IS a workspace. On failure returns (False,
    reason-string); the fill simply proceeds without AI answers (fields stay
    'left for you'), it never blocks the batch.

    `workdir` is OPTIONAL now. It used to be required, which meant open
    questions were only ever answered when the user had tailored first (that's
    what creates the workspace) — apply-without-tailoring silently left every
    essay question blank (user report 2026-07-24). Callers with no workspace
    pass the job description and resume directly instead; the truth rules are
    identical either way."""
    if not questions:
        return True, []

    if workdir:
        job_md = _read(os.path.join(workdir, "job.md"))
        resume = _read(os.path.join(workdir, "content.tailored.yaml"))
        prepared = _read(os.path.join(workdir, "answers.yaml"))
    else:
        job_md, resume, prepared = job_text, resume_yaml, ""
    # Ground or bail: answering with no job AND no resume would be invention,
    # which the truth rules forbid outright.
    if not (job_md.strip() or resume.strip()):
        return False, "no job description or resume to ground answers in"

    prompt = _PROMPT.format(
        job_md=job_md[:20000],
        resume_yaml=resume[:20000],
        profile_yaml=_read(profile_path)[:8000],
        answers_yaml=prepared[:6000],
        questions=_format_questions(questions),
        guidance=_style_guidance(profile_path),
    )
    # ai.complete registers the call with the shared abort machinery, so the
    # dashboard's Abort/Stop-batch buttons kill an in-flight answer generation
    # exactly like an in-flight tailoring — regardless of provider.
    ok, out = ai.complete(prompt, feature="qa", timeout=AI_TIMEOUT)
    if not ok:
        return False, out

    data = ai.as_list(ai.load_json(out), "answers")
    if data is None:
        return False, f"no JSON answers in the model output: {out[:200]}"

    valid_matches = {q["match"] for q in questions}
    limits = {q["match"]: q.get("maxlength") for q in questions}
    # Match the model's echoed key back LENIENTLY. The caller's key is a
    # truncated, lowercased slice of the label, and a model that re-wraps or
    # re-punctuates it would otherwise have its answer silently dropped — and
    # then reported as "couldn't answer truthfully", which is a lie about a
    # question it did answer. Exact, then normalised, then prefix, then
    # position.
    by_norm = {_norm_key(m): m for m in valid_matches}
    ordered = [q["match"] for q in questions]

    def resolve_key(raw: str, i: int) -> Optional[str]:
        if raw in valid_matches:
            return raw
        n = _norm_key(raw)
        if n in by_norm:
            return by_norm[n]
        for norm, original in by_norm.items():       # truncation either way
            if n and norm and (n.startswith(norm) or norm.startswith(n)):
                return original
        # Same count and order as asked: trust the position.
        return ordered[i] if len(data) == len(ordered) and i < len(ordered) else None

    answers: List[dict] = []
    seen = set()
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            continue
        answer = str(it.get("answer") or "").strip()
        if not answer:
            continue                    # truthfully unanswerable — leave blank
        match = resolve_key(str(it.get("match") or "").strip(), i)
        if match is None or match in seen:
            continue                    # unknown question, or a duplicate echo
        seen.add(match)
        ml = limits.get(match)
        if ml and len(answer) > ml:
            answer = answer[:ml].rsplit(" ", 1)[0].rstrip(".,;: ") + "."
        answers.append({"match": match, "answer": answer})

    if workdir:      # nothing to persist into without one
        for a in answers:
            upsert_auto_answer(workdir, a["match"], a["answer"])
    return True, answers
