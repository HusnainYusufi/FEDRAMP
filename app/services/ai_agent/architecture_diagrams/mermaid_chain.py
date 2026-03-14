from __future__ import annotations

import json
from typing import Any

from app.config.logging_config import get_logger
from app.services.ai_agent.architecture_diagrams.mermaid_generator import (
    MERMAID_SYSTEM,
    _strip_fences,  # type: ignore
    build_mermaid_user_message,
    deterministic_mermaid_from_evidence,
    generate_mermaid_from_evidence,
)
from app.services.ai_agent.architecture_diagrams.mermaid_validator import validate_mermaid_flowchart
from app.services.ai_agent.narrative.llm_client import invoke_text

logger = get_logger(__name__)


EXAMPLE_STYLE_GUIDE = """\
You are evaluating Mermaid diagrams against the visual/layout conventions of an example FedRAMP ABD diagram.

Style guide (derived from the example):
- Prominent title (ABD Overview / Authorization Boundary) and clear boundary label.
- Outer dashed authorization boundary enclosing CSP-managed services.
- Clear separation of tiers/subnets and side panels for external services / security services / management path.
- Legend box that defines: boundary style, dataflow arrows, and placeholder style (NOT EVIDENCED / PLANNED).
- Short, readable labels; group repeated elements; avoid long AWS IDs as main labels (can be shown as secondary).
- Data flows are explicit directional arrows and distinguish business traffic vs management traffic.
- Use polished board-style layout with soft blue/grey panels, dark service boxes, and lighter placeholder boxes.
- Prefer left-to-right layout with side panels and nested service groups rather than a tall single-column flow.
- Mermaid should use classDef/style statements so the output feels presentation-ready, not like an unstyled draft.
"""


EVALUATOR_SYSTEM = """\
You are a FedRAMP 3PAO auditor reviewing an Authorization Boundary Diagram (ABD) and Dataflow Diagram (DFD).
Return ONLY valid JSON. No markdown. No extra keys.
"""


def _evaluator_user_message(*, evidence_json: dict[str, Any], mermaid_code: str) -> str:
    return (
        f"{EXAMPLE_STYLE_GUIDE}\n\n"
        "TASK: Evaluate the Mermaid diagram for FedRAMP audit readiness and style alignment.\n"
        "Constraints:\n"
        "- Do NOT ask for components that aren't evidenced unless they are required placeholders (Management Path, External IdP, Legend).\n"
        "- If a required element is missing, mark as MUST_FIX.\n\n"
        "Return JSON exactly:\n"
        "{\n"
        '  "score": 0-100,\n'
        '  "must_fix": ["..."],\n'
        '  "suggestions": ["..."],\n'
        '  "hallucinations": ["..."],\n'
        '  "missing_required_placeholders": ["..."]\n'
        "}\n\n"
        "Evidence JSON (counts only, for validation):\n"
        + json.dumps(evidence_json.get("counts") or {}, indent=2, default=str)
        + "\n\nMermaid diagram to evaluate:\n"
        + (mermaid_code or "")
    )


REWRITE_SYSTEM = """\
You are a Lead Cloud Architect producing FedRAMP audit-ready Mermaid diagrams.
Return ONLY Mermaid syntax (no code fences, no explanations). Start with: flowchart LR
"""

FIXER_SYSTEM = """\
You are a Mermaid syntax fixer.
Your job: rewrite the Mermaid so it parses successfully in Mermaid v10.
Preserve meaning, but you MUST:
- Use only safe node IDs: [A-Za-z][A-Za-z0-9_]*
- Node IDs MUST NOT start with "end" (reserved keyword).
- Avoid parentheses in edge labels (use 'Auth - NOT EVIDENCED', not 'Auth (NOT EVIDENCED)').
- Keep AWS IDs inside labels (not IDs)
- Keep required placeholders labeled "NOT EVIDENCED / PLANNED"
Return ONLY Mermaid syntax. Start with: flowchart LR
"""


def _fixer_user_message(*, error: str, mermaid_code: str) -> str:
    return (
        "The Mermaid code below fails to parse. Fix it.\n\n"
        f"Parser error hint: {error}\n\n"
        "Broken Mermaid:\n"
        + (mermaid_code or "")
    )


def _sanitize_common_mermaid_issues(code: str) -> str:
    """
    Deterministic sanitizer for common Mermaid v10 parse failures we see from LLMs.

    - Convert edge node IDs that start with 'end' (e.g., endIDP --> ...) into safe IDs.
      Mermaid's grammar can confuse these with the reserved `end` token.
    - If `end` is accidentally concatenated to the previous line (e.g. ..."]endIDP -->),
      force `end` onto its own line: ..."]\nend\nIDP --> ...
    - Mermaid v10 can be picky about parentheses inside edge labels (the `|label|` portion).
      Remove parentheses characters within pipe labels to prevent parse errors.
    - Convert literal "\\n" sequences inside labels into Mermaid-friendly "<br/>" line breaks.
    """
    text = (code or "")

    # Convert literal backslash-n sequences into HTML line breaks for Mermaid labels.
    # Mermaid often renders "\\n" as the characters "\" and "n" instead of a newline.
    text = text.replace("\\n", "<br/>")
    # Replace node IDs like "endIDP" at the start of an edge statement.
    # Example: endIDP -->|label| IGW  ->  IDP -->|label| IGW
    import re

    def repl(m: re.Match) -> str:
        rest = m.group(1) or ""
        # If rest already starts with underscore or digit, prefix safely
        safe = rest
        if not safe or not safe[0].isalpha():
            safe = "n_" + safe
        return safe + " "

    text = re.sub(r"(?m)^\s*end([A-Za-z0-9_]+)\s+", lambda m: repl(m), text)

    # Fix missing newline before `end` when it's concatenated after a node declaration.
    # Example: PUBMORE["(+2 more)"]endIDP --> ...  ->  PUBMORE["(+2 more)"]\nend\nIDP --> ...
    text = re.sub(r'(\]|\)|\}|"|\')end([A-Za-z0-9_]+)\b', r"\1\nend\n\2", text)

    # Also split `end` followed immediately by a safe ID on the same line (rare but seen).
    # Example: endIDP --> ...  -> end\nIDP --> ...
    text = re.sub(r"(?m)^\s*end(?=[A-Za-z0-9_])", "end\n", text)

    # Sanitize edge labels: replace parentheses inside |...| with hyphens/spaces.
    def _clean_label(m: re.Match) -> str:
        inner = m.group(1) or ""
        inner = inner.replace("(", " ").replace(")", " ")
        inner = re.sub(r"\s+", " ", inner).strip()
        return f"|{inner}|"

    text = re.sub(r"\|([^|\n]{1,200})\|", _clean_label, text)
    return text

def _rewrite_user_message(
    *, evidence_json: dict[str, Any], current_mermaid: str, must_fix: list[str], suggestions: list[str]
) -> str:
    return (
        f"{EXAMPLE_STYLE_GUIDE}\n\n"
        "Revise the Mermaid diagram to address the audit feedback.\n"
        "Hard rules:\n"
        "- Evidence is the source of truth. Do not add implemented components that are not evidenced.\n"
        '- Always include placeholders labeled "NOT EVIDENCED / PLANNED" for Management Path and External IdP.\n'
        "- Always include a Legend.\n\n"
        "MUST FIX:\n"
        + "\n".join([f"- {x}" for x in (must_fix or [])])
        + "\n\nSUGGESTIONS:\n"
        + "\n".join([f"- {x}" for x in (suggestions or [])])
        + "\n\nEvidence JSON (full):\n"
        + json.dumps(evidence_json, indent=2, default=str)
        + "\n\nCurrent Mermaid:\n"
        + (current_mermaid or "")
    )


async def generate_mermaid_with_feedback(
    *,
    evidence_json: dict[str, Any],
    max_attempts: int = 3,
    min_score: int = 85,
) -> dict[str, Any]:
    """
    Generate Mermaid with an evaluator feedback loop.

    Returns:
      { mermaid_code, evaluation, attempts, used_fallback }
    """
    used_fallback = False
    draft = await generate_mermaid_from_evidence(evidence_json=evidence_json)
    mermaid_code = draft.get("mermaid_code") or ""
    mermaid_prompt = draft.get("mermaid_prompt") or ""

    evaluation: dict[str, Any] | None = None

    # --- Pre-validate Mermaid syntax BEFORE evaluator loop ---
    ok, err = validate_mermaid_flowchart(mermaid_code)
    if not ok:
        mermaid_code = _sanitize_common_mermaid_issues(mermaid_code)
        ok_s, err_s = validate_mermaid_flowchart(mermaid_code)
        if ok_s:
            err = None
        else:
            err = err_s or err
        # Attempt one repair pass, then fallback.
        raw_fixed = await invoke_text(
            system_message=FIXER_SYSTEM,
            user_message=_fixer_user_message(error=str(err), mermaid_code=mermaid_code),
            temperature=0.0,
        )
        fixed = _strip_fences(raw_fixed)
        if not fixed.lower().startswith("flowchart"):
            fixed = "flowchart LR\n" + fixed
        ok2, err2 = validate_mermaid_flowchart(fixed)
        if ok2:
            mermaid_code = fixed
        else:
            used_fallback = True
            mermaid_code = deterministic_mermaid_from_evidence(evidence_json=evidence_json)

    for attempt in range(1, max_attempts + 1):
        # Validate Mermaid before asking evaluator (prevents "syntax error" diagrams reaching UI)
        ok, err = validate_mermaid_flowchart(mermaid_code)
        if not ok:
            mermaid_code = _sanitize_common_mermaid_issues(mermaid_code)
            ok_s, err_s = validate_mermaid_flowchart(mermaid_code)
            if ok_s:
                err = None
            else:
                err = err_s or err
            raw_fixed = await invoke_text(
                system_message=FIXER_SYSTEM,
                user_message=_fixer_user_message(error=str(err), mermaid_code=mermaid_code),
                temperature=0.0,
            )
            fixed = _strip_fences(raw_fixed)
            if not fixed.lower().startswith("flowchart"):
                fixed = "flowchart LR\n" + fixed
            ok2, err2 = validate_mermaid_flowchart(fixed)
            if ok2:
                mermaid_code = fixed
            else:
                used_fallback = True
                mermaid_code = deterministic_mermaid_from_evidence(evidence_json=evidence_json)

        raw_eval = await invoke_text(
            system_message=EVALUATOR_SYSTEM,
            user_message=_evaluator_user_message(evidence_json=evidence_json, mermaid_code=mermaid_code),
            temperature=0.0,
        )
        try:
            evaluation = json.loads(raw_eval)
            if not isinstance(evaluation, dict):
                raise ValueError("eval_not_object")
            score = int(evaluation.get("score") or 0)
            must_fix = evaluation.get("must_fix") or []
            suggestions = evaluation.get("suggestions") or []
            if score >= min_score and not must_fix:
                return {
                    "mermaid_code": mermaid_code,
                    "mermaid_prompt": mermaid_prompt,
                    "evaluation": evaluation,
                    "attempts": attempt,
                    "used_fallback": used_fallback,
                }
        except Exception as exc:
            logger.warning("mermaid_evaluation_parse_failed", error=str(exc))
            evaluation = {
                "score": 0,
                "must_fix": ["Evaluator output could not be parsed as JSON."],
                "suggestions": [],
                "hallucinations": [],
                "missing_required_placeholders": [],
            }
            must_fix = evaluation["must_fix"]
            suggestions = evaluation["suggestions"]

        # Rewrite unless last attempt
        if attempt < max_attempts:
            must_fix = evaluation.get("must_fix") or []
            suggestions = evaluation.get("suggestions") or []
            user_msg = _rewrite_user_message(
                evidence_json=evidence_json,
                current_mermaid=mermaid_code,
                must_fix=list(must_fix) if isinstance(must_fix, list) else [],
                suggestions=list(suggestions) if isinstance(suggestions, list) else [],
            )
            raw = await invoke_text(system_message=REWRITE_SYSTEM, user_message=user_msg, temperature=0.1)
            mermaid_code = _strip_fences(raw)
            if not mermaid_code.lower().startswith("flowchart"):
                mermaid_code = "flowchart LR\n" + mermaid_code

            # Validate rewritten Mermaid; if invalid, fallback early.
            ok3, err3 = validate_mermaid_flowchart(mermaid_code)
            if not ok3:
                used_fallback = True
                mermaid_code = deterministic_mermaid_from_evidence(evidence_json=evidence_json)

    # If still not good, return deterministic fallback
    used_fallback = True
    fallback = deterministic_mermaid_from_evidence(evidence_json=evidence_json)
    return {
        "mermaid_code": fallback,
        "mermaid_prompt": mermaid_prompt,
        "evaluation": evaluation
        or {
            "score": 0,
            "must_fix": ["Fallback used."],
            "suggestions": [],
            "hallucinations": [],
            "missing_required_placeholders": [],
        },
        "attempts": max_attempts,
        "used_fallback": used_fallback,
    }

