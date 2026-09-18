"""Quota reservation crash recovery (unit)."""
from datetime import datetime, timedelta, timezone

from app.backend.models.db_models import QuotaReservation, Tenant
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
