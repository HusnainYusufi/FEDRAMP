from __future__ import annotations

import re


# Minimal deterministic mapping rules. Expand over time with org-specific patterns.
_RULES: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\bwarning banner\b|\blogin banner\b|\bssh banner\b", re.IGNORECASE), "AC-8", 0.9),
    (re.compile(r"\bmulti[- ]factor\b|\bMFA\b", re.IGNORECASE), "IA-2", 0.85),
    (re.compile(r"\bpassword policy\b|\bminimum password length\b|\bcomplexity\b", re.IGNORECASE), "IA-5", 0.75),
    (re.compile(r"\bcloudtrail\b|\baudit log\b|\blogging enabled\b", re.IGNORECASE), "AU-2", 0.7),
    (re.compile(r"\bpublicly accessible\b|\bpublic access\b|\bS3 public\b", re.IGNORECASE), "AC-3", 0.6),
    (re.compile(r"\bencrypt(ed|ion)\b|\bKMS\b", re.IGNORECASE), "SC-28", 0.6),
]


def rule_map_finding(*, title: str, description: str | None) -> tuple[str | None, float | None]:
    text = (title or "") + "\n" + (description or "")
    for pat, control_id, conf in _RULES:
        if pat.search(text):
            return control_id, conf
    return None, None

