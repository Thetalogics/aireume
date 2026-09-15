"""Tests for enterprise suite: trials, CRM, branding, OAuth providers, NPS."""
import os
from datetime import datetime, timedelta, timezone

from app.backend.models.db_models import Tenant, User, SubscriptionPlan
from app.backend.services.trial_service import start_trial, expire_trials, is_trial_active, trial_days_remaining
from app.backend.services.crm_service import get_nps_summary


class TestTrialService:
    def test_start_trial_sets_status_and_end_date(self, db, seed_subscription_plans, auth_client):
        tenant = db.query(Tenant).first()
        assert tenant is not None

        start_trial(db, tenant, plan_name="growth", trial_days=14)
        db.commit()
        db.refresh(tenant)

        assert tenant.subscription_status == "trialing"
        assert tenant.trial_ends_at is not None
        assert is_trial_active(tenant) is True
        assert trial_days_remaining(tenant) is not None
        assert trial_days_remaining(tenant) >= 13

    def test_expire_trials_marks_past_due(self, db, seed_subscription_plans):
        from app.backend.models.db_models import SubscriptionPlan
        pro = db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("growth", "pro"))).first()
        tenant = Tenant(
            name="ExpiredTrial",
            slug="expiredtrial",
            subscription_status="trialing",
            trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
            plan_id=pro.id if pro else None,
        )
        db.add(tenant)
        db.commit()

        count = expire_trials(db)
        db.refresh(tenant)
        assert count >= 1
        assert tenant.subscription_status == "expired"


class TestCrmService:
    def test_compute_health_score(self, db, auth_client):
        from app.backend.services.crm_service import compute_health_score
        tenant = db.query(Tenant).first()
        assert tenant is not None
        tenant.onboarding_completed = True
        tenant.subscription_status = "active"
        db.commit()

        result = compute_health_score(db, tenant)
        db.commit()

        assert 0 <= result["health_score"] <= 100
        assert result["churn_risk"] in ("low", "medium", "high")
        assert tenant.health_score == result["health_score"]

    def test_account_notes_via_api(self, platform_admin_client, db):
        tenant = db.query(Tenant).first()
        resp = platform_admin_client.post(
            f"/api/admin/crm/tenants/{tenant.id}/notes",
            json={"body": "Kickoff call completed", "note_type": "general"},
        )
        assert resp.status_code == 200

        notes_resp = platform_admin_client.get(f"/api/admin/crm/tenants/{tenant.id}/notes")
        assert notes_resp.status_code == 200
        notes = notes_resp.json()["notes"]
        assert any(n["body"] == "Kickoff call completed" for n in notes)

    def test_health_overview(self, platform_admin_client):
        resp = platform_admin_client.get("/api/admin/crm/health-overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "tenants" in data
        assert "at_risk_count" in data


class TestBrandingApi:
    def test_get_and_update_branding(self, auth_client, db, seed_subscription_plans):
        from app.backend.models.db_models import Tenant, User
        user = db.query(User).filter(User.email == "test@example.com").first()
        if not user:
            user = db.query(User).first()
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        enterprise = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()
        tenant.plan_id = enterprise.id
        db.commit()

        me = auth_client.get("/api/branding/me")
        assert me.status_code == 200
        assert "branding" in me.json()

        update = auth_client.put("/api/branding/me", json={
            "brand_name": "Acme Hiring",
            "brand_primary_color": "#2563EB",
            "custom_domain": "",
        })
        assert update.status_code == 200
        assert update.json()["branding"]["brand_name"] == "Acme Hiring"

    def test_resolve_branding_unknown_host(self, client):
        resp = client.get("/api/branding/resolve?host=unknown.example.com")
        assert resp.status_code == 200
        assert resp.json()["branding"] is None


class TestOAuthProviders:
    def test_list_providers_public(self, client):
        resp = client.get("/api/auth/oauth/providers")
        assert resp.status_code == 200
        assert "providers" in resp.json()
        assert isinstance(resp.json()["providers"], list)


class TestNpsApi:
    def test_submit_nps(self, auth_client, db):
        resp = auth_client.post("/api/nps", json={"score": 9, "comment": "Great product"})
        assert resp.status_code == 200

        tenant = db.query(Tenant).first()
        summary = get_nps_summary(db, tenant.id)
        assert summary["count"] >= 1
        assert summary["average"] is not None


class TestE2EVerifyEndpoint:
    def test_verify_email_test_helper(self, client, db):
        os.environ["TESTING"] = "1"
        tenant = Tenant(name="VerifyCo", slug="verifyco")
        db.add(tenant)
        db.commit()
        user = User(
            email="e2ehelper@example.com",
            hashed_password="x",
            tenant_id=tenant.id,
            email_verified=False,
            is_active=True,
            role="admin",
        )
        db.add(user)
        db.commit()

        resp = client.post("/api/auth/test/verify-email", json={"email": "e2ehelper@example.com"})
        assert resp.status_code == 200
        db.refresh(user)
        assert user.email_verified is True
