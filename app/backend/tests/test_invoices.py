"""Tests for Invoice model, service, API endpoints, and webhook integration."""
import json
import time
from datetime import datetime, timezone, timedelta

import pytest

from app.backend.models.db_models import Invoice, Tenant, User
from app.backend.services.billing.invoice_service import (
    generate_invoice_number,
    create_invoice_from_payment,
    get_tenant_invoices,
    get_tenant_invoice_count,
    get_invoice_by_id,
)
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


# ─── Invoice number generation ────────────────────────────────────────────

class TestInvoiceNumberGeneration:
    """Tests for sequential, year-based invoice number generation."""

    def test_first_invoice_number(self, db):
        """First invoice number in a year starts at INV-{YEAR}-00001."""
        inv_num = generate_invoice_number(db)
        year = datetime.now(timezone.utc).year
        assert inv_num == f"INV-{year}-00001"

    def test_sequential_numbers(self, db):
        """Invoice numbers increment sequentially within a year."""
        num1 = generate_invoice_number(db)
        # Create an invoice with num1 so the next one increments
        tenant = _make_tenant(db)
        inv = Invoice(
            tenant_id=tenant.id,
            invoice_number=num1,
            status="paid",
            amount=4900,
            currency="usd",
        )
        db.add(inv)
        db.commit()

        num2 = generate_invoice_number(db)
        year = datetime.now(timezone.utc).year
        assert num1 == f"INV-{year}-00001"
        assert num2 == f"INV-{year}-00002"

    def test_multiple_invoices_sequential(self, db):
        """Creating multiple invoices produces sequential numbers."""
        tenant = _make_tenant(db)
        numbers = []
        for i in range(5):
            inv = create_invoice_from_payment(
                db,
                tenant_id=tenant.id,
                amount=4900,
                currency="usd",
                plan_name="Pro Plan",
            )
            db.commit()
            numbers.append(inv.invoice_number)

        year = datetime.now(timezone.utc).year
        for i, num in enumerate(numbers, start=1):
            assert num == f"INV-{year}-{i:05d}"


# ─── Invoice creation ─────────────────────────────────────────────────────

class TestCreateInvoiceFromPayment:
    """Tests for create_invoice_from_payment service function."""

    def test_creates_invoice_with_correct_fields(self, db):
        """create_invoice_from_payment creates an Invoice with all fields."""
        tenant = _make_tenant(db)
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=30)
        period_end = now

        inv = create_invoice_from_payment(
            db,
            tenant_id=tenant.id,
            amount=4900,
            currency="usd",
            plan_name="Pro Plan - Monthly",
            period_start=period_start,
            period_end=period_end,
            payment_provider="stripe",
            provider_invoice_id="in_stripe_123",
        )
        db.commit()

        assert inv.id is not None
        assert inv.tenant_id == tenant.id
        assert inv.invoice_number.startswith("INV-")
        assert inv.status == "paid"
        assert inv.amount == 4900
        assert inv.currency == "usd"
        assert inv.description == "Pro Plan - Monthly"
        assert inv.line_items == [{"description": "Pro Plan - Monthly", "amount": 4900, "quantity": 1}]
        assert inv.payment_provider == "stripe"
        assert inv.provider_invoice_id == "in_stripe_123"
        assert inv.paid_at is not None

    def test_invoice_default_currency_is_usd(self, db):
        """Invoice defaults to 'usd' currency when not specified."""
        tenant = _make_tenant(db)
        inv = create_invoice_from_payment(
            db, tenant_id=tenant.id, amount=0, plan_name="Free",
        )
        db.commit()
        assert inv.currency == "usd"

    def test_invoice_description_fallback(self, db):
        """Invoice description falls back to plan_name then 'Subscription payment'."""
        tenant = _make_tenant(db)

        # With plan_name
        inv1 = create_invoice_from_payment(
            db, tenant_id=tenant.id, amount=100, plan_name="Pro Plan",
        )
        db.commit()
        assert inv1.description == "Pro Plan"

        # Without plan_name
        inv2 = create_invoice_from_payment(
            db, tenant_id=tenant.id, amount=100,
        )
        db.commit()
        assert inv2.description == "Subscription payment"

    def test_invoice_number_is_unique(self, db):
        """No two invoices share the same invoice_number."""
        tenant = _make_tenant(db)
        numbers = set()
        for _ in range(10):
            inv = create_invoice_from_payment(
                db, tenant_id=tenant.id, amount=4900,
            )
            db.commit()
            numbers.add(inv.invoice_number)
        assert len(numbers) == 10


# ─── Invoice retrieval ────────────────────────────────────────────────────

class TestInvoiceRetrieval:
    """Tests for get_tenant_invoices, get_tenant_invoice_count, get_invoice_by_id."""

    def test_get_tenant_invoices_returns_tenant_scoped(self, db):
        """get_tenant_invoices only returns invoices for the given tenant."""
        tenant_a = _make_tenant(db, name="Tenant A", slug="tenant-a-inv")
        tenant_b = _make_tenant(db, name="Tenant B", slug="tenant-b-inv")

        create_invoice_from_payment(db, tenant_id=tenant_a.id, amount=100, plan_name="A Plan")
        create_invoice_from_payment(db, tenant_id=tenant_a.id, amount=200, plan_name="A Plan 2")
        create_invoice_from_payment(db, tenant_id=tenant_b.id, amount=300, plan_name="B Plan")
        db.commit()

        invoices_a = get_tenant_invoices(db, tenant_id=tenant_a.id)
        invoices_b = get_tenant_invoices(db, tenant_id=tenant_b.id)

        assert len(invoices_a) == 2
        assert len(invoices_b) == 1
        assert invoices_b[0].amount == 300

    def test_get_tenant_invoices_pagination(self, db):
        """Pagination works with limit and offset."""
        tenant = _make_tenant(db, slug="pag-tenant")
        for i in range(5):
            create_invoice_from_payment(db, tenant_id=tenant.id, amount=100 * (i + 1))
        db.commit()

        page1 = get_tenant_invoices(db, tenant_id=tenant.id, limit=2, offset=0)
        page2 = get_tenant_invoices(db, tenant_id=tenant.id, limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2

    def test_get_tenant_invoices_newest_first(self, db):
        """Invoices are returned newest first."""
        tenant = _make_tenant(db, slug="order-tenant")
        inv1 = create_invoice_from_payment(db, tenant_id=tenant.id, amount=100)
        db.commit()
        inv2 = create_invoice_from_payment(db, tenant_id=tenant.id, amount=200)
        db.commit()

        invoices = get_tenant_invoices(db, tenant_id=tenant.id)
        assert invoices[0].id == inv2.id  # newest first
        assert invoices[1].id == inv1.id

    def test_get_tenant_invoice_count(self, db):
        """get_tenant_invoice_count returns correct count."""
        tenant = _make_tenant(db, slug="count-tenant")
        assert get_tenant_invoice_count(db, tenant_id=tenant.id) == 0

        create_invoice_from_payment(db, tenant_id=tenant.id, amount=100)
        db.commit()
        assert get_tenant_invoice_count(db, tenant_id=tenant.id) == 1

        create_invoice_from_payment(db, tenant_id=tenant.id, amount=200)
        db.commit()
        assert get_tenant_invoice_count(db, tenant_id=tenant.id) == 2

    def test_get_invoice_by_id_tenant_scoped(self, db):
        """get_invoice_by_id returns None if invoice belongs to another tenant."""
        tenant_a = _make_tenant(db, name="A", slug="scope-a")
        tenant_b = _make_tenant(db, name="B", slug="scope-b")

        inv = create_invoice_from_payment(db, tenant_id=tenant_a.id, amount=500)
        db.commit()

        # Same tenant sees it
        found = get_invoice_by_id(db, invoice_id=inv.id, tenant_id=tenant_a.id)
        assert found is not None
        assert found.id == inv.id

        # Different tenant gets None
        not_found = get_invoice_by_id(db, invoice_id=inv.id, tenant_id=tenant_b.id)
        assert not_found is None


# ─── API endpoint tests ───────────────────────────────────────────────────

class TestInvoiceAPIEndpoints:
    """Tests for GET /api/billing/invoices and GET /api/billing/invoices/{id}."""

    def test_list_invoices_requires_auth(self, client, db):
        """GET /api/billing/invoices returns 401 without auth."""
        resp = client.get("/api/billing/invoices")
        assert resp.status_code in (401, 403)

    def test_list_invoices_returns_tenant_invoices(self, auth_client, db):
        """GET /api/billing/invoices returns invoices for the current tenant."""
        # Get the tenant for the auth_client
        user = db.query(User).filter(User.email == "admin@testcorp.com").first()
        tenant_id = user.tenant_id

        # Create some invoices directly
        create_invoice_from_payment(db, tenant_id=tenant_id, amount=4900, plan_name="Pro Plan")
        create_invoice_from_payment(db, tenant_id=tenant_id, amount=9900, plan_name="Enterprise Plan")
        db.commit()

        resp = auth_client.get("/api/billing/invoices")
        assert resp.status_code == 200
        data = resp.json()
        assert "invoices" in data
        assert "total" in data
        assert data["total"] == 2
        assert len(data["invoices"]) == 2

        # Verify invoice fields
        inv = data["invoices"][0]
        assert "id" in inv
        assert "invoice_number" in inv
        assert "status" in inv
        assert "amount" in inv
        assert "currency" in inv
        assert "line_items" in inv

    def test_list_invoices_pagination(self, auth_client, db):
        """GET /api/billing/invoices supports limit/offset."""
        user = db.query(User).filter(User.email == "admin@testcorp.com").first()
        tenant_id = user.tenant_id

        for i in range(5):
            create_invoice_from_payment(db, tenant_id=tenant_id, amount=100 * (i + 1))
        db.commit()

        resp = auth_client.get("/api/billing/invoices?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["invoices"]) == 2
        assert data["total"] == 5

    def test_get_single_invoice(self, auth_client, db):
        """GET /api/billing/invoices/{id} returns invoice detail."""
        user = db.query(User).filter(User.email == "admin@testcorp.com").first()
        tenant_id = user.tenant_id

        inv = create_invoice_from_payment(
            db, tenant_id=tenant_id, amount=4900, plan_name="Pro Plan",
            payment_provider="stripe", provider_invoice_id="in_test123",
        )
        db.commit()

        resp = auth_client.get(f"/api/billing/invoices/{inv.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == inv.id
        assert data["invoice_number"] == inv.invoice_number
        assert data["amount"] == 4900
        assert data["provider_invoice_id"] == "in_test123"

    def test_get_invoice_not_found(self, auth_client, db):
        """GET /api/billing/invoices/{id} returns 404 for non-existent or other-tenant invoice."""
        resp = auth_client.get("/api/billing/invoices/99999")
        assert resp.status_code == 404

    def test_list_invoices_empty_for_new_tenant(self, auth_client, db):
        """New tenant with no invoices gets empty list."""
        resp = auth_client.get("/api/billing/invoices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["invoices"] == []
        assert data["total"] == 0


# ─── Webhook integration tests ────────────────────────────────────────────

class TestInvoiceWebhookIntegration:
    """Tests that invoice records are created on payment webhook events."""

    def test_stripe_invoice_paid_creates_invoice(self, db):
        """Stripe invoice.paid webhook creates an Invoice record."""
        tenant = _make_tenant(db, stripe_customer_id="cus_inv_test", subscription_status="past_due")
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "object": {
                "customer": "cus_inv_test",
                "subscription": "sub_inv_test",
                "id": "in_stripe_001",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
                "amount_paid": 4900,
                "currency": "usd",
                "total": 4900,
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.paid",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        # Verify invoice was created
        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv.amount == 4900
        assert inv.currency == "usd"
        assert inv.status == "paid"
        assert inv.payment_provider == "stripe"
        assert inv.provider_invoice_id == "in_stripe_001"
        assert inv.invoice_number.startswith("INV-")

    def test_razorpay_subscription_activated_creates_invoice(self, db):
        """Razorpay subscription.activated webhook creates an Invoice record."""
        tenant = _make_tenant(db, slug="rzp-inv-tenant")
        # Set a subscription ID so the tenant can be found
        tenant.stripe_subscription_id = "sub_rzp_inv_test"
        db.commit()

        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "subscription": {
                "id": "sub_rzp_inv_test",
                "current_start": now_ts - 2592000,
                "current_end": now_ts,
            },
            "payload": {
                "payment": {
                    "entity": {
                        "amount": 50000,  # Razorpay uses paise (500 INR)
                        "currency": "INR",
                    }
                }
            },
        }
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.activated",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv.amount == 50000
        assert inv.payment_provider == "razorpay"

    def test_razorpay_subscription_charged_creates_invoice(self, db):
        """Razorpay subscription.charged webhook creates an Invoice record."""
        tenant = _make_tenant(db, slug="rzp-charged-tenant")
        tenant.stripe_subscription_id = "sub_rzp_charged"
        db.commit()

        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "subscription": {
                "id": "sub_rzp_charged",
                "current_start": now_ts - 2592000,
                "current_end": now_ts,
            },
            "payload": {
                "payment": {
                    "entity": {
                        "amount": 75000,
                        "currency": "INR",
                    }
                }
            },
        }
        result = process_webhook_event(
            db, provider="razorpay", event_type="subscription.charged",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
        assert len(invoices) == 1
        assert invoices[0].payment_provider == "razorpay"

    def test_manual_payment_approved_creates_invoice(self, db):
        """Manual payment.approved webhook creates an Invoice record."""
        tenant = _make_tenant(db, slug="manual-inv-tenant")

        data = {
            "tenant_id": tenant.id,
            "amount": 9900,
            "currency": "usd",
            "payment_id": "manual_pay_001",
            "period_start": datetime.now(timezone.utc).isoformat(),
            "period_end": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }
        result = process_webhook_event(
            db, provider="manual", event_type="payment.approved",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv.amount == 9900
        assert inv.currency == "usd"
        assert inv.payment_provider == "manual"
        assert inv.provider_invoice_id == "manual_pay_001"

    def test_no_invoice_created_on_payment_failure(self, db):
        """No invoice is created for payment failure events."""
        tenant = _make_tenant(db, stripe_customer_id="cus_fail_test", subscription_status="active")

        data = {
            "object": {
                "customer": "cus_fail_test",
                "subscription": "sub_fail_test",
            }
        }
        result = process_webhook_event(
            db, provider="stripe", event_type="invoice.payment_failed",
            data=data, raw_payload=json.dumps(data),
        )
        assert result["processed"] is True

        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
        assert len(invoices) == 0

    def test_invoice_generation_failure_does_not_break_webhook(self, db):
        """Invoice failure rolls back the webhook transaction (no partial success)."""
        tenant = _make_tenant(db, stripe_customer_id="cus_resilient", subscription_status="past_due")
        now_ts = int(datetime.now(timezone.utc).timestamp())

        data = {
            "object": {
                "customer": "cus_resilient",
                "subscription": "sub_resilient",
                "period_start": now_ts - 2592000,
                "period_end": now_ts,
            }
        }
        from unittest.mock import patch
        with patch("app.backend.services.billing.webhook_processor.create_invoice_from_payment", side_effect=Exception("DB error")):
            result = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data=data, raw_payload=json.dumps(data),
            )

        assert result["processed"] is False
        assert result["reason"] == "error"
        db.refresh(tenant)
        assert tenant.subscription_status == "past_due"
