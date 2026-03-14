from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProhibitedMatch:
    rule_id: str
    phrase: str
    start: int
    end: int
    line: int
    excerpt: str


_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("not_evidenced", "Not evidenced", re.compile(r"\bnot\s+evidenced\b", re.IGNORECASE)),
    (
        "not_effectively_implemented",
        "Not effectively implemented",
        re.compile(r"\bnot\s+effectively\s+implemented\b", re.IGNORECASE),
    ),
    ("not_implemented", "Not implemented", re.compile(r"\bnot\s+implemented\b", re.IGNORECASE)),
    ("absent", "Absent", re.compile(r"\babsent\b", re.IGNORECASE)),
    ("missing", "Missing", re.compile(r"\bmissing\b", re.IGNORECASE)),
    ("failure", "Failure", re.compile(r"\bfailure(s)?\b", re.IGNORECASE)),
    ("does_not", "Does not", re.compile(r"\bdoes\s+not\b", re.IGNORECASE)),
    ("lack_of", "Lack of", re.compile(r"\black\s+of\b", re.IGNORECASE)),
]


def find_prohibited_language(text: str) -> list[ProhibitedMatch]:
    s = text or ""
    if not s.strip():
        return []

    # Precompute line start offsets for stable line mapping.
    line_starts = [0]
    for m in re.finditer(r"\n", s):
        line_starts.append(m.end())

    def _line_for_offset(off: int) -> int:
        # 1-indexed line number
        lo, hi = 0, len(line_starts) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if line_starts[mid] <= off:
                lo = mid + 1
            else:
                hi = mid - 1
        return hi + 1

    out: list[ProhibitedMatch] = []
    for rule_id, phrase, pat in _RULES:
        for m in pat.finditer(s):
            start, end = m.start(), m.end()
            ln = _line_for_offset(start)
            excerpt = s[max(0, start - 60) : min(len(s), end + 60)].replace("\n", " ")
            out.append(
                ProhibitedMatch(
                    rule_id=rule_id,
                    phrase=phrase,
                    start=start,
                    end=end,
                    line=ln,
                    excerpt=excerpt,
                )
            )

    # De-dupe exact overlaps while preserving order.
    seen: set[tuple[str, int, int]] = set()
    deduped: list[ProhibitedMatch] = []
    for m in out:
        key = (m.rule_id, m.start, m.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped

