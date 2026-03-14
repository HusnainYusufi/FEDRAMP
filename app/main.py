"""
FastAPI application entry point — FedRAMP Ingestion Core + Assessment AI.

Exposes APIs for:
  - Health checking
  - AWS ingestion triggering         (Use Case 2)
  - Evidence browsing                (Use Case 2)
  - SSP narrative generation         (Use Case 1)
  - FedRAMP AI Studio demo dashboard

The application is designed to run behind a reverse proxy in a
FedRAMP-authorized environment. Authentication is handled at the
infrastructure layer (not in this service).
"""

import sys
from pathlib import Path
import warnings

# If someone runs this file directly (`python app/main.py`), Python puts `app/`
# on sys.path, which breaks absolute imports like `from app.api...`.
# Ensure the repository root is importable so `app.*` imports work.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# LangChain emits a noisy warning on Python 3.14+ because it imports a
# Pydantic v1 compatibility layer. We don't use Pydantic v1 in this app.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3\\.14 or greater\\.",
    category=UserWarning,
)

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.health import router as health_router
from app.api.aws import router as aws_router
from app.api.ai_agent import router as ai_router
from app.api.templates import router as templates_router
from app.api.ui import router as ui_router
from app.config import settings
from app.config.logging_config import setup_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle — setup and teardown."""
    setup_logging()
    logger = get_logger("app.main")
    logger.info(
        "application_starting",
        service=settings.app_name,
        version=settings.app_version,
    )
    yield
    logger.info("application_shutting_down")


app = FastAPI(
    title="FedRAMP AI Studio",
    description=(
        "Compliance data engine and Assessment AI for fetching, normalizing, "
        "storing AWS configuration data, and generating FedRAMP SSP narratives. "
        "Designed for FedRAMP, CMMC, and NIST 800-53 compliance."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Core ingestion ---
app.include_router(health_router)
app.include_router(aws_router)
app.include_router(ai_router)
app.include_router(templates_router)
app.include_router(ui_router)

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/ui/")

@app.get("/ui", include_in_schema=False)
async def ui_redirect():
    return RedirectResponse(url="/ui/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
