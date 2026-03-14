"""
Controls repository — fetch NIST 800-53 control definitions from the DB.

Thin async wrapper around the `fedramp_controls` table.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FedRAMPControl
from app.config.logging_config import get_logger

logger = get_logger(__name__)


async def get_control(control_id: str, db: AsyncSession) -> dict[str, Any] | None:
    """Fetch a single FedRAMP control by ID."""
    stmt = select(FedRAMPControl).where(FedRAMPControl.control_id == control_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        logger.warning("control_not_found", control_id=control_id)
        return None

    return {
        "control_id": row.control_id,
        "family": row.family,
        "title": row.title,
        "nist_description": row.nist_description,
        "fedramp_parameters": row.fedramp_parameters or "",
        "guidance": row.guidance or "",
        "baseline": row.baseline,
    }


async def list_controls(db: AsyncSession, family: str | None = None) -> list[dict[str, str]]:
    """List available controls (id + title + family)."""
    stmt = select(
        FedRAMPControl.control_id,
        FedRAMPControl.title,
        FedRAMPControl.family,
    ).order_by(FedRAMPControl.control_id)

    if family:
        stmt = stmt.where(FedRAMPControl.family == family)

    result = await db.execute(stmt)
    return [{"control_id": cid, "title": title, "family": fam} for cid, title, fam in result.all()]

