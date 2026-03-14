#!/usr/bin/env sh
set -eu

# Container entrypoint:
# - optionally run Alembic migrations
# - optionally load FedRAMP controls (one-time / idempotent script)
# - then exec the provided CMD (uvicorn)

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
LOAD_CONTROLS="${LOAD_CONTROLS:-1}"
DB_CONNECT_RETRIES="${DB_CONNECT_RETRIES:-30}"
DB_CONNECT_SLEEP_SECONDS="${DB_CONNECT_SLEEP_SECONDS:-2}"

resolve_db_url() {
  # Prefer explicit sync URL; otherwise fall back to DATABASE_URL.
  # Keep this simple: normalize to a plain `postgresql://...` URI for the connectivity check.
  DB_URL="${DATABASE_URL_SYNC:-${DATABASE_URL:-}}"
  if [ -z "$DB_URL" ]; then
    return 0
  fi

  DB_URL="$(echo "$DB_URL" | sed \
    -e 's%^postgresql\\+asyncpg://%postgresql://%' \
    -e 's%^postgresql\\+psycopg2://%postgresql://%' \
    -e 's%^postgresql\\+psycopg://%postgresql://%' \
    -e 's%^postgres://%postgresql://%')"

  export DATABASE_URL_SYNC="$DB_URL"
}

wait_for_db() {
  if [ -z "${DATABASE_URL_SYNC:-}" ]; then
    return 0
  fi
  echo "[entrypoint] Waiting for Postgres to accept connections..."
  i=1
  while [ "$i" -le "$DB_CONNECT_RETRIES" ]; do
    python - <<'PY' && return 0
import os
import psycopg

url = os.environ.get("DATABASE_URL_SYNC", "")
conn = psycopg.connect(url)
conn.close()
PY
    echo "[entrypoint] DB not ready yet ($i/$DB_CONNECT_RETRIES). Sleeping ${DB_CONNECT_SLEEP_SECONDS}s..."
    sleep "$DB_CONNECT_SLEEP_SECONDS"
    i=$((i+1))
  done
  echo "[entrypoint] ERROR: Postgres never became ready. Check DATABASE_URL(_SYNC) env var and Railway Postgres plugin."
  return 1
}

if [ "$RUN_MIGRATIONS" = "1" ]; then
  resolve_db_url
  wait_for_db
  echo "[entrypoint] Running alembic migrations..."
  alembic upgrade head
fi



if [ "$LOAD_CONTROLS" = "1" ]; then
  echo "[entrypoint] Loading FedRAMP controls..."
  python scripts/load_fedramp_controls.py
fi

echo "[entrypoint] Starting app..."
exec "$@"

