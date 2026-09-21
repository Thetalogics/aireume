"""PostgreSQL-backed atomic request idempotency."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backend.models.db_models import IdempotencyKey

KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
LEASE_SECONDS = 30
STATE_PROCESSING = "processing"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"


class IdempotencyUnavailableError(Exception):
    pass


class IdempotencyConflictError(Exception):
    pass


class IdempotencyInProgressError(Exception):
    pass


class IdempotencyKeyInvalidError(Exception):
    pass


def validate_idempotency_key(key: str) -> str:
    if key is None or not KEY_RE.fullmatch(key):
        raise IdempotencyKeyInvalidError("idempotency key must be 1-128 of [A-Za-z0-9._:-]")
    return key


@dataclass
class IdempotencyLease:
    key: str
    tenant_id: int
    endpoint: str
    owner_token: str
    replay: bool = False
    response_status: int | None = None
    response_body: Any = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def acquire_idempotency(
    db: Session,
    *,
    tenant_id: int,
    endpoint: str,
    key: str,
    fingerprint: str,
) -> IdempotencyLease:
    key = validate_idempotency_key(key)
    owner = uuid.uuid4().hex
    expires = _now() + timedelta(hours=24)
    lease = _now() + timedelta(seconds=LEASE_SECONDS)
    row = IdempotencyKey(
        key=key,
        tenant_id=tenant_id,
        endpoint=endpoint[:200],
        request_fingerprint=fingerprint,
        state=STATE_PROCESSING,
        owner_token=owner,
        lease_expires_at=lease,
        expires_at=expires,
    )
    try:
        db.add(row)
        db.flush()
        return IdempotencyLease(key=key, tenant_id=tenant_id, endpoint=endpoint[:200], owner_token=owner)
    except IntegrityError:
        db.rollback()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise IdempotencyUnavailableError("idempotency storage unavailable") from exc

    existing = (
        db.query(IdempotencyKey)
        .filter_by(key=key, tenant_id=tenant_id, endpoint=endpoint[:200])
        .one_or_none()
    )
    if existing is None:
        raise IdempotencyUnavailableError("idempotency row vanished after conflict")
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyConflictError("idempotency key reused with a different request")
    if existing.expires_at and existing.expires_at.replace(tzinfo=timezone.utc) < _now():
        existing.state = STATE_PROCESSING
        existing.owner_token = owner
        existing.lease_expires_at = lease
        existing.expires_at = expires
        existing.response_status = None
        existing.response_body = None
        db.flush()
        return IdempotencyLease(key=key, tenant_id=tenant_id, endpoint=endpoint[:200], owner_token=owner)
    if existing.state == STATE_COMPLETED and existing.response_status is not None:
        return IdempotencyLease(
            key=key,
            tenant_id=tenant_id,
            endpoint=endpoint[:200],
            owner_token=existing.owner_token or "",
            replay=True,
            response_status=existing.response_status,
            response_body=existing.response_body,
        )
    stale = (
        existing.state == STATE_PROCESSING
        and existing.lease_expires_at
        and existing.lease_expires_at.replace(tzinfo=timezone.utc) < _now()
    ) or existing.state == STATE_FAILED
    if stale:
        existing.state = STATE_PROCESSING
        existing.owner_token = owner
        existing.lease_expires_at = lease
        db.flush()
        return IdempotencyLease(
            key=key,
            tenant_id=tenant_id,
            endpoint=endpoint[:200],
            owner_token=owner,
            response_body=existing.response_body,
        )
    raise IdempotencyInProgressError("identical request is already in progress")


def complete_idempotency(
    db: Session,
    lease: IdempotencyLease,
    *,
    status: int,
    body: Any,
) -> None:
    row = (
        db.query(IdempotencyKey)
        .filter_by(key=lease.key, tenant_id=lease.tenant_id, endpoint=lease.endpoint)
        .one()
    )
    if row.owner_token != lease.owner_token:
        raise IdempotencyConflictError("idempotency lease is no longer owned")
    row.state = STATE_COMPLETED
    row.response_status = status
    row.response_body = body if isinstance(body, (dict, list)) else {"ok": True}
    db.flush()


def fail_idempotency(db: Session, lease: IdempotencyLease, *, body: Any | None = None) -> None:
    row = (
        db.query(IdempotencyKey)
        .filter_by(key=lease.key, tenant_id=lease.tenant_id, endpoint=lease.endpoint)
        .one_or_none()
    )
    if row is None or row.owner_token != lease.owner_token:
        return
    row.state = STATE_FAILED
    if body is not None:
        row.response_body = body
    db.flush()
