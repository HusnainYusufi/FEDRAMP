from __future__ import annotations

import json
from xml.etree import ElementTree

from app.config import settings
from app.services.ai_agent.architecture_diagrams.models import InfraSpec
from app.services.ai_agent.narrative.llm_client import invoke_text

STYLE_BRIEF = """\
Style brief:
- Professional FedRAMP architecture board, not a screenshot clone.
- Wide top banner with dark blue header.
- Light blue / grey workspace panels with darker blue service boxes.
- Nested authorization boundary framing with left and right side panels.
- Clean, spacious layout with generous margins and consistent alignment.
- Use orthogonal x/y routing only, with connectors attaching cleanly to box edges.
- Connectors should be colored by flow type, but never have text labels on the lines.
- Show flow meanings only in a legend.
- Show boundary meanings only in a legend.
- Use data-flow lines only for interactions with outside services, users, admins, external providers, or external logging/feeds.
- Do not draw internal-only service-to-service data-flow lines inside the boundary.
- Group repeated infrastructure instead of rendering every low-value resource individually.
- Use a canvas as large as needed for readability. Do not constrain the size unnecessarily.
"""

SVG_GENERATOR_SYSTEM = """\
You are a Lead Cloud Architect, FedRAMP assessor, and SVG diagram author.
Your task is to generate a polished AWS authorization boundary plus external data-flow diagram directly as SVG markup.

Rules:
- Return ONLY valid SVG markup beginning with <svg and ending with </svg>.
- Follow the provided style brief. Do not copy any reference diagram literally.
- The result must be both an authorization boundary diagram and a data flow diagram.
- Use the AWS evidence and context summary as the source of truth. Do not hallucinate implemented services.
- You may include placeholders for expected FedRAMP diagram sections when evidence is missing.
- Keep labels short and readable. Avoid overlapping text, cropped content, and connector paths that run through labels.
- Use grouped panels, legend, title bar, and explicit directional flows.
- Use orthogonal connector paths only: horizontal and vertical segments with 90-degree bends.
- Each connector must visibly terminate at the edge of a box, not stop in open whitespace.
- Route connectors around boxes and labels. Do not run a connector across the middle of a text block.
- Add more whitespace between boxes than a typical dense cloud diagram.
- Keep the number of primary boxes manageable. Group repeated infrastructure rather than drawing every minor resource individually.
- Do not place any text on connector lines.
- Use multiple connector colors by flow type and show those same colors in a data-flow legend.
- Use colored boundary outlines for key boundary types and show those same colors in a boundary legend.
- Include a legend that demonstrates:
  - data flow colors
  - boundary colors
  - evidenced component style
  - placeholder / not-evidenced style
- Only show data-flow lines when interacting with outside services, users, admins, external providers, or external monitoring/security feeds.
- Do not show internal-only data flow lines between components inside the authorization boundary.
- Choose whatever canvas size is needed so the diagram breathes and nothing feels cramped.
- The output must be self-contained SVG with inline shapes, text, lines, paths, and styles only.
- Do not use script tags, foreignObject, embedded raster images, or external references.
"""

SVG_EVALUATOR_SYSTEM = """\
You are a FedRAMP architecture diagram reviewer.
Evaluate whether the candidate SVG diagram follows the style brief and accurately reflects the provided AWS evidence.
Return ONLY valid JSON with keys:
{
  "score": 0-100,
  "must_fix": ["..."],
  "suggestions": ["..."]
}

Review rules:
- The goal is the same style family as the style brief, not an exact clone of any single example.
- Use MUST_FIX only for serious issues:
  - invalid or broken SVG structure
  - obvious evidence hallucinations
  - heavy line overlap through labels or boxes
  - unreadable layout or cropped content
  - connectors that do not visibly connect to boxes
  - connectors that are not predominantly orthogonal
  - text placed directly on connector lines
  - internal-only data-flow lines drawn inside the boundary
  - missing data-flow color legend or missing boundary color legend
- Use suggestions for smaller polish items.
"""


def _extract_svg(text: str) -> str:
    raw = (text or "").strip()
    start = raw.find("<svg")
    end = raw.rfind("</svg>")
    if start == -1 or end == -1:
        raise ValueError("svg_not_found")
    return raw[start : end + len("</svg>")]


def validate_svg_markup(svg_markup: str) -> tuple[bool, str | None]:
    raw = (svg_markup or "").strip()
    if not raw.startswith("<svg"):
        return False, "SVG must start with <svg"
    if "<script" in raw.lower():
        return False, "SVG must not contain script tags"
    if "<foreignobject" in raw.lower():
        return False, "SVG must not contain foreignObject"
    try:
        ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        return False, str(exc)
    return True, None


def _compact_evidence(evidence_json: dict) -> dict:
    resources = evidence_json.get("resources") or {}

    def _trim(items: list, limit: int = 4) -> list:
        out = []
        for item in (items or [])[:limit]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "resource_id": item.get("resource_id"),
                    "region": item.get("region"),
                    "data": item.get("data"),
                }
            )
        return out

    return {
        "account_id": evidence_json.get("account_id"),
        "ingestion_run_id": evidence_json.get("ingestion_run_id"),
        "counts": evidence_json.get("counts") or {},
        "resources": {
            "vpcs": _trim(resources.get("vpcs") or [], 2),
            "subnets": _trim(resources.get("subnets") or [], 6),
            "internet_gateways": _trim(resources.get("internet_gateways") or [], 2),
            "nat_gateways": _trim(resources.get("nat_gateways") or [], 2),
            "vpc_endpoints": _trim(resources.get("vpc_endpoints") or [], 3),
            "ec2_instances": _trim(resources.get("ec2_instances") or [], 4),
            "rds_instances": _trim(resources.get("rds_instances") or [], 3),
            "s3_buckets": _trim(resources.get("s3_buckets") or [], 3),
            "cloudtrail_trails": _trim(resources.get("cloudtrail_trails") or [], 2),
            "cloudwatch_log_groups": _trim(resources.get("cloudwatch_log_groups") or [], 3),
            "vpc_flow_logs": _trim(resources.get("vpc_flow_logs") or [], 2),
        },
        "notes": evidence_json.get("notes") or {},
    }


def _test_fallback_svg(spec: InfraSpec) -> str:
    title = spec.title or "ABD Overview"
    boundary = spec.boundary_label or "FedRAMP Authorization Boundary"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="780" viewBox="0 0 1200 780" role="img" aria-label="Authorization boundary diagram">
  <rect width="1200" height="780" fill="#f4f7fb"/>
  <rect x="30" y="24" width="1140" height="74" rx="14" fill="#0f4c81"/>
  <text x="60" y="68" fill="#ffffff" font-size="34" font-weight="700" font-family="Arial, Helvetica, sans-serif">{title}</text>
  <rect x="220" y="125" width="900" height="610" rx="14" fill="#eef4fa" stroke="#a8bfd5" stroke-width="2" stroke-dasharray="8 6"/>
  <text x="670" y="160" text-anchor="middle" fill="#17324d" font-size="22" font-weight="700" font-family="Arial, Helvetica, sans-serif">{boundary}</text>
  <rect x="300" y="230" width="640" height="120" rx="12" fill="#163f6b" stroke="#0f2f50" stroke-width="1.6"/>
  <text x="620" y="278" text-anchor="middle" fill="#ffffff" font-size="24" font-weight="700" font-family="Arial, Helvetica, sans-serif">AI fallback diagram</text>
  <text x="620" y="314" text-anchor="middle" fill="#dce9f8" font-size="18" font-weight="500" font-family="Arial, Helvetica, sans-serif">OpenAI SVG generation unavailable</text>
</svg>"""


async def generate_svg_with_feedback(
    *,
    evidence_json: dict,
    spec: InfraSpec,
    max_attempts: int = 2,
) -> dict:
    summary = spec.context_summary or {}
    compact_evidence = _compact_evidence(evidence_json)
    current_feedback = ""
    last_svg = ""
    last_evaluation = {"score": 0, "must_fix": [], "suggestions": []}

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
            "renderer_version": "abd_svg_ai_v2",
        }

    for attempt in range(1, max_attempts + 1):
        user_message = (
            "Generate a high-quality SVG authorization boundary plus external data-flow diagram.\n\n"
            + STYLE_BRIEF
            + "\n\n"
            "Context summary JSON:\n"
            + json.dumps(summary, indent=2)
            + "\n\nCompact evidence JSON:\n"
            + json.dumps(compact_evidence, indent=2, default=str)
            + ("\n\nFix these issues from the previous attempt:\n" + current_feedback if current_feedback else "")
        )
        raw_svg = await invoke_text(
            system_message=SVG_GENERATOR_SYSTEM,
            user_message=user_message,
            temperature=0.2,
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

        eval_message = (
            "Evaluate this candidate SVG against the style brief and evidence.\n\n"
            + STYLE_BRIEF
            + "\n\n"
            "Primary focus:\n"
            "- same style family as the brief, not exact duplication\n"
            "- readability and spacing\n"
            "- whether orthogonal line routing overlaps boxes or labels too much\n"
            "- whether colored flow lines are explained in the legend\n"
            "- whether colored boundaries are explained in the legend\n"
            "- whether lines visibly touch their boxes\n"
            "- whether there is no text on connector lines\n"
            "- whether data flows are shown only for outside interactions\n"
            "- whether the drawn components are supported by the evidence\n\n"
            "Context summary JSON:\n"
            + json.dumps(summary, indent=2)
            + "\n\nEvidence JSON counts:\n"
            + json.dumps(compact_evidence.get("counts") or {}, indent=2, default=str)
            + "\n\nCandidate SVG:\n"
            + candidate_svg
        )
        raw_eval = await invoke_text(
            system_message=SVG_EVALUATOR_SYSTEM,
            user_message=eval_message,
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
                "renderer_version": "abd_svg_ai_v2",
            }
        if attempt < max_attempts:
            current_feedback = "\n".join([f"- {item}" for item in must_fix + suggestions]) or "Improve style fidelity, spacing, and line readability."

    if not last_svg:
        last_svg = _test_fallback_svg(spec)
    return {
        "svg_markup": last_svg,
        "evaluation": last_evaluation,
        "attempts": max_attempts,
        "renderer_version": "abd_svg_ai_v2",
    }
