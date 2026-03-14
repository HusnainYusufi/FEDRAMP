from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.db.models import ControlRoadmap
from app.services.ai_agent.narrative import llm_client
from app.services.ai_agent.validator.models import ValidationFindings

logger = get_logger(__name__)


def _minify_evidence_snapshot(snapshot: dict[str, Any], *, max_list_items: int = 10) -> dict[str, Any]:
    """
    Deterministically reduce evidence payload size so validator prompts stay stable.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    out: dict[str, Any] = {
        "control_id": snap.get("control_id"),
        "account_id": snap.get("account_id"),
        "ingestion_run_id": snap.get("ingestion_run_id"),
        "analysis": snap.get("analysis", {}),
        "tool_calls": snap.get("tool_calls", []),
    }

    tool_outputs = snap.get("tool_outputs")
    if not isinstance(tool_outputs, list):
        return out

    trimmed: list[dict[str, Any]] = []
    for item in tool_outputs[:25]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        result = item.get("result")
        err = item.get("error")
        if err and not result:
            trimmed.append({"name": name, "error": err})
            continue
        if not isinstance(result, dict):
            trimmed.append({"name": name, "result_type": type(result).__name__})
            continue

        res2: dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, list):
                res2[k] = v[:max_list_items]
            else:
                res2[k] = v
        trimmed.append({"name": name, "result": res2})

    out["tool_outputs"] = trimmed
    return out


async def _get_roadmap_override(
    db: AsyncSession,
    *,
    control_id: str,
    account_id: str | None,
) -> ControlRoadmap | None:
    stmt = select(ControlRoadmap).where(ControlRoadmap.control_id == control_id)
    if account_id is None:
        stmt = stmt.where(ControlRoadmap.account_id.is_(None))
    else:
        stmt = stmt.where(or_(ControlRoadmap.account_id == account_id, ControlRoadmap.account_id.is_(None)))
        # Prefer account-scoped override, then global override.
        stmt = stmt.order_by(ControlRoadmap.account_id.is_(None))
    row = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    return row


def _fallback_findings(*, control_id: str, status: str, reason: str) -> ValidationFindings:
    impl = status if status in {"Implemented", "Partially Implemented", "Planned", "Not Implemented"} else "Not Implemented"
    return ValidationFindings(
        control_id=control_id,
        implementation_status=impl,  # type: ignore[arg-type]
        strengths=[],
        gaps=[reason] if reason else [],
        inherited_controls=[],
        customer_responsibilities=[],
        remediation_items=[],
    )


async def validate_control(
    *,
    db: AsyncSession,
    control: dict[str, Any],
    evidence_snapshot: dict[str, Any],
    account_id: str | None,
) -> dict[str, Any]:
    """
    Run internal compliance validation and return structured findings JSON.

    The narrative layer MUST consume only this structured state (or a sanitized
    derivative), never raw audit phrasing.
    """
    control_id = str(control.get("control_id") or evidence_snapshot.get("control_id") or "").strip()
    if not control_id:
        return _fallback_findings(control_id="UNKNOWN", status="Not Implemented", reason="Missing control_id.").model_dump()

    tool_outputs = evidence_snapshot.get("tool_outputs", [])
    has_valid_output = isinstance(tool_outputs, list) and any(out.get("result") and not out.get("error") for out in tool_outputs if isinstance(out, dict))
    if not has_valid_output:
        findings = _fallback_findings(
            control_id=control_id,
            status="Not Implemented",
            reason="No valid technical evidence could be retrieved from infrastructure scans.",
        )
        # Roadmap override can still lift Not Implemented -> Planned (manual override).
        try:
            rm = await _get_roadmap_override(db, control_id=control_id, account_id=account_id)
            if rm and (rm.status_override or "").strip().lower() == "planned":
                findings.implementation_status = "Planned"
                if rm.narrative_roadmap_summary:
                    findings.remediation_items.append(rm.narrative_roadmap_summary.strip())
        except Exception as exc:
            logger.warning("roadmap_override_lookup_failed", control_id=control_id, error=str(exc))
        return findings.model_dump()

    system_msg = (
        "You are an internal Compliance Validator for a FedRAMP program.\n"
        "- You MAY use factual deficiency language.\n"
        "- You are NOT writing SSP narrative prose.\n"
        "- Do not include accusatory language; keep items concise and objective.\n"
        "- Use ONLY the provided evidence snapshot and control text.\n"
        "- If evidence is insufficient, explicitly record that as a gap.\n\n"
        "Return ONLY valid JSON with EXACTLY these keys:\n"
        "{\n"
        '  \"control_id\": string,\n'
        '  \"implementation_status\": \"Implemented\"|\"Partially Implemented\"|\"Planned\"|\"Not Implemented\",\n'
        '  \"strengths\": string[],\n'
        '  \"gaps\": string[],\n'
        '  \"inherited_controls\": string[],\n'
        '  \"customer_responsibilities\": string[],\n'
        '  \"remediation_items\": string[]\n'
        "}"
    )

    evidence_min = _minify_evidence_snapshot(evidence_snapshot or {}, max_list_items=10)
    user_msg = (
        f"Control:\n{json.dumps(control, default=str)}\n\n"
        f"Evidence snapshot (minified):\n{json.dumps(evidence_min, default=str)}\n\n"
        "Now return the validator findings JSON."
    )

    raw = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.0)
    try:
        obj = json.loads(raw)
        findings = ValidationFindings.model_validate(obj)
    except Exception as exc:
        logger.warning("validator_parse_failed", control_id=control_id, error=str(exc))
        findings = _fallback_findings(
            control_id=control_id,
            status="Not Implemented",
            reason="Validator output could not be parsed as structured findings JSON.",
        )

    # Apply manual roadmap override (Planned) when present and not already Implemented.
    try:
        rm = await _get_roadmap_override(db, control_id=control_id, account_id=account_id)
        if rm and (rm.status_override or "").strip().lower() == "planned":
            if findings.implementation_status != "Implemented":
                findings.implementation_status = "Planned"
                if rm.narrative_roadmap_summary and rm.narrative_roadmap_summary.strip() not in findings.remediation_items:
                    findings.remediation_items.append(rm.narrative_roadmap_summary.strip())
                if rm.target_date:
                    td = str(rm.target_date).strip()
                    if td:
                        findings.remediation_items.append(f"Target implementation date: {td}")
    except Exception as exc:
        logger.warning("roadmap_override_apply_failed", control_id=control_id, error=str(exc))

    # Ensure required ID is correct.
    findings.control_id = control_id
    return findings.model_dump()

