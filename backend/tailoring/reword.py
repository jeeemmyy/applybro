"""AI rewording — the step that makes tailoring real.

The Apply room calls this for every selected job whose content.tailored.yaml
still reads like the master. It sends the job description, the tailoring
rules, and the master content through backend/ai (the user's configured
provider — OpenAI by default, Claude CLI opt-in) and asks for a complete
rewritten YAML. The output is then passed through tailor.verify(), which
mechanically enforces the no-fabrication rules (CLAUDE.md) and compiles the
PDF. Up to two retries with the violations fed back; anything still failing
is reported to the Apply room and the job is NOT filled — an untailored
application is mass-applying, which defeats the product's whole point
(maximum interview calls, not maximum submissions).
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

import yaml

from ..paths import CONTENT_YAML
from .. import ai
from .tailor import verify

# How long one rewrite may take. Generous: a cold model call + a full resume
# rewrite is normally well under this.
REWORD_TIMEOUT = 600

# Re-exported so apply_engine can keep calling reword.abort_current() /
# comparing against reword.ABORTED. The abort registry now lives in backend.ai
# and cancels whichever provider (Claude CLI subprocess or OpenAI stream) is
# in flight.
ABORTED = ai.ABORTED
abort_current = ai.abort_current

_PROMPT = """You are tailoring a resume for one specific job. Rewrite the
resume content YAML below so it reads like it was written for this job.

Your GOAL is to raise this resume's fit for the job as far as the TRUTH
allows. The job description below includes a "Fit gaps" section — the things
holding the fit down. For each gap, if the resume genuinely has relevant
experience that's currently buried or under-emphasized, surface it (reword,
reorder, relabel) so it reads clearly against this role. If a gap reflects
something the resume simply does not contain, LEAVE IT — do not invent, imply,
or inflate to close it. A higher fit earned by lying is worse than an honest
lower fit.

Doctrine: BE WATER — reshape the headline (contact.title), summary, skill
group labels/order, and bullet wording/order to mirror the job's language
and priorities as strongly as the truth allows. A result that reads almost
identical to the original is a tailoring failure.

Allowed: rewrite the headline to mirror the target role (if it describes
work genuinely done); reword existing bullets using the job description's
vocabulary; reorder bullets and skills to put the most relevant first;
relabel skill groupings when truthful; rewrite the summary; drop weak
bullets.

NEVER: invent experience, skills, tools, employers, dates, or achievements;
add a skill just because the job asks for it; inflate scope, seniority, or
metrics; change dates, employment titles, or company names; introduce any
number that the original does not state. If the job needs something the
resume doesn't have, leave it out. A "Fit gap" the resume can't truthfully
support must NOT appear in your output unless it was already in the original.

Keep roughly the same amount of text — the compiled PDF must stay the same
page count.

# The job

{job_md}
{comments}
# The resume content YAML to tailor

```yaml
{master}
```
{feedback}
Output ONLY the complete rewritten YAML document — same top-level structure
and keys, no markdown fences, no commentary before or after. Never refuse
and never ask questions: even if the job is a weak match, tailor as strongly
as the truth allows (emphasize the genuinely transferable work, drop what's
irrelevant) and output the YAML.
"""


def _extract_yaml(text: str) -> Optional[str]:
    """Pull the YAML document out of model output. Despite the instructions
    the model sometimes adds a preamble line and/or ```yaml fences (seen in
    production), so try, in order: every fenced block (longest first, and
    tolerate a missing closing fence), the raw text, and the text from its
    first top-level `key:` line onward. Returns the first candidate that
    parses to a dict, else None."""
    t = text.strip()
    fenced = re.findall(r"```[ \t]*(?:ya?ml)?[ \t]*\n(.*?)(?:\n```|\Z)", t, re.S)
    candidates = sorted(fenced, key=len, reverse=True)
    # From the first top-level `key:` line onward — BEFORE the raw text, so a
    # prose preamble can't sneak in as an extra YAML key (a bare preamble
    # line ending in ":" makes the whole output parse as a dict).
    m = re.search(r"^[A-Za-z_][\w-]*:(\s|$)", t, re.M)
    if m:
        candidates.append(t[m.start():])
    candidates.append(t)
    for c in candidates:
        try:
            if isinstance(yaml.safe_load(c), dict):
                return c
        except yaml.YAMLError:
            continue
    return None


def _read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def _structure_ok(tailored: dict, master: dict) -> Optional[str]:
    """Cheap sanity check before the real verify(): same top-level keys,
    all sections present. Catches truncated/partial model output early."""
    if not isinstance(tailored, dict):
        return "output was not a YAML mapping"
    missing = set(master.keys()) - set(tailored.keys())
    if missing:
        return f"output is missing sections: {sorted(missing)}"
    extra = set(tailored.keys()) - set(master.keys())
    if extra:
        return f"output added unknown sections: {sorted(extra)}"
    return None


def reword_workspace(workdir: str, master_path: str = CONTENT_YAML) -> Tuple[bool, str]:
    """Rewrite workdir/content.tailored.yaml for the job described by
    workdir/job.md, then verify (which also compiles the PDF the autofiller
    uploads). Returns (ok, human-readable message). On failure the tailored
    file is reset to the master copy so a rule-breaking resume can never be
    left on disk as if it were ready."""
    job_md = _read(os.path.join(workdir, "job.md"))
    if not job_md:
        return False, "job.md missing from this job's folder"
    comments = _read(os.path.join(workdir, "comments.md"))
    comments_block = (
        f"\n# Your feedback from earlier drafts (follow it)\n\n{comments}\n"
        if comments.strip() else ""
    )
    master_raw = _read(master_path)
    master = yaml.safe_load(master_raw)
    ty = os.path.join(workdir, "content.tailored.yaml")

    problems: List[str] = []
    for attempt in (1, 2, 3):
        feedback = ""
        if problems:
            feedback = ("\n# Your previous attempt broke these rules — fix "
                        "them this time\n\n"
                        + "\n".join(f"- {p}" for p in problems) + "\n")
        prompt = _PROMPT.format(job_md=job_md, comments=comments_block,
                                master=master_raw, feedback=feedback)
        ok, out = ai.complete(prompt, feature="tailoring", timeout=REWORD_TIMEOUT)
        if not ok:
            return False, out
        # Keep the raw output on disk so a failure is debuggable, not a
        # mystery ("what did the model actually say?").
        with open(os.path.join(workdir, f"reword_output_{attempt}.txt"), "w") as f:
            f.write(out)

        text = _extract_yaml(out)
        if text is None:
            head = " ".join(out.strip().split())[:160]
            problems = ["output was not a valid YAML document (remember: "
                        "no fences, no commentary — YAML only). "
                        f"You said: \"{head}\""]
            continue
        data = yaml.safe_load(text)
        err = _structure_ok(data, master)
        if err:
            problems = [err]
            continue

        with open(ty, "w") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        ok, problems = verify(ty, master=master_path)
        if ok:
            return True, "tailored and verified"

    # Both attempts failed: reset to master so nothing rule-breaking can be
    # mistaken for a ready resume (workspace_status will show reworded=False).
    with open(ty, "w") as f:
        f.write(master_raw)
    return False, "; ".join(problems) if problems else "tailoring failed"
