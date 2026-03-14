"""
Cross-dialect SQLAlchemy types.

The application runs primarily on PostgreSQL, but tests and local development
may use SQLite. These helpers keep model definitions portable while still using
PostgreSQL-optimized types where available.
"""

from __future__ import annotations

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

# Optional dependency: pgvector (Postgres only). SQLite tests should still run.
try:
    from pgvector.sqlalchemy import Vector as PGVector  # type: ignore
except Exception:  # pragma: no cover
    PGVector = None  # type: ignore

# Use JSONB on PostgreSQL, JSON elsewhere.
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")

# Use UUID on PostgreSQL, a portable UUID representation elsewhere.
UUID_TYPE = Uuid(as_uuid=True).with_variant(PG_UUID(as_uuid=True), "postgresql")

# Vector embeddings: pgvector on Postgres, JSON elsewhere.
#
# NOTE: We intentionally use a fixed dimension for the pgvector type so the
# column can be indexed. The embedding list is also stored in JSON for
# portability/debugging and to support SQLite-based tests.
DEFAULT_EMBEDDING_DIM = 1536

if PGVector is not None:
    VECTOR_TYPE = JSON().with_variant(PGVector(DEFAULT_EMBEDDING_DIM), "postgresql")
else:  # pragma: no cover
    VECTOR_TYPE = JSON()
