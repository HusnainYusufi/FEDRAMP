"""
LangGraph workflow — narrative generation state machine + evidence/Judge subgraph.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.config.logging_config import get_logger
from app.db.models import FedRAMPControl
from app.services.ai_agent.narrative import controls_repo, llm_client, output_parser, prompt_engine
from app.services.ai_agent.narrative.modes import GenerationMode, ToneTier
from app.services.ai_agent.ai_tools.catalog import render_tool_catalog
from app.services.ai_agent.ai_tools.registry import build_tools, default_tool_call_plan
from app.services.ai_agent.narrative.template_direct import generate_narrative_from_template
from app.services.ai_agent.validator import validator as compliance_validator
from app.services.aws.evidence_service import AWSEvidenceService

logger = get_logger(__name__)


class NarrativeState(TypedDict, total=False):
    control_id: str
    account_id: str
    ingestion_run_id: str | None
    db: Any  # AsyncSession — not serialized

    control: dict[str, Any]
    analysis: dict[str, Any]
    tool_plan: dict[str, Any]
    evidence_snapshot: dict[str, Any]
    compliance_evaluation: dict[str, Any]
    validation_findings: dict[str, Any]
    system_message: str
    user_message: str
    llm_raw_output: str

    result: dict[str, Any]
    error: str | None


async def load_control(state: NarrativeState) -> dict:
    db: AsyncSession = state["db"]
    control_id = state["control_id"]
    control = await controls_repo.get_control(control_id, db)
    if control is None:
        return {
            "error": (
                f"Control '{control_id}' not found in database. "
                "Run scripts/load_fedramp_controls.py first."
            )
        }
    return {"control": control}


async def analyze_control(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}

    control = state["control"]
    system_msg = (
        "You are a FedRAMP assessor. Return ONLY valid JSON. "
        "Do not include chain-of-thought. Keep summaries short."
    )
    user_msg = (
        "Given this control definition, identify what evidence categories are needed.\n\n"
        f"Control ID: {control['control_id']}\n"
        f"Title: {control['title']}\n"
        f"Family: {control['family']}\n\n"
        "Return JSON with keys:\n"
        f"- assessor_summary: array of 2-{settings.assessment_ai_max_assessor_summary_items} short bullet strings (high-level, non-sensitive)\n"
        "- evidence_needs: array of short strings\n"
        "- preferred_tables: array subset of [identities, assets, network_components, data_stores]\n"
    )

    raw = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.2)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("analysis_not_object")
        if isinstance(parsed.get("assessor_summary"), list):
            parsed["assessor_summary"] = parsed["assessor_summary"][: settings.assessment_ai_max_assessor_summary_items]
        return {"analysis": parsed}
    except Exception:
        return {"analysis": {"evidence_needs": [], "preferred_tables": []}}


async def plan_tool_calls(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}

    control = state["control"]
    analysis = state.get("analysis", {})
    tool_catalog = render_tool_catalog()

    system_msg = (
        "You are a tool planner for a compliance evidence system. "
        "Return ONLY valid JSON. Do not include explanations."
    )
    user_msg = (
        f"{tool_catalog}\n\n"
        "Plan evidence retrieval for this control.\n\n"
        f"Control ID: {control['control_id']}\n"
        f"Title: {control['title']}\n"
        f"Family: {control['family']}\n\n"
        "Execution context (MUST use these values):\n"
        f"- account_id: {state['account_id']}\n"
        f"- ingestion_run_id: {state.get('ingestion_run_id') or 'null (use latest successful)'}\n\n"
        f"Evidence needs (hints): {json.dumps(analysis, default=str)}\n\n"
        "Return JSON with key tool_calls: array of objects:\n"
        "  - name: tool name\n"
        "  - args: tool args object\n"
        "Constraints:\n"
        f"- Max {settings.assessment_ai_max_tool_calls} tool calls\n"
        "- You may return fewer tool calls (e.g., 3) if you already have sufficient evidence\n"
        "- Always include account_id in args (use the provided account_id exactly)\n"
        "- ingestion_run_id must be a UUID string or null; do NOT use sentinel strings like 'LATEST'\n"
        "- For aws_list_records: limit must be <= 50 (the system will clamp larger values)\n"
        "- Avoid redundant calls: if you include aws_default_evidence_snapshot, do NOT also call overlapping aws_summarize_* tools unless you need extra detail beyond the snapshot.\n"
    )

    raw = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.2)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or "tool_calls" not in parsed:
            raise ValueError("tool_plan_invalid")
        return {"tool_plan": parsed}
    except Exception:
        return {
            "tool_plan": default_tool_call_plan(
                account_id=state["account_id"],
                ingestion_run_id=state.get("ingestion_run_id"),
            )
        }


async def execute_tools(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}

    db: AsyncSession = state["db"]
    account_id = state["account_id"]
    ingestion_run_id = state.get("ingestion_run_id")
    tools = {t.name: t for t in build_tools(db)}
    tool_plan = state.get("tool_plan", {})
    tool_calls = tool_plan.get("tool_calls", [])

    svc = AWSEvidenceService(db)
    resolved_run_uuid = await svc.resolve_run_id(account_id, ingestion_run_id)
    resolved_run_id = str(resolved_run_uuid) if resolved_run_uuid else None

    outputs: list[dict[str, Any]] = []
    normalized_calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in tool_calls[: settings.assessment_ai_max_tool_calls]:
        name = call.get("name")
        args = call.get("args", {}) or {}

        if not args.get("account_id") or str(args.get("account_id")).strip().upper() == "UNKNOWN":
            args["account_id"] = account_id

        if args.get("ingestion_run_id") is not None:
            cand = str(args.get("ingestion_run_id")).strip()
            if not cand or cand.upper() == "LATEST":
                args["ingestion_run_id"] = None

        if "ingestion_run_id" not in args or args.get("ingestion_run_id") is None:
            args["ingestion_run_id"] = resolved_run_id or ingestion_run_id

        if name == "aws_list_records" and isinstance(args.get("limit"), int) and args["limit"] > 50:
            args["limit"] = 50

        call_key = hashlib.sha256(
            (name or "").encode("utf-8")
            + b"\n"
            + json.dumps(args, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if call_key in seen:
            continue
        seen.add(call_key)

        normalized_calls.append({"name": name, "args": args})
        tool = tools.get(name)
        if not tool:
            outputs.append({"name": name, "error": "unknown_tool"})
            continue
        try:
            result = await tool.ainvoke(args)
            outputs.append({"name": name, "args": args, "result": result})
        except Exception as exc:
            outputs.append({"name": name, "args": args, "error": str(exc)})

    evidence_snapshot = {
        "control_id": state["control_id"],
        "account_id": account_id,
        "ingestion_run_id": resolved_run_id,
        "analysis": state.get("analysis", {}),
        "tool_plan": state.get("tool_plan", {}),
        "tool_calls": normalized_calls,
        "tool_outputs": outputs,
    }
    return {"evidence_snapshot": evidence_snapshot}


async def evaluate_compliance(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}

    db: AsyncSession = state["db"]
    evidence = state["evidence_snapshot"]
    control = state["control"]

    tool_outputs = evidence.get("tool_outputs", [])
    has_valid_output = isinstance(tool_outputs, list) and any(
        isinstance(out, dict) and out.get("result") and not out.get("error") for out in tool_outputs
    )

    findings = await compliance_validator.validate_control(
        db=db,
        control=control,
        evidence_snapshot=evidence,
        account_id=state.get("account_id"),
    )

    # Back-compat: keep a compact `compliance_evaluation` block for callers/UI
    # while the narrative system migrates to the structured findings contract.
    status = str(findings.get("implementation_status") or "Not Implemented").strip() or "Not Implemented"
    reasoning = ""
    gaps = findings.get("gaps")
    if isinstance(gaps, list) and gaps:
        reasoning = str(gaps[0] or "").strip()
    if not reasoning:
        strengths = findings.get("strengths")
        if isinstance(strengths, list) and strengths:
            reasoning = str(strengths[0] or "").strip()
    if not reasoning:
        reasoning = "Structured validator findings generated."

    return {
        "validation_findings": findings,
        "compliance_evaluation": {
            "status": status,
            "reasoning": reasoning,
            "valid_evidence_found": bool(has_valid_output),
        },
    }


async def build_prompt(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}
    system_msg, user_msg = prompt_engine.build_prompt(
        control=state["control"],
        evidence_snapshot=state["evidence_snapshot"],
        compliance_evaluation=state.get("compliance_evaluation"),
    )
    return {"system_message": system_msg, "user_message": user_msg}


async def write_narrative(state: NarrativeState) -> dict:
    if state.get("error"):
        return {}
    try:
        raw_output = await llm_client.invoke_text(
            system_message=state["system_message"],
            user_message=state["user_message"],
            temperature=0.3,
        )
        return {"llm_raw_output": raw_output}
    except Exception as exc:
        logger.error("llm_call_failed", error=str(exc))
        return {"error": f"LLM call failed: {exc}"}


async def parse_output(state: NarrativeState) -> dict:
    if state.get("error"):
        return {
            "result": {
                "markdown": "",
                "implementation_status": "Error",
                "is_valid": False,
                "error": state["error"],
            }
        }

    parsed = output_parser.parse_narrative(state["llm_raw_output"])
    return {
        "result": {
            "markdown": parsed["markdown"],
            "implementation_status": parsed["implementation_status"],
            "is_valid": parsed["is_valid"],
            "missing_headings": parsed["missing_headings"],
            "model": settings.openai_model,
        }
    }


def _build_graph() -> StateGraph:
    workflow = StateGraph(NarrativeState)
    workflow.add_node("load_control", load_control)
    workflow.add_node("analyze_control", analyze_control)
    workflow.add_node("plan_tool_calls", plan_tool_calls)
    workflow.add_node("execute_tools", execute_tools)
    workflow.add_node("evaluate_compliance", evaluate_compliance)
    workflow.add_node("build_prompt", build_prompt)
    workflow.add_node("write_narrative", write_narrative)
    workflow.add_node("parse_output", parse_output)

    workflow.set_entry_point("load_control")
    workflow.add_edge("load_control", "analyze_control")
    workflow.add_edge("analyze_control", "plan_tool_calls")
    workflow.add_edge("plan_tool_calls", "execute_tools")
    workflow.add_edge("execute_tools", "evaluate_compliance")
    workflow.add_edge("evaluate_compliance", "build_prompt")
    workflow.add_edge("build_prompt", "write_narrative")
    workflow.add_edge("write_narrative", "parse_output")
    workflow.add_edge("parse_output", END)
    return workflow.compile()


narrative_graph = _build_graph()


def _build_evidence_graph() -> StateGraph:
    workflow = StateGraph(NarrativeState)
    workflow.add_node("load_control", load_control)
    workflow.add_node("analyze_control", analyze_control)
    workflow.add_node("plan_tool_calls", plan_tool_calls)
    workflow.add_node("execute_tools", execute_tools)
    workflow.add_node("evaluate_compliance", evaluate_compliance)

    workflow.set_entry_point("load_control")
    workflow.add_edge("load_control", "analyze_control")
    workflow.add_edge("analyze_control", "plan_tool_calls")
    workflow.add_edge("plan_tool_calls", "execute_tools")
    workflow.add_edge("execute_tools", "evaluate_compliance")
    workflow.add_edge("evaluate_compliance", END)
    return workflow.compile()


evidence_graph = _build_evidence_graph()


async def collect_evidence_and_evaluate(
    *,
    control_id: str,
    account_id: str,
    db: AsyncSession,
    ingestion_run_id: str | None = None,
) -> dict[str, Any]:
    initial_state: NarrativeState = {
        "control_id": control_id,
        "account_id": account_id,
        "ingestion_run_id": ingestion_run_id,
        "db": db,
    }
    final_state = await evidence_graph.ainvoke(initial_state)
    return {
        "error": final_state.get("error"),
        "control": final_state.get("control"),
        "analysis": final_state.get("analysis"),
        "tool_plan": final_state.get("tool_plan"),
        "evidence_snapshot": final_state.get("evidence_snapshot"),
        "compliance_evaluation": final_state.get("compliance_evaluation"),
        "validation_findings": final_state.get("validation_findings"),
    }


async def generate_narrative(
    control_id: str,
    account_id: str,
    db: AsyncSession,
    ingestion_run_id: str | None = None,
    mode: GenerationMode | str | None = None,
    tone_tier: ToneTier | str | None = None,
) -> dict[str, Any]:
    # Normalize mode/tone.
    try:
        mode2 = GenerationMode(mode) if isinstance(mode, str) else (mode or GenerationMode.SSP_NARRATIVE_MODE)
    except Exception:
        mode2 = GenerationMode.SSP_NARRATIVE_MODE
    try:
        tone2 = ToneTier(tone_tier) if isinstance(tone_tier, str) else (tone_tier or ToneTier.meduim)
    except Exception:
        tone2 = ToneTier.meduim

    if mode2 == GenerationMode.AUDIT_MODE:
        return {
            "markdown": "",
            "implementation_status": "Unknown",
            "is_valid": False,
            "missing_headings": [],
            "model": settings.openai_model,
            "error": "AUDIT_MODE does not generate narratives. Use /ai/validator/evaluate instead.",
            "evidence_snapshot": {},
        }

    # SSP_NARRATIVE_MODE: evidence -> validator findings -> strategy -> narrative writer
    try:
        evidence_result = await collect_evidence_and_evaluate(
            control_id=control_id,
            account_id=account_id,
            db=db,
            ingestion_run_id=ingestion_run_id,
        )
        if evidence_result.get("error"):
            return {
                "markdown": "",
                "implementation_status": "Error",
                "is_valid": False,
                "missing_headings": [],
                "model": settings.openai_model,
                "error": str(evidence_result["error"]),
                "evidence_snapshot": evidence_result.get("evidence_snapshot", {}),
            }

        from app.services.ai_agent.narrative.ssp_writer import (  # local import to avoid cycles
            generate_ssp_narrative_from_state,
        )

        return await generate_ssp_narrative_from_state(
            db=db,
            control=evidence_result.get("control") or {"control_id": control_id},
            evidence_snapshot=evidence_result.get("evidence_snapshot") or {},
            validation_findings=evidence_result.get("validation_findings") or {},
            account_id=account_id,
            tone_tier=tone2.value,
            max_attempts=3,
        )
    except Exception as exc:
        logger.error("ssp_narrative_generation_failed", error=str(exc))
        return {
            "markdown": "",
            "implementation_status": "Error",
            "is_valid": False,
            "missing_headings": [],
            "model": settings.openai_model,
            "error": f"SSP narrative generation failed: {exc}",
            "evidence_snapshot": {},
        }

