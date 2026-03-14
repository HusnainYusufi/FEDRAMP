"""UI routes (Jinja). Not part of OpenAPI."""

from fastapi import APIRouter

from .routes import router as ui_router

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)
router.include_router(ui_router)

