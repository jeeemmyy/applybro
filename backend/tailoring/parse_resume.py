"""Parse an uploaded PDF resume into ApplyBro's candidate-facts YAML.

SaaS Phase 2 (decided 2026-07-14): users upload a PDF, nothing else. The PDF's
text is extracted locally (pypdf — the file itself is never sent to a model
provider) and the model converts that text into the content.yaml schema — the
same structure scoring, tailoring, verify() and the typst renderer already
consume, so everything downstream works unchanged for parsed resumes.

TRUTH RULES: the output may contain ONLY facts present in the PDF text. A
section the resume doesn't have is omitted, never invented — this file is the
user's ground truth that every later no-fabrication check diffs against, so
an invention here would poison the whole pipeline.
"""
from __future__ import annotations

import io
from typing import Tuple

import yaml

from .. import ai

PARSE_TIMEOUT = 420

# The schema's top-level sections (resume/content.example.yaml). contact +
# experience are the floor for a usable resume; the rest are optional.
SCHEMA_KEYS = ("contact", "summary", "skills", "featured", "experience",
               "education")
REQUIRED_KEYS = ("contact", "experience")

_PROMPT = """Convert this resume text (extracted from an uploaded PDF) into
YAML with EXACTLY this structure — it feeds an automated pipeline, so the
shape must match:

contact:
  name: <full name>
  title: <professional headline, e.g. "Full Stack Developer">
  info:                      # one entry per contact item found
    - text: <city/location>
    - text: <phone>
      link: true
      href: tel:<phone>
    - text: <email>
      link: true
      href: mailto:<email>
    - text: <shortened url, e.g. linkedin.com/in/x>
      link: true
      href: <full url>
summary: >-
  <the resume's summary/profile paragraph>
skills:                      # label + one line of items, from the resume's
  - label: <group name>      # own skill groupings where it has them
    text: <comma-separated items>
featured:                    # ONE highlighted project section, if the resume
  title: <project name>      # has one (a mapping, not a list)
  bullets:
    - <each bullet, text preserved faithfully>
experience:                  # one entry per EMPLOYER
  - company: <employer>
    meta: <location / employment type line as written, if stated>
    summary: <the employer-level intro line, if the resume has one>
    roles:                   # one per title held there (usually one)
      - title: <job title>
        dates: <as written, e.g. "Jan 2021 - Present">
        bullets:
          - <each bullet, text preserved faithfully>
education:                   # a mapping, not a list
  degrees:
    - degree: <degree as written>
      meta: <institution, location | dates — as written>
  certifications: <one line joining any certifications with " | ">
  notes:
    - <any other education-section line>

HARD RULES — this file becomes the user's verified ground truth:
- ONLY facts that appear in the resume text below. NEVER invent, infer, or
  embellish anything: no skills, employers, dates, titles, metrics, or
  summary claims that aren't in the text.
- Omit any section the resume doesn't have (drop the key entirely). Never
  include a `photo` key.
- Keep bullet wording essentially verbatim (fix only obvious PDF-extraction
  artifacts like broken words or stray line-break hyphens).
- If the text is clearly NOT a resume, output exactly: NOT_A_RESUME

RESUME TEXT:
<<<
{text}
>>>

Output ONLY the YAML document — no markdown fences, no commentary.
"""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _validate(text: str) -> Tuple[bool, str]:
    """Returns (ok, yaml_text-or-error). Structure must match the schema —
    a malformed parse must never become a user's ground truth."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, f"model output wasn't valid YAML: {str(e)[:120]}"
    if not isinstance(data, dict):
        return False, "model output wasn't a YAML mapping"
    unknown = set(data.keys()) - set(SCHEMA_KEYS)
    if unknown:
        return False, f"unexpected sections: {sorted(unknown)}"
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        return False, (f"couldn't find {' and '.join(missing)} in the PDF — "
                       "is this a complete resume?")
    contact = data.get("contact") or {}
    if not str(contact.get("name") or "").strip():
        return False, "couldn't find a name in the PDF"
    contact.pop("photo", None)   # belt and braces: parsed resumes have none
    return True, yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                                width=88)


def parse_pdf_resume(pdf_bytes: bytes) -> Tuple[bool, str]:
    """PDF bytes -> (True, content_yaml) or (False, user-facing error)."""
    if not pdf_bytes.startswith(b"%PDF"):
        return False, "That file doesn't look like a PDF."
    try:
        text = extract_pdf_text(pdf_bytes)
    except Exception as e:
        return False, f"Couldn't read the PDF: {type(e).__name__}"
    if len(text.strip()) < 200:
        return False, ("Couldn't extract text from this PDF — it may be a "
                       "scan/image. Export a text-based PDF and try again.")

    ok, out = ai.complete(_PROMPT.format(text=text[:24000]),
                          feature="extraction", timeout=PARSE_TIMEOUT)
    if not ok:
        return False, out
    if "NOT_A_RESUME" in (out or "")[:200]:
        return False, "That PDF doesn't look like a resume."

    # Tolerate fences/preamble the same way tailoring does (seen in prod).
    from .reword import _extract_yaml
    cleaned = _extract_yaml(out)
    if cleaned is None:
        return False, "The model's output couldn't be read — try again."
    return _validate(cleaned)
