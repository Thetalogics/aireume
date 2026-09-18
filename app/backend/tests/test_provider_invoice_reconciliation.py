"""Unit tests for duplicate provider-invoice keeper selection."""
from datetime import datetime, timezone

import pytest

from app.backend.services.billing.provider_invoice_reconciliation import (
    InvoiceDuplicateConflict,
    choose_invoice_keeper_id,
    invoices_are_equivalent,
)


def _row(**kwargs):
    base = {
        "id": 1,
        "tenant_id": 9,
        "amount": 4900,
        "currency": "usd",
        "status": "paid",
        "paid_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    base.update(kwargs)
    return base


def test_equivalent_stripe_keeps_paid_newest():
    rows = [
        _row(id=1, status="paid", paid_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _row(id=2, status="paid", paid_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
    ]
    assert invoices_are_equivalent(rows)
    assert choose_invoice_keeper_id(rows) == 2


def test_equivalent_razorpay_keeps_highest_id_when_dates_tie():
    ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = [_row(id=10, paid_at=ts, issued_at=ts), _row(id=11, paid_at=ts, issued_at=ts)]
    assert choose_invoice_keeper_id(rows) == 11


def test_three_equivalent_one_keeper():
    rows = [_row(id=3), _row(id=4), _row(id=5)]
    assert choose_invoice_keeper_id(rows) == 5


def test_conflicting_amount_raises():
    rows = [_row(id=1, amount=100), _row(id=2, amount=200)]
    with pytest.raises(InvoiceDuplicateConflict):
        choose_invoice_keeper_id(rows)


def test_conflicting_tenant_raises():
    rows = [_row(id=1, tenant_id=1), _row(id=2, tenant_id=2)]
    with pytest.raises(InvoiceDuplicateConflict):
        choose_invoice_keeper_id(rows)
