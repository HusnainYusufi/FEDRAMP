"""
Shared test fixtures.

Tests use moto to mock AWS APIs — no real AWS calls are made.
Database tests use an in-memory SQLite or a dedicated test PostgreSQL
instance (configured via TEST_DATABASE_URL env var).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test environment setup (must run BEFORE importing app/main, which imports DB)
# ---------------------------------------------------------------------------

# Psycopg (async) cannot run under ProactorEventLoop on Windows.
if sys.platform == "win32":  # pragma: no cover
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Default to a local SQLite DB for tests to avoid accidentally using a dev/prod Postgres.
_TEST_DB_PATH = Path(__file__).resolve().parent / ".test.sqlite3"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH.as_posix()}")
os.environ.setdefault("DATABASE_URL_SYNC", f"sqlite:///{_TEST_DB_PATH.as_posix()}")

from app.main import app  # noqa: E402


async def _create_schema() -> None:
    from app.db.base import Base
    from app.db.session import engine
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop_schema() -> None:
    from app.db.base import Base
    from app.db.session import engine
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def _dispose_engine() -> None:
    from app.db.session import engine

    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _test_db_schema():
    import anyio

    # Start from a clean file each run.
    try:
        _TEST_DB_PATH.unlink()
    except FileNotFoundError:
        pass

    anyio.run(_create_schema)
    yield
    anyio.run(_drop_schema)
    anyio.run(_dispose_engine)
    try:
        _TEST_DB_PATH.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
