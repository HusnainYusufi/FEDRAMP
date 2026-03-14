"""
AWS ingestion API.

POST /aws/ingestions triggers a full read-only ingestion run against a target AWS account.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestionRun, RunStatus
from app.db.session import get_db
from app.config.logging_config import get_logger
from app.services.aws.ingestor import AWSIngestor

logger = get_logger(__name__)

router = APIRouter(prefix="/ingestions", tags=["aws-ingestion"])

_ARN_PATTERN = re.compile(r"^arn:aws:iam::\d{12}:role/[\w+=,.@\-/]+$")


class IngestRequest(BaseModel):
    account_id: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit AWS account ID",
    )
    role_arn: str = Field(
        ...,
        description="ARN of the IAM role to assume (SecurityAudit)",
    )
    regions: list[str] = Field(
        ...,
        min_length=1,
        description="AWS regions to ingest (e.g. ['us-east-1'])",
    )

    @field_validator("role_arn")
    @classmethod
    def validate_role_arn(cls, v: str) -> str:
        if not _ARN_PATTERN.match(v):
            raise ValueError("role_arn must be a valid IAM role ARN")
        return v

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, v: list[str]) -> list[str]:
        valid_pattern = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
        for region in v:
            if not valid_pattern.match(region):
                raise ValueError(f"Invalid AWS region format: {region}")
        return v


class IngestResponse(BaseModel):
    status: str
    run_id: str | None = None
    assets_ingested: int = 0
    identities_ingested: int = 0
    networks_ingested: int = 0
    data_stores_ingested: int = 0
    error: str | None = None


@router.post("", response_model=IngestResponse)
async def ingest_aws(
    request: IngestRequest,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    """
    Trigger a read-only ingestion run against an AWS account.

    The role_arn must point to a role with the SecurityAudit managed policy attached.
    """
    arn_account = request.role_arn.split(":")[4]
    if arn_account != request.account_id:
        raise HTTPException(
            status_code=400,
            detail="account_id does not match the account in role_arn",
        )

    run = IngestionRun(
        account_id=request.account_id,
        regions=request.regions,
        status=RunStatus.RUNNING,
    )
    db.add(run)
    await db.flush()

    logger.info(
        "ingestion_requested",
        account_id=request.account_id,
        regions=request.regions,
        run_id=str(run.id),
    )

    try:
        ingestor = AWSIngestor(
            db=db,
            account_id=request.account_id,
            role_arn=request.role_arn,
            regions=request.regions,
            run_id=run.id,
        )
        result = await ingestor.run()

        run.status = RunStatus.SUCCESS
        run.assets_count = result["assets_ingested"]
        run.identities_count = result["identities_ingested"]
        run.networks_count = result["networks_ingested"]
        run.data_stores_count = result["data_stores_ingested"]
        run.finished_at = datetime.now(timezone.utc)

        return IngestResponse(
            status="success",
            run_id=str(run.id),
            assets_ingested=result["assets_ingested"],
            identities_ingested=result["identities_ingested"],
            networks_ingested=result["networks_ingested"],
            data_stores_ingested=result["data_stores_ingested"],
        )
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc)

        logger.error("ingestion_error", run_id=str(run.id), error=str(exc))
        return IngestResponse(status="failed", run_id=str(run.id), error=str(exc))

