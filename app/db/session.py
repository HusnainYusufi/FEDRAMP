"""
Async database session factory.

Uses SQLAlchemy 2.0 async engine + sessionmaker.
The get_db dependency yields a session per-request and guarantees
cleanup on exit, preventing connection leaks.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

def _to_async_database_url(url: str) -> str:
    """
    Ensure we use an async driver URL for SQLAlchemy asyncio engine.

    Many providers expose DATABASE_URL as `postgresql://...` (sync URL). We convert it
    to `postgresql+psycopg://...` so `create_async_engine()` works.
    """
    u = (url or "").strip()
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql://", 1)
    if u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql+asyncpg://"):
        # Back-compat: project used to run on asyncpg
        u = u.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql+psycopg2://"):
        u = u.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return u

_ASYNC_DB_URL = _to_async_database_url(settings.database_url)

# SQLite is used in tests/local workflows. Its drivers don't accept Postgres
# connection args (e.g., connect_timeout) and pooling semantics differ.
_engine_kwargs: dict[str, object] = {"echo": settings.debug}
if _ASYNC_DB_URL.startswith("sqlite"):
    engine = create_async_engine(_ASYNC_DB_URL, **_engine_kwargs)
else:
    engine = create_async_engine(
        _ASYNC_DB_URL,
        **_engine_kwargs,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, commits on success."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
