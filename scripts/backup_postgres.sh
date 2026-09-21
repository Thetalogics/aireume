#!/bin/sh
set -euo pipefail
: "${PGHOST:?}"
: "${PGUSER:?}"
: "${PGDATABASE:?}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTDIR=${BACKUP_DIR:-/backups}
mkdir -p "$OUTDIR"
FILE="$OUTDIR/${PGDATABASE}-${STAMP}.dump"
export FILE
pg_dump -Fc -f "$FILE"
test -s "$FILE"
pg_restore --list "$FILE" >/dev/null
echo "backup_ok $FILE"
if [ -n "${BACKUP_S3_BUCKET:-}" ] && [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
  python - <<'PY'
import os, sys
from pathlib import Path
try:
    import boto3
except ImportError:
    sys.exit("boto3 required for S3 upload")
client = boto3.client(
    "s3",
    endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
    aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"],
)
path = Path(os.environ["FILE"])
client.upload_file(str(path), os.environ["BACKUP_S3_BUCKET"], path.name)
print("uploaded", path.name)
PY
fi
RETENTION=${BACKUP_RETENTION_DAYS:-14}
find "$OUTDIR" -name "*.dump" -mtime +"$RETENTION" -delete || true
