from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.config.logging_config import get_logger
from app.db.models import ControlRoadmap, SSPStyleExample
from app.services.ai_agent.narrative.language_policy import find_prohibited_language
from app.services.ai_agent.narrative.ssp_prompt import build_ssp_prompt
from app.services.ai_agent.narrative.ssp_template_prompt import build_ssp_template_prompt
from app.services.ai_agent.narrative.strategy import build_ssp_strategy_input
from app.services.ai_agent.rag import service as rag_service
from app.services.ai_agent.narrative import llm_client

logger = get_logger(__name__)


_REQ_HEADINGS = [
    "Control Overview",
    "Governance and Roles",
    "Technical Implementation",
    "Monitoring and Review",
    "Shared Responsibility",
    "Inheritance",
    "Continuous Improvement",
]


def _missing_headings(md: str) -> list[str]:
    s = md or ""
    missing: list[str] = []
    for h in _REQ_HEADINGS:
        if not re.search(rf"(?im)^\s*#+\s+{re.escape(h)}\s*$", s):
            missing.append(h)
    return missing


def _apply_inherited_placeholders(s: str) -> str:
    """
    Ensure inherited authorization metadata is represented as placeholders.

    We intentionally avoid emitting environment-specific values like:
      (AI-Agent, 02/18/2026)
    and instead keep:
      ({{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}})
    """
    out = s or ""

    # Replace configured values with placeholders (defensive: output may contain real values).
    nm = (getattr(settings, "fedramp_inherited_auth_name", "") or "").strip()
    dt = (getattr(settings, "fedramp_inherited_auth_date", "") or "").strip()
    if nm:
        out = out.replace(nm, "{{INHERITED_AUTH_NAME}}")
    if dt:
        out = out.replace(dt, "{{INHERITED_AUTH_DATE}}")

    # Normalize common patterns: "(Name, 02/18/2026)" -> "(placeholders)".
    out = re.sub(
        r"\(\s*[^()\n,]{2,}\s*,\s*\d{2}/\d{2}/\d{4}\s*\)",
        "({{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}})",
        out,
    )
    out = re.sub(
        r"(?i)Inherited\s+from\s+pre-existing\s+FedRAMP\s+Authorization\s+for\s+[^,\n]+\s*,\s*\d{2}/\d{2}/\d{4}",
        "Inherited from pre-existing FedRAMP Authorization for {{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}}",
        out,
    )
    out = re.sub(
        r"(?i)FedRAMP\s+authorized\s*\(\s*[^,\n]+\s*,\s*\d{2}/\d{2}/\d{4}\s*\)",
        "FedRAMP authorized ({{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}})",
        out,
    )

    # Normalize cloud inheritance wording to explicitly name the provider (avoid region-based claims).
    out = re.sub(r"(?i)\bAWS\s+East/West\b", "Amazon Web Services (AWS)", out)
    out = re.sub(
        r"(?i)\bwhich\s+is\s+FedRAMP\s+authorized\b",
        "which maintains a FedRAMP authorization",
        out,
    )
    out = re.sub(
        r"(?i)\bwhich\s+maintains\s+a\s+FedRAMP\s+(?:Moderate\s+)?(?:authorization|ATO)\b",
        "which maintains a FedRAMP authorization",
        out,
    )
    out = re.sub(r"Amazon Web Services \(AWS\)\s+which", "Amazon Web Services (AWS), which", out)
    out = re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",") if out else out
    return out


_AC2_INFRA_ANCHOR_PRESENT: dict[str, str] = {
    "a": "AWS IAM users, roles, and groups are used to define account types and administrative roles for the system. The system inherits applicable infrastructure security capabilities from Amazon Web Services (AWS), which maintains a FedRAMP authorization ({{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}}).",
    "b": "Account management privileges are constrained through IAM roles and policies to support accountability and separation of duties.",
    "c": "IAM group and role membership, along with conditional access controls, enforce prerequisites for privileged membership.",
    "d": "IAM policies and role assumptions define authorized users, group and role membership, and access authorizations for system administration.",
    "e": "Provisioning actions are limited to approved administrators via scoped IAM permissions and documented approval workflows.",
    "f": "IAM lifecycle functions are used to create, modify, disable, and remove identities and associated permissions.",
    "g": "Management-plane audit logs provide visibility into account usage and account management events for monitoring and alerting.",
    "h": "Workflow records and system event sources support time-bound notifications and traceability for account status changes.",
    "i": "IAM policy evaluation enforces access decisions based on approved authorization, intended usage, and configured attributes.",
    "j": "IAM reporting and inventory data support periodic privileged and non-privileged access reviews.",
    "k": "Credential management mechanisms support rotation of authenticators for any approved shared or group access mechanisms.",
    "l": "Identity lifecycle controls support timely disablement and removal aligned to personnel status changes.",
}

_AC2_INFRA_ANCHOR_PLANNED: dict[str, str] = {
    "a": "AWS IAM users, roles, and groups will be used to define account types and administrative roles for the system. The system will inherit applicable infrastructure security capabilities from Amazon Web Services (AWS), which maintains a FedRAMP authorization ({{INHERITED_AUTH_NAME}}, {{INHERITED_AUTH_DATE}}).",
    "b": "Account management privileges will be constrained through IAM roles and policies to support accountability and separation of duties.",
    "c": "IAM group and role membership, along with conditional access controls, will enforce prerequisites for privileged membership.",
    "d": "IAM policies and role assumptions will define authorized users, group and role membership, and access authorizations for system administration.",
    "e": "Provisioning actions will be limited to approved administrators via scoped IAM permissions and documented approval workflows.",
    "f": "IAM lifecycle functions will be used to create, modify, disable, and remove identities and associated permissions.",
    "g": "Management-plane audit logs will provide visibility into account usage and account management events for monitoring and alerting.",
    "h": "Workflow records and system event sources will support time-bound notifications and traceability for account status changes.",
    "i": "IAM policy evaluation will enforce access decisions based on approved authorization, intended usage, and configured attributes.",
    "j": "IAM reporting and inventory data will support periodic privileged and non-privileged access reviews.",
    "k": "Credential management mechanisms will support rotation of authenticators for any approved shared or group access mechanisms.",
    "l": "Identity lifecycle controls will support timely disablement and removal aligned to personnel status changes.",
}


_INHERIT_LINE_RE = re.compile(
    r"(?i)\b(partially\s+inherits|inherits\s+applicable\s+platform\s+capabilities|hosted\s+on|fedramp\s+authorized)\b"
)


def _normalize_ac2_template_skeleton(template_skeleton: str, *, implementation_status: str) -> str:
    """
    Normalize AC-2 style-example templates so they:
    - Use consistent Infrastructure/Application/Customer Responsibility subsections per Part
    - Avoid repeating inheritance sentences in every Part (inheritance is already captured in Control Origination)
    """
    text = (template_skeleton or "").replace("\r", "")
    part_re = re.compile(r"(?im)^Part\s+(?P<p>[a-l])\s*:\s*$")
    matches = list(part_re.finditer(text))
    if not matches:
        return text.strip() + "\n"

    def _is_hdr(line: str, hdr: str) -> bool:
        return bool(re.match(rf"(?i)^\s*{re.escape(hdr)}\s*:\s*$", (line or "").strip()))

    out: list[str] = []
    out.append(text[: matches[0].start()])

    for idx, m in enumerate(matches):
        part = (m.group("p") or "").lower()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end]
        lines = [ln.rstrip() for ln in body.splitlines()]

        has_subsections = any(_is_hdr(ln, "Infrastructure") for ln in lines) and any(
            _is_hdr(ln, "Application") for ln in lines
        )

        normalized_lines: list[str] = []
        is_planned = str(implementation_status or "").strip() == "Planned"
        anchor_map = _AC2_INFRA_ANCHOR_PLANNED if is_planned else _AC2_INFRA_ANCHOR_PRESENT
        anchor = anchor_map.get(part, "")

        if has_subsections:
            cur: str | None = None
            infra_anchor_written = False
            for ln in lines:
                s = (ln or "").strip()
                if not s and normalized_lines and normalized_lines[-1] != "":
                    normalized_lines.append("")
                    continue

                if _is_hdr(ln, "Infrastructure"):
                    cur = "infra"
                    normalized_lines.append("Infrastructure:")
                    if anchor:
                        normalized_lines.append(anchor)
                        infra_anchor_written = True
                    normalized_lines.append("")
                    continue
                if _is_hdr(ln, "Application"):
                    cur = "app"
                    normalized_lines.append("Application:")
                    normalized_lines.append("")
                    continue
                if _is_hdr(ln, "Customer Responsibility"):
                    cur = "cust"
                    normalized_lines.append("Customer Responsibility:")
                    normalized_lines.append("")
                    continue

                if cur == "infra" and _INHERIT_LINE_RE.search(s):
                    # Drop repeated inheritance boilerplate inside Parts.
                    continue

                normalized_lines.append(ln)

            # If an Infrastructure section existed but we didn't add an anchor, add one.
            if anchor and not infra_anchor_written:
                # Best effort: prepend an Infrastructure block.
                normalized_lines = ["Infrastructure:", anchor, ""] + normalized_lines
        else:
            app_lines: list[str] = []
            cust_lines: list[str] = []
            in_cust = False
            for ln in lines:
                s = (ln or "").strip()
                if not s:
                    continue
                if _INHERIT_LINE_RE.search(s):
                    continue
                if re.match(r"(?i)^(customers?|customer)\b", s):
                    in_cust = True
                    cust_lines.append(s)
                    continue
                if in_cust:
                    cust_lines.append(ln)
                else:
                    app_lines.append(ln)

            normalized_lines = [
                "Infrastructure:",
                anchor or "AWS IAM provides foundational identity and access primitives used by the system.",
                "",
                "Application:",
                *[x for x in app_lines if str(x).strip()],
                "",
                "Customer Responsibility:",
                *[x for x in cust_lines if str(x).strip()],
                "",
            ]

        # Emit the Part header + normalized body.
        out.append(f"Part {part}:\n")
        # Trim excess blank lines.
        while normalized_lines and normalized_lines[0] == "":
            normalized_lines.pop(0)
        while normalized_lines and normalized_lines[-1] == "":
            normalized_lines.pop()
        out.append("\n".join(normalized_lines).strip() + "\n\n")

    return "".join(out).rstrip() + "\n"


def _code_formatting_issues(md: str) -> list[str]:
    s = md or ""
    issues: list[str] = []
    if "```" in s:
        issues.append("Found Markdown code fence (```), which is prohibited.")
    # Inline code backticks produce <code> in the UI.
    if "`" in s:
        issues.append("Found inline code backticks (`), which is prohibited.")
    if re.search(r"(?i)<\s*/?\s*code\b", s):
        issues.append("Found HTML <code> tag, which is prohibited.")
    if re.search(r"(?i)<\s*/?\s*pre\b", s):
        issues.append("Found HTML <pre> tag, which is prohibited.")
    return issues


def _repetition_and_tone_issues(md: str) -> list[str]:
    s = md or ""
    issues: list[str] = []

    rr = len(re.findall(r"(?i)\bresidual\s+risk(s)?\b", s))
    if rr > 1:
        issues.append(
            f"Too many 'residual risk' mentions ({rr}). Prefer varied governance language (continuous monitoring, change management, periodic reviews)."
        )

    poam = len(re.findall(r"(?i)\bPOA&M\b", s))
    if poam > 6:
        issues.append(
            f"Too many POA&M mentions ({poam}). Mention POA&M sparingly and rotate phrasing across Parts."
        )

    # Repeated "managed risk" sentence fragments make the narrative sound templated.
    if len(re.findall(r"(?i)\bmanages\s+residual\s+risk\b", s)) > 1:
        issues.append("Repeated phrase 'manages residual risk'. Rephrase and avoid repeating identical risk language.")

    # Avoid repeating the same POA&M sentence verbatim.
    poam_sents = re.findall(r"(?is)([^\n.!?]*\bPOA&M\b[^\n.!?]*[.!?])", s)
    norm: list[str] = [re.sub(r"\s+", " ", x).strip().lower() for x in poam_sents if x.strip()]
    dup = {x for x in norm if norm.count(x) > 1 and len(x.split()) >= 6}
    if dup:
        issues.append("Repeated POA&M sentence detected. Keep POA&M references but vary wording across Parts.")

    # Avoid repeating inheritance boilerplate in every Part.
    inher = len(re.findall(r"(?i)\binherits\s+applicable\s+platform\s+capabilities\b", s))
    if inher > 1:
        issues.append(
            f"Repeated inheritance boilerplate ('inherits applicable platform capabilities') found ({inher}). Mention inheritance once or twice (prefer Control Origination and optionally Part a)."
        )
    auth_refs = len(re.findall(r"\{\{INHERITED_AUTH_NAME\}\}", s))
    if auth_refs > 2:
        issues.append(
            f"Too many inherited authorization references ({auth_refs}). Keep inherited authorization placeholders in Control Origination and optionally one Part."
        )

    aws_boiler = len(re.findall(r"(?i)\bthe\s+organization\s+leverages\s+amazon\s+web\s+services\s*\(aws\)", s))
    if aws_boiler > 2:
        issues.append(
            f"Repeated boilerplate opener 'The organization leverages Amazon Web Services (AWS)...' ({aws_boiler}). Use direct implementation language (e.g., 'AWS IAM users, roles, and groups are used...') and avoid repeating the same sentence pattern."
        )

    # Planned-mode phrasing: avoid overusing "planning to / preparing to".
    planning = len(re.findall(r"(?i)\b(is\s+)?(planning|preparing)\s+to\b", s))
    if planning > 3:
        issues.append(
            f"Too many 'planning to / preparing to' phrases ({planning}). Prefer direct future-tense implementation statements (e.g., 'will be implemented', 'will be maintained')."
        )

    return issues


def _evidence_detail_issues(md: str) -> list[str]:
    s = md or ""
    issues: list[str] = []

    if re.search(r"(?i)\b\d+\s+IAM\s+(users?|roles?|groups?|polic(?:y|ies))\b", s):
        issues.append("Numeric IAM evidence counts found (e.g., '5 IAM users'). Use qualitative SSP language instead.")

    if re.search(
        r"(?i)\bIAM\s+(users?|roles?|groups?|polic(?:y|ies))\b\s+(?:were\s+)?(enumerated|identified|detected|observed|found)\b",
        s,
    ):
        issues.append("Evidence-collection verbs (enumerated/identified/etc.) found. Replace with SSP narrative phrasing (e.g., 'are used').")

    return issues


def _template_part_section_issues(md: str) -> list[str]:
    """
    Template-mode structural checks: enforce that each Part contains
    Infrastructure/Application/Customer Responsibility as Markdown subheadings.
    """
    s = md or ""
    matches = list(_PART_HDR_RE.finditer(s))
    if not matches:
        return ["No Part sections found (expected Part a-l)."]

    issues: list[str] = []
    for idx, m in enumerate(matches):
        part = (m.group("p") or "").lower()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(s)
        block = s[start:end]
        for sec in ("Infrastructure", "Application", "Customer Responsibility"):
            if not re.search(rf"(?im)^\s*####\s+{re.escape(sec)}\s*$", block):
                issues.append(f"Part {part} missing heading: #### {sec}")
    return issues


_PART_HDR_RE = re.compile(r"(?im)^\s*(?:###\s+)?Part\s+(?P<p>[a-z])\s*:?\s*$")
_PART_SEC_RE = re.compile(
    r"(?im)^\s*(?:#{3,6}\s+)?(?:\*\*)?(?P<sec>Infrastructure|Application|Customer Responsibility)\s*:?\s*(?:\*\*)?\s*(?P<rest>.*)$"
)
_MAX_WORDS = {"Infrastructure": 45, "Application": 90, "Customer Responsibility": 45}
_MAX_SENTENCES = {"Infrastructure": 2, "Application": 3, "Customer Responsibility": 1}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _count_sentences(text: str) -> int:
    # Best-effort sentence boundary count.
    chunks = re.split(r"[.!?]+", (text or "").strip())
    return len([c for c in chunks if c.strip()])


def _part_brevity_issues(md: str) -> list[str]:
    """
    Enforce short content per Part section (Infrastructure/Application/Customer Responsibility).
    """
    s = md or ""
    matches = list(_PART_HDR_RE.finditer(s))
    if not matches:
        return []

    issues: list[str] = []
    for idx, m in enumerate(matches):
        part = (m.group("p") or "").lower()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(s)
        block = s[start:end]
        lines = block.splitlines()

        sections: dict[str, str] = {}
        cur = None
        buf: list[str] = []

        def _flush() -> None:
            nonlocal cur, buf
            if cur:
                txt = " ".join(x.strip() for x in buf if x.strip())
                txt = re.sub(r"\s+", " ", txt).strip()
                sections[cur] = txt
            cur = None
            buf = []

        for ln in lines:
            ms = _PART_SEC_RE.match(ln or "")
            if ms:
                _flush()
                cur = (ms.group("sec") or "").strip()
                rest = (ms.group("rest") or "").strip()
                if rest:
                    buf.append(rest)
                continue
            if cur:
                buf.append(ln)
        _flush()

        for sec, txt in sections.items():
            if not txt:
                continue
            wc = _count_words(txt)
            sc = _count_sentences(txt)
            mw = _MAX_WORDS.get(sec, 90)
            msent = _MAX_SENTENCES.get(sec, 3)
            if wc > mw or sc > msent:
                issues.append(
                    f"Part {part} — {sec} is too long (≈{sc} sentences, {wc} words; max {msent} sentences, {mw} words)."
                )

    return issues


def _markdownize_template_skeleton(template_skeleton: str, *, control_id: str) -> str:
    """
    Convert a line-oriented FedRAMP template excerpt into a Markdown-friendly skeleton
    with consistent heading hierarchy for rendering in the UI.

    This keeps the original content while making key section lines real Markdown headings.
    """
    cid = (control_id or "").strip()
    if not cid:
        return (template_skeleton or "").strip() + "\n"

    lines = (template_skeleton or "").replace("\r", "").splitlines()
    out: list[str] = []

    def _emit_heading(level: int, text: str) -> None:
        out.append("#" * max(2, min(level, 6)) + " " + text.strip())
        out.append("")  # blank line after heading

    for ln in lines:
        raw = ln or ""
        s = raw.strip()
        if not s:
            out.append("")
            continue
        if s.startswith("#"):
            out.append(raw)
            continue

        up = s.upper()
        # Control title line: "AC-2 Account Management ..."
        if up.startswith(cid.upper() + " "):
            _emit_heading(2, s)
            continue
        # Summary + implementation blocks.
        if up == f"{cid.upper()} CONTROL SUMMARY INFORMATION":
            _emit_heading(2, s)
            continue
        if up.startswith(f"{cid.upper()} WHAT IS THE SOLUTION AND HOW IS IT IMPLEMENTED"):
            _emit_heading(2, s)
            continue
        # Checkbox sections
        if s.lower().startswith("implementation status"):
            _emit_heading(3, s)
            continue
        if s.lower().startswith("control origination"):
            _emit_heading(3, s)
            continue
        # Parts
        m = re.match(r"^Part\s+(?P<p>[a-z])\s*:\s*$", s, flags=re.IGNORECASE)
        if m:
            _emit_heading(3, f"Part {m.group('p').lower()}")
            continue
        # Labels
        if s.lower() in {"infrastructure:", "application:", "customer responsibility:"}:
            _emit_heading(4, s[:-1].strip())
            continue

        out.append(raw)

    # Trim excess blank lines at end
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).strip() + "\n"


def _template_missing_blocks(md: str, *, control_id: str) -> list[str]:
    """
    Minimal template-format validation for style-example-driven outputs.
    """
    s = md or ""
    missing: list[str] = []
    if not control_id or not control_id.strip():
        return ["control_id_missing"]

    cid = control_id.strip()
    if f"{cid} Control Summary Information" not in s:
        missing.append(f"{cid} Control Summary Information")
    if f"{cid} What is the solution and how is it implemented?" not in s:
        missing.append(f"{cid} What is the solution and how is it implemented?")
    if "Implementation Status" not in s:
        missing.append("Implementation Status")
    if "Control Origination" not in s:
        missing.append("Control Origination")
    if "☒" not in s:
        missing.append("At least one checked box (☒)")
    return missing


async def _get_roadmap(
    db: AsyncSession,
    *,
    control_id: str,
    account_id: str | None,
) -> dict[str, Any]:
    """
    Return the best roadmap match for (control_id, account_id), preferring
    account-scoped rows over global rows.
    """
    stmt = select(ControlRoadmap).where(ControlRoadmap.control_id == control_id)
    if account_id is None:
        stmt = stmt.where(ControlRoadmap.account_id.is_(None))
    else:
        stmt = stmt.where(or_(ControlRoadmap.account_id == account_id, ControlRoadmap.account_id.is_(None)))
        stmt = stmt.order_by(ControlRoadmap.account_id.is_(None))
    row = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "status_override": row.status_override,
        "target_date": row.target_date,
        "milestones": row.milestones if isinstance(row.milestones, list) else [],
        "narrative_roadmap_summary": row.narrative_roadmap_summary,
    }


async def _get_style_examples(
    db: AsyncSession,
    *,
    control_id: str,
    tone_tier: str,
    limit: int = 2,
) -> list[str]:
    stmt = (
        select(SSPStyleExample)
        .where(SSPStyleExample.control_id == control_id)
        .where(SSPStyleExample.tone_tier == tone_tier)
        .order_by(SSPStyleExample.updated_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[str] = []
    for r in rows:
        md = (r.example_markdown or "").strip()
        if md:
            out.append(md)
    return out


def _format_snippet(m: dict[str, Any]) -> str:
    title = str(m.get("title") or "").strip()
    src = str(m.get("source_path") or "").strip()
    ps = m.get("page_start")
    pe = m.get("page_end")
    text = str(m.get("text") or "").strip()
    # Keep snippet short and readable.
    text = re.sub(r"\s+", " ", text)
    if len(text) > 420:
        text = text[:420].rstrip() + "…"

    where = title or src or "Document"
    pages = ""
    if isinstance(ps, int) and isinstance(pe, int):
        pages = f" (pp. {ps}-{pe})"
    return f"{where}{pages}: {text}"


async def _fetch_governance_snippets(db: AsyncSession, *, account_id: str | None) -> list[str]:
    """
    Pull policy/procedure context via RAG (best-effort).
    """
    queries = [
        "Joiner mover leaver account provisioning deprovisioning workflow",
        "Access Control Policy least privilege role definitions access request approvals",
        "Privileged access review procedure periodic access review cadence",
        "Multi-factor authentication standard phishing-resistant MFA requirement",
        "Annual policy review procedure security policy review cadence",
    ]

    snippets: list[str] = []
    for q in queries:
        try:
            matches = await rag_service.search(db, query=q, top_k=2, account_id=account_id, doc_type=None)
        except Exception as exc:
            logger.info("rag_search_failed", error=str(exc))
            matches = []
        for m in matches or []:
            if isinstance(m, dict):
                snippets.append(_format_snippet(m))

    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for s in snippets:
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 8:
            break
    return out


async def generate_ssp_narrative_from_state(
    *,
    db: AsyncSession,
    control: dict[str, Any],
    evidence_snapshot: dict[str, Any],
    validation_findings: dict[str, Any],
    account_id: str,
    tone_tier: str,
    max_attempts: int = 2,
    previous_markdown: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """
    Produce a client-facing SSP narrative using only structured compliance state.
    """
    control_id = str(control.get("control_id") or "").strip() or str(evidence_snapshot.get("control_id") or "").strip()
    roadmap = await _get_roadmap(db, control_id=control_id, account_id=account_id)
    style_examples = await _get_style_examples(db, control_id=control_id, tone_tier=tone_tier, limit=2)
    governance_snippets = await _fetch_governance_snippets(db, account_id=account_id)

    strategy_input = build_ssp_strategy_input(
        control=control,
        validation_findings=validation_findings or {},
        governance_snippets=governance_snippets,
        tone_tier=tone_tier,
        roadmap=roadmap,
    )

    # If we have a style example for this control+tier, treat it as the output template skeleton
    # so the generated narrative matches the expected FedRAMP control template format.
    use_template_skeleton = bool(style_examples)
    template_skeleton = ""
    if use_template_skeleton:
        template_skeleton = _apply_inherited_placeholders(style_examples[0])
        if control_id.strip().upper() == "AC-2":
            template_skeleton = _normalize_ac2_template_skeleton(
                template_skeleton,
                implementation_status=str(strategy_input.get("implementation_status") or "").strip(),
            )
        template_skeleton = _markdownize_template_skeleton(template_skeleton, control_id=control_id)
        system_msg, user_msg = build_ssp_template_prompt(
            control_id=control_id,
            template_skeleton=template_skeleton,
            strategy_input=strategy_input,
        )
    else:
        system_msg, user_msg = build_ssp_prompt(strategy_input=strategy_input, style_examples=style_examples)

    feedback = ""
    last_md = ""
    missing: list[str] = []
    violations = []

    for attempt in range(max_attempts):
        user_msg2 = user_msg
        if attempt == 0 and (rejection_reason or "").strip():
            prev = (previous_markdown or "").strip()
            if len(prev) > 6000:
                prev = prev[:6000].rstrip() + "…"
            user_msg2 += (
                "\n\nHuman reviewer feedback on the previous draft:\n"
                f"- Rejection reason: {str(rejection_reason).strip()}\n"
            )
            if prev:
                user_msg2 += "\nPrevious draft (for reference; do not quote verbatim):\n" + prev + "\n"
            user_msg2 += "\nRevise the draft to address the feedback while preserving the required structure.\n"
        if feedback:
            user_msg2 += "\n\nRevision feedback (must fix):\n" + feedback.strip() + "\n"

        md = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg2, temperature=0.2)
        last_md = (md or "").strip()
        # Defensive: ensure no environment-specific inherited auth leaks into output.
        last_md = _apply_inherited_placeholders(last_md)

        missing = (
            _template_missing_blocks(last_md, control_id=control_id)
            if use_template_skeleton
            else _missing_headings(last_md)
        )
        violations = find_prohibited_language(last_md)
        code_issues = _code_formatting_issues(last_md)
        brevity_issues = _part_brevity_issues(last_md) if use_template_skeleton else []
        repetition_issues = _repetition_and_tone_issues(last_md)
        evidence_issues = _evidence_detail_issues(last_md)
        section_issues = _template_part_section_issues(last_md) if use_template_skeleton else []

        if (
            not missing
            and not violations
            and not code_issues
            and not brevity_issues
            and not repetition_issues
            and not evidence_issues
            and not section_issues
        ):
            break

        parts: list[str] = []
        if missing:
            parts.append(
                ("Missing required template blocks: " if use_template_skeleton else "Missing required headings: ")
                + ", ".join(missing)
            )
        if violations:
            parts.append(
                "Prohibited language found: "
                + ", ".join(sorted({f"{v.phrase} (line {v.line})" for v in violations}))
            )
        if code_issues:
            parts.append("Code formatting is prohibited: " + "; ".join(code_issues))
        if brevity_issues:
            parts.append("Brevity requirements not met: " + " ".join(brevity_issues[:6]))
        if repetition_issues:
            parts.append(
                "Avoid repetitive risk/POA&M language: "
                + " ".join(repetition_issues[:4])
                + " Use varied governance phrasing (continuous monitoring activities track enhancements; configuration management and periodic reviews validate maturity; enhancements incorporated through standard change management)."
            )
        if evidence_issues:
            parts.append(
                "Avoid operational evidence details: "
                + " ".join(evidence_issues[:4])
                + " Keep statements qualitative (e.g., 'IAM users and roles are used as primary identity constructs')."
            )
        if section_issues:
            parts.append("Template structure not preserved: " + " ".join(section_issues[:6]))
        if use_template_skeleton:
            parts.append("Revise while preserving the template skeleton structure and checkboxes.")
        parts.append("Revise the narrative to remove prohibited phrases while preserving meaning and tone.")
        feedback = "\n".join("- " + p for p in parts)

    is_valid = (
        not missing
        and not violations
        and not _code_formatting_issues(last_md)
        and not _repetition_and_tone_issues(last_md)
        and not _evidence_detail_issues(last_md)
        and (not use_template_skeleton or not _template_part_section_issues(last_md))
        and (not use_template_skeleton or not _part_brevity_issues(last_md))
        and bool(last_md.strip())
    )

    return {
        "markdown": last_md,
        "implementation_status": strategy_input.get("implementation_status") or "Unknown",
        "is_valid": is_valid,
        "missing_headings": missing,
        "model": settings.openai_model,
        # Keep evidence snapshot for audit trace; narrative writer does not see raw snapshot.
        "evidence_snapshot": evidence_snapshot or {},
        "validation_findings": validation_findings or {},
        "strategy_input": strategy_input,
    }

