"""SVG diagram refactor.

Revision ID: 010
Revises: 009
Create Date: 2026-03-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "010"
down_revision: Union[str, None] = "009"
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

    json_type = sa.JSON().with_variant(JSONB(), "postgresql")

    if not _has_column(inspector, "architecture_diagrams", "diagram_spec_json"):
        op.add_column("architecture_diagrams", sa.Column("diagram_spec_json", json_type, nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "svg_markup"):
        op.add_column("architecture_diagrams", sa.Column("svg_markup", sa.Text(), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "renderer_version"):
        op.add_column("architecture_diagrams", sa.Column("renderer_version", sa.String(length=50), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "diagram_evaluation"):
        op.add_column("architecture_diagrams", sa.Column("diagram_evaluation", json_type, nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "diagram_score"):
        op.add_column("architecture_diagrams", sa.Column("diagram_score", sa.Integer(), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "diagram_iterations"):
        op.add_column("architecture_diagrams", sa.Column("diagram_iterations", sa.Integer(), nullable=True))

    try:
        op.alter_column("architecture_diagrams", "summarizer_output", existing_type=json_type, nullable=True)
    except Exception:
        pass
    try:
        op.alter_column("architecture_diagrams", "artist_prompt", existing_type=sa.Text(), nullable=True)
    except Exception:
        pass
    try:
        op.alter_column("architecture_diagrams", "model_image", existing_type=sa.String(length=50), nullable=True)
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "architecture_diagrams" not in tables:
        return

    for column in [
        "diagram_iterations",
        "diagram_score",
        "diagram_evaluation",
        "renderer_version",
        "svg_markup",
        "diagram_spec_json",
    ]:
        if _has_column(inspector, "architecture_diagrams", column):
            op.drop_column("architecture_diagrams", column)
