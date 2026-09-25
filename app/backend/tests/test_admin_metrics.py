"""
Tests for the platform admin metrics API endpoints.
"""
import json
import pytest
from datetime import datetime, timezone, timedelta

from app.backend.models.db_models import (
    Tenant, User, SubscriptionPlan, UsageLog,
)


def _add_usage_log(db, tenant_id, user_id=None, action="resume_analysis", quantity=1, created_at=None):
    """Add a usage log entry with optional custom timestamp."""
    log = UsageLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        quantity=quantity,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    return log


class TestMetricsOverview:
    """Tests for GET /api/admin/metrics/overview."""

    def test_metrics_overview_returns_correct_structure(self, platform_admin_client_with_plans, db):
        """Verify all expected fields are present in the response."""
        resp = platform_admin_client_with_plans.get("/api/admin/metrics/overview")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Top-level keys
        assert "tenants" in data
        assert "users" in data
        assert "analyses" in data
        assert "storage" in data
        assert "plans" in data
        assert "revenue" in data

        # tenants sub-keys
        for key in ("total", "active", "suspended", "trialing", "cancelled", "past_due"):
            assert key in data["tenants"], f"Missing tenants key: {key}"

        # users sub-keys
        assert "total" in data["users"]

        # analyses sub-keys
        for key in ("today", "this_week", "this_month"):
            assert key in data["analyses"], f"Missing analyses key: {key}"

        # storage sub-keys
        assert "total_gb" in data["storage"]

        # revenue sub-keys
        assert "mrr_cents" in data["revenue"]
        assert "arr_estimate_cents" in data["revenue"]

    def test_metrics_overview_counts_tenants(self, platform_admin_client_with_plans, db):
        """Create multiple tenants and verify counts are correct."""
        # The platform admin client already created one tenant ("PlatformAdminCorp")
        # Register two more tenants
        for name in ["TenantA", "TenantB"]:
            tenant = Tenant(name=name, slug=name.lower(), subscription_status="active")
            db.add(tenant)
            db.flush()
            user = User(
                tenant_id=tenant.id,
                email=f"admin@{name.lower()}.com",
                hashed_password="not-used-by-metrics-test",
                role="admin",
                is_active=True,
            )
            db.add(user)
        db.commit()

        resp = platform_admin_client_with_plans.get("/api/admin/metrics/overview")
        assert resp.status_code == 200
        data = resp.json()

        # Should have at least 3 tenants total (PlatformAdminCorp + TenantA + TenantB)
        assert data["tenants"]["total"] >= 3
        # All newly created ones are active
        assert data["tenants"]["active"] >= 3

    def test_metrics_overview_requires_platform_admin(self, auth_client, db):
        """Regular user gets 403 on metrics overview."""
        resp = auth_client.get("/api/admin/metrics/overview")
        assert resp.status_code == 403

    def test_metrics_with_no_data(self, platform_admin_client, db):
        """Empty database (no subscription plans, no extra tenants) still returns valid structure with zeroes."""
        resp = platform_admin_client_with_plans.get("/api/admin/metrics/overview") if False else None
        # Use platform_admin_client (no plans seeded) to test with minimal data
        resp = platform_admin_client.get("/api/admin/metrics/overview")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # All numeric values should be present and zero or more
        assert data["tenants"]["total"] >= 0
        assert data["users"]["total"] >= 0
        assert data["analyses"]["today"] >= 0
        assert data["analyses"]["this_week"] >= 0
        assert data["analyses"]["this_month"] >= 0
        assert data["storage"]["total_gb"] >= 0
        assert data["revenue"]["mrr_cents"] >= 0
        assert data["revenue"]["arr_estimate_cents"] >= 0


class TestUsageTrends:
    """Tests for GET /api/admin/metrics/usage-trends."""

    def test_usage_trends_returns_correct_structure(self, platform_admin_client_with_plans, db):
        """Verify analyses and signups arrays are present with correct item structure."""
        # Add some usage data so the arrays are non-empty
        tenant = db.query(Tenant).first()
        user = db.query(User).filter(User.tenant_id == tenant.id).first()
        _add_usage_log(db, tenant.id, user.id, "resume_analysis", 3)

        resp = platform_admin_client_with_plans.get("/api/admin/metrics/usage-trends")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "period_days" in data
        assert "analyses" in data
        assert "signups" in data
        assert isinstance(data["analyses"], list)
        assert isinstance(data["signups"], list)

        # Each analysis item should have date and count
        if data["analyses"]:
            item = data["analyses"][0]
            assert "date" in item
            assert "count" in item

        # Each signup item should have date and count
        if data["signups"]:
            item = data["signups"][0]
            assert "date" in item
            assert "count" in item

    def test_usage_trends_custom_days_param(self, platform_admin_client_with_plans, db):
        """Test that the days query parameter is respected."""
        resp = platform_admin_client_with_plans.get("/api/admin/metrics/usage-trends?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_days"] == 7

        # Also test default
        resp2 = platform_admin_client_with_plans.get("/api/admin/metrics/usage-trends")
        assert resp2.status_code == 200
        assert resp2.json()["period_days"] == 30
