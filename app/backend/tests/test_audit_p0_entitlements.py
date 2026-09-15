"""AUD-002 / AUD-003: paid entitlement requires verified checkout/trial state."""
from datetime import datetime, timedelta, timezone

from app.backend.models.db_models import AuditLog, SubscriptionPlan, Tenant, User
from app.backend.routes.auth import _hash_password
from app.backend.services.plan_entitlement_service import get_tenant_plan, tenant_has_feature
from app.backend.services.trial_service import expire_trials, is_trial_active, start_trial


def _growth(db):
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "growth").first()


def _starter(db):
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("starter", "free"))).first()


def _login_recruiter(client, db, tenant_id, email="recruiter-entitlement@freecorp.com"):
    user = User(
        tenant_id=tenant_id,
        email=email,
        hashed_password=_hash_password("RecruiterPass123!"),
        role="recruiter",
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": "RecruiterPass123!"})
    assert login.status_code == 200, login.text
    token = login.cookies.get("access_token") or login.json().get("access_token")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return user


def test_recruiter_cannot_select_paid_plan_for_tenant(auth_client_with_free_plan, db, seed_subscription_plans):
    tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
    _login_recruiter(auth_client_with_free_plan, db, tenant.id)
    growth = _growth(db)
    resp = auth_client_with_free_plan.post("/api/onboarding/select-plan", json={"plan_id": growth.id})
    assert resp.status_code == 403
    db.refresh(tenant)
    starter = _starter(db)
    assert tenant.plan_id == starter.id


def test_tenant_admin_plan_selection_does_not_activate_paid_entitlement_without_checkout(
    auth_client_with_free_plan, db, seed_subscription_plans
):
    tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
    starter = _starter(db)
    growth = _growth(db)
    resp = auth_client_with_free_plan.post("/api/onboarding/select-plan", json={"plan_id": growth.id})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("checkout") or data.get("checkout_url") or data.get("reference_id")
    db.refresh(tenant)
    assert tenant.plan_id == starter.id
    assert tenant.desired_plan_id == growth.id
    assert tenant.subscription_status != "trialing"
    effective = get_tenant_plan(db, tenant.id)
    assert effective.name in ("starter", "free")
    assert tenant_has_feature(db, tenant.id, "requisitions") is False


def test_direct_subscription_change_requires_platform_admin(
    auth_client_with_free_plan, db, seed_subscription_plans
):
    tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
    starter = _starter(db)
    growth = _growth(db)
    resp = auth_client_with_free_plan.post(f"/api/subscription/admin/change-plan/{growth.id}")
    assert resp.status_code == 200, resp.text
    db.refresh(tenant)
    assert tenant.plan_id == starter.id
    assert tenant.desired_plan_id == growth.id
    assert tenant.subscription_status != "active" or tenant.plan_id == starter.id
    effective = get_tenant_plan(db, tenant.id)
    assert effective.id == starter.id


def test_platform_admin_manual_override_is_audited(platform_admin_client_with_plans, db, seed_subscription_plans):
    tenant = db.query(Tenant).filter(Tenant.slug == "testcorp").first()
    if tenant is None:
        tenant = db.query(Tenant).first()
    growth = _growth(db)
    resp = platform_admin_client_with_plans.post(
        f"/api/admin/tenants/{tenant.id}/change-plan",
        json={"plan_id": growth.id, "reason": "support ticket T-100"},
    )
    assert resp.status_code == 200, resp.text
    db.refresh(tenant)
    assert tenant.plan_id == growth.id
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.resource_id == tenant.id, AuditLog.action == "tenant.change_plan")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert "T-100" in (audit.details or "")


def test_free_plan_selection_remains_functional(auth_client_with_free_plan, db, seed_subscription_plans):
    starter = _starter(db)
    resp = auth_client_with_free_plan.post("/api/onboarding/select-plan", json={"plan_id": starter.id})
    assert resp.status_code == 200, resp.text
    tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
    assert tenant.plan_id == starter.id
    assert get_tenant_plan(db, tenant.id).id == starter.id


def test_paid_feature_gate_denies_when_desired_plan_is_paid_but_provider_state_is_not_active(
    db, seed_subscription_plans
):
    starter = _starter(db)
    growth = _growth(db)
    tenant = Tenant(
        name="DesiredPaid",
        slug="desiredpaid",
        plan_id=starter.id,
        desired_plan_id=growth.id,
        subscription_status="active",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    assert get_tenant_plan(db, tenant.id).id == starter.id
    assert tenant_has_feature(db, tenant.id, "requisitions") is False


def test_active_trial_has_paid_features_before_expiry(db, seed_subscription_plans):
    tenant = Tenant(name="TrialLive", slug="triallive", subscription_status="active")
    db.add(tenant)
    db.commit()
    start_trial(db, tenant, plan_name="growth", trial_days=14)
    db.commit()
    db.refresh(tenant)
    assert is_trial_active(tenant)
    plan = get_tenant_plan(db, tenant.id)
    assert plan.name == "growth"
    assert tenant_has_feature(db, tenant.id, "requisitions") is True


def test_expired_unconverted_trial_loses_paid_features_immediately(db, seed_subscription_plans):
    growth = _growth(db)
    tenant = Tenant(
        name="TrialDead",
        slug="trialdead",
        plan_id=growth.id,
        subscription_status="trialing",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(tenant)
    db.commit()
    count = expire_trials(db)
    db.refresh(tenant)
    assert count >= 1
    assert tenant.subscription_status == "expired"
    effective = get_tenant_plan(db, tenant.id)
    assert effective.name in ("starter", "free")
    assert tenant_has_feature(db, tenant.id, "requisitions") is False


def test_trial_conversion_to_paid_remains_active_after_original_trial_end(db, seed_subscription_plans):
    tenant = Tenant(name="Converted", slug="converted", subscription_status="active")
    db.add(tenant)
    db.commit()
    start_trial(db, tenant, plan_name="growth", trial_days=14)
    db.commit()
    tenant.subscription_status = "active"
    tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    expire_trials(db)
    db.refresh(tenant)
    assert tenant.subscription_status == "active"
    assert get_tenant_plan(db, tenant.id).name == "growth"


def test_expire_trials_is_idempotent(db, seed_subscription_plans):
    growth = _growth(db)
    tenant = Tenant(
        name="IdemTrial",
        slug="idemtrial",
        plan_id=growth.id,
        subscription_status="trialing",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db.add(tenant)
    db.commit()
    first = expire_trials(db)
    second = expire_trials(db)
    assert first >= 1
    assert second == 0


def test_trial_expiry_does_not_start_normal_dunning_unless_a_real_payment_subscription_exists(
    db, seed_subscription_plans
):
    growth = _growth(db)
    tenant = Tenant(
        name="NoDunning",
        slug="nodunning",
        plan_id=growth.id,
        subscription_status="trialing",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        stripe_subscription_id=None,
    )
    db.add(tenant)
    db.commit()
    expire_trials(db)
    db.refresh(tenant)
    assert tenant.subscription_status == "expired"
    assert tenant.subscription_status != "past_due"


def test_checkout_webhook_applies_desired_paid_plan(db, seed_subscription_plans):
    from app.backend.services.billing.webhook_processor import _handle_stripe_checkout_completed

    starter = _starter(db)
    growth = _growth(db)
    tenant = Tenant(
        name="CheckoutApply",
        slug="checkout-apply",
        plan_id=starter.id,
        desired_plan_id=growth.id,
        subscription_status="incomplete",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    _handle_stripe_checkout_completed(
        db,
        {
            "object": {
                "metadata": {"tenant_id": str(tenant.id)},
                "customer": "cus_phase_a",
                "subscription": "sub_phase_a",
            }
        },
        "{}",
    )
    db.refresh(tenant)
    assert tenant.plan_id == growth.id
    assert tenant.subscription_status == "active"
    assert tenant_has_feature(db, tenant.id, "requisitions") is True
