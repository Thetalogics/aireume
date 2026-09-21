"""Durable voice-schedule operation identity on top of request idempotency."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.backend.models.db_models import VoiceScreeningSession
from app.backend.services.request_idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    acquire_idempotency,
    complete_idempotency,
    fail_idempotency,
)


def operation_key(request: Request | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if request is None:
        return None
    return request.headers.get("X-Idempotency-Key", "").strip() or None


def payload_fingerprint(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def schedule_once(
    db: Session,
    *,
    tenant_id: int,
    endpoint: str,
    key: str | None,
    payload: dict[str, Any],
    create_session: Callable[[], VoiceScreeningSession],
    register_scheduler: Callable[[VoiceScreeningSession], None],
) -> tuple[VoiceScreeningSession, bool]:
    if not key:
        session = create_session()
        db.commit()
        db.refresh(session)
        try:
            register_scheduler(session)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"scheduler registration failed: {exc}") from exc
        return session, False

    fingerprint = payload_fingerprint(payload)
    try:
        lease = acquire_idempotency(
            db, tenant_id=tenant_id, endpoint=endpoint, key=key, fingerprint=fingerprint
        )
    except IdempotencyKeyInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IdempotencyConflictError:
        raise HTTPException(status_code=409, detail="Idempotency key reused with different request")
    except IdempotencyInProgressError:
        raise HTTPException(status_code=409, detail="Request already in progress")

    if lease.replay and isinstance(lease.response_body, dict) and lease.response_body.get("session_id"):
        session = db.get(VoiceScreeningSession, int(lease.response_body["session_id"]))
        if session is None:
            raise HTTPException(status_code=409, detail="Idempotent session is no longer available")
        return session, True

    existing_id = None
    if isinstance(lease.response_body, dict):
        existing_id = lease.response_body.get("session_id")
    session = db.get(VoiceScreeningSession, int(existing_id)) if existing_id else None
    if session is None:
        session = create_session()
        db.flush()

    try:
        register_scheduler(session)
        complete_idempotency(
            db,
            lease,
            status=200,
            body={"session_id": session.id, "status": session.status},
        )
        db.commit()
        db.refresh(session)
    except HTTPException:
        fail_idempotency(db, lease, body={"session_id": session.id})
        db.commit()
        raise
    except Exception as exc:
        fail_idempotency(db, lease, body={"session_id": session.id})
        db.commit()
        raise HTTPException(status_code=503, detail=f"scheduler registration failed: {exc}") from exc
    return session, False
