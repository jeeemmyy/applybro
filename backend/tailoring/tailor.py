"""Layer 2.5 — tailoring workspace + mechanical guardrails.

`tailor` packages one scored job into data/applications/<slug>/:
    job.md                  the JD + fit evidence (matched / missing skills)
    content.tailored.yaml   a copy of the master content.yaml to be reworded
    RULES.md                the CLAUDE.md tailoring rules, restated

The rewording itself is a judgment task — done automatically by the Apply
room via the local Claude Code CLI (tailoring.reword), or by hand — and this
module's job is to make rule violations MECHANICALLY IMPOSSIBLE to miss. `verify` diffs the tailored YAML against the master and fails if:

  V1  contact block changed
  V2  company names, role titles, role dates, or company meta lines changed,
      or roles were added/removed/reordered
  V3  education degrees/certifications changed (set-wise for certs)
  V4  taxonomy skills appear in the tailored file that the master never had
      (the "no invented skills" rule, enforced at taxonomy level)
  V5  numeric claims (metrics like "8+", "20+", "10-12") appear that the
      master never stated (the "no inflated metrics" rule)
  V6  the compiled PDF page count differs from the master's

Passing verify does not replace human review; it catches the failure modes
that matter most (fabrication, identity changes, layout overflow).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from typing import List, Optional, Set, Tuple

import yaml

from ..paths import APPLICATIONS_DIR, CONTENT_YAML, TEMPLATE_TYP
from ..scoring.score import extract_skills

RULES_MD = """# Tailoring rules (from CLAUDE.md — verify enforces most of these)

Doctrine: BE WATER — reshape headline, summary, skill labels/order, and bullet
emphasis to fill this job's container as strongly as the truth allows. A
tailored resume that reads like the master is a tailoring failure.

Allowed: rewrite the headline to mirror the target role (if it describes work
genuinely done); reword existing bullets using JD language; reorder/relabel
skills; rewrite the summary; drop weak bullets.

NEVER: invent experience/skills/tools/employers/dates/achievements; add a
skill just because the JD wants it; inflate scope, seniority, or metrics;
change dates, titles, or company names. If the job needs something the master
doesn't have, leave it out.

Workflow: edit content.tailored.yaml, then run
    python3 -m backend.cli verify --dir <this directory>
which diffs against the master and compiles the PDF. Fix anything it flags.
"""


def upsert_auto_answer(workdir: str, match: str, answer: str) -> None:
    """Add/replace an auto-computed answer in answers.yaml without touching
    hand-written entries (essays Claude wrote stay; only same-match auto
    entries from a previous tailor run are replaced)."""
    path = os.path.join(workdir, "answers.yaml")
    items = []
    if os.path.exists(path):
        with open(path) as f:
            items = yaml.safe_load(f) or []
    items = [it for it in items
             if not (it.get("auto") and it.get("match") == match)]
    items.append({"match": match, "answer": answer, "auto": True})
    with open(path, "w") as f:
        yaml.safe_dump(items, f, sort_keys=False, allow_unicode=True)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().encode()).hexdigest()[:8]


def find_workspace_by_url(url: str, base_dir: str = APPLICATIONS_DIR) -> Optional[str]:
    """Locate an existing workspace directory for this exact job URL by
    reading each workspace's job.md (which always records '- URL: ...').
    Workspace IDENTITY is the URL, not the directory name — this lets
    old-style (pre-hash-suffix) and new-style workspace names coexist
    without ever creating a duplicate folder for the same job."""
    if not os.path.isdir(base_dir):
        return None
    for name in os.listdir(base_dir):
        d = os.path.join(base_dir, name)
        jm = os.path.join(d, "job.md")
        if not os.path.isfile(jm):
            continue
        try:
            with open(jm) as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(r"^- URL: (\S+)", text, re.M)
        if m and m.group(1).strip() == url.strip():
            return d
    return None


def make_package(row: dict, base_dir: str = APPLICATIONS_DIR,
                 master: str = CONTENT_YAML) -> str:
    """Create (or reuse) the tailoring workspace for one job. Identity is
    the job's URL: if a workspace already exists for this URL (found via
    find_workspace_by_url, regardless of its directory name), it is reused
    in place. Otherwise a new directory is created, named with a short
    URL-hash suffix — two different postings that happen to share a
    company+title (e.g. multiple "Lead Value Engineer" openings at the same
    company, a real occurrence) can never collide into the same folder."""
    d = find_workspace_by_url(row["URL"], base_dir)
    if d is None:
        slug = f"{slugify(row['Company'])}--{slugify(row['Title'])}-{url_hash(row['URL'])}"
        d = os.path.join(base_dir, slug)
    os.makedirs(d, exist_ok=True)

    with open(os.path.join(d, "job.md"), "w") as f:
        f.write(f"# {row['Title']} — {row['Company']}\n\n")
        f.write(f"- URL: {row['URL']}\n- Location: {row['Location']}\n")
        f.write(f"- AI fit (current resume): {row['Fit %']}%\n")
        f.write(f"- Required years: {row.get('Req Years') or 'not stated'}"
                f" (meets: {row.get('Meets Years','yes')})\n\n")
        if row.get("AI Reason"):
            f.write(f"## Fit assessment\n{row['AI Reason']}\n\n")
        f.write(f"## Strengths the resume already shows\n{row['Matched Skills'] or '(none)'}\n\n")
        # Reframed for the tailor-to-raise-fit goal (Phase C): these are the
        # gaps holding the fit % down. Close them ONLY where the master resume
        # genuinely supports it — surfacing real-but-buried experience. Never
        # invent anything to close a gap (verify() enforces this).
        f.write(f"## Fit gaps — raise the fit by addressing these where TRUTHFUL\n"
                f"{row['Missing Skills'] or '(none)'}\n\n")
        desc = row.get("Description", "")
        if desc:
            f.write(f"## Job description\n\n{desc}\n")

    with open(master) as src, open(os.path.join(d, "content.tailored.yaml"), "w") as dst:
        dst.write(src.read())

    with open(os.path.join(d, "RULES.md"), "w") as f:
        f.write(RULES_MD)
    return d


# ----------------------------------------------------------------- verify
_METRIC = re.compile(r"\b\d+(?:[+%]|\s*-\s*\d+)?")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_PLUS = re.compile(r"^(\d+)\+$")


def _numbers(text: str) -> Set[str]:
    return {m.group(0).replace(" ", "") for m in _METRIC.finditer(text)}


def _truthful_subclaims(nums: Set[str]) -> Set[str]:
    """A stated range or "+" claim truthfully implies each number inside it.
    Master says "10-12" (a team size of 10-12) -> tailored truthfully saying
    "12" isn't a new claim, it's a real value from the same stated range;
    without this, V5 flagged real production output as fabrication. "5+"
    similarly implies "5" (the stated floor) is a truthful claim on its own,
    but NOT any number above it — that would be an unverified inflation."""
    out = set(nums)
    for n in nums:
        m = _RANGE.match(n)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 <= hi - lo <= 100:
                out.update(str(i) for i in range(lo, hi + 1))
            continue
        m = _PLUS.match(n)
        if m:
            out.add(m.group(1))
    return out


def verify(tailored_yaml: str, master: str = CONTENT_YAML,
           template: str = TEMPLATE_TYP) -> Tuple[bool, List[str]]:
    problems: List[str] = []
    with open(master) as f:
        m_raw = f.read()
    with open(tailored_yaml) as f:
        t_raw = f.read()
    M, T = yaml.safe_load(m_raw), yaml.safe_load(t_raw)

    # V1 contact identity — the HEADLINE (contact.title) is deliberately
    # exempt: CLAUDE.md allows reshaping it per job ("be water"). Name,
    # photo, and contact details must stay identical.
    mc_ = {k: v for k, v in M["contact"].items() if k != "title"}
    tc_ = {k: v for k, v in T["contact"].items() if k != "title"}
    if mc_ != tc_:
        problems.append("V1: contact identity changed (name/photo/contact info "
                        "must stay identical; only the headline may be tailored)")

    # V2 experience identity
    me, te = M["experience"], T["experience"]
    if [c["company"] for c in me] != [c["company"] for c in te]:
        problems.append("V2: company list changed")
    else:
        for mc, tc in zip(me, te):
            if mc.get("meta") != tc.get("meta"):
                problems.append(f"V2: meta line changed for {mc['company']}")
            mr = [(r["title"], r.get("dates")) for r in mc["roles"]]
            tr = [(r["title"], r.get("dates")) for r in tc["roles"]]
            if mr != tr:
                problems.append(f"V2: role titles/dates changed for {mc['company']}")

    # V3 education
    md, td = M["education"], T["education"]
    if [(x["degree"], x["meta"]) for x in md["degrees"]] != \
       [(x["degree"], x["meta"]) for x in td["degrees"]]:
        problems.append("V3: degrees changed")
    if {c.strip() for c in md["certifications"].split("|")} != \
       {c.strip() for c in td["certifications"].split("|")}:
        problems.append("V3: certification set changed")

    # V4 no invented skills (taxonomy level)
    invented = extract_skills(t_raw) - extract_skills(m_raw)
    if invented:
        problems.append(f"V4: skills present that the master never claims: {sorted(invented)}")

    # V5 no invented numeric claims (numbers truthfully implied by a stated
    # range/floor in the master, e.g. "12" from "10-12", don't count as new)
    new_nums = _numbers(t_raw) - _truthful_subclaims(_numbers(m_raw))
    if new_nums:
        problems.append(f"V5: numeric claims not present in master: {sorted(new_nums)}")

    # V6 page count identical to master's output
    def pages(content_file: str, out: str) -> int:
        # --root <project> + a root-relative /path for the content input:
        # typst resolves the yaml() path relative to template.typ (which
        # lives in resume/) and refuses paths that escape its root, so a
        # workspace file under data/applications/ must be addressed from an
        # explicitly-widened project root, not relative to the template.
        rel = os.path.relpath(os.path.abspath(content_file), os.getcwd())
        r = subprocess.run(
            ["typst", "compile", "--root", os.getcwd(),
             "--input", f"content=/{rel}", template, out],
            capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"V6: typst compile failed for {content_file}: "
                            f"{r.stderr.strip()[:300]}")
            return -1
        import fitz
        return fitz.open(out).page_count

    out_pdf = tailored_yaml.rsplit(".yaml", 1)[0] + ".pdf"
    tp = pages(tailored_yaml, out_pdf)
    mp = pages(master, "/tmp/applybro_master_check.pdf")
    if tp > 0 and mp > 0 and tp != mp:
        problems.append(f"V6: tailored PDF is {tp} page(s), master is {mp} — tighten wording")

    return (not problems), problems
