"""Dedicated worker liveness heartbeat."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.backend.models.db_models import WorkerHeartbeat

ROLE = "queue-worker"
STALE_AFTER_SECONDS = 90


def touch_worker_heartbeat(db: Session, *, detail: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(WorkerHeartbeat, ROLE)
    if row is None:
        db.add(WorkerHeartbeat(role=ROLE, updated_at=now, detail=detail))
    else:
        row.updated_at = now
        row.detail = detail
    db.commit()


def worker_heartbeat_fresh(db: Session, *, max_age_seconds: int = STALE_AFTER_SECONDS) -> bool:
    row = db.get(WorkerHeartbeat, ROLE)
    if row is None or row.updated_at is None:
        return False
    stamp = row.updated_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp < timedelta(seconds=max_age_seconds)
