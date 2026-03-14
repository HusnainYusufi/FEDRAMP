"""Add architecture diagrams table (Use Case 11 persistence).

Revision ID: 005
Revises: 004
Create Date: 2026-02-23 00:00:00.000000

Adds:
  - architecture_diagrams
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "architecture_diagrams" in existing_tables:
        return

    # Cross-dialect: use JSONB on Postgres, JSON elsewhere; UUID on Postgres, portable elsewhere.
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    uuid_type = sa.Uuid(as_uuid=True).with_variant(UUID(as_uuid=True), "postgresql")

    op.create_table(
        "architecture_diagrams",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("account_id", sa.String(12), nullable=False, index=True),
        sa.Column("ingestion_run_id", uuid_type, sa.ForeignKey("ingestion_runs.id"), nullable=True, index=True),
        sa.Column("evidence_json", json_type, nullable=False),
        sa.Column("summarizer_output", json_type, nullable=False),
        sa.Column("artist_prompt", sa.Text(), nullable=False),
        sa.Column("image_mime_type", sa.String(50), nullable=False, server_default="image/png"),
        sa.Column("image_base64", sa.Text(), nullable=False),
        sa.Column("model_text", sa.String(50), nullable=False),
        sa.Column("model_image", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "architecture_diagrams" in existing_tables:
        op.drop_table("architecture_diagrams")

