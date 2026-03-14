"""
Dynamic tool registry for AWS evidence tools consumed by the AI agent.

These tools are AWS-focused but are *used by* the AI agent, so the registry
lives under the AI agent service domain per repo architecture.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.aws.evidence_service import AWSEvidenceService


@dataclass(frozen=True)
class ToolSpec:
    name: str
    purpose: str
    is_default: bool = False


ToolBuilder = Callable[[AWSEvidenceService], StructuredTool]

_REGISTRY: dict[str, tuple[ToolSpec, ToolBuilder]] = {}
_LOADED = False


def register_tool(*, spec: ToolSpec) -> Callable[[ToolBuilder], ToolBuilder]:
    """Decorator to register a tool builder under ToolSpec.name."""

    def _decorator(builder: ToolBuilder) -> ToolBuilder:
        _REGISTRY[spec.name] = (spec, builder)
        return builder

    return _decorator


def ensure_tools_loaded() -> None:
    """
    Auto-discover and import all modules in the tools package so their
    registration decorators run.
    """
    global _LOADED
    if _LOADED:
        return

    package_name = __name__.rsplit(".", 1)[0]  # app.services.ai_agent.ai_tools
    package = importlib.import_module(package_name)

    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name in {"registry", "catalog", "__init__"}:
            continue
        importlib.import_module(f"{package_name}.{mod.name}")

    _LOADED = True


def tool_specs() -> list[ToolSpec]:
    ensure_tools_loaded()
    return [spec for spec, _ in _REGISTRY.values()]


def enabled_tool_specs() -> list[ToolSpec]:
    """Apply optional allowlist from settings.assessment_ai_enabled_tools."""
    allow = {t.strip() for t in (settings.assessment_ai_enabled_tools or "").split(",") if t.strip()}
    specs = tool_specs()
    if not allow:
        return sorted(specs, key=lambda s: s.name)
    return sorted([s for s in specs if s.name in allow], key=lambda s: s.name)


def build_tools(db: AsyncSession) -> list[StructuredTool]:
    """Build enabled tools bound to the provided DB session."""
    ensure_tools_loaded()
    svc = AWSEvidenceService(db)

    allow = {t.strip() for t in (settings.assessment_ai_enabled_tools or "").split(",") if t.strip()}
    tools: list[StructuredTool] = []
    for name, (spec, builder) in _REGISTRY.items():
        if allow and name not in allow:
            continue
        tools.append(builder(svc))

    return sorted(tools, key=lambda t: t.name)


def default_tool_call_plan(*, account_id: str, ingestion_run_id: str | None) -> dict[str, Any]:
    """Return a fallback tool plan derived from registered default tools."""
    ensure_tools_loaded()
    defaults = [spec for spec, _ in _REGISTRY.values() if spec.is_default]
    defaults = sorted(defaults, key=lambda s: s.name)
    tool_calls = []
    for spec in defaults:
        if spec.name == "aws_default_evidence_snapshot":
            tool_calls.append(
                {
                    "name": spec.name,
                    "args": {
                        "account_id": account_id,
                        "ingestion_run_id": ingestion_run_id,
                        "sample_limit": settings.assessment_ai_default_sample_limit,
                    },
                }
            )
        else:
            tool_calls.append(
                {"name": spec.name, "args": {"account_id": account_id, "ingestion_run_id": ingestion_run_id}}
            )
    return {"tool_calls": tool_calls}

