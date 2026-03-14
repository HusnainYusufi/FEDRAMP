"""Document RAG + scan findings + POA&M items.

Revision ID: 008
Revises: 007
Create Date: 2026-02-23

Adds:
  - documents
  - document_chunks (with optional pgvector embedding + index)
  - compliance_findings
  - poam_items
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "008"
down_revision: Union[str, None] = "007"
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

    # pgvector support (Postgres only)
    if dialect_name == "postgresql":
        # IMPORTANT: if CREATE EXTENSION fails inside a transaction, Postgres marks the
        # whole transaction as aborted. Even if we catch the Python exception, all
        # subsequent statements will fail with InFailedSqlTransaction.
        #
        # To avoid poisoning the migration transaction, run these best-effort DDL
        # statements in an autocommit block.
        try:
            with op.get_context().autocommit_block():
                op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            # Some managed PG environments restrict CREATE EXTENSION.
            pass

    # documents
    if not _has_table(inspector, "documents"):
        op.create_table(
            "documents",
            sa.Column(
                "id",
                sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql"),
                primary_key=True,
            ),
            sa.Column("account_id", sa.String(length=12), nullable=True),
            sa.Column("doc_type", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="uploaded"),
            sa.Column(
                "metadata",
                json_type,
                nullable=False,
                server_default=sa.text("'{}'::jsonb") if dialect_name == "postgresql" else sa.text("'{}'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # Ensure expected indexes exist (idempotent)
    if not _has_index(inspector, "documents", "ix_documents_account_id"):
        op.create_index("ix_documents_account_id", "documents", ["account_id"])
    if not _has_index(inspector, "documents", "ix_documents_doc_type"):
        op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    if not _has_index(inspector, "documents", "ix_documents_content_sha256"):
        op.create_index("ix_documents_content_sha256", "documents", ["content_sha256"])

    # document_chunks
    # NOTE: We create embedding as JSON for non-Postgres for portability.
    # If vector extension is available, we promote it to a vector type via raw SQL.
    if not _has_table(inspector, "document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column(
                "id",
                sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql"),
                primary_key=True,
            ),
            sa.Column(
                "document_id",
                sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql"),
                nullable=False,
            ),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("embedding", json_type if dialect_name != "postgresql" else sa.Text(), nullable=True),
            sa.Column("token_count", sa.Integer(), nullable=True),
            sa.Column("page_start", sa.Integer(), nullable=True),
            sa.Column("page_end", sa.Integer(), nullable=True),
            sa.Column("section", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        )

    if not _has_index(inspector, "document_chunks", "ix_document_chunks_document_id"):
        op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    if not _has_index(inspector, "document_chunks", "ix_document_chunks_document_chunk"):
        op.create_index(
            "ix_document_chunks_document_chunk",
            "document_chunks",
            ["document_id", "chunk_index"],
            unique=True,
        )

    if dialect_name == "postgresql":
        # Best-effort: convert embedding column to vector(1536) if possible.
        # If the vector extension isn't available, leave it as text.
        try:
            with op.get_context().autocommit_block():
                op.execute(
                    "ALTER TABLE document_chunks "
                    "ALTER COLUMN embedding TYPE vector(1536) "
                    "USING NULL::vector(1536)"
                )
        except Exception:
            pass

        # Best-effort ivfflat index for cosine similarity (requires vector type)
        try:
            with op.get_context().autocommit_block():
                op.execute(
                    "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_ivfflat "
                    "ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
        except Exception:
            pass

    # compliance_findings
    if not _has_table(inspector, "compliance_findings"):
        op.create_table(
            "compliance_findings",
            sa.Column(
                "id",
                sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql"),
                primary_key=True,
            ),
            sa.Column("account_id", sa.String(length=12), nullable=True),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("finding_key", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("severity", sa.String(length=50), nullable=True),
            sa.Column("resource_id", sa.Text(), nullable=True),
            sa.Column(
                "raw",
                json_type,
                nullable=False,
                server_default=sa.text("'{}'::jsonb") if dialect_name == "postgresql" else sa.text("'{}'"),
            ),
            sa.Column("mapped_control_id", sa.String(length=20), nullable=True),
            sa.Column("mapping_method", sa.String(length=50), nullable=True),
            sa.Column("mapping_confidence", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    # Indexes (idempotent)
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_account_id"):
        op.create_index("ix_compliance_findings_account_id", "compliance_findings", ["account_id"])
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_source"):
        op.create_index("ix_compliance_findings_source", "compliance_findings", ["source"])
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_finding_key"):
        op.create_index("ix_compliance_findings_finding_key", "compliance_findings", ["finding_key"])
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_severity"):
        op.create_index("ix_compliance_findings_severity", "compliance_findings", ["severity"])
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_resource_id"):
        op.create_index("ix_compliance_findings_resource_id", "compliance_findings", ["resource_id"])
    if not _has_index(inspector, "compliance_findings", "ix_compliance_findings_mapped_control_id"):
        op.create_index("ix_compliance_findings_mapped_control_id", "compliance_findings", ["mapped_control_id"])

    # poam_items
    if not _has_table(inspector, "poam_items"):
        op.create_table(
            "poam_items",
            sa.Column(
                "id",
                sa.Uuid(as_uuid=True).with_variant(sa.dialects.postgresql.UUID(as_uuid=True), "postgresql"),
                primary_key=True,
            ),
            sa.Column("account_id", sa.String(length=12), nullable=True),
            sa.Column("item_id", sa.String(length=100), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "cve_ids",
                json_type,
                nullable=False,
                server_default=sa.text("'[]'::jsonb") if dialect_name == "postgresql" else sa.text("'[]'"),
            ),
            sa.Column("vendor", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True),
            sa.Column("due_date", sa.String(length=30), nullable=True),
            sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_notes", sa.Text(), nullable=True),
            sa.Column(
                "raw",
                json_type,
                nullable=False,
                server_default=sa.text("'{}'::jsonb") if dialect_name == "postgresql" else sa.text("'{}'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not _has_index(inspector, "poam_items", "ix_poam_items_account_id"):
        op.create_index("ix_poam_items_account_id", "poam_items", ["account_id"])
    if not _has_index(inspector, "poam_items", "ix_poam_items_item_id"):
        op.create_index("ix_poam_items_item_id", "poam_items", ["item_id"])
    if not _has_index(inspector, "poam_items", "ix_poam_items_status"):
        op.create_index("ix_poam_items_status", "poam_items", ["status"])


def downgrade() -> None:
    # Drop in reverse order
    try:
        op.drop_table("poam_items")
    except Exception:
        pass
    try:
        op.drop_table("compliance_findings")
    except Exception:
        pass
    try:
        op.drop_index("ix_document_chunks_embedding_ivfflat", table_name="document_chunks")
    except Exception:
        pass
    try:
        op.drop_table("document_chunks")
    except Exception:
        pass
    try:
        op.drop_table("documents")
    except Exception:
        pass

