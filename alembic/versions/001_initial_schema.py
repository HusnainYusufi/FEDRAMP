"""Initial compliance data schema.

Revision ID: 001
Revises: None
Create Date: 2025-01-01 00:00:00.000000

Creates the core tables for the FedRAMP ingestion engine:
- ingestion_runs: audit trail of every ingestion invocation
- assets: compute resources (EC2)
- identities: IAM principals (users, roles, policies)
- network_components: VPCs, subnets, security groups
- data_stores: S3 buckets, RDS instances

Design decisions:
- UUID primary keys prevent enumeration attacks
- JSONB `data` columns store normalized (not raw) attributes
- All tables share account_id + region + timestamps for boundary scoping
- ingestion_run_id FK provides full traceability
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Ingestion runs ---
    op.create_table(
        "ingestion_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", sa.String(12), nullable=False, index=True),
        sa.Column("regions", JSONB, nullable=False),
        sa.Column(
            "status",
            sa.Enum("running", "success", "failed", name="run_status"),
            nullable=False,
            server_default="running",
        ),
        sa.Column("assets_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("identities_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("networks_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("data_stores_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Shared column definitions for compliance tables ---
    compliance_columns = [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_id", sa.Text, nullable=False, index=True),
        sa.Column("account_id", sa.String(12), nullable=False, index=True),
        sa.Column("region", sa.String(30), nullable=False, index=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="aws"),
        sa.Column("resource_type", sa.String(50), nullable=False, index=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column(
            "ingestion_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]

    for table_name in ["assets", "identities", "network_components", "data_stores"]:
        op.create_table(table_name, *[c.copy() for c in compliance_columns])


def downgrade() -> None:
    for table_name in ["data_stores", "network_components", "identities", "assets"]:
        op.drop_table(table_name)
    op.drop_table("ingestion_runs")
    op.execute("DROP TYPE IF EXISTS run_status")
