"""
SQLAlchemy declarative base and shared column mixins.

FedRAMP note: Every record carries account_id, region, and timestamps
to satisfy NIST 800-53 AU-3 (Content of Audit Records) and to enable
boundary-scoped queries for multi-account environments.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase

from app.db.types import UUID_TYPE

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class ComplianceRecordMixin:
    """
    Columns present on every compliance data table.

    - id:          Surrogate primary key (UUID v4).
    - resource_id: The cloud-provider-native identifier (e.g. ARN).
    - account_id:  AWS account that owns the resource.
    - region:      AWS region where the resource resides.
    - created_at:  When this record was first ingested.
    - updated_at:  When this record was last refreshed.
    - source:      Provenance tag (e.g. "aws", "azure" — future-proof).
    """

    id = Column(
        UUID_TYPE,
        primary_key=True,
        default=uuid.uuid4,
        comment="Surrogate PK — never exposed to external systems",
    )
    resource_id = Column(
        Text,
        nullable=False,
        index=True,
        comment="Cloud-native resource identifier (ARN, resource ID, etc.)",
    )
    account_id = Column(
        String(12),
        nullable=False,
        index=True,
        comment="AWS account ID that owns this resource",
    )
    region = Column(
        String(30),
        nullable=False,
        index=True,
        comment="AWS region (or 'global' for IAM resources)",
    )
    source = Column(
        String(20),
        nullable=False,
        default="aws",
        comment="Cloud provider or data source identifier",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp of first ingestion",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp of last refresh",
    )
