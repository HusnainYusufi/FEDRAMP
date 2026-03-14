from __future__ import annotations

import json
from typing import Any

from app.services.ai_agent.architecture_diagrams.models import InfraSpec
from app.services.ai_agent.narrative.llm_client import invoke_text


ARCH_SUMMARY_SYSTEM = """\
You are a Lead Cloud Architect and FedRAMP assessor.
You read AWS evidence JSON and produce concise diagram-planning summaries for an authorization boundary diagram.

Rules:
- Use only components and relationships that are present in the evidence.
- You may recommend placeholders for audit completeness if the evidence is missing a common FedRAMP component.
- Prefer concise architecture language that helps a diagram generator create a clean, readable, low-overlap layout.
- Return ONLY valid JSON. No markdown. No extra commentary.
"""


def deterministic_context_summary(*, evidence_json: dict[str, Any]) -> dict[str, Any]:
    resources = evidence_json.get("resources") or {}
    return {
        "title": "ABD Overview",
        "boundary_label": "FedRAMP Authorization Boundary",
        "overview": "AWS authorization boundary derived from ingested evidence.",
        "network_summary": f"VPCs: {len(resources.get('vpcs') or [])}, Subnets: {len(resources.get('subnets') or [])}, Perimeter services: {len(resources.get('internet_gateways') or []) + len(resources.get('nat_gateways') or []) + len(resources.get('vpc_endpoints') or [])}.",
        "app_summary": f"Application resources evidenced: {len(resources.get('ec2_instances') or [])}.",
        "data_summary": f"Data services evidenced: {len(resources.get('rds_instances') or []) + len(resources.get('s3_buckets') or [])}.",
        "security_summary": f"Security and observability services evidenced: {len(resources.get('cloudtrail_trails') or []) + len(resources.get('cloudwatch_log_groups') or [])}.",
        "style_goals": [
            "Use side panels with a central authorization boundary",
            "Keep connector routing readable with minimal overlap",
            "Group repeated resources rather than drawing every low-value detail",
            "Use colored orthogonal data-flow lines with a matching legend",
            "Use colored boundary outlines with a matching legend",
            "Leave generous spacing between major boxes and use a larger canvas if needed",
            "Do not place text on connector lines",
            "Show data flows only for interactions with external actors or external services",
        ],
        "flow_labels": ["User Access", "Admin Access", "External Ingress", "External Egress", "Logging", "Security Feed"],
    }


async def summarize_architecture_context(*, evidence_json: dict[str, Any]) -> dict[str, Any]:
    fallback = deterministic_context_summary(evidence_json=evidence_json)
    user_message = (
        "Review this AWS evidence and produce concise JSON for an SVG authorization boundary diagram.\n"
        "Return JSON exactly with keys:\n"
        "{\n"
        '  "title": "string",\n'
        '  "boundary_label": "string",\n'
        '  "overview": "string",\n'
        '  "network_summary": "string",\n'
        '  "app_summary": "string",\n'
        '  "data_summary": "string",\n'
        '  "security_summary": "string",\n'
        '  "style_goals": ["goal1", "goal2", "goal3"],\n'
        '  "flow_labels": ["label1", "label2", "label3", "label4", "label5", "label6"]\n'
        "}\n\n"
        "Style goals must describe visual intent only, such as panel hierarchy, readable routing, orthogonal colored lines, colored boundaries, no text on connector lines, external-only data flows, and compact grouping.\n\n"
        "Evidence JSON:\n"
        + json.dumps(evidence_json, indent=2, default=str)
    )
    try:
        raw = await invoke_text(
            system_message=ARCH_SUMMARY_SYSTEM,
            user_message=user_message,
            temperature=0.1,
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return fallback
        summary = dict(fallback)
        summary.update({k: v for k, v in parsed.items() if v is not None})
        labels = summary.get("flow_labels")
        if not isinstance(labels, list) or not labels:
            summary["flow_labels"] = fallback["flow_labels"]
        else:
            summary["flow_labels"] = [str(v) for v in labels[:6]]
            while len(summary["flow_labels"]) < 6:
                summary["flow_labels"].append(fallback["flow_labels"][len(summary["flow_labels"])])
        return summary
    except Exception:
        return fallback


def _safe_text(value: Any, fallback: str = "") -> str:
    text = str(value or fallback).strip()
    return text or fallback


def build_infra_spec_from_evidence(
    *, evidence_json: dict[str, Any], context_summary: dict[str, Any] | None = None
) -> InfraSpec:
    account_id = _safe_text(evidence_json.get("account_id"))
    ingestion_run_id = evidence_json.get("ingestion_run_id")
    summary = context_summary or deterministic_context_summary(evidence_json=evidence_json)

    return InfraSpec(
        account_id=account_id,
        ingestion_run_id=_safe_text(ingestion_run_id) or None,
        title=_safe_text(summary.get("title"), "ABD Overview"),
        boundary_label=_safe_text(summary.get("boundary_label"), "FedRAMP Authorization Boundary"),
        evidence={
            "counts": evidence_json.get("counts") or {},
            "resources": evidence_json.get("resources") or {},
            "notes": evidence_json.get("notes") or {},
        },
        context_summary=summary,
    )


def evaluate_infra_spec(*, spec: InfraSpec) -> dict[str, Any]:
    suggestions: list[str] = []
    must_fix: list[str] = []
    score = 72

    if spec.vpcs:
        score += 8
    else:
        must_fix.append("No VPC evidence was available for the authorization boundary.")

    if spec.public_subnets or spec.private_subnets:
        score += 6
    else:
        suggestions.append("Add subnet evidence to improve tier separation in the diagram.")

    if spec.app_tier and not spec.app_tier[0].meta.get("placeholder"):
        score += 5
    else:
        suggestions.append("No evidenced application tier was found; the diagram is using placeholders.")

    if spec.data_tier and not spec.data_tier[0].meta.get("placeholder"):
        score += 4
    else:
        suggestions.append("No evidenced data tier was found; the diagram is using placeholders.")

    if spec.security_services and not spec.security_services[0].meta.get("placeholder"):
        score += 4
    else:
        suggestions.append("Security services were not evidenced; observability remains placeholder-based.")

    if spec.perimeter:
        score += 4
    else:
        suggestions.append("No internet-facing perimeter services were evidenced.")

    score = max(50, min(score, 99))
    if must_fix:
        score = min(score, 68)

    return {
        "score": score,
        "must_fix": must_fix,
        "suggestions": suggestions,
    }


