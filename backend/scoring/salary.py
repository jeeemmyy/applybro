"""Per-job salary estimation.

Same honesty standard as fit scoring (CLAUDE.md): this produces a DEFENSIBLE
market estimate with visible reasoning, never a fake-precise number. It is
built from public, order-of-magnitude market bands for a mid-level (3-5y)
software engineer in each country's tech hub, adjusted for the job's detected
seniority and how the user's actual experience compares to what the JD asks
for. It is explicitly a starting point for the user to edit, not a quote.

Bands are approximate annual GROSS totals in local currency, deliberately
round numbers — precision here would be dishonest given the inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

# country -> (typical mid-level annual gross, currency)
COUNTRY_BANDS = {
    "germany": (65000, "EUR"), "netherlands": (68000, "EUR"),
    "ireland": (70000, "EUR"), "france": (55000, "EUR"),
    "spain": (42000, "EUR"), "portugal": (38000, "EUR"),
    "denmark": (62000, "EUR"), "sweden": (52000, "EUR"),
    "finland": (50000, "EUR"), "austria": (58000, "EUR"),
    "belgium": (55000, "EUR"), "switzerland": (95000, "CHF"),
    "czechia": (32000, "EUR"), "czech republic": (32000, "EUR"),
    "poland": (30000, "EUR"), "italy": (40000, "EUR"),
    "united kingdom": (55000, "GBP"), "uk": (55000, "GBP"),
    "united states": (110000, "USD"), "usa": (110000, "USD"),
}
DEFAULT_BAND = (50000, "EUR")   # unrecognized country / generic EU remote

LEVEL_MULT = {
    "junior": 0.75, "mid": 1.0, "senior": 1.25,
    "staff": 1.55, "principal": 1.55, "lead": 1.45, "manager": 1.5,
}
LEVEL_TYPICAL_YEARS = {
    "junior": 1, "mid": 3, "senior": 6, "staff": 9,
    "principal": 10, "lead": 8, "manager": 8,
}

_LEVEL_PATTERNS: List[Tuple[str, str]] = [
    ("principal", r"\bprincipal\b"),
    ("staff", r"\bstaff\b"),
    ("lead", r"\blead\b"),
    ("manager", r"\b(engineering|team) manager\b"),
    ("senior", r"\bsenior\b|\bsr\.?\b"),
    ("junior", r"\bjunior\b|\bjr\.?\b|\bgraduate\b|\bintern\b|\bentry.level\b"),
]


def detect_level(title: str, jd_text: str = "") -> Tuple[str, str]:
    """Returns (level, reason)."""
    t = (title or "").lower()
    for level, pat in _LEVEL_PATTERNS:
        if re.search(pat, t):
            return level, f"title contains '{level}'"
    j = (jd_text or "").lower()[:2000]
    for level, pat in _LEVEL_PATTERNS:
        if re.search(pat, j):
            return level, f"job description mentions '{level}'"
    return "mid", "no seniority keyword found in title/JD — assumed mid-level"


_COUNTRY_NAMES = sorted(COUNTRY_BANDS.keys(), key=len, reverse=True)


def parse_country(location: str) -> Tuple[str, str]:
    """Returns (country_key_or_'', reason). '' means unrecognized/remote."""
    loc = (location or "").strip()
    low = loc.lower()
    if "remote" in low and not any(c in low for c in _COUNTRY_NAMES):
        return "", "location says Remote with no specific country"
    # prefer the last comma-separated segment ("Berlin, Germany" -> "Germany")
    last = low.split(",")[-1].strip()
    for c in _COUNTRY_NAMES:
        if c == last or c in low:
            return c, f"parsed '{loc}'"
    return "", f"couldn't recognize a country in '{loc}'"


@dataclass
class SalaryEstimate:
    level: str
    country: str          # display name, "" if unrecognized
    currency: str
    low: int
    mid: int
    high: int
    reasoning: List[str] = field(default_factory=list)

    def render(self) -> str:
        return f"{self.currency} {self.low:,} - {self.high:,} (target ~{self.currency} {self.mid:,})"


def estimate(title: str, jd_text: str, location: str,
            my_years: float = 5, req_years: int = 0) -> SalaryEstimate:
    reasoning = []

    level, why = detect_level(title, jd_text)
    reasoning.append(f"Seniority: {level} ({why}).")

    ckey, cwhy = parse_country(location)
    base, currency = COUNTRY_BANDS.get(ckey, DEFAULT_BAND)
    country_disp = ckey.title() if ckey else "Unrecognized/Remote"
    reasoning.append(
        f"Country: {country_disp} ({cwhy}) — mid-level market anchor "
        f"{currency} {base:,}/yr.")
    if not ckey:
        reasoning.append("No country match: using a generic Western-Europe "
                         "remote band — verify manually before relying on this.")

    mult = LEVEL_MULT.get(level, 1.0)
    point = base * mult
    reasoning.append(f"Level adjustment: x{mult} for '{level}' -> "
                     f"{currency} {round(point):,}.")

    # experience-fit nudge: JD wants more years than the user has -> lean
    # toward the lower end of the band; user exceeds the ask -> lean higher.
    typical = LEVEL_TYPICAL_YEARS.get(level, 3)
    ask = req_years or typical
    if my_years < ask:
        skew = -0.06
        reasoning.append(f"JD implies ~{ask}y experience; you have {my_years}y "
                         f"— leaning toward the lower end of the range.")
    elif my_years > ask + 2:
        skew = 0.06
        reasoning.append(f"You exceed the typical {ask}y for this level "
                         f"({my_years}y) — leaning toward the higher end.")
    else:
        skew = 0.0

    low = point * (0.90 + skew)
    high = point * (1.10 + skew)

    def rnd(x):
        return int(round(x / 1000.0) * 1000)

    reasoning.append("This is a market estimate from public salary-band data, "
                     "not a quote — edit it if you know better (e.g. via "
                     "levels.fyi/Glassdoor for this specific company).")
    return SalaryEstimate(level=level, country=country_disp, currency=currency,
                          low=rnd(low), mid=rnd(point), high=rnd(high),
                          reasoning=reasoning)
