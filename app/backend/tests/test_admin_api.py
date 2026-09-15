"""
Tests for the platform admin API endpoints.
"""
import json
import pytest
from app.backend.models.db_models import (
    Tenant, User, SubscriptionPlan, UsageLog, AuditLog,
)
from app.backend.tests.test_helpers import resolve_plan


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _create_second_tenant(client, db):
    """Register a second tenant for multi-tenant tests."""
    register_payload = {
        "company_name": "OtherCorp",
        "email": "admin@othercorp.com",
        "password": "OtherPass123!",
        "full_name": "Other Admin",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"
    tenant = db.query(Tenant).filter(Tenant.slug == "othercorp").first()
    return tenant


def _add_usage_log(db, tenant_id, user_id=None, action="resume_analysis", quantity=1, details=None):
    """Add a usage log entry."""
    log = UsageLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        quantity=quantity,
        details=json.dumps(details) if details else None,
    )
    db.add(log)
    db.commit()
    return log


# ─── Permission Checks ──────────────────────────────────────────────────────────

class TestPermissionChecks:
    """Regular (non-platform-admin) users should get 403 on all admin endpoints."""

    ENDPOINTS = [
        ("get", "/api/admin/tenants"),
        ("get", "/api/admin/tenants/1"),
        ("post", "/api/admin/tenants/1/suspend"),
        ("post", "/api/admin/tenants/1/reactivate"),
        ("post", "/api/admin/tenants/1/change-plan"),
        ("post", "/api/admin/tenants/1/adjust-usage"),
        ("get", "/api/admin/tenants/1/usage-history"),
        ("get", "/api/admin/audit-logs"),
    ]

    def test_regular_user_gets_403(self, auth_client):
        """Regular authenticated user gets 403 Forbidden on all admin endpoints."""
        for method, url in self.ENDPOINTS:
            if method == "get":
                resp = auth_client.get(url)
            else:
                # POST endpoints need a body where required
                if "suspend" in url:
                    resp = auth_client.post(url, json={"reason": "test"})
                elif "change-plan" in url:
                    resp = auth_client.post(url, json={"plan_id": 1, "reason": "unauthorized"})
                elif "adjust-usage" in url:
                    resp = auth_client.post(url, json={"analyses_count": 0})
                else:
                    resp = auth_client.post(url)
            assert resp.status_code == 403, (
                f"Expected 403 for {method.upper()} {url}, got {resp.status_code}"
            )


# ─── List Tenants ───────────────────────────────────────────────────────────────

class TestListTenants:

    def test_list_tenants_returns_data(self, platform_admin_client_with_plans, db):
        """List tenants returns at least the platform admin's tenant."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    def test_list_tenants_pagination(self, platform_admin_client_with_plans, db):
        """Pagination parameters are respected."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants?per_page=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_page"] == 1
        assert len(data["items"]) <= 1

    def test_list_tenants_search(self, platform_admin_client_with_plans, db):
        """Search filter works on name and slug."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants?search=PlatformAdmin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # All returned items should match the search term
        for item in data["items"]:
            assert "platformadmin" in item["slug"].lower() or "platformadmin" in item["name"].lower()

    def test_list_tenants_status_filter(self, platform_admin_client_with_plans, db):
        """Status filter works."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants?status=active")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["subscription_status"] == "active"

    def test_list_tenants_includes_user_count(self, platform_admin_client_with_plans, db):
        """Each tenant item includes user_count."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert "user_count" in item
            assert item["user_count"] >= 1


# ─── Get Tenant Detail ──────────────────────────────────────────────────────────

class TestGetTenantDetail:

    def test_get_tenant_detail(self, platform_admin_client_with_plans, db):
        """Get tenant detail returns full info."""
        tenant = db.query(Tenant).first()
        resp = platform_admin_client_with_plans.get(f"/api/admin/tenants/{tenant.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == tenant.id
        assert data["name"] == tenant.name
        assert data["slug"] == tenant.slug
        assert "users" in data
        assert "recent_usage_logs" in data
        assert "recent_audit_logs" in data
        assert isinstance(data["users"], list)

    def test_get_tenant_detail_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 for non-existent tenant."""
        resp = platform_admin_client_with_plans.get("/api/admin/tenants/99999")
        assert resp.status_code == 404


# ─── Suspend / Reactivate ──────────────────────────────────────────────────────

class TestSuspendReactivate:

    def test_suspend_reactivate_lifecycle(self, platform_admin_client_with_plans, db):
        """Full suspend → reactivate lifecycle with audit logs."""
        tenant = db.query(Tenant).first()

        # Suspend
        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/suspend",
            json={"reason": "Terms violation"},
        )
        assert resp.status_code == 200
        db.refresh(tenant)
        assert tenant.suspended_at is not None
        assert tenant.suspended_reason == "Terms violation"
        assert tenant.subscription_status == "suspended"

        # Audit log was created
        audit = db.query(AuditLog).filter(
            AuditLog.resource_id == tenant.id,
            AuditLog.action == "tenant.suspend",
        ).first()
        assert audit is not None
        details = json.loads(audit.details) if audit.details else {}
        assert details.get("reason") == "Terms violation"

        # Cannot suspend again
        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/suspend",
            json={"reason": "Another reason"},
        )
        assert resp.status_code == 400

        # Reactivate
        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/reactivate",
        )
        assert resp.status_code == 200
        db.refresh(tenant)
        assert tenant.suspended_at is None
        assert tenant.suspended_reason is None
        assert tenant.subscription_status == "active"

        # Reactivate audit log created
        audit = db.query(AuditLog).filter(
            AuditLog.resource_id == tenant.id,
            AuditLog.action == "tenant.reactivate",
        ).first()
        assert audit is not None

        # Cannot reactivate again
        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/reactivate",
        )
        assert resp.status_code == 400

    def test_suspend_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 when suspending non-existent tenant."""
        resp = platform_admin_client_with_plans.post(
            "/api/admin/tenants/99999/suspend",
            json={"reason": "test"},
        )
        assert resp.status_code == 404

    def test_reactivate_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 when reactivating non-existent tenant."""
        resp = platform_admin_client_with_plans.post(
            "/api/admin/tenants/99999/reactivate",
        )
        assert resp.status_code == 404


# ─── Change Plan ────────────────────────────────────────────────────────────────

class TestChangePlan:

    def test_change_plan(self, platform_admin_client_with_plans, db):
        """Change tenant plan works with valid plan."""
        tenant = db.query(Tenant).first()
        pro_plan = resolve_plan(db, "growth", "pro")
        assert pro_plan is not None

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": pro_plan.id, "reason": "platform support override"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_plan"] == pro_plan.name

        db.refresh(tenant)
        assert tenant.plan_id == pro_plan.id

        # Audit log created
        audit = db.query(AuditLog).filter(
            AuditLog.resource_id == tenant.id,
            AuditLog.action == "tenant.change_plan",
        ).first()
        assert audit is not None
        details = json.loads(audit.details) if audit.details else {}
        assert "old_plan_name" in details
        assert "new_plan_name" in details

    def test_change_plan_tenant_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 for non-existent tenant."""
        resp = platform_admin_client_with_plans.post(
            "/api/admin/tenants/99999/change-plan",
            json={"plan_id": 1, "reason": "missing tenant"},
        )
        assert resp.status_code == 404

    def test_change_plan_invalid_plan(self, platform_admin_client_with_plans, db):
        """Returns 404 for non-existent plan."""
        tenant = db.query(Tenant).first()
        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/change-plan",
            json={"plan_id": 99999, "reason": "invalid plan"},
        )
        assert resp.status_code == 404


# ─── Adjust Usage ───────────────────────────────────────────────────────────────

class TestAdjustUsage:

    def test_adjust_analyses_count(self, platform_admin_client_with_plans, db):
        """Adjusting analyses_count works."""
        tenant = db.query(Tenant).first()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/adjust-usage",
            json={"analyses_count": 42},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["analyses_count_this_month"] == 42

        db.refresh(tenant)
        assert tenant.analyses_count_this_month == 42

    def test_adjust_storage_used(self, platform_admin_client_with_plans, db):
        """Adjusting storage_used_bytes works."""
        tenant = db.query(Tenant).first()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/adjust-usage",
            json={"storage_used_bytes": 1024},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["storage_used_bytes"] == 1024

        db.refresh(tenant)
        assert tenant.storage_used_bytes == 1024

    def test_adjust_both_fields(self, platform_admin_client_with_plans, db):
        """Adjusting both fields at once works."""
        tenant = db.query(Tenant).first()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/adjust-usage",
            json={"analyses_count": 10, "storage_used_bytes": 2048},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["analyses_count_this_month"] == 10
        assert data["storage_used_bytes"] == 2048

    def test_adjust_usage_audit_log(self, platform_admin_client_with_plans, db):
        """Adjusting usage creates an audit log with old/new values."""
        tenant = db.query(Tenant).first()
        tenant.analyses_count_this_month = 5
        db.commit()

        resp = platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/adjust-usage",
            json={"analyses_count": 99},
        )
        assert resp.status_code == 200

        audit = db.query(AuditLog).filter(
            AuditLog.resource_id == tenant.id,
            AuditLog.action == "tenant.adjust_usage",
        ).first()
        assert audit is not None
        details = json.loads(audit.details)
        assert details["old_analyses_count"] == 5
        assert details["new_analyses_count"] == 99

    def test_adjust_usage_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 for non-existent tenant."""
        resp = platform_admin_client_with_plans.post(
            "/api/admin/tenants/99999/adjust-usage",
            json={"analyses_count": 0},
        )
        assert resp.status_code == 404


# ─── Usage History ──────────────────────────────────────────────────────────────

class TestUsageHistory:

    def test_usage_history_returns_logs(self, platform_admin_client_with_plans, db):
        """Usage history endpoint returns usage log entries."""
        tenant = db.query(Tenant).first()
        user = db.query(User).filter(User.tenant_id == tenant.id).first()

        # Add some usage logs
        _add_usage_log(db, tenant.id, user.id, "resume_analysis", 1, {"source": "upload"})
        _add_usage_log(db, tenant.id, user.id, "batch_analysis", 5)

        resp = platform_admin_client_with_plans.get(
            f"/api/admin/tenants/{tenant.id}/usage-history"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        # Both actions should be present (order may vary with identical timestamps)
        actions = {item["action"] for item in data}
        assert "resume_analysis" in actions
        assert "batch_analysis" in actions

    def test_usage_history_limit(self, platform_admin_client_with_plans, db):
        """Limit parameter is respected."""
        tenant = db.query(Tenant).first()
        user = db.query(User).filter(User.tenant_id == tenant.id).first()

        for i in range(5):
            _add_usage_log(db, tenant.id, user.id, "resume_analysis", 1)

        resp = platform_admin_client_with_plans.get(
            f"/api/admin/tenants/{tenant.id}/usage-history?limit=2"
        )
        assert resp.status_code == 200
        assert len(resp.json()) <= 2

    def test_usage_history_not_found(self, platform_admin_client_with_plans, db):
        """Returns 404 for non-existent tenant."""
        resp = platform_admin_client_with_plans.get(
            "/api/admin/tenants/99999/usage-history"
        )
        assert resp.status_code == 404


# ─── Audit Logs ─────────────────────────────────────────────────────────────────

class TestAuditLogs:

    def test_audit_logs_returns_entries(self, platform_admin_client_with_plans, db):
        """Audit logs endpoint returns paginated entries."""
        # First, create an audit entry via suspend
        tenant = db.query(Tenant).first()
        platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/suspend",
            json={"reason": "test audit"},
        )

        resp = platform_admin_client_with_plans.get("/api/admin/audit-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_audit_logs_pagination(self, platform_admin_client_with_plans, db):
        """Pagination works for audit logs."""
        resp = platform_admin_client_with_plans.get("/api/admin/audit-logs?per_page=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_page"] == 1
        assert len(data["items"]) <= 1

    def test_audit_logs_action_filter(self, platform_admin_client_with_plans, db):
        """Action filter works."""
        tenant = db.query(Tenant).first()
        # Create a suspend action
        platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/suspend",
            json={"reason": "filter test"},
        )
        # Then reactivate so we can reuse
        platform_admin_client_with_plans.post(
            f"/api/admin/tenants/{tenant.id}/reactivate",
        )

        resp = platform_admin_client_with_plans.get(
            "/api/admin/audit-logs?action=tenant.suspend"
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["action"] == "tenant.suspend"

    def test_audit_logs_resource_type_filter(self, platform_admin_client_with_plans, db):
        """Resource type filter works."""
        resp = platform_admin_client_with_plans.get(
            "/api/admin/audit-logs?resource_type=tenant"
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["resource_type"] == "tenant"

    def test_audit_logs_actor_email_filter(self, platform_admin_client_with_plans, db):
        """Actor email filter works."""
        resp = platform_admin_client_with_plans.get(
            "/api/admin/audit-logs?actor_email=platformadmin"
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert "platformadmin" in item["actor_email"].lower()
