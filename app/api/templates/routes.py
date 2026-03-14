"""
Templates API (template markdown ingestion).

This API ingests a FedRAMP SSP template document and stores the per-control
template Markdown directly on the `fedramp_controls` table (template_markdown).

Routes:
  POST /templates/ingest
  GET  /templates/controls
  GET  /templates/controls/{control_id}
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FedRAMPControl
from app.db.session import get_db
from app.config.logging_config import get_logger
from app.services.templates.template_parser import parse_docx_to_control_markdown, split_markdown_by_control

logger = get_logger(__name__)

router = APIRouter()


class IngestResponse(BaseModel):
    tag: str | None = None
    source_filename: str
    parser_version: str
    parse_meta: dict[str, Any] = Field(default_factory=dict)
    controls_updated: int
    control_ids: list[str]


class TemplateControlListItem(BaseModel):
    control_id: str
    title: str | None = None


@router.post("/ingest", response_model=IngestResponse)
async def ingest_template(
    file: UploadFile = File(..., description="FedRAMP narrative template (.docx) or parsed markdown (.md)"),
    tag: str | None = Form(default=None, description="Optional template tag label"),
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    filename = (file.filename or "").strip()
    lower = filename.lower()
    is_docx = lower.endswith(".docx")
    is_md = lower.endswith(".md") or lower.endswith(".markdown")
    if not (is_docx or is_md):
        raise HTTPException(status_code=400, detail="Only .docx and .md files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # If user uploads markdown, skip LlamaParse entirely.
    if is_md:
        try:
            md = content.decode("utf-8", errors="replace")
        except Exception:
            md = str(content, errors="replace")  # type: ignore[arg-type]

        control_md = split_markdown_by_control(md)
        parse_meta = {"backend": "user-markdown", "controls": len(control_md)}
        parser_version = "md-upload-v1"

        # Store to DB (shared path below).
        tmp_path = None
    else:
        parser_version = "md-v1"
        tmp_path = None

    try:
        try:
            if is_docx:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                control_md, parse_meta = parse_docx_to_control_markdown(tmp_path)
        except RuntimeError as exc:
            # Most common cause: missing optional dependency (e.g., docx2txt)
            await db.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            ingested_control_ids = sorted(control_md.keys())

            # Preload any existing controls in one query (avoid per-control queries + autoflush).
            if ingested_control_ids:
                existing_stmt = select(FedRAMPControl).where(FedRAMPControl.control_id.in_(ingested_control_ids))
                existing_result = await db.execute(existing_stmt)
                existing_controls = {c.control_id: c for c in existing_result.scalars().all()}
            else:
                existing_controls = {}

            # Ensure every blueprint control_id exists in fedramp_controls to satisfy FK constraint.
            # If the controls catalog hasn't been loaded yet, we create lightweight placeholders.
            created_placeholders: list[str] = []
            for control_id in ingested_control_ids:
                if control_id in existing_controls:
                    continue

                md = control_md.get(control_id, "")
                # Best-effort title guess from first line.
                title = (md.splitlines()[0] if md else control_id).strip()[:255] or control_id
                placeholder = FedRAMPControl(
                    control_id=control_id,
                    family="UNKNOWN",
                    title=title or control_id,
                    nist_description=(
                        "Placeholder created during template ingest. "
                        "Run scripts/load_fedramp_controls.py to load the full FedRAMP controls catalog."
                    ),
                    fedramp_parameters=None,
                    guidance=None,
                    baseline="HIGH",
                )
                db.add(placeholder)
                existing_controls[control_id] = placeholder
                created_placeholders.append(control_id)

            # Update per-control markdown.
            updated = 0
            for control_id in ingested_control_ids:
                ctrl = existing_controls.get(control_id)
                if ctrl is None:
                    continue
                ctrl.template_markdown = control_md.get(control_id, "").strip() or None
                updated += 1 if ctrl.template_markdown else 0

            logger.info(
                "template_markdown_ingested",
                tag=(tag or "").strip() or None,
                controls=len(ingested_control_ids),
                updated=updated,
                placeholders=len(created_placeholders),
            )

            return IngestResponse(
                tag=(tag or "").strip() or None,
                source_filename=filename or ("template.md" if is_md else "template.docx"),
                parser_version=parser_version,
                parse_meta=parse_meta,
                controls_updated=updated,
                control_ids=ingested_control_ids,
            )
        except Exception as exc:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Template ingest failed: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@router.get("/controls", response_model=list[TemplateControlListItem])
async def list_controls_with_templates(
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, description="Optional search on control_id or title"),
    limit: int = Query(default=5000, ge=1, le=20000, description="Max controls to return"),
) -> list[TemplateControlListItem]:
    """
    List controls that have an ingested template markdown segment.
    """
    stmt = select(FedRAMPControl.control_id, FedRAMPControl.title).where(
        FedRAMPControl.template_markdown.isnot(None)
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(FedRAMPControl.control_id.ilike(like), FedRAMPControl.title.ilike(like)))
    stmt = stmt.order_by(FedRAMPControl.control_id).limit(limit)
    result = await db.execute(stmt)
    return [TemplateControlListItem(control_id=cid, title=title) for cid, title in result.all()]


@router.get("/controls/{control_id}")
async def get_control_template_markdown(
    control_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ctrl = await db.get(FedRAMPControl, control_id)
    if ctrl is None:
        raise HTTPException(status_code=404, detail="Control not found")
    if not ctrl.template_markdown:
        raise HTTPException(status_code=404, detail="No template markdown stored for this control")
    return {"control_id": ctrl.control_id, "title": ctrl.title, "template_markdown": ctrl.template_markdown}

