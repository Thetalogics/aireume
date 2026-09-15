"""
Tests for subscription API routes and usage tracking.
"""
import pytest
import json
from datetime import datetime, timezone, timedelta
from app.backend.models.db_models import SubscriptionPlan, Tenant, UsageLog


# ─── Get Available Plans Tests ──────────────────────────────────────────────────

class TestGetAvailablePlans:
    """Tests for GET /api/subscription/plans endpoint."""
    
    def test_get_plans_returns_all_active_plans(self, client, seed_subscription_plans):
        """Should return all active subscription plans sorted by sort_order."""
        response = client.get("/api/subscription/plans")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 4
        
        # Check order
        assert data[0]["name"] == "starter"
        assert data[1]["name"] == "growth"
        assert data[2]["name"] == "agency"
        assert data[3]["name"] == "enterprise"
    
    def test_plan_structure_includes_all_fields(self, client, seed_subscription_plans):
        """Each plan should have all required fields."""
        response = client.get("/api/subscription/plans")
        
        assert response.status_code == 200
        data = response.json()
        
        for plan in data:
            assert "id" in plan
            assert "name" in plan
            assert "display_name" in plan
            assert "description" in plan
            assert "price_monthly" in plan
            assert "price_yearly" in plan
            assert "currency" in plan
            assert "features" in plan
            assert "limits" in plan
            assert isinstance(plan["features"], list)
            assert isinstance(plan["limits"], dict)
    
    def test_starter_plan_limits(self, client, seed_subscription_plans):
        """Starter plan should have limited features."""
        response = client.get("/api/subscription/plans")
        
        data = response.json()
        starter_plan = next(p for p in data if p["name"] == "starter")
        
        assert starter_plan["price_monthly"] == 0
        assert starter_plan["limits"]["analyses_per_month"] == 5
        assert starter_plan["limits"]["api_access"] is False
    
    def test_enterprise_plan_unlimited(self, client, seed_subscription_plans):
        """Enterprise plan should have unlimited analyses."""
        response = client.get("/api/subscription/plans")
        
        data = response.json()
        enterprise_plan = next(p for p in data if p["name"] == "enterprise")
        
        assert enterprise_plan["limits"]["analyses_per_month"] == -1
        assert enterprise_plan["limits"]["dedicated_support"] is True


# ─── Get My Subscription Tests ──────────────────────────────────────────────────

class TestGetMySubscription:
    """Tests for GET /api/subscription endpoint."""
    
    def test_get_subscription_requires_auth(self, client, seed_subscription_plans):
        """Should return 401 without authentication."""
        response = client.get("/api/subscription")
        assert response.status_code == 401
    
    def test_get_subscription_returns_full_data(self, auth_client_with_pro_plan):
        """Authenticated user should get complete subscription info."""
        response = auth_client_with_pro_plan.get("/api/subscription")
        
        assert response.status_code == 200
        data = response.json()
        
        # Check top-level structure
        assert "current_plan" in data
        assert "usage" in data
        assert "available_plans" in data
        assert "days_until_reset" in data
        
        # Check current plan
        plan = data["current_plan"]
        assert plan["plan"]["name"] == "growth"
        assert plan["status"] == "active"
        assert plan["billing_cycle"] in ["monthly", "yearly"]
        
        # Check usage
        usage = data["usage"]
        assert "analyses_used" in usage
        assert "analyses_limit" in usage
        assert "percent_used" in usage
    
    def test_free_plan_subscription(self, auth_client_with_free_plan):
        """Free plan user should see correct limits."""
        response = auth_client_with_free_plan.get("/api/subscription")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["current_plan"]["plan"]["name"] == "starter"
        assert data["usage"]["analyses_limit"] == 5  # Free plan limit
    
    def test_unlimited_analyses_display(self, auth_client, db, seed_subscription_plans):
        """Enterprise plan should show unlimited (negative number) correctly."""
        from app.backend.models.db_models import Tenant
        
        # Get enterprise plan and assign to tenant
        enterprise_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()
        tenant = db.query(Tenant).filter(Tenant.slug == "testcorp").first()
        tenant.plan_id = enterprise_plan.id
        db.commit()
        
        response = auth_client.get("/api/subscription")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["current_plan"]["plan"]["name"] == "enterprise"
        assert data["usage"]["analyses_limit"] == -1  # Unlimited


# ─── Usage Check Tests ──────────────────────────────────────────────────────────

class TestCheckUsage:
    """Tests for GET /api/subscription/check/{action} endpoint."""
    
    def test_check_usage_requires_auth(self, client, seed_subscription_plans):
        """Should return 401 without authentication."""
        response = client.get("/api/subscription/check/resume_analysis")
        assert response.status_code == 401
    
    def test_check_usage_allowed_when_under_limit(self, auth_client_with_pro_plan):
        """Should return allowed=True when under limit."""
        response = auth_client_with_pro_plan.get("/api/subscription/check/resume_analysis?quantity=1")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["allowed"] is True
        assert "current_usage" in data
        assert "limit" in data
    
    def test_check_usage_denied_when_at_limit(self, auth_client_at_usage_limit):
        """Should return allowed=False when at limit."""
        response = auth_client_at_usage_limit.get("/api/subscription/check/resume_analysis?quantity=1")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["allowed"] is False
        assert "message" in data
        assert "limit exceeded" in data["message"].lower() or "remaining" in data["message"].lower()
    
    def test_check_batch_usage(self, auth_client_with_pro_plan):
        """Should correctly check batch size."""
        # Pro plan has 100 limit
        response = auth_client_with_pro_plan.get("/api/subscription/check/batch_analysis?quantity=10")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["allowed"] is True
    
    def test_check_batch_denied_when_would_exceed(self, auth_client_with_free_plan):
        """Should deny batch that would exceed limit."""
        # Free plan has 5 limit
        response = auth_client_with_free_plan.get("/api/subscription/check/batch_analysis?quantity=10")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["allowed"] is False


# ─── Usage History Tests ──────────────────────────────────────────────────────────

class TestUsageHistory:
    """Tests for GET /api/subscription/usage-history endpoint."""
    
    def test_get_usage_history_requires_auth(self, client):
        """Should return 401 without authentication."""
        response = client.get("/api/subscription/usage-history")
        assert response.status_code == 401
    
    def test_get_empty_usage_history(self, auth_client_with_pro_plan):
        """Should return empty list when no usage."""
        response = auth_client_with_pro_plan.get("/api/subscription/usage-history")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_get_usage_with_logs(self, auth_client_with_pro_plan, db):
        """Should return usage logs after actions."""
        from app.backend.models.db_models import UsageLog, Tenant, User
        
        # Get tenant and user IDs
        user = db.query(User).filter(User.email == "pro@procorp.com").first()
        tenant = db.query(Tenant).filter(Tenant.slug == "procorp").first()
        
        # Create some usage logs
        for i in range(3):
            log = UsageLog(
                tenant_id=tenant.id,
                user_id=user.id,
                action="resume_analysis",
                quantity=1,
                details=json.dumps({"filename": f"test{i}.pdf"}),
            )
            db.add(log)
        db.commit()
        
        response = auth_client_with_pro_plan.get("/api/subscription/usage-history")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        
        for log in data:
            assert "id" in log
            assert "action" in log
            assert "quantity" in log
            assert "created_at" in log


# ─── Admin Reset Usage Tests ────────────────────────────────────────────────────

class TestAdminResetUsage:
    """Tests for POST /api/subscription/admin/reset-usage endpoint."""
    
    def test_reset_usage_requires_auth(self, client):
        """Should return 401 without authentication (JWT middleware blocks before auth check)."""
        response = client.post("/api/subscription/admin/reset-usage")
        assert response.status_code == 401  # JWT middleware blocks before auth check
    
    def test_reset_usage_works(self, auth_client_at_usage_limit, db):
        """Should reset usage counters to zero."""
        from app.backend.models.db_models import Tenant
        
        # Verify tenant is at limit
        tenant = db.query(Tenant).filter(Tenant.slug == "limitedcorp").first()
        assert tenant.analyses_count_this_month == 5
        
        response = auth_client_at_usage_limit.post("/api/subscription/admin/reset-usage")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["message"] == "Usage counters reset"
        assert data["previous_count"] == 5
        assert data["new_count"] == 0
        
        # Verify in database
        db.refresh(tenant)
        assert tenant.analyses_count_this_month == 0


# ─── Admin Change Plan Tests ────────────────────────────────────────────────────

class TestAdminChangePlan:
    """Tests for POST /api/subscription/admin/change-plan/{plan_id} endpoint."""
    
    def test_change_plan_requires_auth(self, client, seed_subscription_plans):
        """Should return 401 without authentication (JWT middleware blocks before auth check)."""
        pro_plan = 2  # Pro plan ID
        response = client.post(f"/api/subscription/admin/change-plan/{pro_plan}")
        assert response.status_code == 401  # JWT middleware blocks before auth check
    
    def test_change_plan_success(self, auth_client_with_free_plan, db, seed_subscription_plans):
        """Should successfully change subscription plan."""
        from app.backend.models.db_models import Tenant, SubscriptionPlan

        growth_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "growth").first()

        response = auth_client_with_free_plan.post(f"/api/subscription/admin/change-plan/{growth_plan.id}")

        assert response.status_code == 200
        data = response.json()

        assert data["message"] == "Checkout required to activate paid plan"
        assert data["desired_plan"] == "growth"

        tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
        assert tenant.desired_plan_id == growth_plan.id
        starter = db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("starter", "free"))).first()
        assert tenant.plan_id == starter.id

    def test_change_plan_enterprise_blocked(self, auth_client_with_free_plan, db, seed_subscription_plans):
        """Enterprise is sales-led — self-serve switch must be rejected."""
        from app.backend.models.db_models import SubscriptionPlan

        enterprise_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()
        response = auth_client_with_free_plan.post(f"/api/subscription/admin/change-plan/{enterprise_plan.id}")
        assert response.status_code == 400
        assert "sales" in response.json()["detail"].lower()
    
    def test_change_plan_invalid_plan(self, auth_client_with_free_plan):
        """Should return 404 for non-existent plan."""
        response = auth_client_with_free_plan.post("/api/subscription/admin/change-plan/99999")
        
        assert response.status_code == 404


# ─── Usage Reset (Monthly) Tests ──────────────────────────────────────────────────

class TestMonthlyUsageReset:
    """Tests for automatic monthly usage reset functionality."""
    
    def test_usage_reset_on_new_month(self, auth_client_with_pro_plan, db):
        """Should reset usage counters when it's a new month."""
        from app.backend.models.db_models import Tenant
        from app.backend.routes.subscription import _ensure_monthly_reset
        
        tenant = db.query(Tenant).filter(Tenant.slug == "procorp").first()
        
        # Set usage and last reset to previous month
        tenant.analyses_count_this_month = 50
        tenant.usage_reset_at = (datetime.now(timezone.utc) - timedelta(days=35)).replace(day=1)
        db.commit()
        
        # Call reset function
        _ensure_monthly_reset(tenant)
        db.commit()
        
        # Verify reset
        db.refresh(tenant)
        assert tenant.analyses_count_this_month == 0
        assert tenant.usage_reset_at.month == datetime.now(timezone.utc).month
    
    def test_no_reset_within_same_month(self, auth_client_with_pro_plan, db):
        """Should not reset usage within the same month."""
        from app.backend.models.db_models import Tenant
        from app.backend.routes.subscription import _ensure_monthly_reset
        
        tenant = db.query(Tenant).filter(Tenant.slug == "procorp").first()
        
        # Set usage in current month
        tenant.analyses_count_this_month = 25
        tenant.usage_reset_at = datetime.now(timezone.utc).replace(day=1)
        db.commit()
        
        # Call reset function
        _ensure_monthly_reset(tenant)
        db.commit()
        
        # Verify unchanged
        db.refresh(tenant)
        assert tenant.analyses_count_this_month == 25


# ─── Database Model Tests ─────────────────────────────────────────────────────

class TestUsageLogModel:
    """Tests for UsageLog database model."""
    
    def test_create_usage_log(self, auth_client_with_pro_plan, db):
        """Should create usage log entry."""
        from app.backend.models.db_models import UsageLog, Tenant, User
        
        user = db.query(User).filter(User.email == "pro@procorp.com").first()
        tenant = db.query(Tenant).filter(Tenant.slug == "procorp").first()
        
        log = UsageLog(
            tenant_id=tenant.id,
            user_id=user.id,
            action="resume_analysis",
            quantity=1,
            details=json.dumps({"test": True}),
        )
        db.add(log)
        db.commit()
        
        # Verify
        saved_log = db.query(UsageLog).filter(UsageLog.tenant_id == tenant.id).first()
        assert saved_log is not None
        assert saved_log.action == "resume_analysis"
        assert saved_log.quantity == 1
