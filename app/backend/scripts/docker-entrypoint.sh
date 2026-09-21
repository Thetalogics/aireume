#!/bin/sh
set -e

# Migrations run only in the dedicated migrate job (RUN_DB_MIGRATIONS=1).
# Backend and worker must not race Alembic.
if [ "${RUN_DB_MIGRATIONS:-0}" = "1" ]; then
  echo "[entrypoint] Running single-owner Alembic upgrade head..."
  cd /app && python -m app.backend.services.migration_owner
  echo "[entrypoint] Migrations complete."
else
  echo "[entrypoint] Skipping Alembic (RUN_DB_MIGRATIONS!=1)."
fi

if [ -f /app/wait_for_ollama.py ]; then
  python /app/wait_for_ollama.py
fi
exec "$@"
