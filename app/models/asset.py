"""
Pydantic schemas for Asset (compute) resources.

These are the *canonical compliance objects* — the normalized
representation that downstream engines (control mapping, evidence
validation) will consume. Raw AWS responses are never persisted.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssetData(BaseModel):
    """Normalized attributes stored in the JSONB `data` column."""

    instance_id: str = Field(description="EC2 instance ID")
    instance_type: str = Field(description="Instance size (e.g. t3.micro)")
    state: str = Field(description="running, stopped, terminated, etc.")
    vpc_id: str | None = Field(default=None, description="VPC this instance belongs to")
    subnet_id: str | None = Field(default=None)
    private_ip: str | None = Field(default=None)
    public_ip: str | None = Field(default=None)
    iam_instance_profile: str | None = Field(
        default=None,
        description="IAM role attached — critical for AC-6 (Least Privilege)",
    )
    launch_time: datetime | None = Field(default=None)
    tags: dict[str, str] = Field(default_factory=dict)
    security_group_ids: list[str] = Field(default_factory=list)
    ebs_optimized: bool = Field(default=False)
    monitoring_enabled: bool = Field(
        default=False,
        description="CloudWatch detailed monitoring — relevant to AU-6",
    )


class AssetRecord(BaseModel):
    """Full record as returned by internal APIs."""

    id: str
    resource_id: str
    account_id: str
    region: str
    resource_type: str
    source: str
    data: AssetData
    created_at: datetime
    updated_at: datetime
