#!/usr/bin/env bash
# Production start path: preflight, migrate, then hand off to uvicorn.
#
#   ./deploy.sh                 # 1 worker on 0.0.0.0:8000
#   WORKERS=4 ./deploy.sh       # 4 workers (Postgres only; see the preflight below)
#
# Config comes from the environment or .env: SECRET_KEY and DATABASE_URL are required
# in a real deploy; HOST, PORT, WORKERS are optional overrides.
source "$(dirname "$0")/scripts/_env.sh"   # cd repo root, pick the venv's python as $PY

# .env holds the secrets in a real deployment; export them so both the preflight
# checks here and every forked uvicorn worker see the same values.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}
WORKERS=${WORKERS:-1}
DATABASE_URL=${DATABASE_URL:-sqlite:///./bookmarks.db}

# --- preflight: refuse to half-start; fail here, loudly -----------------------

# app.auth enforces this (>=32 chars) at import too, but that error would surface once
# per worker after fork. Catch the common "forgot to set it" case up front.
if [ -z "${SECRET_KEY:-}" ]; then
    echo "deploy: SECRET_KEY is not set. Copy .env.example to .env, or export it." >&2
    exit 1
fi

# SQLite + multiple workers is a trap: writes serialise on the file, and anything kept
# in process (the credential rate limiter) diverges per worker. One worker, or Postgres.
if [ "$WORKERS" -gt 1 ] && [[ "$DATABASE_URL" == sqlite* ]]; then
    echo "deploy: refusing $WORKERS workers on SQLite ($DATABASE_URL); use 1 worker or Postgres." >&2
    exit 1
fi

# --- migrate, then serve ------------------------------------------------------

"$PY" -m alembic upgrade head

# exec so uvicorn replaces this shell and receives SIGTERM directly (clean container
# stop). --proxy-headers reads X-Forwarded-* from a fronting proxy; pair it with
# --forwarded-allow-ips in a real deploy so those headers can't be spoofed.
exec "$PY" -m uvicorn app.main:app \
    --host "$HOST" --port "$PORT" --workers "$WORKERS" --proxy-headers
