"""AI agent APIs (narratives + blueprint hydration)."""

from fastapi import APIRouter

from .routes import router as ai_router

router = APIRouter(prefix="/ai", tags=["ai-agent"])
router.include_router(ai_router)

