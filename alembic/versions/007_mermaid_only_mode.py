"""Mermaid-only mode: make image columns nullable and store Mermaid evaluation.

Revision ID: 007
Revises: 006
Create Date: 2026-02-23 00:00:00.000000

Changes:
  - architecture_diagrams.image_mime_type nullable
  - architecture_diagrams.image_base64 nullable
  - add: mermaid_evaluation (JSON/JSONB)
  - add: mermaid_score (int)
  - add: mermaid_iterations (int)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "007"
down_revision: Union[str, None] = "006"
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
    tables = set(inspector.get_table_names())
    if "architecture_diagrams" not in tables:
        return

    # Make image columns nullable (Mermaid-only mode)
    try:
        op.alter_column("architecture_diagrams", "image_mime_type", existing_type=sa.String(50), nullable=True)
    except Exception:
        pass
    try:
        op.alter_column("architecture_diagrams", "image_base64", existing_type=sa.Text(), nullable=True)
    except Exception:
        pass

    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    if not _has_column(inspector, "architecture_diagrams", "mermaid_evaluation"):
        op.add_column("architecture_diagrams", sa.Column("mermaid_evaluation", json_type, nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "mermaid_score"):
        op.add_column("architecture_diagrams", sa.Column("mermaid_score", sa.Integer(), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "mermaid_iterations"):
        op.add_column("architecture_diagrams", sa.Column("mermaid_iterations", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "architecture_diagrams" not in tables:
        return

    if _has_column(inspector, "architecture_diagrams", "mermaid_iterations"):
        op.drop_column("architecture_diagrams", "mermaid_iterations")
    if _has_column(inspector, "architecture_diagrams", "mermaid_score"):
        op.drop_column("architecture_diagrams", "mermaid_score")
    if _has_column(inspector, "architecture_diagrams", "mermaid_evaluation"):
        op.drop_column("architecture_diagrams", "mermaid_evaluation")

    # Restore non-null (best-effort)
    try:
        op.alter_column("architecture_diagrams", "image_mime_type", existing_type=sa.String(50), nullable=False)
    except Exception:
        pass
    try:
        op.alter_column("architecture_diagrams", "image_base64", existing_type=sa.Text(), nullable=False)
    except Exception:
        pass

