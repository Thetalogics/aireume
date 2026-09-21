#!/bin/sh
set -euo pipefail
: "${PGHOST:?}"
: "${PGUSER:?}"
: "${TARGET_DB:?}"
: "${DUMP_FILE:?}"
if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  echo "Refusing restore without CONFIRM_RESTORE=YES" >&2
  exit 2
fi
if [ "${ALLOW_PRODUCTION_RESTORE:-}" != "1" ] && [ "${ENVIRONMENT:-}" = "production" ]; then
  echo "Refusing production restore without ALLOW_PRODUCTION_RESTORE=1" >&2
  exit 2
fi
test -s "$DUMP_FILE"
pg_restore --list "$DUMP_FILE" >/dev/null
createdb "$TARGET_DB" || true
pg_restore --clean --if-exists -d "$TARGET_DB" "$DUMP_FILE"
echo "restore_ok $TARGET_DB"
