"""Tests for the billing system — provider abstraction, factory, routes, admin endpoints."""
import json
import pytest
from unittest.mock import patch

from app.backend.models.db_models import PlatformConfig, Tenant
from app.backend.tests.test_helpers import _verify_user_via_api


# ─── Unit tests: ManualProvider ───────────────────────────────────────────────

def test_manual_provider_create_checkout_returns_reference(db):
    """ManualProvider.create_checkout_session returns a reference_id."""
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = ManualProvider(db=db)
    result = provider.create_checkout_session(
        tenant_id=1, plan="pro", success_url="http://x", cancel_url="http://y"
    )
    assert "reference_id" in result
    assert result["reference_id"].startswith("manual_")
    assert result["provider"] == "manual"
    assert result["plan"] == "pro"


def test_manual_provider_cancel_subscription(db):
    """ManualProvider.cancel_subscription marks subscription as cancelled."""
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = ManualProvider(db=db)
    result = provider.cancel_subscription(tenant_id=1, subscription_id="sub_123")
    assert result["status"] == "cancelled"
    assert result["provider"] == "manual"


def test_manual_provider_cancel_updates_tenant_in_db(client, db):
    """ManualProvider.cancel_subscription updates tenant.subscription_status in DB."""
    from app.backend.services.billing.manual_provider import ManualProvider
    # Register a tenant through the auth flow
    reg = client.post("/api/auth/register", json={
        "company_name": "CancelCorp", "email": "cancel@cancelcorp.com",
        "password": "CancelPass123!", "full_name": "Cancel User",
    })
    assert reg.status_code in (200, 201)

    _verify_user_via_api("cancel@cancelcorp.com")

    tenant = db.query(Tenant).filter(Tenant.slug == "cancelcorp").first()
    assert tenant is not None

    provider = ManualProvider(db=db)
    provider.cancel_subscription(tenant_id=tenant.id, subscription_id="sub_x")
    db.refresh(tenant)
    assert tenant.subscription_status == "cancelled"


def test_manual_provider_get_subscription_status(db):
    """ManualProvider.get_subscription_status returns tenant status from DB."""
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = ManualProvider(db=db)
    result = provider.get_subscription_status(tenant_id=9999, subscription_id="sub_1")
    assert result["status"] == "unknown"
    assert result["provider"] == "manual"


def test_manual_provider_webhook_is_noop(db):
    """ManualProvider.handle_webhook_event parses the event payload."""
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = ManualProvider(db=db)
    result = provider.handle_webhook_event(b"{}", "")
    assert result["event_type"] == "unknown"  # empty payload → no event key
    assert result["provider"] == "manual"


def test_manual_provider_provider_name(db):
    """ManualProvider.provider_name returns 'manual'."""
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = ManualProvider(db=db)
    assert provider.provider_name == "manual"


# ─── Unit tests: Factory ─────────────────────────────────────────────────────

def test_factory_returns_manual_by_default(db):
    """get_payment_provider returns ManualProvider when no config is set."""
    from app.backend.services.billing.factory import get_payment_provider
    from app.backend.services.billing.manual_provider import ManualProvider
    provider = get_payment_provider(db)
    assert isinstance(provider, ManualProvider)


def test_factory_returns_stripe_when_configured(db):
    """get_payment_provider returns StripeProvider when configured."""
    from app.backend.services.billing.factory import get_payment_provider
    from app.backend.services.billing.stripe_provider import StripeProvider

    db.add(PlatformConfig(config_key="billing.active_provider", config_value="stripe"))
    db.add(PlatformConfig(config_key="billing.stripe.api_key", config_value="sk_test_123"))
    db.add(PlatformConfig(config_key="billing.stripe.webhook_secret", config_value="whsec_abc"))
    db.commit()

    provider = get_payment_provider(db)
    assert isinstance(provider, StripeProvider)


def test_factory_returns_razorpay_when_configured(db):
    """get_payment_provider returns RazorpayProvider when configured."""
    from app.backend.services.billing.factory import get_payment_provider
    from app.backend.services.billing.razorpay_provider import RazorpayProvider

    db.add(PlatformConfig(config_key="billing.active_provider", config_value="razorpay"))
    db.add(PlatformConfig(config_key="billing.razorpay.key_id", config_value="rp_key"))
    db.add(PlatformConfig(config_key="billing.razorpay.key_secret", config_value="rp_secret"))
    db.add(PlatformConfig(config_key="billing.razorpay.webhook_secret", config_value="rp_wh"))
    db.commit()

    provider = get_payment_provider(db)
    assert isinstance(provider, RazorpayProvider)


def test_factory_falls_back_to_manual_for_unknown_provider(db):
    """get_payment_provider falls back to ManualProvider for unknown provider."""
    from app.backend.services.billing.factory import get_payment_provider
    from app.backend.services.billing.manual_provider import ManualProvider

    db.add(PlatformConfig(config_key="billing.active_provider", config_value="unknown_provider"))
    db.commit()

    provider = get_payment_provider(db)
    assert isinstance(provider, ManualProvider)


# ─── Integration tests: Admin billing endpoints ──────────────────────────────

def test_admin_get_billing_config(platform_admin_client, db):
    """GET /api/admin/billing/config returns default config."""
    resp = platform_admin_client.get("/api/admin/billing/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_provider"] == "manual"
    assert isinstance(data["configs"], list)


def test_admin_put_billing_config_sets_provider(platform_admin_client, db):
    """PUT /api/admin/billing/config sets active provider and persists."""
    resp = platform_admin_client.put("/api/admin/billing/config", json={
        "active_provider": "manual",
        "configs": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_provider"] == "manual"

    # Verify persisted
    row = db.query(PlatformConfig).filter(
        PlatformConfig.config_key == "billing.active_provider"
    ).first()
    assert row is not None
    assert row.config_value == "manual"


def test_admin_put_billing_config_with_stripe_credentials(platform_admin_client, db):
    """PUT /api/admin/billing/config stores Stripe credentials (masked on GET)."""
    # Set Stripe config
    resp = platform_admin_client.put("/api/admin/billing/config", json={
        "active_provider": "stripe",
        "configs": {
            "billing.stripe.api_key": "sk_test_abcdef1234567890",
            "billing.stripe.webhook_secret": "whsec_supersecret123",
        },
    })
    assert resp.status_code == 200

    # GET should show masked values
    resp = platform_admin_client.get("/api/admin/billing/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_provider"] == "stripe"

    # Find the api_key config item
    api_key_item = next(
        (c for c in data["configs"] if c["key"] == "billing.stripe.api_key"), None
    )
    assert api_key_item is not None
    # Should be masked — last 4 chars visible
    assert api_key_item["value"].endswith("7890")
    assert "*" in api_key_item["value"]


def test_admin_get_billing_providers_lists_all_three(platform_admin_client, db):
    """GET /api/admin/billing/providers lists all three providers."""
    resp = platform_admin_client.get("/api/admin/billing/providers")
    assert resp.status_code == 200
    data = resp.json()
    provider_names = [p["name"] for p in data]
    assert "stripe" in provider_names
    assert "razorpay" in provider_names
    assert "manual" in provider_names


def test_admin_put_billing_config_rejects_unknown_provider(platform_admin_client, db):
    """PUT /api/admin/billing/config rejects unknown provider name."""
    resp = platform_admin_client.put("/api/admin/billing/config", json={
        "active_provider": "nonexistent",
        "configs": {},
    })
    assert resp.status_code == 400


def test_non_admin_cannot_access_billing_config(client, db):
    """Non-admin users get 403 on admin billing endpoints."""
    # Register a normal user
    client.post("/api/auth/register", json={
        "company_name": "NormalCorp", "email": "normal@normalcorp.com",
        "password": "NormalPass123!", "full_name": "Normal User",
    })
    _verify_user_via_api("normal@normalcorp.com")
    login = client.post("/api/auth/login", json={
        "email": "normal@normalcorp.com",
        "password": "NormalPass123!",
    })
    token = (login.cookies.get("access_token") or login.json().get("access_token"))
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = client.get("/api/admin/billing/config")
    assert resp.status_code == 403


# ─── Integration tests: Billing routes ────────────────────────────────────────

def test_billing_checkout_creates_session(auth_client, db):
    """POST /api/billing/checkout creates a checkout session."""
    resp = auth_client.post("/api/billing/checkout", json={
        "plan": "pro",
        "success_url": "http://localhost/success",
        "cancel_url": "http://localhost/cancel",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "manual"
    assert "reference_id" in data


def test_billing_webhook_handles_events(client, db):
    """POST /api/billing/webhook handles events (no auth required).

    The endpoint always returns 200 with {received: true} to prevent
    provider-side retries.  Actual processing happens internally.
    """
    resp = client.post(
        "/api/billing/webhook",
        content=b'{"test": true}',
        headers={"X-Signature": "sig_123", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] is True


def test_billing_get_subscription_status(auth_client, db):
    """GET /api/billing/subscription/{tenant_id} returns subscription status."""
    # Get the tenant id for the auth_client user
    from app.backend.models.db_models import User
    user = db.query(User).filter(User.email == "admin@testcorp.com").first()
    tenant_id = user.tenant_id

    resp = auth_client.get(f"/api/billing/subscription/{tenant_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["provider"] == "manual"


def test_billing_cancel_subscription(auth_client, db):
    """POST /api/billing/cancel/{tenant_id} cancels subscription."""
    from app.backend.models.db_models import User
    user = db.query(User).filter(User.email == "admin@testcorp.com").first()
    tenant_id = user.tenant_id

    resp = auth_client.post(f"/api/billing/cancel/{tenant_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"


def test_billing_subscription_denied_for_other_tenant(auth_client, db):
    """GET /api/billing/subscription/{other_tenant_id} returns 403."""
    resp = auth_client.get("/api/billing/subscription/9999")
    assert resp.status_code == 403


def test_billing_unauthenticated_checkout_fails(client, db):
    """POST /api/billing/checkout without auth returns 401."""
    resp = client.post("/api/billing/checkout", json={
        "plan": "pro", "success_url": "", "cancel_url": "",
    })
    assert resp.status_code in (401, 403)


# ─── Unit tests: StripeProvider (no real API calls) ──────────────────────────

def test_stripe_provider_name():
    """StripeProvider.provider_name returns 'stripe'."""
    from app.backend.services.billing.stripe_provider import StripeProvider
    provider = StripeProvider(api_key="sk_test_x")
    assert provider.provider_name == "stripe"


def test_stripe_provider_raises_when_stripe_not_installed(db):
    """StripeProvider raises RuntimeError when stripe package is missing."""
    from app.backend.services.billing.stripe_provider import StripeProvider
    provider = StripeProvider(api_key="sk_test_x")
    with patch("app.backend.services.billing.stripe_provider.stripe", None):
        with pytest.raises(RuntimeError, match="stripe"):
            provider.create_checkout_session(
                tenant_id=1, plan="pro", success_url="", cancel_url="", price_id="price_test"
            )


def test_stripe_checkout_uses_mapped_price_and_plan_metadata():
    from types import SimpleNamespace
    from app.backend.services.billing.stripe_provider import StripeProvider

    captured = {}

    class FakeCheckout:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_test", url="https://checkout.test")

    fake_stripe = SimpleNamespace(checkout=SimpleNamespace(Session=FakeCheckout))
    provider = StripeProvider(api_key="sk_test_x")
    with patch("app.backend.services.billing.stripe_provider.stripe", fake_stripe):
        result = provider.create_checkout_session(
            tenant_id=9,
            plan="growth",
            success_url="https://ok",
            cancel_url="https://no",
            price_id="price_growth_123",
            extra_metadata={"plan_id": "42", "tenant_id": "9"},
        )
    assert captured["line_items"][0]["price"] == "price_growth_123"
    assert captured["metadata"]["plan_id"] == "42"
    assert result["session_id"] == "cs_test"


# ─── Unit tests: RazorpayProvider (no real API calls) ────────────────────────

def test_razorpay_provider_name():
    """RazorpayProvider.provider_name returns 'razorpay'."""
    from app.backend.services.billing.razorpay_provider import RazorpayProvider
    provider = RazorpayProvider(key_id="rp_id", key_secret="rp_secret")
    assert provider.provider_name == "razorpay"


def test_razorpay_provider_raises_when_not_installed():
    """RazorpayProvider raises RuntimeError when razorpay package is missing."""
    from app.backend.services.billing.razorpay_provider import RazorpayProvider
    provider = RazorpayProvider(key_id="", key_secret="")
    with patch("app.backend.services.billing.razorpay_provider.razorpay", None):
        with pytest.raises(RuntimeError, match="razorpay"):
            provider.create_checkout_session(
                tenant_id=1, plan="pro", success_url="", cancel_url=""
            )
