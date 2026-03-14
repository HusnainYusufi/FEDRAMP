"""
Seed SSP style/tone examples for AC-2 into the FEDRAMP service database.

Reads the workspace-level @docs templates:
  - @docs/low-ssp.md
  - @docs/meduim-ssp.md
  - @docs/high-ssp.md

Extracts only the AC-2 section and stores it in `ssp_style_examples`
as tone tiers: low | meduim | high.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

# Ensure we can import `app.*` when invoked from any working directory.
FEDRAMP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FEDRAMP_ROOT))

from app.config import settings  # noqa: E402
from app.db.models import SSPStyleExample  # noqa: E402


CONTROL_ID = "AC-2"
TONE_SOURCES: dict[str, str] = {
    "low": "@docs/low-ssp.md",
    "meduim": "@docs/meduim-ssp.md",
    "high": "@docs/high-ssp.md",
}


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
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    return u


def _extract_control_section(*, markdown: str, control_id: str) -> str:
    lines = (markdown or "").replace("\r", "").splitlines()
    if not lines:
        raise ValueError("empty_markdown")

    start_idx = None
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(control_id)}\b", (ln or "").strip(), flags=re.IGNORECASE):
            start_idx = i
            break
    if start_idx is None:
        raise ValueError(f"control_header_not_found:{control_id}")

    header_re = re.compile(r"^(?P<cid>[A-Z]{2,3}-\d+)\b")
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m = header_re.match((lines[j] or "").strip())
        if m and (m.group("cid") or "").upper() != control_id.upper():
            end_idx = j
            break

    out = "\n".join(lines[start_idx:end_idx]).strip()
    return out + "\n"


def _extract_multiline_quoted(name: str, text: str) -> str | None:
    """
    Extract NAME="...possibly multi-line..." from a .env-like file.
    Returns the inner value (without quotes) or None.
    """
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


def _normalize_example(md: str) -> str:
    """
    Make seeded examples less product-specific so they can be reused as
    style/tone guidance across tenants.
    """
    s = (md or "").replace("\r", "")

    # Normalize possessives before the base token replacement.
    s = s.replace("Dragon’s", "The organization’s").replace("Dragon's", "The organization's")

    # Normalize customer responsibility phrasing.
    s = re.sub(r"\bDragon federal customers\b", "Customers", s)
    s = re.sub(r"\bDragon customers\b", "Customers", s)

    # Normalize org/system naming.
    s = re.sub(r"\bDragon\b", "The organization", s)
    s = re.sub(r"\bDGC FedRAMP system\b", "the system", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDGC FedRAMP environment\b", "the system environment", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDGC\b", "the system", s)

    # Normalize inherited authorization reference.
    s = s.replace("AGENCYAMAZONEW", "{{INHERITED_AUTH_NAME}}")
    s = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "{{INHERITED_AUTH_DATE}}", s)

    # Normalize cloud inheritance wording (avoid region-based "AWS East/West is FedRAMP authorized").
    s = re.sub(r"(?i)\bAWS\s+East/West\b", "Amazon Web Services (AWS)", s)
    s = re.sub(
        r"(?i)\bwhich\s+is\s+FedRAMP\s+authorized\b",
        "which maintains a FedRAMP authorization",
        s,
    )
    s = re.sub(
        r"(?i)\bwhich\s+maintains\s+a\s+FedRAMP\s+(?:Moderate\s+)?(?:authorization|ATO)\b",
        "which maintains a FedRAMP authorization",
        s,
    )
    s = re.sub(r"Amazon Web Services \(AWS\)\s+which", "Amazon Web Services (AWS), which", s)

    # Remove audit-style prohibited phrasing from examples so they don't leak into SSP output.
    s = re.sub(r"\bdoes\s+not\b", "currently has no", s, flags=re.IGNORECASE)
    s = re.sub(r"\black\s+of\b", "opportunity to strengthen", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnot\s+evidenced\b", "requires additional supporting artifacts", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnot\s+implemented\b", "is planned and tracked for implementation", s, flags=re.IGNORECASE)
    s = re.sub(r"\babsent\b", "not currently reflected in this snapshot", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmissing\b", "not currently reflected in this snapshot", s, flags=re.IGNORECASE)

    return s.strip() + "\n"


def main() -> None:
    # Prefer explicit environment variables; otherwise read FEDRAMP/.env directly.
    env_file_url = _db_url_from_env_file(FEDRAMP_ROOT / ".env")
    db_url = (
        os.getenv("DATABASE_URL_SYNC")
        or os.getenv("DATABASE_URL")
        or env_file_url
        or settings.database_url_sync
        or settings.database_url
    )
    engine = create_engine(_normalize_sync_db_url(db_url))

    workspace_root = FEDRAMP_ROOT.parent

    inserted = 0
    with Session(engine) as session:
        for tone_tier, rel_path in TONE_SOURCES.items():
            src_path = workspace_root / rel_path
            if not src_path.exists():
                raise SystemExit(f"missing_source_file: {src_path}")

            raw = src_path.read_text(encoding="utf-8", errors="replace")
            ac2 = _extract_control_section(markdown=raw, control_id=CONTROL_ID)
            ac2 = _normalize_example(ac2)

            # Upsert semantics: replace existing example for (control_id, tone_tier).
            session.execute(
                delete(SSPStyleExample).where(
                    SSPStyleExample.control_id == CONTROL_ID,
                    SSPStyleExample.tone_tier == tone_tier,
                )
            )

            session.add(
                SSPStyleExample(
                    control_id=CONTROL_ID,
                    tone_tier=tone_tier,
                    example_markdown=ac2,
                    source_path=rel_path,
                )
            )
            inserted += 1

        session.commit()

    print(f"seed_ok control_id={CONTROL_ID} inserted={inserted}")


if __name__ == "__main__":
    main()

