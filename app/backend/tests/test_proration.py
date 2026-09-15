"""Tests for proration calculation and change-plan endpoint integration."""

import json
import pytest
from datetime import datetime, timezone, timedelta

from app.backend.services.proration_service import calculate_proration, get_plan_price_for_period
from app.backend.models.db_models import Tenant, SubscriptionPlan, AuditLog
from app.backend.tests.test_helpers import resolve_plan


# ─── Unit Tests for calculate_proration ─────────────────────────────────────────

class TestCalculateProration:
    """Unit tests for the proration calculation utility."""

    def _make_period(self, days_total: int):
        """Helper to create a billing period."""
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=days_total)
        return start, end

    def test_mid_month_upgrade(self):
        """Upgrade mid-cycle: net_amount should be positive."""
        start, end = self._make_period(30)
        change = start + timedelta(days=10)  # 20 days remaining

        result = calculate_proration(
            old_plan_price=10000,   # $100
            new_plan_price=20000,   # $200
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["days_total"] == 30
        assert result["days_remaining"] == 20
        assert result["proration_factor"] == pytest.approx(20 / 30, 0.001)
        assert result["credit_amount"] == int(round(10000 * (20 / 30)))
        assert result["charge_amount"] == int(round(20000 * (20 / 30)))
        assert result["net_amount"] == result["charge_amount"] - result["credit_amount"]
        assert result["net_amount"] > 0

    def test_mid_month_downgrade(self):
        """Downgrade mid-cycle: net_amount should be negative."""
        start, end = self._make_period(30)
        change = start + timedelta(days=10)  # 20 days remaining

        result = calculate_proration(
            old_plan_price=20000,   # $200
            new_plan_price=10000,   # $100
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["net_amount"] < 0
        assert result["credit_amount"] == int(round(20000 * (20 / 30)))
        assert result["charge_amount"] == int(round(10000 * (20 / 30)))

    def test_same_price(self):
        """Same price: net_amount should be zero."""
        start, end = self._make_period(30)
        change = start + timedelta(days=15)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=10000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["net_amount"] == 0

    def test_first_day_of_period(self):
        """Change on first day: almost full proration factor."""
        start, end = self._make_period(30)
        change = start

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["days_remaining"] == 30
        assert result["proration_factor"] == pytest.approx(1.0, 0.001)
        assert result["net_amount"] == 10000

    def test_last_day_of_period(self):
        """Change on last day: minimal proration (at least 1 day)."""
        start, end = self._make_period(30)
        change = end - timedelta(days=1)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["days_remaining"] == 1
        assert result["proration_factor"] == pytest.approx(1 / 30, 0.001)
        # credit = round(10000/30) = 333, charge = round(20000/30) = 667, net = 334
        assert result["net_amount"] == 334

    def test_no_period_dates(self):
        """No billing period dates: skip proration."""
        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=None,
            period_end=None,
        )

        assert result["skipped"] is True
        assert result["skip_reason"] == "No billing period dates set"
        assert result["net_amount"] == 0

    def test_change_date_before_period(self):
        """Change date before period: skip proration."""
        start, end = self._make_period(30)
        change = start - timedelta(days=5)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is True
        assert "before the current billing period" in result["skip_reason"]

    def test_change_date_after_period(self):
        """Change date after period: skip proration."""
        start, end = self._make_period(30)
        change = end + timedelta(days=1)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is True
        assert "after the end" in result["skip_reason"]

    def test_default_change_date_is_now(self):
        """Default change_date falls back to UTC now."""
        start = datetime.now(timezone.utc) - timedelta(days=15)
        end = start + timedelta(days=30)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
        )

        assert result["skipped"] is False
        assert 0 < result["days_remaining"] < 30

    def test_naive_datetime_handling(self):
        """Naive datetimes are treated as UTC."""
        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 31)
        change = datetime(2026, 1, 15)

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
            change_date=change,
        )

        assert result["skipped"] is False
        assert result["days_total"] == 30
        assert result["days_remaining"] == 16  # Jan 15 -> Jan 31

    def test_zero_day_period(self):
        """Zero-day period is handled gracefully."""
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start  # same day

        result = calculate_proration(
            old_plan_price=10000,
            new_plan_price=20000,
            period_start=start,
            period_end=end,
        )

        assert result["skipped"] is True
        assert "Invalid billing period" in result["skip_reason"]


# ─── Unit Tests for get_plan_price_for_period ───────────────────────────────────

class TestGetPlanPriceForPeriod:
    """Unit tests for billing-cycle price selection."""

    @pytest.fixture
    def plan(self, db):
        plan = SubscriptionPlan(
            name="test_plan",
            price_monthly=4900,
            price_yearly=47000,
            currency="USD",
            limits="{}",
            features="[]",
        )
        db.add(plan)
        db.commit()
        return plan

    def test_monthly_period(self, plan):
        """Short periods use monthly price."""
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=30)
        assert get_plan_price_for_period(plan, start, end) == 4900

    def test_yearly_period(self, plan):
        """Long periods (>=300 days) use yearly price."""
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=365)
        assert get_plan_price_for_period(plan, start, end) == 47000

    def test_none_period(self, plan):
        """None period falls back to monthly."""
        assert get_plan_price_for_period(plan, None, None) == 4900


# ─── Integration Tests for change-plan endpoint ─────────────────────────────────

class TestChangePlanProration:
    """Integration tests for proration in the change-plan endpoint."""

    def test_change_plan_with_period_upgrade(self, platform_admin_client_with_plans, db):
        """Changing plan with active period returns proration for upgrade."""
        tenant = db.query(Tenant).first()
        pro_plan = resolve_plan(db, "growth", "pro")
        enterprise_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()

        # Set tenant on pro plan with an active billing period that includes today
        tenant.plan_id = pro_plan.id
        today = datetime.now(timezone.utc)
        tenant.current_period_start = today - timedelta(days=10)
        tenant.current_period_end = today + timedelta(days=20)
        tenant.stripe_subscription_id = "sub_test_123"
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": enterprise_plan.id, "reason": "proration upgrade test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_plan"] == "enterprise"
        assert "proration" in data
        assert data["proration"]["skipped"] is False
        assert data["proration"]["net_amount"] > 0  # upgrade

        # Audit log contains proration details
        audit = db.query(AuditLog).filter(
            AuditLog.resource_id == tenant.id,
            AuditLog.action == "tenant.change_plan",
        ).order_by(AuditLog.id.desc()).first()
        assert audit is not None
        details = json.loads(audit.details)
        assert "proration" in details
        assert "provider_result" in details

    def test_change_plan_with_period_downgrade(self, platform_admin_client_with_plans, db):
        """Downgrade returns negative net_amount (credit)."""
        tenant = db.query(Tenant).first()
        enterprise_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()
        pro_plan = resolve_plan(db, "growth", "pro")

        tenant.plan_id = enterprise_plan.id
        today = datetime.now(timezone.utc)
        tenant.current_period_start = today - timedelta(days=10)
        tenant.current_period_end = today + timedelta(days=20)
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": pro_plan.id, "reason": "proration change-plan test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "proration" in data
        assert data["proration"]["net_amount"] < 0  # downgrade

    def test_change_plan_without_period_dates(self, platform_admin_client_with_plans, db):
        """No period dates means proration is skipped."""
        tenant = db.query(Tenant).first()
        pro_plan = resolve_plan(db, "growth", "pro")

        tenant.current_period_start = None
        tenant.current_period_end = None
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": pro_plan.id, "reason": "proration change-plan test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # When no period dates and no old plan, proration is None
        assert "proration" not in data or data.get("proration") is None

    def test_change_plan_same_price(self, platform_admin_client_with_plans, db):
        """Same price plan change results in zero net."""
        tenant = db.query(Tenant).first()
        pro_plan = resolve_plan(db, "growth", "pro")

        # Create a plan with same price as pro
        same_price_plan = SubscriptionPlan(
            name="same_price",
            display_name="Same Price",
            price_monthly=pro_plan.price_monthly,
            price_yearly=pro_plan.price_yearly,
            currency="USD",
            limits="{}",
            features="[]",
        )
        db.add(same_price_plan)
        db.commit()

        tenant.plan_id = pro_plan.id
        today = datetime.now(timezone.utc)
        tenant.current_period_start = today - timedelta(days=10)
        tenant.current_period_end = today + timedelta(days=20)
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": same_price_plan.id, "reason": "proration same-price test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "proration" in data
        assert data["proration"]["net_amount"] == 0

    def test_change_plan_updates_subscription_updated_at(self, platform_admin_client_with_plans, db):
        """Plan change updates the subscription_updated_at timestamp."""
        tenant = db.query(Tenant).first()
        pro_plan = resolve_plan(db, "growth", "pro")

        tenant.subscription_updated_at = None
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": pro_plan.id, "reason": "proration change-plan test"},
        )
        assert resp.status_code == 200
        db.refresh(tenant)
        assert tenant.subscription_updated_at is not None
