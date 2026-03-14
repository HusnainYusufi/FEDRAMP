"""
Run Alembic migrations using DATABASE_URL from `.env`.

Why this exists:
- On Windows/PowerShell, quoting env vars inline is error-prone.
- Some local `.env` files may contain multi-line quoted values.
- We want to avoid printing credentials to the console.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config


def _extract_multiline_quoted(name: str, text: str) -> str | None:
    """
    Extract NAME="...possibly multi-line..." from a .env-like file.
    Returns the inner value (without quotes) or None.
    """
    # Support multi-line quoted values where the closing quote can appear on a later line.
    m = re.search(
        rf'(?ms)^{re.escape(name)}\s*=\s*"(.*?)"\s*(?:\r?\n|$)',
        text,
    )
    if not m:
        return None
    return m.group(1)


def _extract_single_line_unquoted(name: str, text: str) -> str | None:
    """Extract NAME=value from a .env-like file (single line, unquoted)."""
    m = re.search(rf"(?m)^{re.escape(name)}\s*=\s*([^\r\n#]*)", text)
    if not m:
        return None
    return m.group(1).strip()


def _normalize_sync_db_url(url: str) -> str:
    # Collapse whitespace/newlines that can appear in a multiline-quoted .env value
    u = "".join((url or "").split())
    if not u:
        raise ValueError("Empty DATABASE_URL")
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql://", 1)
    # Ensure we have a SQLAlchemy driver for Alembic (sync)
    if u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql+asyncpg://"):
        u = u.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql+psycopg2://"):
        u = u.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return u


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    if not env_path.exists():
        raise SystemExit("ERROR: .env not found. Create one or set DATABASE_URL(_SYNC).")

    text = env_path.read_text(encoding="utf-8", errors="replace")

    url = (
        _extract_multiline_quoted("DATABASE_URL_SYNC", text)
        or _extract_multiline_quoted("DATABASE_URL", text)
        or _extract_single_line_unquoted("DATABASE_URL_SYNC", text)
        or _extract_single_line_unquoted("DATABASE_URL", text)
    )
    if not url:
        raise SystemExit("ERROR: DATABASE_URL not found in .env.")

    os.environ["DATABASE_URL_SYNC"] = _normalize_sync_db_url(url)

    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")
    print("migrations_ok")


if __name__ == "__main__":
    main()

