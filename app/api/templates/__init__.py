"""Template ingestion + blueprint retrieval APIs."""

from fastapi import APIRouter

from .routes import router as templates_router

router = APIRouter(prefix="/templates", tags=["templates"])
router.include_router(templates_router)

