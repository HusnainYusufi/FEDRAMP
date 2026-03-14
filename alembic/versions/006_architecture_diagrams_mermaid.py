"""Add Mermaid storage to architecture_diagrams (Use Case 11).

Revision ID: 006
Revises: 005
Create Date: 2026-02-23 00:00:00.000000

Adds:
  - architecture_diagrams.mermaid_code
  - architecture_diagrams.mermaid_prompt
  - architecture_diagrams.model_mermaid
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
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

    if not _has_column(inspector, "architecture_diagrams", "mermaid_code"):
        op.add_column("architecture_diagrams", sa.Column("mermaid_code", sa.Text(), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "mermaid_prompt"):
        op.add_column("architecture_diagrams", sa.Column("mermaid_prompt", sa.Text(), nullable=True))
    if not _has_column(inspector, "architecture_diagrams", "model_mermaid"):
        op.add_column("architecture_diagrams", sa.Column("model_mermaid", sa.String(50), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "architecture_diagrams" not in tables:
        return

    if _has_column(inspector, "architecture_diagrams", "model_mermaid"):
        op.drop_column("architecture_diagrams", "model_mermaid")
    if _has_column(inspector, "architecture_diagrams", "mermaid_prompt"):
        op.drop_column("architecture_diagrams", "mermaid_prompt")
    if _has_column(inspector, "architecture_diagrams", "mermaid_code"):
        op.drop_column("architecture_diagrams", "mermaid_code")

