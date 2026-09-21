"""Container healthcheck for the dedicated worker."""
from __future__ import annotations

import os
import sys

from sqlalchemy import text

from app.backend.db.database import SessionLocal
from app.backend.services.worker_heartbeat import worker_heartbeat_fresh


def main() -> int:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        if not worker_heartbeat_fresh(db):
            print("unhealthy: stale worker heartbeat", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"unhealthy: database {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    redis_required = os.getenv("REDIS_REQUIRED", "").lower() in ("1", "true", "yes")
    if redis_required:
        try:
            from app.backend.services.shared_cache import redis_is_healthy

            if not redis_is_healthy():
                print("unhealthy: redis required but unavailable", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"unhealthy: redis {exc}", file=sys.stderr)
            return 1
    print("healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
