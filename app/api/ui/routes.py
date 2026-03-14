"""
UI routes (Jinja).

GET /ui/                 — dashboard
GET /ui/narratives/{id}  — view a generated narrative
GET /ui/templates        — template manager page
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ArchitectureDiagram, FedRAMPControl, IngestionRun, RunStatus, SSPNarrative
from app.db.session import get_db

router = APIRouter()
# routes.py is at app/api/ui/routes.py
# parents[2] == app/ (the package root where templates/ lives)
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "fedramp_ai_studio"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _control_sort_key(control_id: str):
    import re

    m = re.match(r"^([A-Z]{2})-(\d+)(?:\((\d+)\))?$", (control_id or "").strip())
    if not m:
        return (control_id or "",)
    family = m.group(1)
    num = int(m.group(2))
    sub = int(m.group(3)) if m.group(3) else -1
    return (family, num, sub)


@router.get("/", response_class=HTMLResponse)
async def ui_home(request: Request, db: AsyncSession = Depends(get_db)):
    db_error: str | None = None
    runs = []
    accounts = []
    narratives = []
    diagrams = []

    try:
        runs_stmt = select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(50)
        runs_result = await db.execute(runs_stmt)
        runs = runs_result.scalars().all()

        latest_run_by_account: dict[str, IngestionRun] = {}
        latest_success_by_account: dict[str, IngestionRun] = {}
        for r in runs:
            if r.account_id not in latest_run_by_account:
                latest_run_by_account[r.account_id] = r
            if r.status == RunStatus.SUCCESS and r.account_id not in latest_success_by_account:
                latest_success_by_account[r.account_id] = r

        for account_id, latest in latest_run_by_account.items():
            chosen = latest_success_by_account.get(account_id, latest)
            accounts.append(
                {
                    "account_id": account_id,
                    "latest_run_id": str(chosen.id),
                    "latest_status": chosen.status.value if chosen.status else "unknown",
                    "latest_started_at": chosen.started_at,
                    "assets_count": chosen.assets_count,
                    "identities_count": chosen.identities_count,
                    "networks_count": chosen.networks_count,
                    "data_stores_count": chosen.data_stores_count,
                }
            )
        accounts.sort(
            key=lambda a: a["latest_started_at"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        narratives_stmt = select(SSPNarrative).order_by(SSPNarrative.created_at.desc()).limit(10)
        narratives_result = await db.execute(narratives_stmt)
        narratives = narratives_result.scalars().all()

        diagrams_stmt = select(ArchitectureDiagram).order_by(ArchitectureDiagram.created_at.desc())
        diagrams_result = await db.execute(diagrams_stmt)
        diagrams = diagrams_result.scalars().all()
    except OperationalError:
        db_error = (
            "Database unreachable. Start Postgres (e.g. `docker-compose up db`) "
            "or set `DATABASE_URL` to a reachable instance."
        )
    except DBAPIError:
        db_error = "Database error. If this is a fresh DB, run `alembic upgrade head`."

    # Controls are loaded lazily from the Templates API to keep the initial page fast.
    controls: list[dict[str, str]] = []

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "runs": runs,
            "accounts": accounts,
            "controls": controls,
            "narratives": narratives,
            "diagrams": diagrams,
            "settings": settings,
            "db_error": db_error,
        },
    )


@router.get("/narratives/{narrative_id}", response_class=HTMLResponse)
async def ui_view_narrative(
    request: Request,
    narrative_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SSPNarrative).where(SSPNarrative.id == narrative_id)
    result = await db.execute(stmt)
    narrative = result.scalar_one_or_none()
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")

    # Render Markdown to HTML
    import markdown as md

    html = md.markdown(
        narrative.generated_markdown or "",
        extensions=["fenced_code", "tables"],
        output_format="html5",
    )

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "control_id": narrative.control_id,
            "account_id": narrative.account_id,
            "narrative_id": str(narrative.id),
            "result": {
                "markdown": narrative.generated_markdown,
                "html": html,
                "implementation_status": "",
                "is_valid": True,
                "evidence_snapshot": narrative.evidence_snapshot,
                "model": narrative.model,
            },
            "error": None,
        },
    )


@router.get("/templates", response_class=HTMLResponse)
async def ui_templates(request: Request):
    return templates.TemplateResponse("template_manager.html", {"request": request, "settings": settings})


@router.get("/playground", response_class=HTMLResponse)
async def ui_playground(request: Request):
    """Testing playground for RAG/scans/vendors/policies/POA&M endpoints."""
    return templates.TemplateResponse("playground.html", {"request": request, "settings": settings})


@router.get("/diagrams/{diagram_id}", response_class=HTMLResponse)
async def ui_view_diagram(
    request: Request,
    diagram_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ArchitectureDiagram).where(ArchitectureDiagram.id == diagram_id)
    result = await db.execute(stmt)
    diagram = result.scalar_one_or_none()
    if diagram is None:
        raise HTTPException(status_code=404, detail="Diagram not found")

    return templates.TemplateResponse(
        "diagram_result.html",
        {
            "request": request,
            "diagram_id": str(diagram.id),
            "account_id": diagram.account_id,
            "ingestion_run_id": str(diagram.ingestion_run_id) if diagram.ingestion_run_id else None,
            "model_text": diagram.model_text,
            "renderer_version": diagram.renderer_version,
            "image_mime_type": diagram.image_mime_type,
            "image_base64": diagram.image_base64,
            "artist_prompt": diagram.artist_prompt,
            "summarizer_output": diagram.summarizer_output,
            "svg_markup": diagram.svg_markup,
            "diagram_spec_json": diagram.diagram_spec_json,
            "diagram_evaluation": diagram.diagram_evaluation,
            "diagram_score": diagram.diagram_score,
            "diagram_iterations": diagram.diagram_iterations,
            "evidence_json": diagram.evidence_json,
            "settings": settings,
            "error": None,
        },
    )

