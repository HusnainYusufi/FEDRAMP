"""
Output parser — validates and extracts structured fields from LLM output.

Ensures the generated narrative has the required Markdown headings and
extracts the implementation status for DB storage.
"""

import re
from typing import Any

from app.config.logging_config import get_logger

logger = get_logger(__name__)

REQUIRED_HEADINGS = [
    "Control Summary",
    "Implementation Status",
    "What is the solution and how is it implemented?",
    "Deviations and Observations",
]

VALID_STATUSES = ["implemented", "partially implemented", "planned", "not implemented"]


def parse_narrative(raw_output: str) -> dict[str, Any]:
    """Validate structure and extract metadata from the LLM output."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    missing: list[str] = []
    for heading in REQUIRED_HEADINGS:
        pattern = rf"^##\s+{re.escape(heading)}"
        if not re.search(pattern, cleaned, re.MULTILINE | re.IGNORECASE):
            missing.append(heading)

    if missing:
        logger.warning("narrative_missing_headings", missing=missing)

    status = _extract_status(cleaned)
    return {
        "markdown": cleaned,
        "implementation_status": status,
        "missing_headings": missing,
        "is_valid": len(missing) == 0,
    }


def _extract_status(text: str) -> str:
    pattern = r"##\s+Implementation Status\s*\n(.*?)(?=\n##|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        section = match.group(1).lower()
        ordered = [
            "partially implemented",
            "not implemented",
            "implemented",
            "planned",
        ]
        for status in ordered:
            if re.search(rf"\b{re.escape(status)}\b", section):
                return status.title()
    return "Unknown"

