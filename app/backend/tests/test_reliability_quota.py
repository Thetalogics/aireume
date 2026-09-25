"""Quota reservation crash recovery (unit)."""
from datetime import datetime, timedelta, timezone

from app.backend.models.db_models import QuotaReservation, Tenant, UsageLog
from app.backend.services.reliability.quota_reservation import reconcile_expired_quota_reservations


def test_expired_pending_reservation_is_released(db, seed_subscription_plans):
    tenant = Tenant(name="rel-quota", slug="rel-quota", analyses_count_this_month=3)
    db.add(tenant)
    db.flush()
    db.add(
        QuotaReservation(
            operation_id="op-abandoned-1",
            tenant_id=tenant.id,
            quantity=1,
            status="pending",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
    )
    db.commit()
    n = reconcile_expired_quota_reservations(db)
    assert n == 1
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == 2
    row = db.query(QuotaReservation).filter_by(operation_id="op-abandoned-1").one()
    assert row.status == "released"


def test_direct_quota_hold_consumes_or_releases(db, seed_subscription_plans):
    from app.backend.routes.analyze_helpers import (
        consume_direct_analysis_quota,
        release_direct_analysis_quota,
        reserve_direct_analysis_quota,
    )

    tenant = Tenant(name="direct-quota", slug="direct-quota", analyses_count_this_month=0)
    db.add(tenant)
    db.commit()

    consumed, message = reserve_direct_analysis_quota(
        db,
        tenant.id,
        user_id=None,
        quantity=1,
        operation_id="direct-consume",
    )
    assert message == ""
    assert consumed is not None
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == 1

    assert consume_direct_analysis_quota(db, consumed) is True
    consumed_row = db.query(QuotaReservation).filter_by(operation_id="direct-consume").one()
    assert consumed_row.status == "consumed"

    released, message = reserve_direct_analysis_quota(
        db,
        tenant.id,
        user_id=None,
        quantity=1,
        operation_id="direct-release",
    )
    assert message == ""
    assert released is not None
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == 2

    assert release_direct_analysis_quota(db, released, reason="test") is True
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == 1
    released_row = db.query(QuotaReservation).filter_by(operation_id="direct-release").one()
    assert released_row.status == "released"
    assert db.query(UsageLog).filter(UsageLog.tenant_id == tenant.id).count() == 2
