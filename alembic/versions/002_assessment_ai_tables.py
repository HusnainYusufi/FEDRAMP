"""Add Assessment AI tables: fedramp_controls and ssp_narratives.

Revision ID: 002
Revises: 001
Create Date: 2026-02-07 00:00:00.000000

Adds the rule store and narrative output tables required for
Use Case 1 (Write Narratives) of the FedRAMP AI Studio.

Tables:
  fedramp_controls  — NIST 800-53 control definitions (FedRAMP baseline)
  ssp_narratives    — AI-generated SSP narrative drafts with evidence snapshots
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM as PGEnum

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # --- Narrative status enum ---
    # IMPORTANT: use PostgreSQL ENUM type to control CREATE TYPE behavior.
    # We create the type idempotently via DO block, then use create_type=False
    # so table creation never tries to create it again.
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE narrative_status AS ENUM ('draft','reviewed','approved'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$;"
    )
    narrative_status = PGEnum(
        "draft",
        "reviewed",
        "approved",
        name="narrative_status",
        create_type=False,
    )

    # --- FedRAMP Controls ---
    # Guarded because a failed/partial previous run may have created it already.
    if "fedramp_controls" not in existing_tables:
        op.create_table(
            "fedramp_controls",
            sa.Column("control_id", sa.String(20), primary_key=True),
            sa.Column("family", sa.String(50), nullable=False, index=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("nist_description", sa.Text, nullable=False),
            sa.Column("fedramp_parameters", sa.Text, nullable=True),
            sa.Column("guidance", sa.Text, nullable=True),
            sa.Column(
                "baseline",
                sa.String(20),
                nullable=False,
                server_default="HIGH",
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
        )

    # Refresh tables list after potential create
    existing_tables = set(sa.inspect(bind).get_table_names())

    # --- SSP Narratives ---
    if "ssp_narratives" not in existing_tables:
        op.create_table(
            "ssp_narratives",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "control_id",
                sa.String(20),
                sa.ForeignKey("fedramp_controls.control_id"),
                nullable=False,
                index=True,
            ),
            sa.Column("account_id", sa.String(12), nullable=False, index=True),
            sa.Column(
                "ingestion_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ingestion_runs.id"),
                nullable=True,
                index=True,
            ),
            sa.Column("generated_markdown", sa.Text, nullable=False),
            sa.Column(
                "status",
                narrative_status,
                nullable=False,
                server_default="draft",
            ),
            sa.Column("model", sa.String(50), nullable=False),
            sa.Column("evidence_snapshot", JSONB, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "ssp_narratives" in existing_tables:
        op.drop_table("ssp_narratives")
    if "fedramp_controls" in existing_tables:
        op.drop_table("fedramp_controls")
    op.execute("DROP TYPE IF EXISTS narrative_status")
