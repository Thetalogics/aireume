"""Webhook event processor — maps provider events to tenant subscription updates.

This module is the *single source of truth* for converting payment-provider
webhook events into database state changes.  Each provider's raw event is
normalised into a (provider, event_type, tenant_lookup, payload) tuple and
dispatched to the appropriate handler.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.backend.models.db_models import BillingEvent, Tenant
from app.backend.services.billing.dunning_service import dunning_service
from app.backend.services.billing.invoice_service import create_invoice_from_payment
from app.backend.services.webhook_service import dispatch_event_background
from app.backend.db.database import SessionLocal

log = logging.getLogger(__name__)


# ─── Tenant lookup helpers ──────────────────────────────────────────────────

def _find_tenant_by_stripe_customer_id(
    db: Session, customer_id: str
) -> Optional[Tenant]:
    """Look up a tenant by their Stripe customer ID."""
    return (
        db.query(Tenant)
        .filter(Tenant.stripe_customer_id == customer_id)
        .first()
    )


def _find_tenant_by_stripe_subscription_id(
    db: Session, subscription_id: str
) -> Optional[Tenant]:
    """Look up a tenant by their Stripe subscription ID."""
    return (
        db.query(Tenant)
        .filter(Tenant.stripe_subscription_id == subscription_id)
        .first()
    )


def _find_tenant_by_razorpay_subscription_id(
    db: Session, subscription_id: str
) -> Optional[Tenant]:
    """Look up a tenant by Razorpay subscription ID stored in stripe_subscription_id.

    The column is reused for all providers' subscription identifiers.
    """
    return (
        db.query(Tenant)
        .filter(Tenant.stripe_subscription_id == subscription_id)
        .first()
    )


def _find_tenant_by_id(db: Session, tenant_id: int) -> Optional[Tenant]:
    """Look up a tenant by primary key."""
    return db.query(Tenant).filter(Tenant.id == tenant_id).first()


# ─── Audit logging ──────────────────────────────────────────────────────────

def _idempotency_event_id(
    provider: str,
    event_type: str,
    event_id: Optional[str],
    raw_payload: Optional[str],
) -> str:
    """Stable webhook de-dupe key. Stripe sends event_id; Razorpay/manual often do not."""
    if event_id:
        return event_id
    if isinstance(raw_payload, (bytes, bytearray)):
        payload_bytes = bytes(raw_payload)
    else:
        payload_bytes = (raw_payload or "").encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()
    return f"{provider}:{event_type}:{digest}"[:255]


def _log_billing_event(
    db: Session,
    *,
    provider: str,
    event_type: str,
    tenant_id: Optional[int],
    raw_payload: str,
    result: str,
    error_detail: Optional[str] = None,
    event_id: Optional[str] = None,
) -> BillingEvent:
    """Persist a BillingEvent row for audit.

    The caller is responsible for calling db.commit() after this.
    """
    evt = BillingEvent(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        raw_payload=raw_payload[:10000] if raw_payload else None,  # cap size
        result=result,
        error_detail=error_detail[:2000] if error_detail else None,
    )
    db.add(evt)
    return evt


# ─── Notification helper ────────────────────────────────────────────────────

def _fire_subscription_changed(tenant_id: int, new_status: str):
    """Fire the subscription.changed webhook event via the existing dispatch service."""
    try:
        dispatch_event_background(
            db_session_factory=SessionLocal,
            tenant_id=tenant_id,
            event="subscription.changed",
            payload={
                "subscription_status": new_status,
                "changed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        log.exception("Failed to fire subscription.changed webhook for tenant %s", tenant_id)


# ─── Stripe event handlers ──────────────────────────────────────────────────

def _handle_stripe_checkout_completed(db: Session, data: dict, raw_payload: str):
    """checkout.session.completed → store customer_id and subscription_id on tenant."""
    session_obj = data.get("object", {}) if isinstance(data, dict) else {}
    tenant_id_str = session_obj.get("metadata", {}).get("tenant_id")

    tenant = None
    if tenant_id_str:
        try:
            tenant = _find_tenant_by_id(db, int(tenant_id_str))
        except (ValueError, TypeError):
            pass

    # Fall back to customer lookup if metadata is missing
    customer_id = session_obj.get("customer", "")
    if tenant is None and customer_id:
        tenant = _find_tenant_by_stripe_customer_id(db, customer_id)

    if tenant is None:
        _log_billing_event(
            db, provider="stripe", event_type="checkout.session.completed",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for tenant_id={tenant_id_str} customer_id={customer_id}",
        )
        db.commit()
        return

    subscription_id = session_obj.get("subscription", "")

    # Persist customer and subscription IDs so future webhooks can look up tenant
    if customer_id and not tenant.stripe_customer_id:
        tenant.stripe_customer_id = customer_id
    if subscription_id and not tenant.stripe_subscription_id:
        tenant.stripe_subscription_id = subscription_id

    from app.backend.services.plan_entitlement_service import apply_verified_paid_plan

    purchased_plan_id = (session_obj.get("metadata") or {}).get("plan_id")
    if not purchased_plan_id:
        _log_billing_event(
            db, provider="stripe", event_type="checkout.session.completed",
            tenant_id=tenant.id, raw_payload=raw_payload, result="error",
            error_detail="checkout session missing immutable plan_id metadata",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    try:
        apply_verified_paid_plan(db, tenant, purchased_plan_id=int(purchased_plan_id))
    except (TypeError, ValueError) as exc:
        _log_billing_event(
            db, provider="stripe", event_type="checkout.session.completed",
            tenant_id=tenant.id, raw_payload=raw_payload, result="error",
            error_detail=str(exc),
        )
        db.commit()
        return
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="stripe", event_type="checkout.session.completed",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    db.commit()

    if old_status != "active":
        _fire_subscription_changed(tenant.id, "active")


def _handle_stripe_invoice_paid(db: Session, data: dict, raw_payload: str):
    """invoice.paid → set subscription_status = active, update period dates."""
    sub_data = data.get("object", {}) if isinstance(data, dict) else {}
    customer_id = sub_data.get("customer") or sub_data.get("customer_id", "")
    subscription_id = sub_data.get("subscription") or sub_data.get("subscription_id", "")

    tenant = None
    if customer_id:
        tenant = _find_tenant_by_stripe_customer_id(db, customer_id)
    if tenant is None and subscription_id:
        tenant = _find_tenant_by_stripe_subscription_id(db, subscription_id)

    if tenant is None:
        _log_billing_event(
            db, provider="stripe", event_type="invoice.paid",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for customer_id={customer_id} subscription_id={subscription_id}",
        )
        db.commit()
        return

    from app.backend.services.plan_entitlement_service import mark_subscription_active

    old_status = tenant.subscription_status
    mark_subscription_active(db, tenant)

    # Update period dates from subscription lines
    period_start = sub_data.get("period_start")
    period_end = sub_data.get("period_end")
    if period_start:
        tenant.current_period_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
    if period_end:
        tenant.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="stripe", event_type="invoice.paid",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )

    # Generate invoice record
    try:
        invoice_amount = sub_data.get("amount_paid") or sub_data.get("total") or 0
        invoice_currency = sub_data.get("currency", "usd")
        stripe_invoice_id = sub_data.get("id", "")
        period_start_dt = datetime.fromtimestamp(period_start, tz=timezone.utc) if period_start else None
        period_end_dt = datetime.fromtimestamp(period_end, tz=timezone.utc) if period_end else None
        create_invoice_from_payment(
            db,
            tenant_id=tenant.id,
            amount=invoice_amount,
            currency=invoice_currency,
            plan_name=_resolve_plan_name(tenant),
            period_start=period_start_dt,
            period_end=period_end_dt,
            payment_provider="stripe",
            provider_invoice_id=stripe_invoice_id,
        )
    except Exception:
        log.exception("Failed to generate invoice for stripe/invoice.paid tenant=%s", tenant.id)

    # Resolve any active dunning for this tenant
    dunning_service.resolve_dunning(db, tenant.id)
    db.commit()

    if old_status != "active":
        _fire_subscription_changed(tenant.id, "active")


def _handle_stripe_invoice_payment_failed(db: Session, data: dict, raw_payload: str):
    """invoice.payment_failed → set subscription_status = past_due."""
    sub_data = data.get("object", {}) if isinstance(data, dict) else {}
    customer_id = sub_data.get("customer") or sub_data.get("customer_id", "")
    subscription_id = sub_data.get("subscription") or sub_data.get("subscription_id", "")

    tenant = None
    if customer_id:
        tenant = _find_tenant_by_stripe_customer_id(db, customer_id)
    if tenant is None and subscription_id:
        tenant = _find_tenant_by_stripe_subscription_id(db, subscription_id)

    if tenant is None:
        _log_billing_event(
            db, provider="stripe", event_type="invoice.payment_failed",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for customer_id={customer_id} subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "past_due"
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="stripe", event_type="invoice.payment_failed",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    # Initiate dunning for failed payment
    try:
        dunning_service.initiate_dunning(
            db, tenant.id,
            failure_reason=f"Stripe invoice.payment_failed: customer={customer_id}",
        )
    except Exception:
        log.exception("Failed to initiate dunning for tenant %s", tenant.id)
    db.commit()

    if old_status != "past_due":
        _fire_subscription_changed(tenant.id, "past_due")


def _handle_stripe_subscription_updated(db: Session, data: dict, raw_payload: str):
    """customer.subscription.updated → update period dates, plan if changed."""
    sub_data = data.get("object", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_stripe_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="stripe", event_type="customer.subscription.updated",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    # Map Stripe subscription status to our status
    stripe_status = sub_data.get("status", "")
    status_map = {
        "active": "active",
        "trialing": "trialing",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "past_due",
        "incomplete": "past_due",
        "incomplete_expired": "cancelled",
        "paused": "past_due",
    }
    new_status = status_map.get(stripe_status, tenant.subscription_status)

    old_status = tenant.subscription_status
    tenant.subscription_status = new_status

    # Update period dates
    period_start = sub_data.get("current_period_start")
    period_end = sub_data.get("current_period_end")
    if period_start:
        tenant.current_period_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
    if period_end:
        tenant.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)

    # Store subscription_id if not already stored
    if subscription_id and not tenant.stripe_subscription_id:
        tenant.stripe_subscription_id = subscription_id

    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="stripe", event_type="customer.subscription.updated",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    db.commit()

    if old_status != new_status:
        _fire_subscription_changed(tenant.id, new_status)


def _handle_stripe_subscription_deleted(db: Session, data: dict, raw_payload: str):
    """customer.subscription.deleted → set subscription_status = cancelled, clear dates."""
    sub_data = data.get("object", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_stripe_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="stripe", event_type="customer.subscription.deleted",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "cancelled"
    tenant.current_period_start = None
    tenant.current_period_end = None
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="stripe", event_type="customer.subscription.deleted",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    db.commit()

    if old_status != "cancelled":
        _fire_subscription_changed(tenant.id, "cancelled")


# ─── Razorpay event handlers ────────────────────────────────────────────────

def _handle_razorpay_subscription_activated(db: Session, data: dict, raw_payload: str):
    """subscription.activated → set subscription_status = active."""
    sub_data = data.get("subscription", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_razorpay_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="razorpay", event_type="subscription.activated",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "active"

    # Update period dates if available
    start = sub_data.get("current_start")
    end = sub_data.get("current_end")
    if start:
        tenant.current_period_start = datetime.fromtimestamp(start, tz=timezone.utc)
    if end:
        tenant.current_period_end = datetime.fromtimestamp(end, tz=timezone.utc)

    if subscription_id and not tenant.stripe_subscription_id:
        tenant.stripe_subscription_id = subscription_id

    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="razorpay", event_type="subscription.activated",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )

    # Generate invoice record
    try:
        amount = data.get("payload", {}).get("payment", {}).get("entity", {}).get("amount", 0)
        if not amount:
            amount = data.get("amount", 0)
        currency = data.get("payload", {}).get("payment", {}).get("entity", {}).get("currency", "inr")
        if not currency:
            currency = "inr"
        period_start_dt = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        period_end_dt = datetime.fromtimestamp(end, tz=timezone.utc) if end else None
        create_invoice_from_payment(
            db,
            tenant_id=tenant.id,
            amount=amount,
            currency=currency,
            plan_name=_resolve_plan_name(tenant),
            period_start=period_start_dt,
            period_end=period_end_dt,
            payment_provider="razorpay",
            provider_invoice_id=subscription_id,
        )
    except Exception:
        log.exception("Failed to generate invoice for razorpay/subscription.activated tenant=%s", tenant.id)

    # Resolve any active dunning for this tenant
    dunning_service.resolve_dunning(db, tenant.id)
    db.commit()

    if old_status != "active":
        _fire_subscription_changed(tenant.id, "active")


def _handle_razorpay_subscription_charged(db: Session, data: dict, raw_payload: str):
    """subscription.charged → update period dates, status = active."""
    sub_data = data.get("subscription", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_razorpay_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="razorpay", event_type="subscription.charged",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "active"

    start = sub_data.get("current_start")
    end = sub_data.get("current_end")
    if start:
        tenant.current_period_start = datetime.fromtimestamp(start, tz=timezone.utc)
    if end:
        tenant.current_period_end = datetime.fromtimestamp(end, tz=timezone.utc)

    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="razorpay", event_type="subscription.charged",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )

    # Generate invoice record
    try:
        amount = data.get("payload", {}).get("payment", {}).get("entity", {}).get("amount", 0)
        if not amount:
            amount = data.get("amount", 0)
        currency = data.get("payload", {}).get("payment", {}).get("entity", {}).get("currency", "inr")
        if not currency:
            currency = "inr"
        period_start_dt = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        period_end_dt = datetime.fromtimestamp(end, tz=timezone.utc) if end else None
        create_invoice_from_payment(
            db,
            tenant_id=tenant.id,
            amount=amount,
            currency=currency,
            plan_name=_resolve_plan_name(tenant),
            period_start=period_start_dt,
            period_end=period_end_dt,
            payment_provider="razorpay",
            provider_invoice_id=subscription_id,
        )
    except Exception:
        log.exception("Failed to generate invoice for razorpay/subscription.charged tenant=%s", tenant.id)

    # Resolve any active dunning for this tenant
    dunning_service.resolve_dunning(db, tenant.id)
    db.commit()

    if old_status != "active":
        _fire_subscription_changed(tenant.id, "active")


def _handle_razorpay_subscription_pending(db: Session, data: dict, raw_payload: str):
    """subscription.pending → set subscription_status = past_due."""
    sub_data = data.get("subscription", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_razorpay_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="razorpay", event_type="subscription.pending",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "past_due"
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="razorpay", event_type="subscription.pending",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    # Initiate dunning for failed payment
    try:
        dunning_service.initiate_dunning(
            db, tenant.id,
            failure_reason=f"Razorpay subscription.pending: sub={subscription_id}",
        )
    except Exception:
        log.exception("Failed to initiate dunning for tenant %s", tenant.id)
    db.commit()

    if old_status != "past_due":
        _fire_subscription_changed(tenant.id, "past_due")


def _handle_razorpay_subscription_cancelled(db: Session, data: dict, raw_payload: str):
    """subscription.cancelled → set subscription_status = cancelled."""
    sub_data = data.get("subscription", {}) if isinstance(data, dict) else {}
    subscription_id = sub_data.get("id", "")

    tenant = _find_tenant_by_razorpay_subscription_id(db, subscription_id)
    if tenant is None:
        _log_billing_event(
            db, provider="razorpay", event_type="subscription.cancelled",
            tenant_id=None, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for subscription_id={subscription_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "cancelled"
    tenant.current_period_start = None
    tenant.current_period_end = None
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="razorpay", event_type="subscription.cancelled",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    db.commit()

    if old_status != "cancelled":
        _fire_subscription_changed(tenant.id, "cancelled")


# ─── Manual provider event handlers ─────────────────────────────────────────

def _handle_manual_payment_approved(db: Session, data: dict, raw_payload: str):
    """payment.approved → set subscription_status = active, set period dates."""
    tenant_id = data.get("tenant_id")

    tenant = None
    if tenant_id:
        tenant = _find_tenant_by_id(db, int(tenant_id))

    if tenant is None:
        _log_billing_event(
            db, provider="manual", event_type="payment.approved",
            tenant_id=tenant_id, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for tenant_id={tenant_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "active"

    # Period dates from payload or default to 1 month from now
    period_start = data.get("period_start")
    period_end = data.get("period_end")
    now = datetime.now(timezone.utc)

    if period_start:
        tenant.current_period_start = datetime.fromisoformat(period_start) if isinstance(period_start, str) else datetime.fromtimestamp(period_start, tz=timezone.utc)
    else:
        tenant.current_period_start = now

    if period_end:
        tenant.current_period_end = datetime.fromisoformat(period_end) if isinstance(period_end, str) else datetime.fromtimestamp(period_end, tz=timezone.utc)
    else:
        tenant.current_period_end = now + timedelta(days=30)

    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="manual", event_type="payment.approved",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )

    # Generate invoice record
    try:
        amount = data.get("amount", 0)
        currency = data.get("currency", "usd")
        create_invoice_from_payment(
            db,
            tenant_id=tenant.id,
            amount=amount,
            currency=currency,
            plan_name=_resolve_plan_name(tenant),
            period_start=tenant.current_period_start,
            period_end=tenant.current_period_end,
            payment_provider="manual",
            provider_invoice_id=data.get("payment_id"),
        )
    except Exception:
        log.exception("Failed to generate invoice for manual/payment.approved tenant=%s", tenant.id)

    # Resolve any active dunning for this tenant
    dunning_service.resolve_dunning(db, tenant.id)
    db.commit()

    if old_status != "active":
        _fire_subscription_changed(tenant.id, "active")


def _handle_manual_payment_rejected(db: Session, data: dict, raw_payload: str):
    """payment.rejected → set subscription_status = past_due."""
    tenant_id = data.get("tenant_id")

    tenant = None
    if tenant_id:
        tenant = _find_tenant_by_id(db, int(tenant_id))

    if tenant is None:
        _log_billing_event(
            db, provider="manual", event_type="payment.rejected",
            tenant_id=tenant_id, raw_payload=raw_payload, result="error",
            error_detail=f"No tenant found for tenant_id={tenant_id}",
        )
        db.commit()
        return

    old_status = tenant.subscription_status
    tenant.subscription_status = "past_due"
    tenant.subscription_updated_at = datetime.now(timezone.utc)

    _log_billing_event(
        db, provider="manual", event_type="payment.rejected",
        tenant_id=tenant.id, raw_payload=raw_payload, result="success",
    )
    # Initiate dunning for failed payment
    try:
        dunning_service.initiate_dunning(
            db, tenant.id,
            failure_reason=f"Manual payment.rejected: tenant_id={tenant_id}",
        )
    except Exception:
        log.exception("Failed to initiate dunning for tenant %s", tenant.id)
    db.commit()

    if old_status != "past_due":
        _fire_subscription_changed(tenant.id, "past_due")


# ─── Plan name resolution ──────────────────────────────────────────────────

def _resolve_plan_name(tenant: Tenant) -> str:
    """Best-effort resolution of the tenant's current plan display name."""
    try:
        if tenant.plan:
            return tenant.plan.display_name or tenant.plan.name
    except Exception:
        pass
    return ""


# ─── Event handler registry ─────────────────────────────────────────────────

# Maps (provider_name, event_type) → handler callable
_HANDLER_MAP: Dict[tuple[str, str], Callable] = {
    # Stripe
    ("stripe", "checkout.session.completed"): _handle_stripe_checkout_completed,
    ("stripe", "invoice.paid"): _handle_stripe_invoice_paid,
    ("stripe", "invoice.payment_failed"): _handle_stripe_invoice_payment_failed,
    ("stripe", "customer.subscription.updated"): _handle_stripe_subscription_updated,
    ("stripe", "customer.subscription.deleted"): _handle_stripe_subscription_deleted,
    # Razorpay
    ("razorpay", "subscription.activated"): _handle_razorpay_subscription_activated,
    ("razorpay", "subscription.charged"): _handle_razorpay_subscription_charged,
    ("razorpay", "subscription.pending"): _handle_razorpay_subscription_pending,
    ("razorpay", "subscription.cancelled"): _handle_razorpay_subscription_cancelled,
    # Manual
    ("manual", "payment.approved"): _handle_manual_payment_approved,
    ("manual", "payment.rejected"): _handle_manual_payment_rejected,
}


def process_webhook_event(
    db: Session,
    *,
    provider: str,
    event_type: str,
    data: dict,
    raw_payload: str,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a validated webhook event and update tenant state.

    This is the main entry point called from the route after signature
    verification succeeds.  It:

    1. Looks up the handler for (provider, event_type)
    2. Executes the handler which updates the tenant record atomically
    3. Logs the event for audit
    4. Fires subscription.changed webhook for downstream consumers

    Returns a normalised result dict.  Unknown events are logged and
    ignored gracefully — we never raise so the HTTP response is always 200.
    """
    # ── Idempotency: skip events we've already processed successfully ─────────
    event_id = _idempotency_event_id(provider, event_type, event_id, raw_payload)
    existing = (
        db.query(BillingEvent)
        .filter(
            BillingEvent.provider == provider,
            BillingEvent.event_id == event_id,
            BillingEvent.result == "success",
        )
        .first()
    )
    if existing:
        log.info(
            "Duplicate webhook ignored: provider=%s event_id=%s type=%s",
            provider, event_id, event_type,
        )
        return {
            "processed": False,
            "reason": "duplicate",
            "provider": provider,
            "event_type": event_type,
        }

    handler = _HANDLER_MAP.get((provider, event_type))

    if handler is None:
        _log_billing_event(
            db, provider=provider, event_type=event_type,
            tenant_id=None, raw_payload=raw_payload, result="ignored",
            error_detail="No handler registered for this event type",
            event_id=event_id,
        )
        db.commit()
        return {
            "processed": False,
            "reason": "ignored",
            "provider": provider,
            "event_type": event_type,
        }

    try:
        handler(db, data, raw_payload)
    except Exception as exc:
        log.exception(
            "Error processing %s/%s webhook: %s", provider, event_type, exc
        )
        # Attempt to log the error — if the session is broken, rollback first
        try:
            db.rollback()
            _log_billing_event(
                db, provider=provider, event_type=event_type,
                tenant_id=None, raw_payload=raw_payload, result="error",
                error_detail=str(exc)[:2000], event_id=event_id,
            )
            db.commit()
        except Exception:
            log.exception("Failed to log billing event error")

        return {
            "processed": False,
            "reason": "error",
            "provider": provider,
            "event_type": event_type,
        }

    # Record a success audit row keyed by event_id so replays are de-duplicated.
    try:
        _log_billing_event(
            db, provider=provider, event_type=event_type,
            tenant_id=None, raw_payload=raw_payload, result="success",
            event_id=event_id,
        )
        db.commit()
    except Exception:
        log.exception("Failed to log billing event success")
        db.rollback()

    return {
        "processed": True,
        "provider": provider,
        "event_type": event_type,
    }
