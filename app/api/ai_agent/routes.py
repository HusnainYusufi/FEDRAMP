"""
AI agent API — narratives + blueprint hydration.

Endpoints:
  POST /ai/narratives/generate
  GET  /ai/narratives/{id}
  GET  /ai/narratives
  GET  /ai/controls
  POST /ai/blueprints/hydrate
"""

from __future__ import annotations

import json
import re
from uuid import UUID
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    ArchitectureDiagram,
    ComplianceFinding,
    Document,
    DocumentChunk,
    FedRAMPControl,
    NarrativeStatus,
    POAMItem,
    SSPNarrative,
)
from app.db.session import get_db
from app.config.logging_config import get_logger
from app.services.ai_agent.narrative import controls_repo
from app.services.ai_agent.narrative.graph import collect_evidence_and_evaluate, generate_narrative
from app.services.ai_agent.narrative.modes import GenerationMode, ToneTier
from app.services.ai_agent.architecture_diagrams.aws_evidence import build_architecture_evidence
from app.services.ai_agent.architecture_diagrams.spec_from_evidence import (
    build_infra_spec_from_evidence,
    summarize_architecture_context,
)
from app.services.ai_agent.architecture_diagrams.mermaid_chain import generate_mermaid_with_feedback
from app.services.ai_agent.architecture_diagrams.mermaid_svg_renderer import (
    render_svg_from_mermaid_with_feedback,
)
from app.services.ai_agent.rag import service as rag_service
from app.services.ai_agent.scans.control_mapping import rule_map_finding
from app.services.ai_agent.scans.ingest import parse_nessus_csv, parse_securityhub_json
from app.services.ai_agent.vendors.mapper import extract_vendor_map, render_vendor_table_html
from app.services.ai_agent.policies.generator import render_policy_markdown, write_generated_policy
from app.services.ai_agent.poam.nvd_client import fetch_cve
from app.services.ai_agent.narrative import llm_client
from app.services.ai_agent.architecture_diagrams.prompt_builder import (
    SUMMARIZER_SYSTEM_MESSAGE,
    build_artist_prompt,
    build_summarizer_user_message,
    load_example_diagram_base64,
)
from app.services.aws.evidence_service import AWSEvidenceService
from app.services.aws.macie_service import MacieService

logger = get_logger(__name__)

router = APIRouter()

_CONTROL_ID_PATTERN = re.compile(r"\b([A-Z]{2}-\d+(?:\s*\(\d+\))?)\b", flags=re.IGNORECASE)
_CONTROL_MAP_STOPWORDS = {
    "about",
    "access",
    "across",
    "after",
    "allows",
    "application",
    "applications",
    "because",
    "between",
    "cloud",
    "controls",
    "customer",
    "customers",
    "describe",
    "describes",
    "document",
    "documents",
    "ensure",
    "fedramp",
    "implementation",
    "implemented",
    "including",
    "information",
    "organizations",
    "policy",
    "procedure",
    "process",
    "required",
    "requirements",
    "security",
    "service",
    "services",
    "shall",
    "support",
    "system",
    "their",
    "these",
    "through",
    "using",
    "users",
    "within",
}


def _normalize_control_id(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().upper())


def _extract_control_mentions(text: str) -> set[str]:
    return {_normalize_control_id(match.group(1)) for match in _CONTROL_ID_PATTERN.finditer(text or "")}


def _tokenize_control_text(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return {
        token
        for token in tokens
        if len(token) >= 4 and not token.isdigit() and token not in _CONTROL_MAP_STOPWORDS
    }


def _candidate_overlap_terms(*, document_tokens: set[str], control: dict) -> list[str]:
    control_tokens = _tokenize_control_text(
        " ".join(
            [
                str(control.get("title") or ""),
                str(control.get("family") or ""),
                str(control.get("nist_description") or ""),
            ]
        )
    )
    return sorted(document_tokens.intersection(control_tokens))


def _fallback_control_reason(*, control: dict, overlap_terms: list[str], explicit_match: bool) -> str:
    if explicit_match:
        return f"The document explicitly references {control['control_id']}."
    if overlap_terms:
        return (
            f"The document language overlaps with {control['control_id']} {control['title']}, "
            f"including: {', '.join(overlap_terms[:5])}."
        )
    return f"The document appears related to {control['control_id']} {control['title']}."


class GenerateRequest(BaseModel):
    control_id: str = Field(..., description="NIST 800-53 control ID (e.g. 'AC-2')", examples=["AC-2"])
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )
    persist: bool = Field(True, description="Whether to save the narrative to the database")
    mode: GenerationMode = Field(
        default=GenerationMode.SSP_NARRATIVE_MODE,
        description="Generation mode: AUDIT_MODE (validator only) or SSP_NARRATIVE_MODE (client-facing narrative)",
    )
    tone_tier: ToneTier = Field(
        default=ToneTier.meduim,
        description="Narrative tone/maturity tier (low|meduim|high)",
    )


class NarrativeResponse(BaseModel):
    id: str | None = None
    control_id: str
    account_id: str
    generated_markdown: str
    implementation_status: str
    status: str
    model: str
    evidence_snapshot: dict
    is_valid: bool
    missing_headings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    error: str | None = None


class NarrativeSaveRequest(BaseModel):
    control_id: str = Field(..., description="NIST 800-53 control ID (e.g. 'AC-2')", examples=["AC-2"])
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID (optional; stored for traceability)"
    )
    generated_markdown: str = Field(..., description="Final narrative markdown to persist")
    implementation_status: str = Field(..., description="Implementation status label for UI")
    evidence_snapshot: dict = Field(default_factory=dict, description="Evidence snapshot used for generation")
    model: str | None = Field(default=None, description="Model identifier (optional)")


class NarrativeRegenerateRequest(BaseModel):
    control_id: str = Field(..., description="NIST 800-53 control ID (e.g. 'AC-2')", examples=["AC-2"])
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )
    tone_tier: ToneTier = Field(
        default=ToneTier.meduim,
        description="Narrative tone/maturity tier (low|meduim|high)",
    )
    previous_markdown: str = Field(..., description="Previously generated narrative (for revision context)")
    rejection_reason: str = Field(..., description="Human reviewer reason for rejection")


class NarrativeListItem(BaseModel):
    id: str
    control_id: str
    account_id: str
    status: str
    model: str
    implementation_status: str | None = None
    created_at: str


class NarrativeListResponse(BaseModel):
    items: list[NarrativeListItem]
    total: int


class ControlListItem(BaseModel):
    control_id: str
    title: str
    family: str


class HydrateRequest(BaseModel):
    control_id: str = Field(..., description="NIST 800-53 control ID (e.g. 'AC-2')")
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )


class HydrateResponse(BaseModel):
    control_id: str
    account_id: str
    template_markdown: str
    compliance_evaluation: dict | None = None
    evidence_snapshot: dict | None = None
    validation_findings: dict | None = None


class ValidateControlRequest(BaseModel):
    control_id: str = Field(..., description="NIST 800-53 control ID (e.g. 'AC-2')")
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )


class ValidateControlResponse(BaseModel):
    control_id: str
    account_id: str
    ingestion_run_id: str | None = None
    validation_findings: dict
    evidence_snapshot: dict | None = None


class ArchitecturePromptsRequest(BaseModel):
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )
    include_example_image_base64: bool = Field(
        default=False,
        description="If true, include base64 of @docs/example_diagram.png for image-generation APIs.",
    )
    # Optional: if the caller already has summarizer output, we can build the artist prompt too.
    infrastructure_to_draw: list[str] | None = Field(
        default=None,
        description="Optional: infrastructure bullets (from Summarizer). If provided, Artist prompt is included.",
    )
    data_flows: list[str] | None = Field(
        default=None,
        description="Optional: data flow bullets (from Summarizer). If provided, Artist prompt is included.",
    )


class ArchitecturePromptsResponse(BaseModel):
    account_id: str
    ingestion_run_id: str | None
    evidence_json: dict
    summarizer_prompt: dict
    artist_prompt: str | None = None
    example_image: dict | None = None


class ArchitectureGenerateImageRequest(BaseModel):
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )
    include_example_image_base64: bool = Field(
        default=False,
        description="If true, include base64 of @docs/example_diagram.png for clients that want it.",
    )
    persist: bool = Field(
        default=True,
        description="If true, save the generated diagram to the database and return an id.",
    )
    use_llm_summarizer: bool = Field(
        default=False,
        description="If true, use the text model for Step 1 summarization. If false, build deterministic bullets from evidence to minimize hallucinations.",
    )


class ArchitectureGenerateImageResponse(BaseModel):
    id: str | None = None
    account_id: str
    ingestion_run_id: str | None
    summarizer_output: dict
    svg_markup: str | None = None
    artist_prompt: str
    image: dict
    example_image: dict | None = None


@router.post("/narratives/generate", response_model=NarrativeResponse)
async def generate_ssp_narrative(
    request: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    logger.info("narrative_generation_requested", control_id=request.control_id, account_id=request.account_id)

    if request.mode == GenerationMode.AUDIT_MODE:
        raise HTTPException(
            status_code=400,
            detail="AUDIT_MODE does not generate narratives. Use /ai/validator/evaluate.",
        )

    result = await generate_narrative(
        control_id=request.control_id,
        account_id=request.account_id,
        db=db,
        ingestion_run_id=request.ingestion_run_id,
        mode=request.mode,
        tone_tier=request.tone_tier,
    )

    narrative_id = None
    if request.persist and result.get("markdown"):
        snapshot = result.get("evidence_snapshot", {}) or {}
        if isinstance(snapshot, dict):
            meta = dict(snapshot.get("narrative_generation") or {})
            meta.update(
                {
                    "implementation_status": result.get("implementation_status", "Unknown"),
                    "mode": request.mode.value,
                    "tone_tier": request.tone_tier.value,
                }
            )
            snapshot["narrative_generation"] = meta
        narrative = SSPNarrative(
            control_id=request.control_id,
            account_id=request.account_id,
            ingestion_run_id=UUID(request.ingestion_run_id) if request.ingestion_run_id else None,
            generated_markdown=result["markdown"],
            status=NarrativeStatus.DRAFT,
            model=result.get("model", settings.openai_model),
            evidence_snapshot=snapshot,
        )
        db.add(narrative)
        await db.flush()
        narrative_id = str(narrative.id)

    return NarrativeResponse(
        id=narrative_id,
        control_id=request.control_id,
        account_id=request.account_id,
        generated_markdown=result.get("markdown", ""),
        implementation_status=result.get("implementation_status", "Unknown"),
        status="draft",
        model=result.get("model", settings.openai_model),
        evidence_snapshot=result.get("evidence_snapshot", {}),
        is_valid=result.get("is_valid", False),
        missing_headings=result.get("missing_headings", []),
        error=result.get("error"),
    )


@router.post("/narratives/save", response_model=NarrativeResponse)
async def save_ssp_narrative(
    request: NarrativeSaveRequest,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """
    Persist a narrative after human review/acceptance.
    """
    if not request.generated_markdown or not request.generated_markdown.strip():
        raise HTTPException(status_code=400, detail="generated_markdown is required")

    snapshot = request.evidence_snapshot or {}
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        meta = dict(snapshot.get("narrative_generation") or {})
        meta.update(
            {
                "implementation_status": request.implementation_status,
                "saved_via": "human_acceptance",
            }
        )
        snapshot["narrative_generation"] = meta

    narrative = SSPNarrative(
        control_id=request.control_id,
        account_id=request.account_id,
        ingestion_run_id=UUID(request.ingestion_run_id) if request.ingestion_run_id else None,
        generated_markdown=request.generated_markdown,
        status=NarrativeStatus.REVIEWED,
        model=request.model or settings.openai_model,
        evidence_snapshot=snapshot if isinstance(snapshot, dict) else (request.evidence_snapshot or {}),
    )
    db.add(narrative)
    await db.flush()

    return NarrativeResponse(
        id=str(narrative.id),
        control_id=narrative.control_id,
        account_id=narrative.account_id,
        generated_markdown=narrative.generated_markdown,
        implementation_status=request.implementation_status or "",
        status=narrative.status.value if isinstance(narrative.status, NarrativeStatus) else str(narrative.status),
        model=narrative.model,
        evidence_snapshot=narrative.evidence_snapshot or {},
        is_valid=True,
        missing_headings=[],
        created_at=narrative.created_at.isoformat() if narrative.created_at else None,
    )


@router.post("/narratives/regenerate", response_model=NarrativeResponse)
async def regenerate_ssp_narrative(
    request: NarrativeRegenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """
    Human feedback loop: regenerate a narrative based on reviewer feedback and previous draft.
    """
    evidence_result = await collect_evidence_and_evaluate(
        control_id=request.control_id,
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        db=db,
    )
    if evidence_result.get("error"):
        raise HTTPException(status_code=400, detail=str(evidence_result["error"]))

    from app.services.ai_agent.narrative.ssp_writer import generate_ssp_narrative_from_state

    gen = await generate_ssp_narrative_from_state(
        db=db,
        control=evidence_result.get("control") or {"control_id": request.control_id},
        evidence_snapshot=evidence_result.get("evidence_snapshot") or {},
        validation_findings=evidence_result.get("validation_findings") or {},
        account_id=request.account_id,
        tone_tier=request.tone_tier.value,
        max_attempts=3,
        previous_markdown=request.previous_markdown,
        rejection_reason=request.rejection_reason,
    )

    return NarrativeResponse(
        id=None,
        control_id=request.control_id,
        account_id=request.account_id,
        generated_markdown=gen.get("markdown") or "",
        implementation_status=gen.get("implementation_status") or "Unknown",
        status="draft",
        model=gen.get("model", settings.openai_model),
        evidence_snapshot=gen.get("evidence_snapshot", {}),
        is_valid=gen.get("is_valid", False),
        missing_headings=gen.get("missing_headings", []),
        error=gen.get("error"),
    )


@router.get("/narratives/{narrative_id}", response_model=NarrativeResponse)
async def get_narrative(
    narrative_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    stmt = select(SSPNarrative).where(SSPNarrative.id == narrative_id)
    result = await db.execute(stmt)
    narrative = result.scalar_one_or_none()
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")

    return NarrativeResponse(
        id=str(narrative.id),
        control_id=narrative.control_id,
        account_id=narrative.account_id,
        generated_markdown=narrative.generated_markdown,
        implementation_status=(
            str(
                (narrative.evidence_snapshot or {}).get("narrative_generation", {}).get("implementation_status") or ""
            )
            if isinstance(narrative.evidence_snapshot, dict)
            else ""
        ),
        status=narrative.status.value if isinstance(narrative.status, NarrativeStatus) else narrative.status,
        model=narrative.model,
        evidence_snapshot=narrative.evidence_snapshot,
        is_valid=True,
        created_at=narrative.created_at.isoformat() if narrative.created_at else None,
    )


@router.get("/narratives", response_model=NarrativeListResponse)
async def list_narratives(
    account_id: str | None = Query(None),
    control_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> NarrativeListResponse:
    base = select(SSPNarrative)
    count_base = select(func.count(SSPNarrative.id))

    if account_id:
        base = base.where(SSPNarrative.account_id == account_id)
        count_base = count_base.where(SSPNarrative.account_id == account_id)
    if control_id:
        base = base.where(SSPNarrative.control_id == control_id)
        count_base = count_base.where(SSPNarrative.control_id == control_id)

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    stmt = base.order_by(SSPNarrative.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    narratives = result.scalars().all()

    return NarrativeListResponse(
        total=total,
        items=[
            NarrativeListItem(
                id=str(n.id),
                control_id=n.control_id,
                account_id=n.account_id,
                status=n.status.value if isinstance(n.status, NarrativeStatus) else n.status,
                model=n.model,
                implementation_status=(
                    str((n.evidence_snapshot or {}).get("narrative_generation", {}).get("implementation_status") or "")
                    if isinstance(n.evidence_snapshot, dict)
                    else ""
                ),
                created_at=n.created_at.isoformat() if n.created_at else "",
            )
            for n in narratives
        ],
    )


@router.get("/controls", response_model=list[ControlListItem])
async def list_controls(
    family: str | None = Query(None, description="Filter by control family"),
    db: AsyncSession = Depends(get_db),
) -> list[ControlListItem]:
    controls = await controls_repo.list_controls(db, family=family)
    return [ControlListItem(**c) for c in controls]


@router.post("/blueprints/hydrate", response_model=HydrateResponse)
async def hydrate_control_blueprint(
    request: HydrateRequest,
    db: AsyncSession = Depends(get_db),
) -> HydrateResponse:
    ctrl = await db.get(FedRAMPControl, request.control_id)
    if ctrl is None:
        raise HTTPException(status_code=404, detail="Control not found")
    if not ctrl.template_markdown:
        raise HTTPException(
            status_code=404,
            detail="No template markdown found for this control. Ingest a template first.",
        )

    evidence_result = await collect_evidence_and_evaluate(
        control_id=request.control_id,
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        db=db,
    )
    if evidence_result.get("error"):
        raise HTTPException(status_code=400, detail=str(evidence_result["error"]))

    return HydrateResponse(
        control_id=request.control_id,
        account_id=request.account_id,
        template_markdown=ctrl.template_markdown,
        compliance_evaluation=evidence_result.get("compliance_evaluation"),
        validation_findings=evidence_result.get("validation_findings"),
        evidence_snapshot=evidence_result.get("evidence_snapshot"),
    )


@router.post("/validator/evaluate", response_model=ValidateControlResponse)
async def validate_control(
    request: ValidateControlRequest,
    db: AsyncSession = Depends(get_db),
) -> ValidateControlResponse:
    """
    AUDIT_MODE:
    Collect evidence and return structured validator findings (no narrative writing).
    """
    evidence_result = await collect_evidence_and_evaluate(
        control_id=request.control_id,
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        db=db,
    )
    if evidence_result.get("error"):
        raise HTTPException(status_code=400, detail=str(evidence_result["error"]))

    evidence_snapshot = evidence_result.get("evidence_snapshot") or {}
    return ValidateControlResponse(
        control_id=request.control_id,
        account_id=request.account_id,
        ingestion_run_id=evidence_snapshot.get("ingestion_run_id"),
        validation_findings=evidence_result.get("validation_findings") or {},
        evidence_snapshot=evidence_snapshot,
    )


@router.post("/architecture-diagrams/prompts", response_model=ArchitecturePromptsResponse)
async def get_architecture_diagram_prompts(
    request: ArchitecturePromptsRequest,
    db: AsyncSession = Depends(get_db),
) -> ArchitecturePromptsResponse:
    """
    Use Case 11:
    Return prompt context for a two-step agent chain:
      Step 1 (Summarizer): evidence JSON -> infrastructure bullets to draw (JSON-only output)
      Step 2 (Artist): infrastructure bullets + example diagram image -> rendered architecture diagram

    This endpoint does not call an image model; it only returns prompts + evidence.
    """
    logger.info(
        "architecture_diagram_prompts_requested",
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        include_example_image_base64=request.include_example_image_base64,
    )
    evidence_json = await build_architecture_evidence(
        db=db,
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        sample_limit=50,
    )

    summarizer = {
        "system_message": SUMMARIZER_SYSTEM_MESSAGE,
        "user_message": build_summarizer_user_message(evidence_json=evidence_json),
        "expected_output_schema": {
            "boundary_label": "string",
            "infrastructure_to_draw": ["bullet 1", "bullet 2"],
            "data_flows": ["A -> B", "C -> D"],
        },
    }

    artist_prompt: str | None = None
    if request.infrastructure_to_draw and request.data_flows:
        artist_prompt = build_artist_prompt(
            infrastructure_to_draw=request.infrastructure_to_draw,
            data_flows=request.data_flows,
        )

    example_image: dict | None = None
    if request.include_example_image_base64:
        repo_root = Path(__file__).resolve().parents[3]
        example_image = load_example_diagram_base64(workspace_root=repo_root)

    return ArchitecturePromptsResponse(
        account_id=request.account_id,
        ingestion_run_id=evidence_json.get("ingestion_run_id"),
        evidence_json=evidence_json,
        summarizer_prompt=summarizer,
        artist_prompt=artist_prompt,
        example_image=example_image,
    )


@router.post("/architecture-diagrams/generate-image", response_model=ArchitectureGenerateImageResponse)
async def generate_architecture_diagram_image(
    request: ArchitectureGenerateImageRequest,
    db: AsyncSession = Depends(get_db),
) -> ArchitectureGenerateImageResponse:
    """
    DEPRECATED:
    Image generation has been removed in favor of SVG diagrams.
    """
    raise HTTPException(status_code=410, detail="Image generation is deprecated. Use /ai/architecture-diagrams/generate.")


class ArchitectureGenerateSVGRequest(BaseModel):
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    ingestion_run_id: str | None = Field(
        None, description="Specific ingestion run UUID; uses latest successful if omitted"
    )
    persist: bool = Field(default=True, description="Save diagram to DB and return id")
    max_attempts: int = Field(default=2, ge=1, le=3, description="AI generation/evaluation passes before returning best SVG")


class ArchitectureGenerateSVGResponse(BaseModel):
    id: str | None = None
    account_id: str
    ingestion_run_id: str | None
    svg_markup: str
    mermaid_code: str | None = None
    diagram_spec: dict
    evaluation: dict | None = None
    attempts: int = 1
    renderer_version: str = "abd_svg_v1"


class ArchitectureDiagramListItem(BaseModel):
    id: str
    account_id: str
    ingestion_run_id: str | None = None
    score: int | None = None
    attempts: int | None = None
    renderer_version: str | None = None
    created_at: str


class ArchitectureDiagramListResponse(BaseModel):
    items: list[ArchitectureDiagramListItem]
    total: int


class ArchitectureDiagramDetailResponse(BaseModel):
    id: str
    account_id: str
    ingestion_run_id: str | None = None
    svg_markup: str
    mermaid_code: str | None = None
    diagram_spec: dict | None = None
    evaluation: dict | None = None
    attempts: int | None = None
    score: int | None = None
    model_text: str | None = None
    renderer_version: str | None = None
    created_at: str | None = None


@router.post("/architecture-diagrams/generate", response_model=ArchitectureGenerateSVGResponse)
async def generate_architecture_svg(
    request: ArchitectureGenerateSVGRequest,
    db: AsyncSession = Depends(get_db),
) -> ArchitectureGenerateSVGResponse:
    logger.info(
        "architecture_svg_requested",
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        max_attempts=request.max_attempts,
    )

    evidence_json = await build_architecture_evidence(
        db=db,
        account_id=request.account_id,
        ingestion_run_id=request.ingestion_run_id,
        sample_limit=50,
    )

    context_summary = await summarize_architecture_context(evidence_json=evidence_json)
    spec = build_infra_spec_from_evidence(
        evidence_json=evidence_json,
        context_summary=context_summary,
    )
    mermaid_out = await generate_mermaid_with_feedback(
        evidence_json=evidence_json,
        max_attempts=request.max_attempts,
    )
    render_out = await render_svg_from_mermaid_with_feedback(
        mermaid_code=str(mermaid_out.get("mermaid_code") or ""),
        evidence_json=evidence_json,
        spec=spec,
        max_attempts=request.max_attempts,
    )
    evaluation = render_out.get("evaluation")
    svg_markup = render_out.get("svg_markup") or ""
    diagram_spec = spec.to_dict()
    renderer_version = str(render_out.get("renderer_version") or "mermaid_svg_ai_v1")
    attempts = int(render_out.get("attempts") or 1)
    mermaid_code = str(mermaid_out.get("mermaid_code") or "")
    mermaid_prompt = mermaid_out.get("mermaid_prompt")
    mermaid_evaluation = mermaid_out.get("evaluation")
    mermaid_attempts = int(mermaid_out.get("attempts") or 1)

    diagram_id: str | None = None
    if request.persist:
        diag = ArchitectureDiagram(
            account_id=request.account_id,
            ingestion_run_id=UUID(request.ingestion_run_id) if request.ingestion_run_id else None,
            evidence_json=evidence_json,
            summarizer_output=context_summary,
            diagram_spec_json=diagram_spec,
            svg_markup=svg_markup,
            mermaid_code=mermaid_code,
            mermaid_prompt=mermaid_prompt,
            artist_prompt=None,
            image_mime_type=None,
            image_base64=None,
            model_text=settings.openai_model,
            model_mermaid=settings.openai_model,
            model_image=None,
            renderer_version=renderer_version,
            mermaid_evaluation=mermaid_evaluation,
            mermaid_score=int((mermaid_evaluation or {}).get("score") or 0)
            if isinstance(mermaid_evaluation, dict)
            else None,
            mermaid_iterations=mermaid_attempts,
            diagram_evaluation=evaluation,
            diagram_score=int((evaluation or {}).get("score") or 0) if isinstance(evaluation, dict) else None,
            diagram_iterations=attempts,
        )
        db.add(diag)
        await db.flush()
        diagram_id = str(diag.id)

    return ArchitectureGenerateSVGResponse(
        id=diagram_id,
        account_id=request.account_id,
        ingestion_run_id=evidence_json.get("ingestion_run_id"),
        svg_markup=svg_markup,
        mermaid_code=mermaid_code or None,
        diagram_spec=diagram_spec,
        evaluation=evaluation,
        attempts=attempts,
        renderer_version=renderer_version,
    )


@router.get("/architecture-diagrams", response_model=ArchitectureDiagramListResponse)
async def list_architecture_diagrams(
    account_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureDiagramListResponse:
    base = select(ArchitectureDiagram)
    count_base = select(func.count(ArchitectureDiagram.id))

    if account_id:
        base = base.where(ArchitectureDiagram.account_id == account_id)
        count_base = count_base.where(ArchitectureDiagram.account_id == account_id)

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    stmt = base.order_by(ArchitectureDiagram.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    diagrams = result.scalars().all()

    return ArchitectureDiagramListResponse(
        total=total,
        items=[
            ArchitectureDiagramListItem(
                id=str(d.id),
                account_id=d.account_id,
                ingestion_run_id=str(d.ingestion_run_id) if d.ingestion_run_id else None,
                score=d.diagram_score,
                attempts=d.diagram_iterations,
                renderer_version=d.renderer_version,
                created_at=d.created_at.isoformat() if d.created_at else "",
            )
            for d in diagrams
        ],
    )


@router.get("/architecture-diagrams/{diagram_id}", response_model=ArchitectureDiagramDetailResponse)
async def get_architecture_diagram(
    diagram_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ArchitectureDiagramDetailResponse:
    stmt = select(ArchitectureDiagram).where(ArchitectureDiagram.id == diagram_id)
    result = await db.execute(stmt)
    diagram = result.scalar_one_or_none()
    if diagram is None:
        raise HTTPException(status_code=404, detail="Diagram not found")

    return ArchitectureDiagramDetailResponse(
        id=str(diagram.id),
        account_id=diagram.account_id,
        ingestion_run_id=str(diagram.ingestion_run_id) if diagram.ingestion_run_id else None,
        svg_markup=diagram.svg_markup or "",
        mermaid_code=diagram.mermaid_code or None,
        diagram_spec=diagram.diagram_spec_json,
        evaluation=diagram.diagram_evaluation,
        attempts=diagram.diagram_iterations,
        score=diagram.diagram_score,
        model_text=diagram.model_text,
        renderer_version=diagram.renderer_version,
        created_at=diagram.created_at.isoformat() if diagram.created_at else None,
    )


# ---------------------------------------------------------------------------
# Use Case 1: Evidence lookup in security documentation (RAG)
# ---------------------------------------------------------------------------
class DocsIngestResponse(BaseModel):
    document_id: str
    deduped: bool
    chunks_created: int
    doc_type: str


@router.post("/docs/ingest", response_model=DocsIngestResponse)
async def ingest_security_document(
    file: UploadFile = File(..., description="PDF/DOCX/MD/TXT"),
    title: str | None = Form(None),
    doc_type: str | None = Form(None),
    account_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> DocsIngestResponse:
    data = await file.read()
    try:
        out = await rag_service.ingest_document(
            db,
            title=title or (file.filename or "document"),
            filename=file.filename or "document",
            data=data,
            doc_type=doc_type,
            account_id=account_id,
            metadata={"content_type": file.content_type or ""},
        )
        return DocsIngestResponse(**out)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DocsSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=8, ge=1, le=25)
    account_id: str | None = None
    doc_type: str | None = None


@router.post("/docs/search")
async def search_security_documents(
    request: DocsSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        matches = await rag_service.search(
            db,
            query=request.query,
            top_k=request.top_k,
            account_id=request.account_id,
            doc_type=request.doc_type,
        )
        return {"query": request.query, "matches": matches}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DocsAnswerRequest(BaseModel):
    question: str = Field(..., description="User question to answer from uploaded documentation")
    top_k: int = Field(default=8, ge=1, le=25)
    account_id: str | None = None
    doc_type: str | None = None


@router.post("/docs/answer")
async def answer_from_security_documents(
    request: DocsAnswerRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    GPT-5 answer grounded in retrieved RAG passages.

    This produces a human-readable answer plus citations (doc + page ranges),
    using ONLY retrieved passages as evidence.
    """
    matches = await rag_service.search(
        db,
        query=request.question,
        top_k=request.top_k,
        account_id=request.account_id,
        doc_type=request.doc_type,
    )

    # Build a compact context for the model.
    passages = []
    for i, m in enumerate(matches):
        passages.append(
            {
                "id": i + 1,
                "title": m.get("title"),
                "source_path": m.get("source_path"),
                "page_start": m.get("page_start"),
                "page_end": m.get("page_end"),
                "text": (m.get("text") or "")[:2500],
            }
        )

    system_message = (
        "You are a FedRAMP auditor. Answer the user's question using ONLY the provided passages. "
        "When answering questions about security controls, always specify the scope "
        "(e.g., which users, roles, locations, access paths, or system components the control applies to) "
        "IF the passages mention it. If scope is not stated in the passages, explicitly say "
        "'Scope not specified in the provided text.' "
        "If the passages do not contain sufficient evidence, say so. "
        "Return ONLY valid JSON. No markdown fences. No commentary."
    )
    user_message = (
        "Question:\n"
        f"{request.question}\n\n"
        "Passages (evidence):\n"
        f"{json.dumps(passages, indent=2)}\n\n"
        "Return JSON with schema:\n"
        "{\n"
        '  "answer": "string",\n'
        '  "citations": [{"id": 1, "title": "string", "source_path": "string", "page_start": 1, "page_end": 1}]\n'
        "}\n"
        "Citations must reference passage ids and include page ranges."
    )

    raw = await llm_client.invoke_text(system_message=system_message, user_message=user_message, temperature=0.1)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("answer_not_object")
    except Exception:
        parsed = {"answer": raw, "citations": []}

    # Enrich citations with titles/paths if the model only returns ids.
    cites = parsed.get("citations")
    if isinstance(cites, list):
        out_cites = []
        by_id = {p["id"]: p for p in passages}
        for c in cites[:25]:
            if not isinstance(c, dict):
                continue
            pid = c.get("id")
            p = by_id.get(pid) if isinstance(pid, int) else None
            out_cites.append(
                {
                    "id": pid,
                    "title": c.get("title") or (p.get("title") if p else None),
                    "source_path": c.get("source_path") or (p.get("source_path") if p else None),
                    "page_start": c.get("page_start") or (p.get("page_start") if p else None),
                    "page_end": c.get("page_end") or (p.get("page_end") if p else None),
                }
            )
        parsed["citations"] = out_cites

    return {"model": settings.openai_model, "answer": parsed.get("answer", ""), "citations": parsed.get("citations", []), "matches": matches}


class DocumentReference(BaseModel):
    document_id: str
    title: str
    doc_type: str
    source_path: str | None = None


class DocsControlMappingEvidence(BaseModel):
    id: int
    title: str | None = None
    source_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    text: str


class DocsControlMappingItem(BaseModel):
    control_id: str
    title: str
    family: str
    confidence: float | None = None
    reason: str
    evidence: list[DocsControlMappingEvidence] = Field(default_factory=list)


class DocsMapControlsRequest(BaseModel):
    text: str | None = Field(
        default=None,
        description="Raw security document text or excerpt to classify against FedRAMP controls",
    )
    document_id: str | None = Field(
        default=None,
        description="Indexed document UUID to classify from the RAG store",
    )
    top_k_chunks: int = Field(
        default=8,
        ge=1,
        le=20,
        description="How many stored chunks to inspect when document_id is provided",
    )
    max_controls: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of likely controls to return",
    )


class DocsMapControlsResponse(BaseModel):
    document: DocumentReference | None = None
    summary: str
    controls: list[DocsControlMappingItem] = Field(default_factory=list)


async def _build_doc_mapping_passages(
    *,
    request: DocsMapControlsRequest,
    db: AsyncSession,
) -> tuple[DocumentReference | None, list[dict]]:
    if request.document_id:
        try:
            doc_uuid = UUID(request.document_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid document_id.") from exc

        doc = (
            await db.execute(
                select(Document).where(Document.id == doc_uuid)
            )
        ).scalar_one_or_none()
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

        chunk_rows = (
            await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == doc_uuid)
                .order_by(DocumentChunk.chunk_index.asc())
                .limit(request.top_k_chunks)
            )
        ).scalars().all()
        if not chunk_rows:
            raise HTTPException(status_code=400, detail="Document has no indexed chunks to map.")

        document_ref = DocumentReference(
            document_id=str(doc.id),
            title=doc.title,
            doc_type=doc.doc_type,
            source_path=doc.source_path,
        )
        passages = [
            {
                "id": idx,
                "title": doc.title,
                "source_path": doc.source_path,
                "page_start": ch.page_start,
                "page_end": ch.page_end,
                "text": (ch.text or "")[:2500],
            }
            for idx, ch in enumerate(chunk_rows, start=1)
            if (ch.text or "").strip()
        ]
        return document_ref, passages

    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Provide either text or document_id.")

    passages = [
        {
            "id": 1,
            "title": "Provided Text",
            "source_path": None,
            "page_start": None,
            "page_end": None,
            "text": text[:4000],
        }
    ]
    return None, passages


async def _load_control_candidates_for_doc_mapping(
    *,
    db: AsyncSession,
    passages: list[dict],
    limit: int = 24,
) -> list[dict]:
    rows = (
        await db.execute(
            select(
                FedRAMPControl.control_id,
                FedRAMPControl.family,
                FedRAMPControl.title,
                FedRAMPControl.nist_description,
            ).order_by(FedRAMPControl.control_id.asc())
        )
    ).all()
    controls = [
        {
            "control_id": control_id,
            "family": family,
            "title": title,
            "nist_description": nist_description or "",
        }
        for control_id, family, title, nist_description in rows
    ]
    if not controls:
        return []

    combined_text = "\n\n".join(str(p.get("text") or "") for p in passages)
    explicit_ids = _extract_control_mentions(combined_text)
    document_tokens = _tokenize_control_text(combined_text)

    ranked: list[tuple[int, dict, list[str], bool]] = []
    for control in controls:
        control_id = _normalize_control_id(str(control.get("control_id") or ""))
        overlap_terms = _candidate_overlap_terms(document_tokens=document_tokens, control=control)
        explicit_match = control_id in explicit_ids
        score = 0
        if explicit_match:
            score += 1000
        score += len(overlap_terms) * 5
        if str(control.get("title") or "").lower() in combined_text.lower():
            score += 25
        if score > 0:
            ranked.append((score, control, overlap_terms, explicit_match))

    if not ranked:
        ranked = [
            (0, control, [], False)
            for control in controls[:limit]
        ]

    ranked.sort(
        key=lambda item: (
            item[0],
            1 if item[3] else 0,
            len(item[2]),
            str(item[1].get("control_id") or ""),
        ),
        reverse=True,
    )

    out: list[dict] = []
    for _, control, overlap_terms, explicit_match in ranked[:limit]:
        item = dict(control)
        item["_overlap_terms"] = overlap_terms
        item["_explicit_match"] = explicit_match
        out.append(item)
    return out


def _fallback_doc_control_mapping(
    *,
    candidates: list[dict],
    passages: list[dict],
    max_controls: int,
) -> dict:
    selected = []
    for control in candidates[:max_controls]:
        selected.append(
            {
                "control_id": control["control_id"],
                "reason": _fallback_control_reason(
                    control=control,
                    overlap_terms=list(control.get("_overlap_terms") or []),
                    explicit_match=bool(control.get("_explicit_match")),
                ),
                "confidence": 0.95 if control.get("_explicit_match") else 0.55,
                "evidence_ids": [p["id"] for p in passages[: min(2, len(passages))]],
            }
        )
    return {
        "summary": "Best-effort control mapping generated from document language overlap.",
        "controls": selected,
    }


@router.post("/docs/map-controls", response_model=DocsMapControlsResponse)
async def map_security_document_to_controls(
    request: DocsMapControlsRequest,
    db: AsyncSession = Depends(get_db),
) -> DocsMapControlsResponse:
    """
    Map an indexed security document or provided text to the most likely
    FedRAMP controls, returning supporting excerpts from the source material.
    """
    document_ref, passages = await _build_doc_mapping_passages(request=request, db=db)
    candidates = await _load_control_candidates_for_doc_mapping(db=db, passages=passages)

    if not candidates:
        return DocsMapControlsResponse(
            document=document_ref,
            summary="No FedRAMP controls are available to map against.",
            controls=[],
        )

    candidate_lookup = {
        _normalize_control_id(str(c.get("control_id") or "")): c for c in candidates
    }
    passage_lookup = {int(p["id"]): p for p in passages}

    system_message = (
        "You are a FedRAMP control-mapping analyst. "
        "Given security document passages and a shortlist of candidate controls, identify the controls "
        "best supported by the evidence. Use ONLY the provided passages and candidates. "
        "Return ONLY valid JSON. No markdown fences. No commentary."
    )
    user_message = (
        "Passages from the source document:\n"
        f"{json.dumps(passages, indent=2)}\n\n"
        "Candidate FedRAMP controls:\n"
        f"{json.dumps([{k: v for k, v in c.items() if not str(k).startswith('_')} for c in candidates], indent=2)}\n\n"
        "Return JSON with schema:\n"
        "{\n"
        '  "summary": "string",\n'
        '  "controls": [\n'
        "    {\n"
        '      "control_id": "string",\n'
        '      "reason": "string",\n'
        '      "confidence": 0.0,\n'
        '      "evidence_ids": [1, 2]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        f"Return at most {request.max_controls} controls. Only include controls clearly supported by the passages."
    )

    try:
        raw = await llm_client.invoke_text(
            system_message=system_message,
            user_message=user_message,
            temperature=0.1,
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("map_controls_response_not_object")
    except Exception as exc:
        logger.warning("docs_map_controls_llm_failed", error=str(exc))
        parsed = _fallback_doc_control_mapping(
            candidates=candidates,
            passages=passages,
            max_controls=request.max_controls,
        )

    controls_out: list[DocsControlMappingItem] = []
    for item in list(parsed.get("controls") or [])[: request.max_controls]:
        if not isinstance(item, dict):
            continue
        control_id = _normalize_control_id(str(item.get("control_id") or ""))
        candidate = candidate_lookup.get(control_id)
        if not candidate:
            continue

        evidence_ids_raw = item.get("evidence_ids") or []
        evidence_ids = [
            int(pid)
            for pid in evidence_ids_raw[:10]
            if isinstance(pid, int) and pid in passage_lookup
        ]
        if not evidence_ids:
            evidence_ids = [passages[0]["id"]]

        confidence_raw = item.get("confidence")
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        reason = str(item.get("reason") or "").strip() or _fallback_control_reason(
            control=candidate,
            overlap_terms=list(candidate.get("_overlap_terms") or []),
            explicit_match=bool(candidate.get("_explicit_match")),
        )

        controls_out.append(
            DocsControlMappingItem(
                control_id=candidate["control_id"],
                title=str(candidate.get("title") or ""),
                family=str(candidate.get("family") or ""),
                confidence=confidence,
                reason=reason,
                evidence=[
                    DocsControlMappingEvidence(
                        id=eid,
                        title=passage_lookup[eid].get("title"),
                        source_path=passage_lookup[eid].get("source_path"),
                        page_start=passage_lookup[eid].get("page_start"),
                        page_end=passage_lookup[eid].get("page_end"),
                        text=str(passage_lookup[eid].get("text") or ""),
                    )
                    for eid in evidence_ids
                ],
            )
        )

    if not controls_out:
        fallback = _fallback_doc_control_mapping(
            candidates=candidates,
            passages=passages,
            max_controls=request.max_controls,
        )
        for item in fallback["controls"]:
            candidate = candidate_lookup.get(_normalize_control_id(str(item.get("control_id") or "")))
            if not candidate:
                continue
            evidence_ids = [eid for eid in item.get("evidence_ids", []) if eid in passage_lookup]
            controls_out.append(
                DocsControlMappingItem(
                    control_id=candidate["control_id"],
                    title=str(candidate.get("title") or ""),
                    family=str(candidate.get("family") or ""),
                    confidence=float(item.get("confidence") or 0.0),
                    reason=str(item.get("reason") or ""),
                    evidence=[
                        DocsControlMappingEvidence(
                            id=eid,
                            title=passage_lookup[eid].get("title"),
                            source_path=passage_lookup[eid].get("source_path"),
                            page_start=passage_lookup[eid].get("page_start"),
                            page_end=passage_lookup[eid].get("page_end"),
                            text=str(passage_lookup[eid].get("text") or ""),
                        )
                        for eid in evidence_ids
                    ],
                )
            )

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = "Likely FedRAMP controls inferred from the provided document evidence."

    return DocsMapControlsResponse(
        document=document_ref,
        summary=summary,
        controls=controls_out[: request.max_controls],
    )


class ControlEvidenceRequest(BaseModel):
    control_id: str = Field(..., description="NIST control ID, e.g. IA-2")
    top_k: int = Field(default=8, ge=1, le=25)
    account_id: str | None = None
    doc_type: str | None = None


@router.post("/docs/control-evidence")
async def find_control_evidence_in_docs(
    request: ControlEvidenceRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    ctrl = await controls_repo.get_control(request.control_id, db)
    if not ctrl:
        raise HTTPException(status_code=404, detail="Control not found")

    query = (
        f"{ctrl['control_id']} {ctrl['title']}\n\n"
        f"{(ctrl.get('nist_description') or '')[:2500]}\n\n"
        "Find policy/procedure evidence paragraphs that demonstrate implementation."
    )
    matches = await rag_service.search(
        db,
        query=query,
        top_k=request.top_k,
        account_id=request.account_id,
        doc_type=request.doc_type,
    )
    return {"control": ctrl, "matches": matches}


# ---------------------------------------------------------------------------
# Use Case 2: Identify where sensitive data may be stored (rule-based on evidence)
# ---------------------------------------------------------------------------
class SensitiveDataRequest(BaseModel):
    account_id: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    ingestion_run_id: str | None = None
    limit: int = Field(default=200, ge=1, le=1000)


@router.post("/data/sensitive-locations")
async def identify_sensitive_data_locations(
    request: SensitiveDataRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = AWSEvidenceService(db)
    run_id = await svc.resolve_run_id(request.account_id, request.ingestion_run_id)
    records = await svc.list_records(
        table="data_stores",
        account_id=request.account_id,
        ingestion_run_id=run_id,
        resource_type=None,
        limit=request.limit,
    )

    indicators = ["cui", "fci", "fedramp", "federal", "pii", "phi", "hipaa", "audit", "export", "controlledunclassified"]
    # Common tag keys used by orgs for data classification across FedRAMP/CMMC programs.
    tag_keys = {
        "dataclassification",
        "data_classification",
        "data-classification",
        "classification",
        "impactlevel",
        "il",
        "cui",
        "fci",
    }
    classification_tokens = {
        "cui": "CUI",
        "controlled unclassified": "CUI",
        "controlledunclassified": "CUI",
        "fci": "FCI",
        "federal contract information": "FCI",
        "pii": "PII",
        "phi": "PHI",
    }

    flagged: list[dict] = []
    for r in records:
        d = r.get("data") or {}
        name = (d.get("bucket_name") or d.get("db_instance_identifier") or r.get("resource_id") or "").lower()
        tags = d.get("tags") or {}
        tag_key_hit = [str(k) for k in tags.keys() if str(k).lower() in tag_keys]
        tag_value_hits: list[str] = []
        for v in tags.values():
            vv = str(v).lower()
            for tok, label in classification_tokens.items():
                if tok in vv and label not in tag_value_hits:
                    tag_value_hits.append(label)
        tag_hit = bool(tag_key_hit or tag_value_hits)
        name_hit = any(tok in name for tok in indicators)
        name_value_hits: list[str] = []
        for tok, label in classification_tokens.items():
            if tok.replace(" ", "") in name.replace(" ", "") and label not in name_value_hits:
                name_value_hits.append(label)

        # posture flags
        posture_flags: list[str] = []
        if r.get("resource_type") == "s3_bucket":
            if not d.get("encryption_algorithm"):
                posture_flags.append("unencrypted_bucket")
            if d.get("public_access_block") is None:
                posture_flags.append("public_access_block_missing")
        if r.get("resource_type") == "rds_instance":
            if not d.get("storage_encrypted"):
                posture_flags.append("unencrypted_rds")
            if d.get("publicly_accessible"):
                posture_flags.append("publicly_accessible_rds")

        if tag_hit or name_hit or posture_flags:
            flagged.append(
                {
                    "resource_type": r.get("resource_type"),
                    "resource_id": r.get("resource_id"),
                    "region": r.get("region"),
                    "name_indicators": [tok for tok in indicators if tok in name],
                    "classification_signals": sorted(set(tag_value_hits + name_value_hits)),
                    "tag_indicator_keys": tag_key_hit,
                    "posture_flags": posture_flags,
                    "tags": tags,
                }
            )

    return {"account_id": request.account_id, "ingestion_run_id": str(run_id) if run_id else None, "flagged": flagged}


class MacieFindingsRequest(BaseModel):
    account_id: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    role_arn: str = Field(..., description="IAM role ARN to assume for Macie read-only calls")
    region: str = Field(default="us-east-1", description="Macie region (must match where Macie is enabled)")
    since_days: int = Field(default=30, ge=1, le=365)
    max_findings: int = Field(default=50, ge=1, le=200)


@router.post("/data/macie/findings")
async def get_macie_findings_summary(
    request: MacieFindingsRequest,
) -> dict:
    """
    Use Case 2 extension (hard evidence):
    Query AWS Macie for sensitive-data discovery findings in S3.
    """
    svc = MacieService(role_arn=request.role_arn, account_id=request.account_id, region=request.region)
    try:
        return svc.fetch_findings_summary(max_findings=request.max_findings, since_days=request.since_days)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Use Case 3: Map technologies/vendors to SSP categories
# ---------------------------------------------------------------------------
class VendorMapRequest(BaseModel):
    account_id: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    ingestion_run_id: str | None = None
    include_doc_snippets: bool = False


@router.post("/vendors/map")
async def map_vendors_and_technologies(
    request: VendorMapRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = AWSEvidenceService(db)
    run_id = await svc.resolve_run_id(request.account_id, request.ingestion_run_id)
    evidence = await svc.default_evidence_snapshot(request.account_id, run_id, sample_limit=10)

    snippets: list[str] = []
    if request.include_doc_snippets:
        matches = await rag_service.search(
            db,
            query="IAM Okta AWS IAM MFA CrowdStrike Nessus Splunk SIEM logging vulnerability scanning",
            top_k=6,
            account_id=None,
            doc_type=None,
        )
        snippets = [m.get("text", "") for m in matches if m.get("text")]

    vendor_map = await extract_vendor_map(evidence_json=evidence, narrative_texts=snippets)
    return {"vendor_map": vendor_map, "html_table": render_vendor_table_html(vendor_map)}


# ---------------------------------------------------------------------------
# Use Case 4: Ingest scan results and map failed checks to NIST controls
# ---------------------------------------------------------------------------
class ScanIngestResponse(BaseModel):
    ingested: int
    mapped_by_rule: int
    unmapped: int


@router.post("/scans/ingest", response_model=ScanIngestResponse)
async def ingest_scan_findings(
    source: str = Form(..., description="nessus|securityhub"),
    account_id: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ScanIngestResponse:
    data = await file.read()
    src = (source or "").strip().lower()
    if src not in {"nessus", "securityhub"}:
        raise HTTPException(status_code=400, detail="Unsupported source. Use 'nessus' or 'securityhub'.")

    if src == "nessus":
        findings = parse_nessus_csv(data)
    else:
        findings = parse_securityhub_json(data)

    mapped = 0
    for f in findings:
        control_id, conf = rule_map_finding(title=f["title"], description=f.get("description"))
        if control_id:
            mapped += 1
        db.add(
            ComplianceFinding(
                account_id=account_id,
                source=f["source"],
                finding_key=f.get("finding_key"),
                title=f["title"],
                description=f.get("description"),
                severity=f.get("severity"),
                resource_id=f.get("resource_id"),
                raw=f.get("raw") or {},
                mapped_control_id=control_id,
                mapping_method="rule" if control_id else None,
                mapping_confidence=conf if control_id else None,
            )
        )

    await db.flush()
    return ScanIngestResponse(ingested=len(findings), mapped_by_rule=mapped, unmapped=len(findings) - mapped)


# Optional: map existing unmapped findings (rule-first, LLM fallback)
class ScanMapControlsRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)
    use_llm_fallback: bool = True


@router.post("/scans/map-controls")
async def map_scan_findings_to_controls(
    request: ScanMapControlsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = (
        select(ComplianceFinding)
        .where(ComplianceFinding.mapped_control_id.is_(None))
        .order_by(ComplianceFinding.created_at.desc())
        .limit(request.limit)
    )
    findings = (await db.execute(stmt)).scalars().all()

    rule_mapped = 0
    llm_mapped = 0

    async def _llm_map(title: str, description: str | None) -> tuple[str | None, float | None]:
        system_message = "Return ONLY JSON. No markdown. No commentary."
        user_message = (
            "Map the following failed compliance/vulnerability finding to the most relevant NIST 800-53 Rev 5 control ID.\n\n"
            "Return JSON with keys: control_id (string like 'AC-8'), confidence (0-1).\n\n"
            f"Title: {title}\n"
            f"Description: {description or ''}\n"
        )
        raw = await llm_client.invoke_text(system_message=system_message, user_message=user_message, temperature=0.1)
        import json

        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return None, None
            cid = str(obj.get("control_id") or "").strip().upper()
            conf = obj.get("confidence")
            conf_f = float(conf) if conf is not None else None
            if not cid:
                return None, conf_f
            return cid, conf_f
        except Exception:
            return None, None

    for f in findings:
        cid, conf = rule_map_finding(title=f.title, description=f.description)
        if cid:
            f.mapped_control_id = cid
            f.mapping_method = "rule"
            f.mapping_confidence = conf
            rule_mapped += 1
            continue

        if request.use_llm_fallback:
            cid2, conf2 = await _llm_map(f.title, f.description)
            if cid2:
                f.mapped_control_id = cid2
                f.mapping_method = "llm"
                f.mapping_confidence = conf2
                llm_mapped += 1

    await db.flush()
    return {"considered": len(findings), "rule_mapped": rule_mapped, "llm_mapped": llm_mapped}


# ---------------------------------------------------------------------------
# Use Case 5: Generate missing policies/procedures and write into repo
# ---------------------------------------------------------------------------
class GeneratePolicyRequest(BaseModel):
    policy_id: str = Field(..., description="Template id (e.g. access_control, incident_response, vulnerability_management)")
    account_id: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    ingestion_run_id: str | None = None
    vendor_map: dict[str, list[str]] | None = None


@router.post("/policies/generate")
async def generate_policy_document(
    request: GeneratePolicyRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    vendor_map = request.vendor_map
    if vendor_map is None:
        svc = AWSEvidenceService(db)
        run_id = await svc.resolve_run_id(request.account_id, request.ingestion_run_id)
        evidence = await svc.default_evidence_snapshot(request.account_id, run_id, sample_limit=10)
        vendor_map = await extract_vendor_map(evidence_json=evidence, narrative_texts=[])

    md = render_policy_markdown(policy_id=request.policy_id, vendor_map=vendor_map)
    repo_root = Path(__file__).resolve().parents[3]
    path = write_generated_policy(workspace_root=repo_root, policy_id=request.policy_id, markdown=md)
    return {"policy_id": request.policy_id, "path": str(path), "vendor_map": vendor_map}


# ---------------------------------------------------------------------------
# Use Case 6: Ingest POA&M / deviations and validate via NVD (best-effort)
# ---------------------------------------------------------------------------
class POAMValidateRequest(BaseModel):
    cve_ids: list[str] = Field(default_factory=list)


@router.post("/poam/validate")
async def validate_poam_items(
    request: POAMValidateRequest,
) -> dict:
    results: list[dict] = []
    for cve in request.cve_ids[:25]:
        try:
            payload = await fetch_cve(cve_id=cve)
            results.append({"cve_id": cve, "found": payload is not None, "nvd": payload})
        except Exception as exc:
            results.append({"cve_id": cve, "found": False, "error": str(exc)})
    return {"results": results}


@router.post("/poam/ingest")
async def ingest_poam_csv(
    file: UploadFile = File(..., description="POA&M CSV export"),
    account_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    import csv
    import io

    data = await file.read()
    text = data.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    ingested = 0
    for row in reader:
        title = row.get("Title") or row.get("Item") or row.get("Finding") or "POA&M Item"
        desc = row.get("Description") or row.get("Details") or ""
        item_id = row.get("Item ID") or row.get("ID") or row.get("PoamId") or None
        status = row.get("Status") or row.get("State") or None
        vendor = row.get("Vendor") or row.get("Product") or None
        due = row.get("Due Date") or row.get("DueDate") or None
        cves_raw = row.get("CVE") or row.get("CVE IDs") or row.get("CVE_ID") or ""
        cves = [c.strip().upper() for c in str(cves_raw).replace(";", ",").split(",") if c.strip()]

        db.add(
            POAMItem(
                account_id=account_id,
                item_id=str(item_id) if item_id else None,
                title=str(title),
                description=str(desc) if desc is not None else None,
                cve_ids=cves,
                vendor=str(vendor) if vendor else None,
                status=str(status) if status else None,
                due_date=str(due) if due else None,
                raw=row,
            )
        )
        ingested += 1

    await db.flush()
    return {"ingested": ingested}

