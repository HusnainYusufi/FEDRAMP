from __future__ import annotations

from typing import Any


SSP_WRITER_SYSTEM_PROMPT = """\
You are a FedRAMP System Security Plan (SSP) narrative author.

Your task is to generate formal FedRAMP-compliant control implementation statements suitable for submission to a 3PAO auditor.

You are NOT an auditor.
You are NOT writing a deficiency report.
You are writing the organization’s official control implementation description.

Writing Style Requirements
- Use formal documentation tone.
- Write in third person (e.g., “The organization implements…”).
- Emphasize governance, policy, and documented procedures.
- Frame partial implementation as managed risk.
- Reference continuous monitoring and POA&M tracking where appropriate.
- Reinforce shared responsibility when applicable.
- Highlight inheritance where relevant.

Quality Requirements (STRICT)
- Avoid repetitive phrasing. Do NOT repeat the same POA&M / monitoring sentence across multiple sections.
- Prefer varied, mature governance language such as:
  - Continuous monitoring activities track enhancements.
  - Configuration management and periodic reviews validate implementation maturity.
  - Enhancements are incorporated through governance workflows and standard change management.
- Keep evidence references subtle and qualitative. Do NOT include numeric evidence counts (e.g., “5 IAM users”, “4 roles enumerated”).
- When describing cloud inheritance, explicitly name the provider as Amazon Web Services (AWS). Do NOT attribute FedRAMP authorization to regions (avoid “AWS East/West is FedRAMP authorized”).
- Do NOT restate inheritance boilerplate in every section. Keep inherited authorization placeholders in the Inheritance/Origination narrative and reference inheritance only when it adds value.
- For Planned status, avoid repetitive phrases like “is planning to” / “is preparing to”. Prefer direct future-tense implementation language (e.g., “will be implemented”, “will be maintained”, “is governed through”).

Prohibited Language
Do NOT use:
- “Not evidenced”
- “Not implemented”
- “Not effectively implemented”
- “Absent”
- “Missing”
- “Failure”
- “Does not”
- “Lack of”
Do not use accusatory or audit-style language.

Narrative Strategy
If implementation status is:
- Implemented: write confidently; describe mechanisms and enforcement.
- Partially Implemented: describe what is implemented first; frame gaps as enhancements tracked under POA&M and continuous monitoring; maintain maturity tone.
- Planned: describe architectural design and roadmap; reference implementation milestones; avoid sounding incomplete.

Required Structure (Markdown headings)
For each control, output Markdown using these headings in this order:
1. Control Overview
2. Governance and Roles
3. Technical Implementation
4. Monitoring and Review
5. Shared Responsibility
6. Inheritance
7. Continuous Improvement

Heading Hierarchy (STRICT)
- Use ## for each of the seven required headings exactly as written (e.g., ## Control Overview).
- Use ### (or deeper) only for subsections under those headings.
- Do NOT use # (single-hash) headings.
- Leave a blank line after each heading.

Brevity Requirements (STRICT)
- Keep each section concise (target 4-8 sentences per section).
- Avoid long paragraphs; use short paragraphs or bullets when helpful.

No Code Formatting (STRICT)
- Do NOT use code blocks, inline code formatting, or HTML code tags.

Return ONLY Markdown. Do not include any JSON.
"""


def build_ssp_prompt(
    *,
    strategy_input: dict[str, Any],
    style_examples: list[str],
) -> tuple[str, str]:
    s = strategy_input if isinstance(strategy_input, dict) else {}

    control_id = str(s.get("control_id") or "").strip()
    title = str(s.get("control_title") or "").strip()
    family = str(s.get("control_family") or "").strip()
    baseline = str(s.get("baseline") or "").strip()
    status = str(s.get("implementation_status") or "").strip()
    tone_tier = str(s.get("tone_tier") or "").strip()

    implemented = s.get("implemented_mechanisms") or []
    enhancements = s.get("enhancement_notes") or []
    gov = s.get("governance_context_snippets") or []
    shared = s.get("shared_responsibility_notes") or []
    inherit = s.get("inheritance_notes") or []
    remediation = s.get("remediation_items") or []
    poam_stmt = str(s.get("poam_tracking_statement") or "").strip()
    roadmap = s.get("roadmap") or {}

    def _bullets(items: Any, *, max_items: int = 8) -> str:
        if not isinstance(items, list):
            return ""
        out = []
        for x in items[:max_items]:
            xs = str(x or "").strip()
            if xs:
                out.append(f"- {xs}")
        return "\n".join(out)

    examples_text = ""
    if style_examples:
        # Keep examples short in the prompt to avoid overfitting/copying.
        clipped = [e.strip() for e in style_examples if isinstance(e, str) and e.strip()][:2]
        if clipped:
            examples_text = "\n\n".join(clipped)

    parts: list[str] = [
        f"Control ID: {control_id}\n",
        f"Control Title: {title}\n",
        f"Control Family: {family}\n",
        f"Baseline: {baseline}\n",
        f"Implementation Status: {status}\n",
        f"Tone Tier: {tone_tier}\n\n",
        "Narrative-safe compliance state (do not treat as audit findings):\n",
        "Implemented mechanisms (summaries):\n",
        f"{_bullets(implemented)}\n\n",
        "Enhancement notes (for Partially Implemented / Planned only):\n",
        f"{_bullets(enhancements)}\n\n",
    ]

    if poam_stmt:
        parts.extend(
            [
                "POA&M / continuous monitoring reference (use sparingly; do not repeat verbatim across sections):\n",
                f"- {poam_stmt}\n\n",
            ]
        )

    parts.extend(
        [
            "Governance context snippets (use to ground policy/process language; do not invent citations):\n",
            f"{_bullets(gov, max_items=6)}\n\n",
            "Shared responsibility notes (use when applicable):\n",
            f"{_bullets(shared, max_items=6)}\n\n",
            "Inheritance notes (use when applicable):\n",
            f"{_bullets(inherit, max_items=6)}\n\n",
            "Roadmap context (for Planned only; if empty, keep roadmap language high level):\n",
            f"- narrative_roadmap_summary: {str(roadmap.get('narrative_roadmap_summary') or '').strip()}\n",
            f"- target_date: {str(roadmap.get('target_date') or '').strip()}\n",
            f"- milestones: {roadmap.get('milestones') if isinstance(roadmap.get('milestones'), list) else []}\n\n",
            "Remediation items (may be referenced as managed improvements; do not use audit-style language):\n",
            f"{_bullets(remediation, max_items=6)}\n",
        ]
    )

    user_message = "".join(parts)

    if examples_text:
        user_message += (
            "\n\nStyle and tone example (for guidance only; do not copy proper nouns, tools, or placeholders verbatim):\n"
            f"{examples_text}\n"
        )

    user_message += "\n\nNow write the SSP narrative using the Required Structure headings."

    return SSP_WRITER_SYSTEM_PROMPT, user_message

