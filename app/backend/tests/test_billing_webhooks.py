"""Tests for billing webhook processing — signature verification, event handling, audit logging."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.backend.models.db_models import BillingEvent, Tenant, User
from app.backend.services.billing.webhook_processor import process_webhook_event


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_tenant(db, **overrides):
    """Create and return a Tenant row with sensible defaults."""
    defaults = {
        "name": "Test Tenant",
        "slug": f"test-{int(time.time()*1000)}",
        "subscription_status": "active",
    }
    defaults.update(overrides)
    tenant = Tenant(**defaults)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _make_user(db, tenant_id):
    """Create and return a User row for the given tenant."""
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
    user = User(
        tenant_id=tenant_id,
        email=f"test-{int(time.time()*1000)}@example.com",
        hashed_password=pwd_context.hash("test"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _sign_payload(payload_str: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for manual provider webhook."""
    return hmac.new(
        secret.encode(), payload_str.encode(), hashlib.sha256
    ).hexdigest()


# ─── Stripe webhook handler tests ───────────────────────────────────────────

class TestStripeWebhookHandlers:
    """Tests for Stripe event processing via process_webhook_event."""

    def test_invoice_paid_sets_active(self, db):
        """invoice.paid → subscription_status = active with period dates."""
        tenant = _make_tenant(db, stripe_customer_id="cus_abc123", subscription_status="past_due")
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "object": {
                "customer": "cus_abc123",
                "subscription": "sub_xyz",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"
        assert tenant.current_period_end is not None
        assert tenant.subscription_updated_at is not None

        # Verify audit log
        evt = db.query(BillingEvent).filter(
            BillingEvent.tenant_id == tenant.id,
            BillingEvent.event_type == "invoice.paid",
        ).first()
        assert evt is not None
        assert evt.result == "success"

    def test_invoice_payment_failed_sets_past_due(self, db):
        """invoice.payment_failed → subscription_status = past_due."""
        tenant = _make_tenant(db, stripe_customer_id="cus_fail", subscription_status="active")

        data = {
            "object": {
                "customer": "cus_fail",
                "subscription": "sub_fail",
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.payment_failed",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "past_due"

    def test_subscription_updated_maps_status(self, db):
        """customer.subscription.updated → maps Stripe status to our status."""
        tenant = _make_tenant(
            db, stripe_subscription_id="sub_update", subscription_status="active"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "object": {
                "id": "sub_update",
                "status": "past_due",
                "current_period_start": now_ts - 2592000,
                "current_period_end": now_ts,
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="customer.subscription.updated",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "past_due"

    def test_subscription_deleted_sets_cancelled(self, db):
        """customer.subscription.deleted → subscription_status = cancelled, dates cleared."""
        tenant = _make_tenant(
            db,
            stripe_subscription_id="sub_delete",
            subscription_status="active",
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

        data = {"object": {"id": "sub_delete"}}
        result = process_webhook_event(
            db, provider="stripe", event_type="customer.subscription.deleted",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "cancelled"
        assert tenant.current_period_start is None
        assert tenant.current_period_end is None

    def test_invoice_paid_lookup_by_subscription_id(self, db):
        """invoice.paid falls back to subscription_id when customer_id has no match."""
        tenant = _make_tenant(
            db, stripe_subscription_id="sub_fallback", subscription_status="trialing"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "object": {
                "customer": "cus_nonexistent",
                "subscription": "sub_fallback",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"

    def test_subscription_updated_trialed_status(self, db):
        """customer.subscription.updated maps 'trialing' correctly."""
        tenant = _make_tenant(
            db, stripe_subscription_id="sub_trial", subscription_status="active"
        )
        data = {
            "object": {
                "id": "sub_trial",
                "status": "trialing",
            }
        }
        process_webhook_event(
            db, provider="stripe", event_type="customer.subscription.updated",
            data=data, raw_payload=json.dumps(data),
        )
        db.refresh(tenant)
        assert tenant.subscription_status == "trialing"


# ─── Razorpay webhook handler tests ────────────────────────────────────────

class TestRazorpayWebhookHandlers:
    """Tests for Razorpay event processing."""

    def test_subscription_activated_sets_active(self, db):
        """subscription.activated → subscription_status = active."""
        tenant = _make_tenant(
            db, stripe_subscription_id="razorpay_sub_1", subscription_status="past_due"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "subscription": {
                "id": "razorpay_sub_1",
                "current_start": now_ts - 2592000,
                "current_end": now_ts,
            }
        }
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.activated",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"
        assert tenant.current_period_end is not None

    def test_subscription_charged_updates_period(self, db):
        """subscription.charged → status = active, period dates updated."""
        tenant = _make_tenant(
            db, stripe_subscription_id="razorpay_sub_2", subscription_status="active"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "subscription": {
                "id": "razorpay_sub_2",
                "current_start": now_ts - 2592000,
                "current_end": now_ts,
            }
        }
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.charged",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"

    def test_subscription_pending_sets_past_due(self, db):
        """subscription.pending → subscription_status = past_due."""
        tenant = _make_tenant(
            db, stripe_subscription_id="razorpay_sub_3", subscription_status="active"
        )

        data = {"subscription": {"id": "razorpay_sub_3"}}
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.pending",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "past_due"

    def test_subscription_cancelled_sets_cancelled(self, db):
        """subscription.cancelled → subscription_status = cancelled."""
        tenant = _make_tenant(
            db, stripe_subscription_id="razorpay_sub_4", subscription_status="active"
        )

        data = {"subscription": {"id": "razorpay_sub_4"}}
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.cancelled",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "cancelled"
        assert tenant.current_period_start is None
        assert tenant.current_period_end is None


# ─── Manual provider webhook handler tests ─────────────────────────────────

class TestManualWebhookHandlers:
    """Tests for manual provider event processing."""

    def test_payment_approved_sets_active(self, db):
        """payment.approved → subscription_status = active with period dates."""
        tenant = _make_tenant(db, subscription_status="past_due")
        now = datetime.now(timezone.utc)

        data = {
            "tenant_id": tenant.id,
            "period_start": now.isoformat(),
            "period_end": (now + timedelta(days=30)).isoformat(),
        }
        result = process_webhook_event(
            db, provider="manual", event_type="payment.approved",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"
        assert tenant.current_period_start is not None
        assert tenant.current_period_end is not None

    def test_payment_rejected_sets_past_due(self, db):
        """payment.rejected → subscription_status = past_due."""
        tenant = _make_tenant(db, subscription_status="active")

        data = {"tenant_id": tenant.id}
        result = process_webhook_event(
            db, provider="manual", event_type="payment.rejected",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "past_due"

    def test_payment_approved_default_period_dates(self, db):
        """payment.approved sets default period dates when not provided."""
        tenant = _make_tenant(db, subscription_status="trialing")

        data = {"tenant_id": tenant.id}
        result = process_webhook_event(
            db, provider="manual", event_type="payment.approved",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        db.refresh(tenant)
        assert tenant.subscription_status == "active"
        assert tenant.current_period_start is not None
        assert tenant.current_period_end is not None
        # Default end date should be ~30 days from start
        delta = tenant.current_period_end - tenant.current_period_start
        assert 29 <= delta.days <= 31


# ─── Unknown / edge case tests ─────────────────────────────────────────────

class TestWebhookEdgeCases:
    """Tests for unknown events, missing tenants, and error handling."""

    def test_unknown_event_type_ignored_gracefully(self, db):
        """Unknown event types are logged and ignored without error."""
        result = process_webhook_event(
            db, provider="stripe", event_type="plan.created",
            data={}, raw_payload="{}",
        )
        assert result["processed"] is False
        assert result["reason"] == "ignored"

        # Verify audit log entry
        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "plan.created",
        ).first()
        assert evt is not None
        assert evt.result == "ignored"

    def test_unknown_provider_event_ignored(self, db):
        """Unknown provider/event combinations are ignored."""
        result = process_webhook_event(
            db, provider="paypal", event_type="payment.completed",
            data={}, raw_payload="{}",
        )
        assert result["processed"] is False
        assert result["reason"] == "ignored"

    def test_invalid_tenant_mapping_returns_success_logs_error(self, db):
        """When no tenant is found, the result is still processed but error is logged."""
        data = {
            "object": {
                "customer": "cus_nonexistent",
                "subscription": "sub_nonexistent",
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        # Handler logs an error on the claimed event and returns without raising.
        # process_webhook_event must not mark that event successful.
        assert result["processed"] is False
        assert result["reason"] == "error"

        # Verify error is logged in audit
        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "invoice.paid",
            BillingEvent.result == "error",
        ).first()
        assert evt is not None
        assert "No tenant found" in evt.error_detail

    def test_razorpay_missing_tenant_logs_error(self, db):
        """Razorpay event with no matching tenant logs error."""
        data = {"subscription": {"id": "nonexistent_sub"}}
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.activated",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is False
        assert result["reason"] == "error"

        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "subscription.activated",
            BillingEvent.result == "error",
        ).first()
        assert evt is not None

    def test_manual_missing_tenant_logs_error(self, db):
        """Manual event with invalid tenant_id logs error."""
        data = {"tenant_id": 99999}
        result = process_webhook_event(
            db, provider="manual", event_type="payment.approved",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is False
        assert result["reason"] == "error"

        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "payment.approved",
            BillingEvent.result == "error",
        ).first()
        assert evt is not None


# ─── Manual provider signature verification tests ──────────────────────────

class TestManualProviderSignatureVerification:
    """Tests for ManualProvider webhook signature validation."""

    def test_valid_signature_passes(self, db):
        """Manual provider accepts payload with valid HMAC signature."""
        from app.backend.services.billing.manual_provider import ManualProvider
        secret = "test_secret_123"
        provider = ManualProvider(db=db, webhook_secret=secret)

        payload = json.dumps({
            "event": "payment.approved",
            "payload": {"tenant_id": 1},
        })
        signature = _sign_payload(payload, secret)

        result = provider.handle_webhook_event(payload.encode(), signature)
        assert result["event_type"] == "payment.approved"
        assert result["provider"] == "manual"

    def test_invalid_signature_raises(self, db):
        """Manual provider rejects payload with invalid signature."""
        from app.backend.services.billing.manual_provider import ManualProvider
        secret = "test_secret_123"
        provider = ManualProvider(db=db, webhook_secret=secret)

        payload = json.dumps({
            "event": "payment.approved",
            "payload": {"tenant_id": 1},
        })

        with pytest.raises(ValueError, match="Invalid webhook signature"):
            provider.handle_webhook_event(payload.encode(), "invalid_sig")

    def test_no_secret_skips_validation(self, db):
        """When no webhook_secret is set, signature validation is skipped."""
        from app.backend.services.billing.manual_provider import ManualProvider
        provider = ManualProvider(db=db, webhook_secret="")

        payload = json.dumps({
            "event": "payment.approved",
            "payload": {"tenant_id": 1},
        })
        # Any signature should be accepted when secret is empty
        result = provider.handle_webhook_event(payload.encode(), "anything")
        assert result["event_type"] == "payment.approved"


# ─── Stripe/Razorpay provider signature verification (mocked) ──────────────

class TestProviderSignatureVerification:
    """Tests for Stripe and Razorpay provider signature verification using mocks."""

    def test_stripe_webhook_verifies_signature(self):
        """Stripe provider calls stripe.Webhook.construct_event for verification."""
        from app.backend.services.billing.stripe_provider import StripeProvider
        provider = StripeProvider(api_key="sk_test_123", webhook_secret="whsec_abc")

        with patch("app.backend.services.billing.stripe_provider.stripe") as mock_stripe:
            mock_stripe.Webhook.construct_event.return_value = {
                "type": "invoice.paid",
                "data": {"object": {"customer": "cus_123"}},
            }
            result = provider.handle_webhook_event(b"test_payload", "sig_123")
            mock_stripe.Webhook.construct_event.assert_called_once_with(
                b"test_payload", "sig_123", "whsec_abc"
            )
            assert result["event_type"] == "invoice.paid"

    def test_razorpay_webhook_verifies_signature(self):
        """Razorpay provider verifies webhook signature via utility."""
        from app.backend.services.billing.razorpay_provider import RazorpayProvider
        provider = RazorpayProvider(
            key_id="key_123", key_secret="secret_123", webhook_secret="whsec_abc"
        )

        payload = json.dumps({
            "event": "subscription.activated",
            "payload": {"subscription": {"id": "sub_1"}},
        })

        # Mock the client and its utility method
        mock_utility = MagicMock()
        mock_utility.verify_webhook_signature = MagicMock()
        mock_client = MagicMock()
        mock_client.utility = mock_utility
        provider._client = mock_client

        # Patch _require_client since razorpay SDK is not installed
        with patch.object(provider, "_require_client"):
            result = provider.handle_webhook_event(payload.encode(), "sig_123")
        mock_utility.verify_webhook_signature.assert_called_once()
        assert result["event_type"] == "subscription.activated"


# ─── BillingEvent audit log tests ──────────────────────────────────────────

class TestBillingEventAuditLog:
    """Tests for BillingEvent audit logging."""

    def test_billing_event_created_on_success(self, db):
        """Successful event processing creates a BillingEvent with result=success."""
        tenant = _make_tenant(db, stripe_customer_id="cus_audit")
        data = {"object": {"customer": "cus_audit"}}
        process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        evt = db.query(BillingEvent).filter(
            BillingEvent.provider == "stripe",
            BillingEvent.event_type == "invoice.paid",
        ).first()
        assert evt is not None
        assert evt.tenant_id == tenant.id
        assert evt.result == "success"
        assert evt.raw_payload is not None
        assert evt.processed_at is not None

    def test_billing_event_created_on_error(self, db):
        """Error scenarios create a BillingEvent with result=error."""
        data = {"object": {"customer": "cus_nonexistent_audit"}}
        process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "invoice.paid",
            BillingEvent.result == "error",
        ).first()
        assert evt is not None
        assert evt.error_detail is not None

    def test_billing_event_created_on_ignored(self, db):
        """Ignored (unknown) event types create a BillingEvent with result=ignored."""
        process_webhook_event(
            db, provider="stripe", event_type="unknown.event",
            data={}, raw_payload="{}",
        )
        evt = db.query(BillingEvent).filter(
            BillingEvent.event_type == "unknown.event",
            BillingEvent.result == "ignored",
        ).first()
        assert evt is not None


# ─── subscription.changed webhook dispatch tests ───────────────────────────

class TestSubscriptionChangedWebhook:
    """Tests that subscription.changed webhook is fired on status changes."""

    @patch("app.backend.services.billing.webhook_processor.dispatch_event_background")
    def test_stripe_invoice_paid_fires_webhook(self, mock_dispatch, db):
        """Status change from past_due to active fires subscription.changed."""
        tenant = _make_tenant(
            db, stripe_customer_id="cus_webhook", subscription_status="past_due"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())
        data = {
            "object": {
                "customer": "cus_webhook",
                "subscription": "sub_wh",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
            }
        }
        process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args
        assert call_kwargs[1]["event"] == "subscription.changed" or call_kwargs[0][2] == "subscription.changed"

    @patch("app.backend.services.billing.webhook_processor.dispatch_event_background")
    def test_no_webhook_when_status_unchanged(self, mock_dispatch, db):
        """No subscription.changed webhook when status stays the same."""
        tenant = _make_tenant(
            db, stripe_customer_id="cus_same", subscription_status="active"
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())
        data = {
            "object": {
                "customer": "cus_same",
                "subscription": "sub_same",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
            }
        }
        process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        mock_dispatch.assert_not_called()

    @patch("app.backend.services.billing.webhook_processor.dispatch_event_background")
    def test_razorpay_cancelled_fires_webhook(self, mock_dispatch, db):
        """Razorpay subscription.cancelled fires subscription.changed."""
        tenant = _make_tenant(
            db, stripe_subscription_id="razor_sub_wh", subscription_status="active"
        )
        data = {"subscription": {"id": "razor_sub_wh"}}
        process_webhook_event(
            db, provider="razorpay", event_type="subscription.cancelled",
            data=data, raw_payload=json.dumps(data),
        )
        mock_dispatch.assert_called_once()


# ─── Route-level integration tests ─────────────────────────────────────────

class TestWebhookRoute:
    """Tests for the POST /api/billing/webhook endpoint."""

    def test_webhook_returns_200_on_success(self, client, db):
        """Webhook endpoint always returns 200, even when using manual provider."""
        # Ensure manual provider is active
        db.query(PlatformConfig).filter(
            PlatformConfig.config_key == "billing.active_provider"
        ).delete()
        db.add(PlatformConfig(
            config_key="billing.active_provider",
            config_value="manual",
        ))
        db.commit()

        payload = json.dumps({
            "event": "payment.approved",
            "payload": {"tenant_id": 9999},  # nonexistent tenant
        })
        resp = client.post(
            "/api/billing/webhook",
            content=payload,
            headers={"X-Signature": "", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["received"] is True

    def test_webhook_returns_401_on_invalid_signature(self, client, db):
        """Invalid signatures return 401 so providers stop retrying a bad secret."""
        # Set up manual provider with a webhook secret
        db.query(PlatformConfig).filter(
            PlatformConfig.config_key == "billing.active_provider"
        ).delete()
        db.add(PlatformConfig(
            config_key="billing.active_provider",
            config_value="manual",
        ))
        db.add(PlatformConfig(
            config_key="billing.manual.webhook_secret",
            config_value="secret123",
        ))
        db.commit()

        payload = json.dumps({"event": "payment.approved", "payload": {}})
        resp = client.post(
            "/api/billing/webhook",
            content=payload,
            headers={"X-Signature": "invalid", "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert "verification failed" in resp.json()["detail"].lower()


from app.backend.models.db_models import PlatformConfig
