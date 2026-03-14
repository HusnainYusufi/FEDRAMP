"""
Alembic environment configuration.

Reads the database URL from environment variables (via app.config)
so that migrations use the same configuration as the application.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.db.base import Base
from app.db.models import (  # noqa: F401 — force model registration
    Asset,
    ComplianceFinding,
    ControlRoadmap,
    DataStore,
    Document,
    DocumentChunk,
    FedRAMPControl,
    Identity,
    IngestionRun,
    NetworkComponent,
    POAMItem,
    SSPNarrative,
    SSPStyleExample,
)

config = context.config

# Override URL from environment.
# Railway (and many platforms) often provide only DATABASE_URL; Alembic needs a sync driver.
db_url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL") or settings.database_url_sync
if db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
if db_url.startswith("postgresql+psycopg2://"):
    db_url = db_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
if db_url.startswith("postgres://"):
    # Normalize legacy scheme
    db_url = db_url.replace("postgres://", "postgresql://", 1)

config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL scripts."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
