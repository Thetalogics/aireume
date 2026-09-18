"""Invoice generation and retrieval service.

Invoice numbers are allocated from invoice_year_counters using an atomic
INSERT ... ON CONFLICT ... RETURNING increment. Production correctness does
not use a process-local mutex. Invoice numbering is global (not tenant-scoped).
"""
import logging
from datetime import datetime, timezone, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.backend.models.db_models import Invoice, InvoiceYearCounter, Tenant, SubscriptionPlan

log = logging.getLogger(__name__)


def _max_existing_seq(db: Session, year: int) -> int:
    """One-time seed helper when the year counter row does not exist yet."""
    prefix = f"INV-{year}-"
    last_invoice = (
        db.query(Invoice.invoice_number)
        .filter(Invoice.invoice_number.like(f"{prefix}%"))
        .order_by(Invoice.invoice_number.desc())
        .first()
    )
    if last_invoice is None:
        return 0
    try:
        return int(last_invoice[0].split("-")[-1])
    except (ValueError, IndexError):
        return 0


def generate_invoice_number(db: Session, *, year: Optional[int] = None) -> str:
    """Allocate INV-{year}-{seq:05d} via atomic upsert of invoice_year_counters.

    First insert for a year seeds last_value to max(existing suffix)+1.
    Concurrent first inserts: one INSERT wins, the other ON CONFLICT increments.
    After the row exists, allocation never uses MAX(invoice_number)+1.
    """
    year = year or datetime.now(timezone.utc).year
    existing = db.execute(
        text("SELECT last_value FROM invoice_year_counters WHERE year = :year"),
        {"year": year},
    ).scalar()
    initial = (_max_existing_seq(db, year) + 1) if existing is None else 1
    for _ in range(8):
        try:
            with db.begin_nested():
                seq = db.execute(
                    text(
                        """
                        INSERT INTO invoice_year_counters (year, last_value)
                        VALUES (:year, :initial)
                        ON CONFLICT (year) DO UPDATE
                        SET last_value = invoice_year_counters.last_value + 1
                        RETURNING last_value
                        """
                    ),
                    {"year": year, "initial": initial},
                ).scalar()
            if seq is None:
                continue
            return f"INV-{year}-{int(seq):05d}"
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate invoice number")


def _is_provider_invoice_conflict(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    blob = f"{exc} {orig}".lower()
    return "uq_invoice_provider_invoice_id" in blob or (
        "provider_invoice_id" in blob and "unique" in blob
    )


def create_invoice_from_payment(
    db: Session,
    *,
    tenant_id: int,
    amount: int,
    currency: str = "usd",
    plan_name: str = "",
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    payment_provider: Optional[str] = None,
    provider_invoice_id: Optional[str] = None,
    description: Optional[str] = None,
) -> Invoice:
    """Create an invoice record after a successful payment.

    The caller is responsible for calling ``db.commit()`` afterwards
    (typically the webhook handler that already manages its own transaction).
    Duplicate (payment_provider, provider_invoice_id) returns the existing row.
    """
    if payment_provider and provider_invoice_id:
        existing = (
            db.query(Invoice)
            .filter(
                Invoice.payment_provider == payment_provider,
                Invoice.provider_invoice_id == provider_invoice_id,
            )
            .first()
        )
        if existing is not None:
            return existing

    invoice_number = generate_invoice_number(db)

    line_items = [
        {
            "description": plan_name or "Subscription payment",
            "amount": amount,
            "quantity": 1,
        }
    ]

    now = datetime.now(timezone.utc)

    invoice = Invoice(
        tenant_id=tenant_id,
        invoice_number=invoice_number,
        status="paid",
        amount=amount,
        currency=currency.lower(),
        description=description or plan_name or "Subscription payment",
        line_items=line_items,
        payment_provider=payment_provider,
        provider_invoice_id=provider_invoice_id,
        period_start=period_start,
        period_end=period_end,
        paid_at=now,
    )
    try:
        with db.begin_nested():
            db.add(invoice)
            db.flush()
    except IntegrityError as exc:
        if payment_provider and provider_invoice_id and _is_provider_invoice_conflict(exc):
            existing = (
                db.query(Invoice)
                .filter(
                    Invoice.payment_provider == payment_provider,
                    Invoice.provider_invoice_id == provider_invoice_id,
                )
                .first()
            )
            if existing is not None:
                return existing
        raise
    log.info(
        "Created invoice %s for tenant_id=%s amount=%d%s provider=%s",
        invoice_number, tenant_id, amount, currency, payment_provider,
    )
    return invoice


def get_tenant_invoices(
    db: Session,
    tenant_id: int,
    limit: int = 50,
    offset: int = 0,
) -> List[Invoice]:
    """Get paginated invoices for a tenant, newest first."""
    return (
        db.query(Invoice)
        .filter(Invoice.tenant_id == tenant_id)
        .order_by(Invoice.issued_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_tenant_invoice_count(db: Session, tenant_id: int) -> int:
    """Get total invoice count for a tenant."""
    return (
        db.query(func.count(Invoice.id))
        .filter(Invoice.tenant_id == tenant_id)
        .scalar()
        or 0
    )


def get_invoice_by_id(db: Session, invoice_id: int, tenant_id: int) -> Optional[Invoice]:
    """Get a single invoice, ensuring it belongs to the given tenant."""
    return (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .first()
    )


def _serialize_invoice(inv: Invoice, tenant_name: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": inv.id,
        "tenant_id": inv.tenant_id,
        "tenant_name": tenant_name,
        "invoice_number": inv.invoice_number,
        "status": inv.status,
        "amount": inv.amount,
        "currency": inv.currency,
        "description": inv.description,
        "line_items": inv.line_items or [],
        "payment_provider": inv.payment_provider,
        "provider_invoice_id": inv.provider_invoice_id,
        "period_start": inv.period_start.isoformat() if inv.period_start else None,
        "period_end": inv.period_end.isoformat() if inv.period_end else None,
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
    }


def get_all_invoices(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Platform-admin view of invoices across all tenants."""
    q = (
        db.query(Invoice, Tenant.name.label("tenant_name"))
        .join(Tenant, Tenant.id == Invoice.tenant_id)
        .order_by(Invoice.issued_at.desc())
    )
    if status:
        q = q.filter(Invoice.status == status)
    if tenant_id:
        q = q.filter(Invoice.tenant_id == tenant_id)
    rows = q.offset(offset).limit(limit).all()
    return [_serialize_invoice(inv, tenant_name) for inv, tenant_name in rows]


def get_revenue_metrics(db: Session, *, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """Platform revenue metrics.

    collected_cash_* sums paid invoices by payment date (cash collections).
    subscription_mrr / mrr_cents is normalized recurring revenue from active
    subscriptions (yearly list price / 12), not trailing cash.
    One-off invoices are excluded from MRR.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    paid = db.query(Invoice).filter(Invoice.status == "paid")
    outstanding_q = db.query(func.coalesce(func.sum(Invoice.amount), 0)).filter(
        Invoice.status.in_(["pending", "overdue"])
    )
    if tenant_id is not None:
        paid = paid.filter(Invoice.tenant_id == tenant_id)
        outstanding_q = outstanding_q.filter(Invoice.tenant_id == tenant_id)

    collected_this_month = (
        paid.filter(Invoice.paid_at >= month_start).with_entities(
            func.coalesce(func.sum(Invoice.amount), 0)
        ).scalar()
        or 0
    )
    collected_30 = db.query(func.coalesce(func.sum(Invoice.amount), 0)).filter(
        Invoice.status == "paid", Invoice.paid_at >= thirty_days_ago
    )
    if tenant_id is not None:
        collected_30 = collected_30.filter(Invoice.tenant_id == tenant_id)
    collected_last_30_days = collected_30.scalar() or 0

    outstanding = outstanding_q.scalar() or 0
    mrr_cents = _subscription_mrr_cents(db, tenant_id=tenant_id)

    return {
        "mrr_cents": int(mrr_cents),
        "arr_estimate_cents": int(mrr_cents) * 12,
        "subscription_mrr_cents": int(mrr_cents),
        "subscription_arr_cents": int(mrr_cents) * 12,
        "collected_cash_30d_cents": int(collected_last_30_days),
        "collected_cash_mtd_cents": int(collected_this_month),
        "collected_this_month_cents": int(collected_this_month),
        "outstanding_cents": int(outstanding),
        "outstanding_receivables_cents": int(outstanding),
        "mrr": round(mrr_cents / 100, 2),
        "collected_this_month": round(collected_this_month / 100, 2),
    }


def _subscription_mrr_cents(db: Session, *, tenant_id: Optional[int] = None) -> int:
    q = (
        db.query(Tenant, SubscriptionPlan)
        .join(SubscriptionPlan, Tenant.plan_id == SubscriptionPlan.id)
        .filter(Tenant.subscription_status.in_(["active", "trialing"]))
        .filter(Tenant.deleted_at.is_(None))
    )
    if tenant_id is not None:
        q = q.filter(Tenant.id == tenant_id)
    total = 0
    for tenant, plan in q.all():
        duration_days = 0
        if tenant.current_period_start and tenant.current_period_end:
            duration_days = (tenant.current_period_end - tenant.current_period_start).days
        yearly = duration_days > 32
        if yearly:
            if plan.price_yearly:
                total += yearly_price_to_mrr_cents(plan.price_yearly)
            else:
                total += int(plan.price_monthly or 0)
        else:
            total += int(plan.price_monthly or 0)
    return total


def yearly_price_to_mrr_cents(price_yearly_cents: int) -> int:
    """Normalize annual list price to monthly recurring cents.

    Rounding: half-up to the nearest integer cent using Decimal, never float.
    ARR for a single annual subscription is 12 * this MRR figure (not the raw
    yearly list price when yearly is not divisible by 12).
    """
    monthly = (Decimal(int(price_yearly_cents)) / Decimal(12)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(monthly)
