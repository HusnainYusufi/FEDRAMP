"""Add template versioning + control blueprints.

Revision ID: 003
Revises: 002
Create Date: 2026-02-17 00:00:00.000000

Adds:
  fedramp_templates
  fedramp_control_blueprints
  fedramp_controls.narrative_template (active cached blueprint)
  fedramp_controls.narrative_template_tag
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    try:
        cols = inspector.get_columns(table)
        return any(c.get("name") == column for c in cols)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "fedramp_templates" not in existing_tables:
        op.create_table(
            "fedramp_templates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("tag", sa.Text, nullable=False, unique=True),
            sa.Column("source_filename", sa.Text, nullable=False),
            sa.Column("sha256", sa.Text, nullable=False),
            sa.Column("parser_version", sa.Text, nullable=False, server_default="v1"),
            sa.Column("parse_meta", JSONB, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    existing_tables = set(sa.inspect(bind).get_table_names())

    if "fedramp_control_blueprints" not in existing_tables:
        op.create_table(
            "fedramp_control_blueprints",
            sa.Column(
                "template_id",
                UUID(as_uuid=True),
                sa.ForeignKey("fedramp_templates.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "control_id",
                sa.String(20),
                sa.ForeignKey("fedramp_controls.control_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("blueprint", JSONB, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    # Add active cached blueprint columns on fedramp_controls
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "fedramp_controls" in existing_tables:
        if not _has_column(inspector, "fedramp_controls", "narrative_template"):
            op.add_column(
                "fedramp_controls",
                sa.Column("narrative_template", JSONB, nullable=True),
            )
        if not _has_column(inspector, "fedramp_controls", "narrative_template_tag"):
            op.add_column(
                "fedramp_controls",
                sa.Column("narrative_template_tag", sa.Text, nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "fedramp_controls" in existing_tables:
        if _has_column(inspector, "fedramp_controls", "narrative_template_tag"):
            op.drop_column("fedramp_controls", "narrative_template_tag")
        if _has_column(inspector, "fedramp_controls", "narrative_template"):
            op.drop_column("fedramp_controls", "narrative_template")

    if "fedramp_control_blueprints" in existing_tables:
        op.drop_table("fedramp_control_blueprints")
    if "fedramp_templates" in existing_tables:
        op.drop_table("fedramp_templates")

