#!/usr/bin/env python
"""
Load FedRAMP High Baseline controls from the provided spreadsheet into
the `fedramp_controls` table.

Usage:
    python scripts/load_fedramp_controls.py

Reads:
    @docs/EXAMPLE_CHATGPT_GENERATED_FedRAMP_High_Baseline_Controls_with_Implementations (2).xlsx

Requires: pandas, openpyxl, psycopg, sqlalchemy
"""

import os
import sys
import re
from pathlib import Path

# Ensure project root is on sys.path so we can import app.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import FedRAMPControl

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SPREADSHEET_PATH = (
    PROJECT_ROOT
    / "@docs"
    / "EXAMPLE_CHATGPT_GENERATED_FedRAMP_High_Baseline_Controls_with_Implementations (2).xlsx"
)

# Column mapping: spreadsheet header → model field
# CRITICAL: We explicitly do NOT map "Control Implementation Statements" (the poison).
COLUMN_MAP = {
    "ID": "control_id",
    "Family": "family",
    "Control Name": "title",
    "NIST Control Description": "nist_description",
    "FedRAMP Parameters": "fedramp_parameters",
    "FedRAMP Guidance": "guidance",
}


def _clean(value) -> str:
    """Convert a cell value to a stripped string (empty string for NaN)."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def _extract_multiline_quoted(name: str, text: str) -> str | None:
    m = re.search(
        rf'(?ms)^{re.escape(name)}\s*=\s*"(.*?)"\s*(?:\r?\n|$)',
        text,
    )
    if not m:
        return None
    return m.group(1)


def _extract_single_line_unquoted(name: str, text: str) -> str | None:
    m = re.search(rf"(?m)^{re.escape(name)}\s*=\s*([^\r\n#]*)", text)
    if not m:
        return None
    return m.group(1).strip()


def _db_url_from_env_file(env_path: Path) -> str | None:
    if not env_path.exists():
        return None
    text = env_path.read_text(encoding="utf-8", errors="replace")
    return (
        _extract_multiline_quoted("DATABASE_URL_SYNC", text)
        or _extract_multiline_quoted("DATABASE_URL", text)
        or _extract_single_line_unquoted("DATABASE_URL_SYNC", text)
        or _extract_single_line_unquoted("DATABASE_URL", text)
    )


def _normalize_sync_db_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("Empty DATABASE_URL")
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql://", 1)
    if u.startswith("postgresql+asyncpg://"):
        u = u.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql+psycopg2://"):
        u = u.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql://"):
        # Ensure we use psycopg3 driver by default.
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    return u


def load_controls() -> int:
    """
    Read the spreadsheet and upsert controls into Postgres.

    Returns the number of controls loaded.
    """
    if not SPREADSHEET_PATH.exists():
        print(f"ERROR: Spreadsheet not found at {SPREADSHEET_PATH}")
        sys.exit(1)

    # Read the first sheet
    try:
        df = pd.read_excel(SPREADSHEET_PATH, engine="openpyxl")
    except Exception as e:
        print(f"ERROR: Failed to read excel file: {e}")
        sys.exit(1)
        
    print(f"Read {len(df)} rows from spreadsheet")

    # Validate required columns exist
    missing = [col for col in COLUMN_MAP if col not in df.columns]
    if missing:
        print(f"ERROR: Missing columns in spreadsheet: {missing}")
        print(f"Available columns: {list(df.columns)}")
        sys.exit(1)

    # Use sync engine.
    # In cloud environments (Railway, etc.) DATABASE_URL is usually provided as postgresql://...
    # If only an async URL is provided, convert it to a sync driver for loading.
    env_file_url = _db_url_from_env_file(PROJECT_ROOT / ".env")
    db_url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL") or env_file_url or settings.database_url_sync
    engine = create_engine(_normalize_sync_db_url(db_url))
    loaded = 0

    with Session(engine) as db:
        for _, row in df.iterrows():
            control_id = _clean(row["ID"])
            if not control_id:
                continue  # Skip rows without a control ID

            control = FedRAMPControl(
                control_id=control_id,
                family=_clean(row["Family"]),
                title=_clean(row["Control Name"]),
                nist_description=_clean(row["NIST Control Description"]),
                # Filter out poison by only taking what we explicitly mapped
                fedramp_parameters=_clean(row.get("FedRAMP Parameters", "")) or None,
                guidance=_clean(row.get("FedRAMP Guidance", "")) or None,
                baseline="HIGH",
            )

            # Upsert: insert or update on conflict
            db.merge(control)
            loaded += 1

        db.commit()

    print(f"Successfully loaded {loaded} FedRAMP controls into database.")
    return loaded


if __name__ == "__main__":
    load_controls()
