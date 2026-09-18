"""Deterministic keeper selection for duplicate BillingEvent (provider, event_id).

Used by migration 077 tests. Production migration uses equivalent SQL.

Precedence (lower rank wins):
    success
    processing
    error / failed
    ignored
    other / empty

Ties keep the newest id (highest). A successful row is never discarded in
favor of an older failed row.
"""
from __future__ import annotations

_STATUS_RANK = {
    "success": 0,
    "processing": 1,
    "error": 2,
    "failed": 2,
    "ignored": 3,
}


def status_rank(result: str | None) -> int:
    return _STATUS_RANK.get((result or "").strip().lower(), 9)


def choose_keeper(rows: list[dict]) -> dict:
    """Return the row that must survive among duplicates of one event identity.

    Each row is a dict with at least ``id`` and ``result``.
    """
    if not rows:
        raise ValueError("no billing event rows")
    return min(rows, key=lambda r: (status_rank(r.get("result")), -int(r["id"])))


def rows_to_delete(rows: list[dict]) -> list[dict]:
    keeper = choose_keeper(rows)
    return [r for r in rows if r["id"] != keeper["id"]]
