"""Billing routes — checkout, webhooks, subscription management."""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_platform_admin
from app.backend.middleware.rbac import is_tenant_admin
from app.backend.models.db_models import User, Tenant, Invoice, SubscriptionPlan
from app.backend.services.billing.factory import get_payment_provider
from app.backend.services.plan_entitlement_service import is_paid_plan, start_paid_plan_checkout
from app.backend.services.billing.invoice_service import get_tenant_invoices, get_tenant_invoice_count, get_invoice_by_id
from app.backend.services.billing.webhook_processor import process_webhook_event

log = logging.getLogger(__name__)

_STRIPE_ERRORS: tuple[type[BaseException], ...] = ()
try:
    import stripe
    stripe_error_mod = getattr(stripe, "error", None)
    stripe_err_cls = getattr(stripe_error_mod, "StripeError", None) if stripe_error_mod else None
    if stripe_err_cls is not None:
        _STRIPE_ERRORS = (stripe_err_cls,)
except ImportError:
    pass

_RAZORPAY_ERRORS: tuple[type[BaseException], ...] = ()
try:
    from razorpay.errors import BadRequestError, ServerError, SignatureVerificationError
    _RAZORPAY_ERRORS = (BadRequestError, ServerError, SignatureVerificationError)
except ImportError:
    pass

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: str
    success_url: str = ""
    cancel_url: str = ""


class WebhookResponse(BaseModel):
    received: bool = True


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _require_tenant_access(current_user: User, tenant_id: int):
    """Ensure the user belongs to the requested tenant or is a platform admin."""
    if getattr(current_user, "is_platform_admin", False):
        return
    if current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied for this tenant")


def _require_billing_admin(current_user: User):
    """Checkout, cancel, and invoices are tenant-admin (or platform admin) only."""
    if getattr(current_user, "is_platform_admin", False):
        return
    if not is_tenant_admin(current_user):
        raise HTTPException(status_code=403, detail="Only tenant admins can manage billing")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/checkout")
def create_checkout_session(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a checkout session for the current user's tenant."""
    _require_billing_admin(current_user)
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.name == body.plan)
        .first()
    )
    if plan and is_paid_plan(plan):
        result = start_paid_plan_checkout(db, tenant, plan)
        db.commit()
        return result
    provider = get_payment_provider(db)
    result = provider.create_checkout_session(
        tenant_id=current_user.tenant_id,
        plan=body.plan,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        stripe_customer_id=tenant.stripe_customer_id or "",
    )
    return result


@router.post("/webhook")
async def handle_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle incoming webhook events from the payment provider.

    Signature failures return 401. A missing production webhook secret returns 403.
    """
    provider = get_payment_provider(db)
    webhook_secret = getattr(provider, "webhook_secret", None) or ""
    if os.getenv("ENVIRONMENT", "development") == "production" and not str(webhook_secret).strip():
        raise HTTPException(status_code=403, detail="Billing webhook secret is not configured")

    try:
        body = await request.body()
        signature = request.headers.get("X-Signature", "") or request.headers.get("Stripe-Signature", "")

        # Verify signature and parse event
        result = provider.handle_webhook_event(body, signature)

        provider_name = result.get("provider", provider.provider_name)
        event_type = result.get("event_type", "unknown")
        event_id = result.get("event_id")
        data = result.get("data", {})

        # Process the event — updates tenant state, logs audit, fires webhooks
        raw_payload = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        process_result = process_webhook_event(
            db,
            provider=provider_name,
            event_type=event_type,
            data=data,
            raw_payload=raw_payload,
            event_id=event_id,
        )

        try:
            from app.backend.services.metrics import BILLING_WEBHOOK_TOTAL
            BILLING_WEBHOOK_TOTAL.labels(outcome="success").inc()
        except Exception:
            pass

        log.info(
            "Webhook processed: provider=%s event=%s result=%s",
            provider_name, event_type, process_result.get("reason", "ok"),
        )
    except HTTPException:
        try:
            from app.backend.services.metrics import BILLING_WEBHOOK_TOTAL
            BILLING_WEBHOOK_TOTAL.labels(outcome="failure").inc()
        except Exception:
            pass
        raise
    except _STRIPE_ERRORS + _RAZORPAY_ERRORS as exc:
        log.warning(
            "Webhook processing error: %s", exc,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=401, detail="Webhook verification failed") from exc
    except (ValueError, TypeError, json.JSONDecodeError, KeyError, UnicodeDecodeError) as exc:
        log.warning(
            "Webhook processing error: %s", exc,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=401, detail="Webhook verification failed") from exc
    except OSError as exc:
        log.error(
            "Webhook processing error: %s", exc,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=401, detail="Webhook verification failed") from exc
    except (SQLAlchemyError, RuntimeError) as exc:
        log.exception(
            "Webhook processing error: %s", exc,
            extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "UPSTREAM_ERROR"},
        )
        raise HTTPException(status_code=401, detail="Webhook verification failed") from exc
    finally:
        # Ensure the session is clean even if an error occurred mid-transaction
        try:
            db.rollback()
        except SQLAlchemyError:
            pass

    return {"received": True}


@router.get("/subscription/{tenant_id}")
def get_subscription_status(
    tenant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get subscription status for a tenant.

    Requires admin or same-tenant membership.
    """
    _require_tenant_access(current_user, tenant_id)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    subscription_id = tenant.stripe_subscription_id or f"manual_{tenant.id}"
    provider = get_payment_provider(db)
    return provider.get_subscription_status(tenant_id, subscription_id)


@router.post("/cancel/{tenant_id}")
def cancel_subscription(
    tenant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel subscription for a tenant.

    Requires tenant admin or platform admin.
    """
    _require_tenant_access(current_user, tenant_id)
    _require_billing_admin(current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    subscription_id = tenant.stripe_subscription_id or f"manual_{tenant.id}"
    provider = get_payment_provider(db)
    result = provider.cancel_subscription(tenant_id, subscription_id)
    return result


@router.get("/invoices")
def list_invoices(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get invoices for the current tenant.

    Tenant admins (and platform admins) can see invoices.
    Returns a paginated list ordered by newest first.
    """
    _require_billing_admin(current_user)
    invoices = get_tenant_invoices(db, tenant_id=current_user.tenant_id, limit=limit, offset=offset)
    total = get_tenant_invoice_count(db, tenant_id=current_user.tenant_id)

    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "status": inv.status,
                "amount": inv.amount,
                "currency": inv.currency,
                "description": inv.description,
                "line_items": inv.line_items,
                "payment_provider": inv.payment_provider,
                "period_start": inv.period_start.isoformat() if inv.period_start else None,
                "period_end": inv.period_end.isoformat() if inv.period_end else None,
                "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            }
            for inv in invoices
        ],
        "total": total,
    }


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single invoice detail.

    Only tenant admins can retrieve invoice detail, and only for their tenant.
    """
    _require_billing_admin(current_user)
    invoice = get_invoice_by_id(db, invoice_id=invoice_id, tenant_id=current_user.tenant_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "amount": invoice.amount,
        "currency": invoice.currency,
        "description": invoice.description,
        "line_items": invoice.line_items,
        "payment_provider": invoice.payment_provider,
        "provider_invoice_id": invoice.provider_invoice_id,
        "period_start": invoice.period_start.isoformat() if invoice.period_start else None,
        "period_end": invoice.period_end.isoformat() if invoice.period_end else None,
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
    }
