"""
Tool catalog renderer for evidence tools.

Tools are dynamically discovered and registered (see ai_tools/registry.py).
"""

from __future__ import annotations

from app.services.ai_agent.ai_tools.registry import enabled_tool_specs


def render_tool_catalog() -> str:
    """Render a concise tool list for LLM context (no chain-of-thought)."""
    specs = enabled_tool_specs()
    lines = ["Available evidence tools:"]
    for spec in specs:
        lines.append(f"- {spec.name}: {spec.purpose}")
    return "\n".join(lines)

