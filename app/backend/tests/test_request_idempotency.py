"""PostgreSQL atomic idempotency proofs."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.backend.models.db_models import IdempotencyKey
from app.backend.services.request_idempotency import (
    IdempotencyConflictError,
    IdempotencyKeyInvalidError,
    acquire_idempotency,
    complete_idempotency,
    validate_idempotency_key,
)
from app.backend.tests.reliability_env import require_postgres


@pytest.fixture()
def PgSession():
    from sqlalchemy import create_engine

    engine = create_engine(require_postgres(), pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_i8_overlong_key_rejected():
    with pytest.raises(IdempotencyKeyInvalidError):
        validate_idempotency_key("x" * 129)


def test_i1_concurrent_same_key_one_owner(PgSession):
    key = f"i1-{uuid.uuid4().hex[:12]}"
    owners = []
    errors = []

    def worker():
        db = PgSession()
        try:
            lease = acquire_idempotency(
                db, tenant_id=1, endpoint="POST:/i1", key=key, fingerprint="aaa"
            )
            db.commit()
            owners.append(lease.owner_token)
        except Exception as exc:
            db.rollback()
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(owners) == 1
    assert errors


def test_i2_fingerprint_conflict(PgSession):
    db = PgSession()
    key = f"i2-{uuid.uuid4().hex[:12]}"
    try:
        acquire_idempotency(db, tenant_id=2, endpoint="POST:/i2", key=key, fingerprint="one")
        db.commit()
        with pytest.raises(IdempotencyConflictError):
            acquire_idempotency(db, tenant_id=2, endpoint="POST:/i2", key=key, fingerprint="two")
    finally:
        db.close()


def test_i3_tenants_independent(PgSession):
    db = PgSession()
    key = f"i3-{uuid.uuid4().hex[:12]}"
    try:
        a = acquire_idempotency(db, tenant_id=11, endpoint="POST:/i3", key=key, fingerprint="x")
        b = acquire_idempotency(db, tenant_id=12, endpoint="POST:/i3", key=key, fingerprint="x")
        db.commit()
        assert a.owner_token != b.owner_token
    finally:
        db.close()


def test_i4_completed_replay(PgSession):
    db = PgSession()
    key = f"i4-{uuid.uuid4().hex[:12]}"
    try:
        lease = acquire_idempotency(db, tenant_id=3, endpoint="POST:/i4", key=key, fingerprint="z")
        complete_idempotency(db, lease, status=200, body={"ok": True})
        db.commit()
        replay = acquire_idempotency(db, tenant_id=3, endpoint="POST:/i4", key=key, fingerprint="z")
        db.commit()
        assert replay.replay is True
        assert replay.response_status == 200
    finally:
        db.close()


def test_i6_stale_processing_takeover(PgSession):
    from datetime import datetime, timedelta, timezone

    db = PgSession()
    key = f"i6-{uuid.uuid4().hex[:12]}"
    try:
        lease = acquire_idempotency(db, tenant_id=4, endpoint="POST:/i6", key=key, fingerprint="s")
        row = db.query(IdempotencyKey).filter_by(key=key, tenant_id=4, endpoint="POST:/i6").one()
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        taken = acquire_idempotency(db, tenant_id=4, endpoint="POST:/i6", key=key, fingerprint="s")
        db.commit()
        assert taken.replay is False
        assert taken.owner_token != lease.owner_token
    finally:
        db.close()


def test_i5_required_storage_failure_raises():
    from app.backend.services.request_idempotency import IdempotencyUnavailableError

    class Boom:
        def add(self, *_a, **_k):
            raise RuntimeError("db down")

        def flush(self):
            raise RuntimeError("db down")

        def rollback(self):
            pass

        def query(self, *_a, **_k):
            raise RuntimeError("db down")

    with pytest.raises(IdempotencyUnavailableError):
        acquire_idempotency(Boom(), tenant_id=1, endpoint="POST:/i5", key="i5-key", fingerprint="f")


def test_i7_conflict_is_typed_not_500(PgSession):
    db = PgSession()
    key = f"i7-{uuid.uuid4().hex[:12]}"
    try:
        acquire_idempotency(db, tenant_id=7, endpoint="POST:/i7", key=key, fingerprint="same")
        db.commit()
        with pytest.raises((IdempotencyConflictError, Exception)) as exc:
            acquire_idempotency(db, tenant_id=7, endpoint="POST:/i7", key=key, fingerprint="same")
        assert exc.type.__name__ in {"IdempotencyInProgressError", "IdempotencyConflictError"}
    finally:
        db.close()
