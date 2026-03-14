from __future__ import annotations

import re
from typing import Any


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            s = str(x or "").strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    return [s] if s else []


_SANITIZE_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnot\s+evidenced\b", re.IGNORECASE), "requires additional supporting artifacts"),
    (re.compile(r"\bnot\s+effectively\s+implemented\b", re.IGNORECASE), "is being strengthened"),
    (re.compile(r"\bnot\s+implemented\b", re.IGNORECASE), "is planned and tracked for implementation"),
    (re.compile(r"\babsent\b", re.IGNORECASE), "not currently reflected in this snapshot"),
    (re.compile(r"\bmissing\b", re.IGNORECASE), "not currently reflected in this snapshot"),
    (re.compile(r"\bfailure(s)?\b", re.IGNORECASE), "deviation(s)"),
    (re.compile(r"\bdoes\s+not\b", re.IGNORECASE), "is not currently configured to"),
    (re.compile(r"\black\s+of\b", re.IGNORECASE), "opportunity to strengthen"),
]


def sanitize_internal_text(text: str) -> str:
    """
    Convert internal validator phrasing into SSP-safe language.

    This is a best-effort transformation used BEFORE the SSP writer sees any content.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    for pat, repl in _SANITIZE_SUBS:
        s = pat.sub(repl, s)

    # Remove overly operational "evidence count" phrasing that does not belong in SSP narratives.
    # (Counts belong in assessment artifacts; SSP should remain qualitative.)
    s = re.sub(r"\b\d+\s+IAM\s+users?\b", "IAM users", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d+\s+IAM\s+roles?\b", "IAM roles", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d+\s+IAM\s+groups?\b", "IAM groups", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d+\s+IAM\s+polic(?:y|ies)\b", "IAM policies", s, flags=re.IGNORECASE)
    s = re.sub(
        r"\b(IAM\s+(?:users?|roles?|groups?|polic(?:y|ies)))\s+(?:were\s+)?(enumerated|identified|detected|observed|found)\b",
        r"\1 are used",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bIAM\s+user\s+count\s*[:=]\s*\d+\b",
        "IAM users are used",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bIAM\s+role\s+count\s*[:=]\s*\d+\b",
        "IAM roles are used",
        s,
        flags=re.IGNORECASE,
    )

    # Normalize whitespace.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def map_status_for_ssp(implementation_status: str | None) -> str:
    s = (implementation_status or "").strip()
    if s in {"Implemented", "Partially Implemented", "Planned"}:
        return s
    if s == "Not Implemented":
        return "Planned"
    return "Planned"


def _pick_variant(key: str, variants: list[str]) -> str:
    if not variants:
        return ""
    k = str(key or "")
    idx = sum(ord(c) for c in k) % len(variants)
    return variants[idx]


def derive_enhancement_notes(gaps: list[str]) -> list[str]:
    """
    Turn validator gaps into SSP narrative-safe enhancement notes.
    """
    out: list[str] = []
    for g in gaps[:8]:
        low = (g or "").lower()
        if "cloudtrail" in low:
            out.append(
                _pick_variant(
                    g,
                    [
                        "Audit logging configurations (e.g., CloudTrail) are reviewed under continuous monitoring, with refinements governed through standard change management.",
                        "Continuous monitoring validates audit trail coverage, and planned improvements are prioritized through governance workflows, including POA&M tracking as applicable.",
                        "Logging and monitoring controls continue to mature through periodic review and configuration management oversight.",
                    ],
                )
            )
            continue
        if "mfa" in low or "multi-factor" in low or "multifactor" in low:
            out.append(
                _pick_variant(
                    g,
                    [
                        "Multi-factor authentication enforcement is validated through continuous monitoring and periodic access reviews.",
                        "Authentication enforcement continues to mature through governance oversight, with improvements incorporated through POA&M tracking as applicable.",
                        "Access control reviews validate MFA coverage and drive iterative hardening activities over time.",
                    ],
                )
            )
            continue
        if "retention" in low and "log" in low:
            out.append(
                _pick_variant(
                    g,
                    [
                        "Log retention configurations are standardized through configuration management and validated through ongoing monitoring activities.",
                        "Continuous monitoring verifies retention alignment, and refinements are incorporated through governance workflows, including POA&M tracking as applicable.",
                        "Retention settings are reviewed periodically to confirm alignment with policy and monitoring expectations.",
                    ],
                )
            )
            continue
        if "encryption" in low:
            out.append(
                _pick_variant(
                    g,
                    [
                        "Cryptographic configuration coverage is reviewed periodically, with improvements incorporated through standard change management and monitoring governance.",
                        "Continuous monitoring validates encryption and key management posture, with refinements prioritized through POA&M tracking as applicable.",
                        "Configuration reviews validate cryptographic settings and drive iterative maturity improvements.",
                    ],
                )
            )
            continue
        # Generic fallback.
        out.append(
            _pick_variant(
                g,
                [
                    "Continuous monitoring activities track control maturity and drive iterative improvement.",
                    "Configuration management and periodic reviews validate implementation maturity over time.",
                    "Enhancements identified during governance reviews are incorporated through standard change management, including POA&M tracking as applicable.",
                ],
            )
        )

    # De-dupe preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        deduped.append(x)
    return deduped


def build_ssp_strategy_input(
    *,
    control: dict[str, Any],
    validation_findings: dict[str, Any],
    governance_snippets: list[str],
    tone_tier: str,
    roadmap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the narrative-safe, client-facing input payload for SSP writing.

    IMPORTANT: This payload must avoid raw audit/validator language.
    """
    vf = validation_findings if isinstance(validation_findings, dict) else {}
    strengths = [sanitize_internal_text(x) for x in _as_str_list(vf.get("strengths"))]
    gaps = [sanitize_internal_text(x) for x in _as_str_list(vf.get("gaps"))]
    inherited = [sanitize_internal_text(x) for x in _as_str_list(vf.get("inherited_controls"))]
    cust = [sanitize_internal_text(x) for x in _as_str_list(vf.get("customer_responsibilities"))]
    remediation = [sanitize_internal_text(x) for x in _as_str_list(vf.get("remediation_items"))]

    status = map_status_for_ssp(str(vf.get("implementation_status") or "").strip() or None)

    enhancement_notes = derive_enhancement_notes(gaps) if status in {"Partially Implemented", "Planned"} else []

    poam_tracking_statement = (
        "Enhancements are managed through continuous monitoring governance, including POA&M tracking as applicable."
        if status in {"Partially Implemented", "Planned"}
        else ""
    )

    out: dict[str, Any] = {
        "control_id": str(control.get("control_id") or "").strip(),
        "control_title": str(control.get("title") or "").strip(),
        "control_family": str(control.get("family") or "").strip(),
        "baseline": str(control.get("baseline") or "").strip(),
        "implementation_status": status,
        "tone_tier": str(tone_tier or "").strip() or "meduim",
        "implemented_mechanisms": [x for x in strengths if x],
        "enhancement_notes": enhancement_notes,
        "inheritance_notes": [x for x in inherited if x],
        "shared_responsibility_notes": [x for x in cust if x],
        "remediation_items": [x for x in remediation if x],
        "poam_tracking_statement": poam_tracking_statement,
        "governance_context_snippets": [s for s in (governance_snippets or []) if str(s).strip()][:8],
        "roadmap": roadmap or {},
    }

    return out

