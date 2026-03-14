"""
AWS domain APIs:
- ingestion orchestration (read-only assume role + normalize + store)
- evidence browsing (read-only DB access)
"""

from fastapi import APIRouter

from .ingestions import router as ingestions_router
from .evidence import router as evidence_router

router = APIRouter(prefix="/aws", tags=["aws"])

router.include_router(ingestions_router)
router.include_router(evidence_router)

