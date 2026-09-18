"""Deterministic keeper selection for duplicate provider invoices.

Equivalent duplicates share tenant_id, amount (cents), and currency.
Material disagreement (tenant, amount, or currency) is a migration failure.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_STATUS_RANK = {
    "paid": 0,
    "pending": 1,
    "draft": 2,
    "refunded": 3,
    "void": 4,
}


class InvoiceDuplicateConflict(ValueError):
    """Duplicate provider invoices disagree on financial identity."""


def invoices_are_equivalent(rows: Sequence[Mapping[str, Any]]) -> bool:
    if not rows:
        return True
    tenants = {row["tenant_id"] for row in rows}
    amounts = {int(row["amount"]) for row in rows}
    currencies = {(row.get("currency") or "usd").lower() for row in rows}
    return len(tenants) == 1 and len(amounts) == 1 and len(currencies) == 1


def choose_invoice_keeper_id(
    rows: Sequence[Mapping[str, Any]],
    *,
    provider: str | None = None,
    provider_invoice_id: str | None = None,
) -> int:
    if not rows:
        raise InvoiceDuplicateConflict(
            "Conflicting provider invoices cannot be auto-merged: "
            f"provider={provider} provider_invoice_id={provider_invoice_id} empty group"
        )
    if not invoices_are_equivalent(rows):
        ids = [row["id"] for row in rows]
        raise InvoiceDuplicateConflict(
            "Conflicting provider invoices cannot be auto-merged: "
            f"provider={provider} provider_invoice_id={provider_invoice_id} "
            f"ids={ids} tenants={[row['tenant_id'] for row in rows]} "
            f"amounts={[row['amount'] for row in rows]} "
            f"currencies={[row.get('currency') for row in rows]}"
        )

    def sort_key(row: Mapping[str, Any]):
        status = (row.get("status") or "").lower()
        rank = _STATUS_RANK.get(status, 9)
        paid_at = row.get("paid_at")
        issued_at = row.get("issued_at")
        return (
            rank,
            -_completeness(row),
            0 if paid_at is not None else 1,
            _neg_ts(paid_at),
            0 if issued_at is not None else 1,
            _neg_ts(issued_at),
            -int(row["id"]),
        )

    return int(sorted(rows, key=sort_key)[0]["id"])


def _completeness(row: Mapping[str, Any]) -> int:
    score = 0
    for field in ("paid_at", "issued_at", "period_start", "period_end", "description", "line_items"):
        value = row.get(field)
        if value is None or value == "" or value == []:
            continue
        score += 1
    return score


def _neg_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        return -float(value.timestamp())
    return 0.0
