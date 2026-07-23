"""Job data model and small text helpers shared across ATS adapters."""
from __future__ import annotations

from dataclasses import dataclass
import html
import re


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str            # which adapter found it: greenhouse / lever / ashby / workday / jsonld
    description: str = ""   # plain-text job description (may be empty for some sources)
    job_id: str = ""        # ATS-native id when available

    def key(self) -> str:
        """Stable de-duplication key. Prefer the job URL; fall back to ids."""
        if self.url:
            return self.url.strip().lower()
        return f"{self.company}:{self.job_id}:{self.title}".strip().lower()


def strip_html(s: str) -> str:
    """Turn an HTML fragment into readable plain text."""
    if not s:
        return ""
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)   # trim spaces hugging newlines
    s = re.sub(r"\n{3,}", "\n\n", s)         # cap blank runs at one blank line
    return s.strip()
