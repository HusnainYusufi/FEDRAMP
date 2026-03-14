# --------------------------------------------------------------------------
# FedRAMP Ingestion Core — Production Dockerfile
#
# Security considerations:
# - Multi-stage build to minimize attack surface
# - Non-root user (principle of least privilege — AC-6)
# - No dev dependencies in final image
# - Pinned base image for reproducibility (CM-2 Baseline Configuration)
# --------------------------------------------------------------------------

FROM python:3.11-slim AS base

# Prevent Python from buffering stdout/stderr (important for Docker logging)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg3 is installed via wheels (no build deps needed).

# --------------------------------------------------------------------------
# Dependencies layer (cached unless requirements.txt changes)
# --------------------------------------------------------------------------
FROM base AS dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------------------
# Final image
# --------------------------------------------------------------------------
FROM dependencies AS runtime

# Copy application code
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/

COPY scripts/ scripts/
COPY @docs/ @docs/
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

# Create non-root user for runtime (AC-6 Least Privilege)
RUN groupadd -r fedramp && \
    useradd -r -g fedramp -d /app -s /sbin/nologin fedramp && \
    chown -R fedramp:fedramp /app

USER fedramp

EXPOSE 8000

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
