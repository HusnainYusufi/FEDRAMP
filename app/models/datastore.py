"""
Pydantic schemas for DataStore resources.

Covers S3 buckets and RDS instances (metadata only). These map to:
  SC-28 (Protection of Information at Rest)
  SC-13 (Cryptographic Protection)
  CP-9  (System Backup)
  MP-4  (Media Storage)
"""

from datetime import datetime

from pydantic import BaseModel, Field


class S3BucketData(BaseModel):
    bucket_name: str
    creation_date: datetime | None = None
    region: str | None = Field(
        default=None,
        description="Bucket location constraint",
    )
    versioning_status: str | None = Field(
        default=None,
        description="Enabled/Suspended — relevant to CP-9",
    )
    encryption_algorithm: str | None = Field(
        default=None,
        description="SSE algorithm (AES256, aws:kms) — SC-28",
    )
    kms_key_id: str | None = None
    public_access_block: dict | None = Field(
        default=None,
        description="Public access block configuration — AC-3",
    )
    logging_enabled: bool = Field(
        default=False,
        description="Server access logging — AU-2",
    )
    tags: dict[str, str] = Field(default_factory=dict)


class RDSInstanceData(BaseModel):
    db_instance_identifier: str
    db_instance_class: str
    engine: str
    engine_version: str
    db_instance_status: str
    master_username: str
    endpoint_address: str | None = None
    endpoint_port: int | None = None
    vpc_id: str | None = None
    multi_az: bool = Field(
        default=False,
        description="Multi-AZ deployment — CP-6 (Alternate Processing Site)",
    )
    storage_encrypted: bool = Field(
        default=False,
        description="Encryption at rest — SC-28",
    )
    kms_key_id: str | None = None
    auto_minor_version_upgrade: bool = Field(
        default=False,
        description="Automatic patching — SI-2 (Flaw Remediation)",
    )
    backup_retention_period: int = Field(
        default=0,
        description="Days of automated backups — CP-9",
    )
    publicly_accessible: bool = Field(
        default=False,
        description="If True, reachable from Internet — SC-7 concern",
    )
    security_group_ids: list[str] = Field(default_factory=list)
    subnet_group_name: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class DataStoreRecord(BaseModel):
    id: str
    resource_id: str
    account_id: str
    region: str
    resource_type: str
    source: str
    data: dict
    created_at: datetime
    updated_at: datetime
