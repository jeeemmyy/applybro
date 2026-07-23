"""Layer 2 — fit scoring (per CLAUDE.md rules).

fit_score = (matched required skills / total required skills) * experience_factor

- "required skills" = taxonomy terms that literally appear in the job
  description. We never guess requirements that aren't written there.
- "matched" = the subset of those that ALSO literally appear in content.yaml
  (same matcher on both sides), so the number is fully auditable: matched and
  missing lists are stored next to every score.
- experience_factor = 1.0 if the JD states no years threshold or I meet it,
  else my_years / required_years (capped at 1.0).

This is a real, defensible overlap percentage. It is NOT a probability of
hearing back and must never be presented as one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple
import re

import yaml

from ..paths import CONTENT_YAML
from .taxonomy import TAXONOMY


# ---------------------------------------------------------------- matching
def _alias_pattern(alias: str) -> re.Pattern:
    # Word-ish boundaries that tolerate ".", "#", "+", "/" inside tech terms.
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", re.I)


_COMPILED: Dict[str, List[re.Pattern]] = {
    canon: [_alias_pattern(a) for a in aliases] for canon, aliases in TAXONOMY.items()
}

# Aliases so short/ambiguous they only count when the canonical form also
# appears somewhere, to avoid e.g. "go" in "go to market" or "ts" in prose.
AMBIGUOUS = {"js", "ts", "go developer", "(go)", "make.com", "node", "english"}


def extract_skills(text: str) -> Set[str]:
    """Canonical taxonomy skills literally present in `text`."""
    t = text or ""
    found: Set[str] = set()
    for canon, pats in _COMPILED.items():
        for alias, pat in zip(TAXONOMY[canon], pats):
            if alias in AMBIGUOUS:
                continue
            if pat.search(t):
                found.add(canon)
                break
    return found


# ------------------------------------------------------- experience threshold
_YEARS = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?)?\s*(\d{1,2})\s*(?:-\s*\d{1,2}\s*)?"
    r"\+?\s*(?:years?|yrs?)(?!\s*ago)\b[^.\n]{0,60}?"
    r"(?:experience|exp\b)", re.I)


def required_years(jd: str) -> int:
    """Highest explicit 'N years ... experience' the JD states; 0 if none."""
    vals = [int(m.group(1)) for m in _YEARS.finditer(jd or "")]
    vals = [v for v in vals if 1 <= v <= 20]
    return max(vals) if vals else 0


# --------------------------------------------------------------------- score
@dataclass
class Fit:
    score: int              # 0-100 defensible overlap percentage
    matched: List[str]
    missing: List[str]
    req_years: int          # 0 = JD states no threshold
    meets_years: bool

    @property
    def req_count(self) -> int:
        """How many taxonomy skills the JD stated. A 100% on 2 stated skills
        is far weaker evidence than 80% on 12 — surface this, don't hide it."""
        return len(self.matched) + len(self.missing)


def load_profile_skills(content_yaml: str = CONTENT_YAML) -> Set[str]:
    """Skills that literally appear in content.yaml (whole file as text, so
    summary, skills lines, and every bullet all count — nothing else does)."""
    with open(content_yaml) as f:
        raw = f.read()
    return extract_skills(raw)


def score_job(jd_text: str, profile: Set[str], my_years: float) -> Fit:
    req = extract_skills(jd_text)
    if not req:
        return Fit(score=0, matched=[], missing=[], req_years=0, meets_years=True)
    matched = sorted(req & profile)
    missing = sorted(req - profile)
    base = len(matched) / len(req)
    ry = required_years(jd_text)
    meets = ry == 0 or my_years >= ry
    factor = 1.0 if meets else max(0.0, my_years / ry)
    return Fit(score=round(base * factor * 100), matched=matched,
               missing=missing, req_years=ry, meets_years=meets)
