"""Which countries to tick on a "where would you work?" multi-select.

Application forms ask this as a checkbox grid, and every ATS spells the
options differently ("US" / "USA" / "United States of America", "UK" /
"United Kingdom", "The Netherlands" / "Netherlands", "South Korea" /
"Korea, Republic of"). This module owns the two things that vary:

  * canon()  — normalize whatever label the form used to one key.
  * selector() — turn the user's profile rule ("Europe, Australia, Canada,
    ..." minus "Romania, Bulgaria") into a predicate over those labels.

The literal token "Europe" in the include list expands to EUROPE below, so a
form listing Norway or Czechia (which Stripe's list happens not to) is still
handled. Pure logic: no Playwright, no I/O.
"""
from __future__ import annotations

import re
from typing import Callable, Set

# EU27 + UK, Switzerland, Norway, Iceland, Liechtenstein — the practical
# "Europe" for someone seeking work/sponsorship. Anything the user wants
# excluded (e.g. Romania, Bulgaria) is removed via the exclude list, not here.
EUROPE: Set[str] = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
    "denmark", "estonia", "finland", "france", "germany", "greece",
    "hungary", "ireland", "italy", "latvia", "lithuania", "luxembourg",
    "malta", "netherlands", "poland", "portugal", "romania", "slovakia",
    "slovenia", "spain", "sweden",
    "united kingdom", "switzerland", "norway", "iceland", "liechtenstein",
}

# Form spelling -> canonical key. Keys are already lowercase/stripped.
_ALIASES = {
    "us": "united states", "usa": "united states", "u.s.": "united states",
    "u.s.a.": "united states", "united states of america": "united states",
    "america": "united states",
    "uk": "united kingdom", "u.k.": "united kingdom",
    "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom",
    "the netherlands": "netherlands", "holland": "netherlands",
    "czech republic": "czechia",
    "south korea": "korea", "korea, republic of": "korea",
    "republic of korea": "korea", "korea (south)": "korea",
    "uae": "united arab emirates", "u.a.e.": "united arab emirates",
    "new zealand": "new zealand",
    "hong kong sar": "hong kong",
}
# Canonical names that EUROPE stores under a different spelling.
for _e in list(EUROPE):
    _ALIASES.setdefault(_e, _e)


def canon(label: str) -> str:
    """Normalize a checkbox's visible label to a comparable country key."""
    s = (label or "").strip().lower()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)      # drop "(+92)", "(South)" etc.
    s = re.sub(r"[^a-z\s,.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .,")
    return _ALIASES.get(s, s)


def _expand(spec: str) -> Set[str]:
    """Comma-separated rule -> set of canonical keys. 'Europe' expands."""
    out: Set[str] = set()
    for raw in (spec or "").split(","):
        tok = raw.strip()
        if not tok:
            continue
        if tok.lower() == "europe":
            out |= {canon(c) for c in EUROPE}
        else:
            out.add(canon(tok))
    return out


def selector(include: str, exclude: str = "") -> Callable[[str], bool]:
    """Predicate: should this checkbox label be ticked? Exclude always wins."""
    allow, deny = _expand(include), _expand(exclude)

    def should_check(label: str) -> bool:
        key = canon(label)
        return bool(key) and key in allow and key not in deny

    return should_check
