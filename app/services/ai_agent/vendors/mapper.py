from __future__ import annotations

import json
from typing import Any

from app.services.ai_agent.narrative import llm_client


DEFAULT_CATEGORIES = [
    "IAM",
    "Operating System",
    "Endpoint Protection",
    "Vulnerability Scanning",
    "Logging / SIEM",
    "Network Security",
    "Encryption / KMS",
    "CI/CD",
    "Backup / DR",
    "Asset Inventory",
]


def render_vendor_table_html(vendor_map: dict[str, list[str]]) -> str:
    rows = []
    for cat in sorted(vendor_map.keys()):
        vendors = ", ".join(vendor_map.get(cat) or [])
        rows.append(f"<tr><td>{cat}</td><td>{vendors}</td></tr>")
    return (
        "<table>"
        "<thead><tr><th>Category</th><th>Technologies / Vendors</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


async def extract_vendor_map(
    *,
    evidence_json: dict[str, Any],
    narrative_texts: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict[str, list[str]]:
    cats = categories or DEFAULT_CATEGORIES
    texts = narrative_texts or []

    system_message = (
        "You are an extraction engine for a FedRAMP SSP 'Security and Management Technologies' table. "
        "Return ONLY valid JSON. No markdown. No commentary."
    )
    user_message = (
        "Extract the technologies/vendors used by the system and categorize them.\n\n"
        "Return STRICT JSON with this schema:\n"
        "{\n"
        '  "Category A": ["Vendor 1", "Vendor 2"],\n'
        '  "Category B": ["Vendor 3"]\n'
        "}\n\n"
        "Rules:\n"
        f"- Only use these categories: {json.dumps(cats)}\n"
        "- Values must be arrays of strings\n"
        "- Do not invent vendors not supported by the evidence\n"
        "- If unknown for a category, return an empty array\n\n"
        "Evidence (AWS scan JSON):\n"
        f"{json.dumps(evidence_json, indent=2, default=str)}\n\n"
        "Additional narrative snippets (policies/SSP statements):\n"
        f"{json.dumps(texts, indent=2)}\n"
    )

    raw = await llm_client.invoke_text(system_message=system_message, user_message=user_message, temperature=0.1)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("vendor_map_not_object")

    out: dict[str, list[str]] = {c: [] for c in cats}
    for k, v in parsed.items():
        if k not in out:
            continue
        if isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            out[k] = []
    return out

