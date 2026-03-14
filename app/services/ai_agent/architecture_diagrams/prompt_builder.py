from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


SUMMARIZER_SYSTEM_MESSAGE = """\
You are a Lead Cloud Architect and FedRAMP assessor.
You read AWS ingestion evidence JSON and extract only what is explicitly present in the evidence.
Never hallucinate. If the evidence is missing a component, do not include it.

Output rules:
- Return ONLY valid JSON.
- No markdown.
- No extra keys.
"""


def build_summarizer_user_message(*, evidence_json: dict[str, Any]) -> str:
    """
    Step 1 (Summarizer): ask a text model to convert evidence.json into short bullet points
    (the "Infrastructure to Draw" list).
    """
    import json

    return (
        "Read this AWS evidence JSON and summarize it into 5-12 bullet points of resources to draw.\n"
        "Focus on boundary + network tiers + security services + primary data flows.\n"
        "Do NOT invent services that are not present.\n\n"
        "Return JSON exactly in this schema:\n"
        "{\n"
        '  "boundary_label": "string",\n'
        '  "infrastructure_to_draw": ["bullet 1", "bullet 2", "..."],\n'
        '  "data_flows": ["A -> B", "C -> D", "..."]\n'
        "}\n\n"
        "AWS evidence JSON:\n"
        + json.dumps(evidence_json, indent=2, default=str)
    )


ARTIST_MASTER_PROMPT_TEMPLATE = """\
ACT AS: Lead Cloud Architect and Technical Illustrator.

CONTEXT: I have attached an example architecture diagram. I want you to study its visual style, layout, color scheme, and use of standard AWS iconography.

TASK: Generate a new, highly professional AWS Authorization Boundary and Dataflow Diagram. You must mimic the exact visual style of the attached example, but you will populate it with the following live infrastructure data:

INFRASTRUCTURE TO DRAW:

{infrastructure_bullets}

DATA FLOW:

{data_flow_bullets}

DESIGN RULES:

Style Matching: Strictly use the professional, clean aesthetic and official AWS isometric icons seen in the attached example.

Text Accuracy: Keep text labels short, large, and perfectly spelled (e.g., "VPC", "EC2", "RDS"). Do not hallucinate or invent any components that are not listed above.

Layout/Framing: Fit ALL boxes, arrows, and text fully within the image canvas with generous margins. Do NOT crop or cut off any content. Avoid overly dense layouts; group repeated items and use concise labels.

FedRAMP Audit Readiness (Option 1 - Transparency):
- You MUST include boxes for required items even if not evidenced (e.g., management/admin path, external IdP, external interconnections, logging services).
- Visually distinguish "NOT EVIDENCED / PLANNED" items using a dotted border and greyed-out fill.
- Include a small legend that defines: boundary line style, dataflow arrows, and the "Not Evidenced / Planned" visual style.
"""


def build_artist_prompt(*, infrastructure_to_draw: list[str], data_flows: list[str]) -> str:
    """
    Step 2 (Artist): prompt for an image generation model. The caller should include the
    example diagram image as an input image (style reference).
    """
    infra = "\n".join([f"- {b}".strip() for b in (infrastructure_to_draw or [])]) or "- (No components provided)"
    flows = "\n".join([f"- {f}".strip() for f in (data_flows or [])]) or "- (No flows provided)"
    return ARTIST_MASTER_PROMPT_TEMPLATE.format(
        infrastructure_bullets=infra,
        data_flow_bullets=flows,
    ).strip()


def build_artist_prompt_with_mermaid(
    *,
    infrastructure_to_draw: list[str],
    data_flows: list[str],
    mermaid_code: str,
) -> str:
    """
    Like build_artist_prompt(), but includes Mermaid syntax as additional context to reduce ambiguity.

    The image generator should use the Mermaid as a structural reference (not as literal text to render).
    """
    base = build_artist_prompt(infrastructure_to_draw=infrastructure_to_draw, data_flows=data_flows).rstrip()
    mm = (mermaid_code or "").strip()
    if not mm:
        return base + "\n"

    return (
        base
        + "\n\nMERMAID DIAGRAM SYNTAX (STRUCTURE REFERENCE ONLY):\n"
        + "Use this Mermaid flowchart as the source-of-truth for component relationships and layout intent.\n"
        + "Do NOT invent extra nodes not present in the Mermaid.\n"
        + "If the Mermaid includes a node labeled 'NOT EVIDENCED' or 'PLANNED', render it as dotted/grey.\n\n"
        + mm
        + "\n"
    )


def load_example_diagram_base64(*, workspace_root: Path) -> dict[str, str]:
    """
    Utility: read `@docs/example_diagram.png` and return base64 for APIs that accept it.

    Returns:
      { "file_name": "...", "mime_type": "...", "base64": "..." }
    """
    p = workspace_root / "@docs" / "example_diagram.png"
    content = p.read_bytes()
    return {
        "file_name": "example_diagram.png",
        "mime_type": "image/png",
        "base64": base64.b64encode(content).decode("utf-8"),
    }

