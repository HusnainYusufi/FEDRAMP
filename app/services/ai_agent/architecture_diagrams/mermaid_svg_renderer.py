from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.config.logging_config import get_logger
from app.services.ai_agent.architecture_diagrams.models import InfraSpec
from app.services.ai_agent.architecture_diagrams.svg_renderer import (
    STYLE_BRIEF,
    SVG_EVALUATOR_SYSTEM,
    _compact_evidence,
    _extract_svg,
    _test_fallback_svg,
    validate_svg_markup,
)
from app.services.ai_agent.narrative.llm_client import invoke_text

logger = get_logger(__name__)


MERMAID_TO_SVG_SYSTEM = """\
You are a Lead Cloud Architect, FedRAMP assessor, and SVG diagram author.
Convert the provided Mermaid authorization-boundary/data-flow diagram into polished SVG markup.

Rules:
- Return ONLY valid SVG markup beginning with <svg and ending with </svg>.
- The Mermaid diagram is the structural source of truth for nodes, groupings, labels, and edges.
- Use the evidence JSON only to avoid hallucinations and to preserve audit context.
- Follow the provided style brief. Produce a presentation-ready architecture board layout.
- Keep labels short and readable. Avoid overlaps, cropped content, and lines through text.
- Use orthogonal connector paths where possible, and visibly connect lines to box edges.
- Include a legend for data-flow colors, boundary styles, and placeholder/not-evidenced styling.
- Preserve any NOT EVIDENCED / PLANNED placeholders from the Mermaid.
- Do not use script tags, foreignObject, embedded raster images, or external references.
"""


def _build_render_user_message(
    *,
    mermaid_code: str,
    evidence_json: dict[str, Any],
    spec: InfraSpec,
    feedback: str = "",
) -> str:
    summary = spec.context_summary or {}
    compact_evidence = _compact_evidence(evidence_json)
    return (
        "Render the Mermaid diagram below as a polished FedRAMP authorization boundary SVG.\n\n"
        + STYLE_BRIEF
        + "\n\nContext summary JSON:\n"
        + json.dumps(summary, indent=2)
        + "\n\nCompact evidence JSON:\n"
        + json.dumps(compact_evidence, indent=2, default=str)
        + "\n\nMermaid diagram:\n"
        + (mermaid_code or "")
        + ("\n\nFix these issues from the previous attempt:\n" + feedback if feedback else "")
    )


async def render_svg_from_mermaid_with_feedback(
    *,
    mermaid_code: str,
    evidence_json: dict[str, Any],
    spec: InfraSpec,
    max_attempts: int = 2,
) -> dict[str, Any]:
    last_svg = ""
    last_evaluation = {"score": 0, "must_fix": [], "suggestions": []}
    current_feedback = ""

    if not settings.openai_api_key:
        fallback_svg = _test_fallback_svg(spec)
        return {
            "svg_markup": fallback_svg,
            "evaluation": {
                "score": 60,
                "must_fix": ["OpenAI API key not configured; returned deterministic fallback SVG."],
                "suggestions": [],
            },
            "attempts": 1,
            "renderer_version": "mermaid_svg_ai_v1",
            "render_error": "openai_api_key_missing",
        }

    for attempt in range(1, max_attempts + 1):
        raw_svg = await invoke_text(
            system_message=MERMAID_TO_SVG_SYSTEM,
            user_message=_build_render_user_message(
                mermaid_code=mermaid_code,
                evidence_json=evidence_json,
                spec=spec,
                feedback=current_feedback,
            ),
            temperature=0.1,
        )
        try:
            candidate_svg = _extract_svg(raw_svg)
        except Exception as exc:
            current_feedback = f"SVG extraction failed: {exc}"
            last_evaluation = {
                "score": 0,
                "must_fix": [current_feedback],
                "suggestions": [],
            }
            continue

        ok, error = validate_svg_markup(candidate_svg)
        if not ok:
            current_feedback = f"SVG validation failed: {error}"
            last_svg = candidate_svg
            last_evaluation = {
                "score": 0,
                "must_fix": [current_feedback],
                "suggestions": [],
            }
            continue

        raw_eval = await invoke_text(
            system_message=SVG_EVALUATOR_SYSTEM,
            user_message=(
                "Evaluate this candidate SVG against the style brief, Mermaid structure, and evidence.\n\n"
                + STYLE_BRIEF
                + "\n\nMermaid diagram:\n"
                + (mermaid_code or "")
                + "\n\nEvidence JSON counts:\n"
                + json.dumps((evidence_json.get("counts") or {}), indent=2, default=str)
                + "\n\nCandidate SVG:\n"
                + candidate_svg
            ),
            temperature=0.0,
        )
        try:
            evaluation = json.loads(raw_eval)
            if not isinstance(evaluation, dict):
                raise ValueError("evaluation_not_object")
        except Exception:
            evaluation = {
                "score": 0,
                "must_fix": ["Evaluator returned invalid JSON."],
                "suggestions": [],
            }

        score = int(evaluation.get("score") or 0)
        must_fix = evaluation.get("must_fix") or []
        suggestions = evaluation.get("suggestions") or []
        last_svg = candidate_svg
        last_evaluation = {
            "score": score,
            "must_fix": must_fix,
            "suggestions": suggestions,
        }
        if score >= 78 and not must_fix:
            return {
                "svg_markup": candidate_svg,
                "evaluation": last_evaluation,
                "attempts": attempt,
                "renderer_version": "mermaid_svg_ai_v1",
                "render_error": None,
            }

        if attempt < max_attempts:
            current_feedback = "\n".join([f"- {item}" for item in must_fix + suggestions]) or (
                "Improve style fidelity, spacing, and line readability while preserving the Mermaid structure."
            )

    if not last_svg:
        last_svg = _test_fallback_svg(spec)
        last_evaluation = {
            "score": 0,
            "must_fix": ["Mermaid rendering failed; returned deterministic fallback SVG."],
            "suggestions": [],
        }

    return {
        "svg_markup": last_svg,
        "evaluation": last_evaluation,
        "attempts": max_attempts,
        "renderer_version": "mermaid_svg_ai_v1",
        "render_error": "mermaid_render_failed",
    }
