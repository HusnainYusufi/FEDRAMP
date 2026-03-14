"""
AWS evidence API (read-only).

Browse ingested AWS infrastructure evidence stored in PostgreSQL.

Endpoints:
  GET /aws/evidence/runs
  GET /aws/evidence/runs/{run_id}/summary
  GET /aws/evidence/runs/{run_id}/identities
  GET /aws/evidence/runs/{run_id}/assets
  GET /aws/evidence/runs/{run_id}/network-components
  GET /aws/evidence/runs/{run_id}/data-stores
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    DataStore,
    Identity,
    IngestionRun,
    NetworkComponent,
    RunStatus,
)
from app.db.session import get_db
from app.config.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/evidence", tags=["aws-evidence"])


class RunSummaryItem(BaseModel):
    id: str
    account_id: str
    regions: list[str]
    status: str
    assets_count: int
    identities_count: int
    networks_count: int
    data_stores_count: int
    started_at: str
    finished_at: str | None = None


class RunListResponse(BaseModel):
    runs: list[RunSummaryItem]
    total: int


class ResourceCountItem(BaseModel):
    resource_type: str
    count: int


class RunDetailSummary(BaseModel):
    run_id: str
    account_id: str
    identities: list[ResourceCountItem]
    assets: list[ResourceCountItem]
    network_components: list[ResourceCountItem]
    data_stores: list[ResourceCountItem]


class ResourceRecord(BaseModel):
    id: str
    resource_id: str
    account_id: str
    region: str
    resource_type: str
    data: dict


class PaginatedResources(BaseModel):
    items: list[ResourceRecord]
    total: int
    limit: int
    offset: int


async def _get_run_or_404(run_id: UUID, db: AsyncSession) -> IngestionRun:
    stmt = select(IngestionRun).where(IngestionRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return run


async def _paginated_query(
    model,
    run_id: UUID,
    db: AsyncSession,
    resource_type: str | None,
    limit: int,
    offset: int,
) -> PaginatedResources:
    base = select(model).where(model.ingestion_run_id == run_id)
    count_base = select(func.count(model.id)).where(model.ingestion_run_id == run_id)

    if resource_type:
        base = base.where(model.resource_type == resource_type)
        count_base = count_base.where(model.resource_type == resource_type)

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    stmt = base.order_by(model.created_at).offset(offset).limit(limit)
    result = await db.execute(stmt)
    records = result.scalars().all()

    items = [
        ResourceRecord(
            id=str(r.id),
            resource_id=r.resource_id,
            account_id=r.account_id,
            region=r.region,
            resource_type=r.resource_type,
            data=r.data,
        )
        for r in records
    ]

    return PaginatedResources(items=items, total=total, limit=limit, offset=offset)


async def _count_by_type(model, run_id: UUID, db: AsyncSession) -> list[ResourceCountItem]:
    stmt = (
        select(model.resource_type, func.count(model.id))
        .where(model.ingestion_run_id == run_id)
        .group_by(model.resource_type)
    )
    result = await db.execute(stmt)
    return [ResourceCountItem(resource_type=rt, count=cnt) for rt, cnt in result.all()]


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    account_id: str | None = Query(None, description="Filter by account"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    base = select(IngestionRun)
    count_base = select(func.count(IngestionRun.id))

    if account_id:
        base = base.where(IngestionRun.account_id == account_id)
        count_base = count_base.where(IngestionRun.account_id == account_id)
    if status:
        base = base.where(IngestionRun.status == status)
        count_base = count_base.where(IngestionRun.status == status)

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    stmt = base.order_by(IngestionRun.started_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    runs = result.scalars().all()

    return RunListResponse(
        total=total,
        runs=[
            RunSummaryItem(
                id=str(r.id),
                account_id=r.account_id,
                regions=r.regions,
                status=r.status.value if isinstance(r.status, RunStatus) else r.status,
                assets_count=r.assets_count,
                identities_count=r.identities_count,
                networks_count=r.networks_count,
                data_stores_count=r.data_stores_count,
                started_at=r.started_at.isoformat(),
                finished_at=r.finished_at.isoformat() if r.finished_at else None,
            )
            for r in runs
        ],
    )


@router.get("/runs/{run_id}/summary", response_model=RunDetailSummary)
async def run_summary(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> RunDetailSummary:
    run = await _get_run_or_404(run_id, db)

    identities = await _count_by_type(Identity, run_id, db)
    assets = await _count_by_type(Asset, run_id, db)
    networks = await _count_by_type(NetworkComponent, run_id, db)
    data_stores = await _count_by_type(DataStore, run_id, db)

    return RunDetailSummary(
        run_id=str(run.id),
        account_id=run.account_id,
        identities=identities,
        assets=assets,
        network_components=networks,
        data_stores=data_stores,
    )


@router.get("/runs/{run_id}/identities", response_model=PaginatedResources)
async def list_identities(
    run_id: UUID,
    resource_type: str | None = Query(None, description="e.g. iam_user, iam_role"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResources:
    await _get_run_or_404(run_id, db)
    return await _paginated_query(Identity, run_id, db, resource_type, limit, offset)


@router.get("/runs/{run_id}/assets", response_model=PaginatedResources)
async def list_assets(
    run_id: UUID,
    resource_type: str | None = Query(None, description="e.g. ec2_instance"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResources:
    await _get_run_or_404(run_id, db)
    return await _paginated_query(Asset, run_id, db, resource_type, limit, offset)


@router.get("/runs/{run_id}/network-components", response_model=PaginatedResources)
async def list_network_components(
    run_id: UUID,
    resource_type: str | None = Query(None, description="e.g. vpc, subnet, security_group"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResources:
    await _get_run_or_404(run_id, db)
    return await _paginated_query(NetworkComponent, run_id, db, resource_type, limit, offset)


@router.get("/runs/{run_id}/data-stores", response_model=PaginatedResources)
async def list_data_stores(
    run_id: UUID,
    resource_type: str | None = Query(None, description="e.g. s3_bucket, rds_instance"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResources:
    await _get_run_or_404(run_id, db)
    return await _paginated_query(DataStore, run_id, db, resource_type, limit, offset)

