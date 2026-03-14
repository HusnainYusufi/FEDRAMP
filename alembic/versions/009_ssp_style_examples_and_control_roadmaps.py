"""SSP style examples + control roadmaps.

Revision ID: 009
Revises: 008
Create Date: 2026-03-05

Adds:
  - ssp_style_examples
  - control_roadmaps
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.Inspector, table: str) -> bool:
    try:
        return table in set(inspector.get_table_names())
    except Exception:
        return False


def _has_index(inspector: sa.Inspector, table: str, index_name: str) -> bool:
    try:
        idx = inspector.get_indexes(table) or []
        return any(i.get("name") == index_name for i in idx)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    dialect = getattr(bind, "dialect", None)
    dialect_name = getattr(dialect, "name", "")
    inspector = sa.inspect(bind)

    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    uuid_type = sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql")

    # ---------------------------------------------------------------------
    # ssp_style_examples
    # ---------------------------------------------------------------------
    if not _has_table(inspector, "ssp_style_examples"):
        op.create_table(
            "ssp_style_examples",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("control_id", sa.String(length=20), nullable=False),
            sa.Column("tone_tier", sa.String(length=20), nullable=False),
            sa.Column("example_markdown", sa.Text(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["control_id"], ["fedramp_controls.control_id"]),
        )

    # Indexes (idempotent)
    if not _has_index(inspector, "ssp_style_examples", "ix_ssp_style_examples_control_id"):
        op.create_index("ix_ssp_style_examples_control_id", "ssp_style_examples", ["control_id"])
    if not _has_index(inspector, "ssp_style_examples", "ix_ssp_style_examples_tone_tier"):
        op.create_index("ix_ssp_style_examples_tone_tier", "ssp_style_examples", ["tone_tier"])
    if not _has_index(inspector, "ssp_style_examples", "ix_ssp_style_examples_control_tone"):
        op.create_index(
            "ix_ssp_style_examples_control_tone",
            "ssp_style_examples",
            ["control_id", "tone_tier"],
        )

    # ---------------------------------------------------------------------
    # control_roadmaps
    # ---------------------------------------------------------------------
    if not _has_table(inspector, "control_roadmaps"):
        op.create_table(
            "control_roadmaps",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("control_id", sa.String(length=20), nullable=False),
            sa.Column("account_id", sa.String(length=12), nullable=True),
            sa.Column("status_override", sa.String(length=30), nullable=True),
            sa.Column("target_date", sa.String(length=30), nullable=True),
            sa.Column(
                "milestones",
                json_type,
                nullable=False,
                server_default=sa.text("'[]'::jsonb") if dialect_name == "postgresql" else sa.text("'[]'"),
            ),
            sa.Column("narrative_roadmap_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["control_id"], ["fedramp_controls.control_id"]),
        )

    # Indexes (idempotent)
    if not _has_index(inspector, "control_roadmaps", "ix_control_roadmaps_control_id"):
        op.create_index("ix_control_roadmaps_control_id", "control_roadmaps", ["control_id"])
    if not _has_index(inspector, "control_roadmaps", "ix_control_roadmaps_account_id"):
        op.create_index("ix_control_roadmaps_account_id", "control_roadmaps", ["account_id"])
    if not _has_index(inspector, "control_roadmaps", "ix_control_roadmaps_status_override"):
        op.create_index("ix_control_roadmaps_status_override", "control_roadmaps", ["status_override"])
    if not _has_index(inspector, "control_roadmaps", "ix_control_roadmaps_lookup"):
        op.create_index(
            "ix_control_roadmaps_lookup",
            "control_roadmaps",
            ["control_id", "account_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "ssp_style_examples" in existing_tables:
        op.drop_table("ssp_style_examples")
    if "control_roadmaps" in existing_tables:
        op.drop_table("control_roadmaps")
