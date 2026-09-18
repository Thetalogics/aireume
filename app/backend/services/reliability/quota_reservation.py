"""Abandoned quota reservation reconciliation."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    AnalysisJob,
    AnalysisResult,
    QuotaReservation,
    Tenant,
    UsageLog,
)

log = logging.getLogger(__name__)


class QuotaLimitExceeded(Exception):
    pass


def reserve_analysis_quota(
    db: Session,
    *,
    tenant_id: int,
    user_id: int | None,
    quantity: int,
    operation_id: str,
    analyses_limit: int | None,
) -> QuotaReservation:
    """Create one durable hold and increment usage in the caller's transaction."""
    existing = (
        db.query(QuotaReservation)
        .filter(
            QuotaReservation.tenant_id == tenant_id,
            QuotaReservation.operation_id == operation_id,
        )
        .with_for_update()
        .first()
    )
    if existing is not None:
        if existing.status in ("pending", "consumed"):
            return existing
        raise ValueError(f"Quota operation {operation_id} is already {existing.status}")

    ttl = int(os.getenv("QUOTA_RESERVATION_TTL_SECONDS", "3600"))
    reservation = QuotaReservation(
        operation_id=operation_id,
        tenant_id=tenant_id,
        quantity=quantity,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )
    db.add(reservation)
    db.flush()

    predicate = [Tenant.id == tenant_id]
    if analyses_limit is not None and analyses_limit >= 0:
        predicate.append(Tenant.analyses_count_this_month + quantity <= analyses_limit)
    result = db.execute(
        update(Tenant)
        .where(*predicate)
        .values(analyses_count_this_month=Tenant.analyses_count_this_month + quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise QuotaLimitExceeded("Monthly analysis limit exceeded")
    db.add(
        UsageLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action="resume_analysis",
            quantity=quantity,
            details=f'{{"operation_id":"{operation_id}"}}',
        )
    )
    db.flush()
    return reservation


def reconcile_expired_quota_reservations(db: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    pending = (
        db.query(QuotaReservation)
        .filter(
            QuotaReservation.status == "pending",
            QuotaReservation.expires_at <= now,
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    released = 0
    from app.backend.routes.analyze_helpers import _release_analysis_quota

    for row in pending:
        if row.job_id:
            job = db.query(AnalysisJob).filter(AnalysisJob.id == row.job_id).first()
            if job is not None and job.status in ("queued", "processing", "retrying"):
                continue
            if job is not None and (
                job.status == "completed"
                or db.query(AnalysisResult.id).filter(AnalysisResult.job_id == job.id).first()
            ):
                continue
        if not _release_analysis_quota(db, row.tenant_id, row.quantity):
            try:
                from app.backend.services.metrics import QUOTA_RECONCILE_FAILURE_TOTAL

                QUOTA_RECONCILE_FAILURE_TOTAL.inc()
            except Exception:
                pass
            log.warning(
                "quota_reconciliation_underflow reservation_id=%s tenant_id=%s quantity=%s",
                row.id,
                row.tenant_id,
                row.quantity,
            )
            continue
        row.status = "released"
        released += 1
    if pending:
        db.commit()
    if released:
        try:
            from app.backend.services.metrics import QUOTA_RECONCILE_RELEASE_TOTAL

            QUOTA_RECONCILE_RELEASE_TOTAL.inc(released)
        except Exception:
            pass
        log.info("quota_reservations_released count=%s", released)
    return released
