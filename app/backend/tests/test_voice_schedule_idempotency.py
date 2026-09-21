"""Voice scheduling idempotency V16–V21 (PostgreSQL)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.backend.models.db_models import Candidate, Tenant, VoiceScreeningSession
from app.backend.services.voice_schedule_idempotency import schedule_once
from app.backend.tests.reliability_env import require_postgres


@pytest.fixture()
def db():
    from sqlalchemy import create_engine

    engine = create_engine(require_postgres(), pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    tenant = Tenant(name="VSched", slug=f"vsched-{uuid.uuid4().hex[:10]}")
    session.add(tenant)
    session.flush()
    candidate = Candidate(tenant_id=tenant.id, name="Voice Candidate")
    session.add(candidate)
    session.commit()
    yield session, tenant, candidate
    session.close()


def _make(db, tenant, candidate, key, register, phone="15555550100"):
    def create():
        row = VoiceScreeningSession(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            phone_number=phone,
            direction="outbound",
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.add(row)
        return row

    return schedule_once(
        db,
        tenant_id=tenant.id,
        endpoint="POST:/api/voice/schedule",
        key=key,
        payload={"candidate_id": candidate.id, "phone": phone},
        create_session=create,
        register_scheduler=register,
    )


def test_v16_scheduler_failure_retry_reuses_session(db):
    session, tenant, candidate = db
    key = f"v16-{uuid.uuid4().hex[:10]}"
    calls = {"n": 0}

    def boom(_row):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("apscheduler down")

    with pytest.raises(HTTPException) as first:
        _make(session, tenant, candidate, key, boom)
    assert first.value.status_code == 503
    row, replayed = _make(session, tenant, candidate, key, boom)
    assert replayed is False or row.id
    assert session.query(VoiceScreeningSession).filter_by(tenant_id=tenant.id).count() == 1


def test_v20_same_key_different_payload_409(db):
    session, tenant, candidate = db
    key = f"v20-{uuid.uuid4().hex[:10]}"
    _make(session, tenant, candidate, key, lambda row: None, phone="15555550100")
    with pytest.raises(HTTPException) as exc:
        _make(session, tenant, candidate, key, lambda row: None, phone="15555550199")
    assert exc.value.status_code == 409


def test_v21_other_tenant_independent(db):
    session, tenant, candidate = db
    other = Tenant(name="OtherV", slug=f"ov-{uuid.uuid4().hex[:10]}")
    session.add(other)
    session.flush()
    cand2 = Candidate(tenant_id=other.id, name="Other")
    session.add(cand2)
    session.commit()
    key = "shared-voice-key"
    a, _ = _make(session, tenant, candidate, key, lambda row: None)
    b, _ = _make(session, other, cand2, key, lambda row: None)
    assert a.id != b.id


def test_v19_concurrent_same_key_one_session(db):
    session, tenant, candidate = db
    key = f"v19-{uuid.uuid4().hex[:10]}"
    errors = []

    def worker():
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import create_engine
        from app.backend.tests.reliability_env import require_postgres

        eng = create_engine(require_postgres(), pool_pre_ping=True)
        db2 = sessionmaker(bind=eng, expire_on_commit=False)()
        try:
            _make(db2, tenant, candidate, key, lambda row: None)
            db2.commit()
        except Exception as exc:
            db2.rollback()
            errors.append(exc)
        finally:
            db2.close()
            eng.dispose()

    import threading

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    count = session.query(VoiceScreeningSession).filter_by(tenant_id=tenant.id).count()
    assert count == 1


def test_v17_v18_scheduler_failure_reconcile_and_single_dispatch(db, monkeypatch):
    """End-to-end Phase 1.2: commit, add_job fail, retry, one job, one dispatch."""
    from datetime import timedelta

    from app.backend.models.db_models import VoiceTenantConfig
    from app.backend.services import voice_call_scheduler as scheduler
    from app.backend.services.voice_call_scheduler import schedule_voice_call
    from app.backend.tests.reliability_env import require_postgres
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    session, tenant, candidate = db
    if session.query(VoiceTenantConfig).filter_by(tenant_id=tenant.id).one_or_none() is None:
        session.add(VoiceTenantConfig(tenant_id=tenant.id))
        session.commit()

    PgSession = sessionmaker(bind=create_engine(require_postgres(), pool_pre_ping=True), expire_on_commit=False)
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)

    key = f"v17-{uuid.uuid4().hex[:10]}"
    when = datetime.now(timezone.utc) + timedelta(minutes=5)
    add_calls = {"n": 0}
    jobs = {}

    def create():
        row = VoiceScreeningSession(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            phone_number="15555550100",
            direction="outbound",
            status="scheduled",
            scheduled_at=when,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    class Job:
        def __init__(self, job_id):
            self.id = job_id

        def remove(self):
            jobs.pop(self.id, None)

    def add_job(_func, **kwargs):
        add_calls["n"] += 1
        if add_calls["n"] == 1:
            raise RuntimeError("apscheduler down after commit")
        jobs[kwargs["id"]] = kwargs
        return Job(kwargs["id"])

    monkeypatch.setattr(scheduler.voice_scheduler, "add_job", add_job)
    monkeypatch.setattr(scheduler.voice_scheduler, "get_jobs", lambda: [Job(jid) for jid in list(jobs)])

    def register(row):
        schedule_voice_call(row.id, when, expected_generation=row.result_generation or 1)

    from app.backend.services.voice_schedule_idempotency import schedule_once as _schedule

    payload = {"candidate_id": candidate.id, "phone": "15555550100"}
    with pytest.raises(HTTPException) as first:
        _schedule(
            session,
            tenant_id=tenant.id,
            endpoint="POST:/api/voice/schedule",
            key=key,
            payload=payload,
            create_session=create,
            register_scheduler=register,
        )
    assert first.value.status_code == 503
    first_id = session.query(VoiceScreeningSession).filter_by(tenant_id=tenant.id).one().id

    row, _ = _schedule(
        session,
        tenant_id=tenant.id,
        endpoint="POST:/api/voice/schedule",
        key=key,
        payload=payload,
        create_session=create,
        register_scheduler=register,
    )
    assert row.id == first_id
    assert session.query(VoiceScreeningSession).filter_by(tenant_id=tenant.id).count() == 1

    scheduler.reconcile_scheduled_voice_calls()
    gen = row.result_generation or 1
    job_id = f"voice_call_{first_id}_{gen}"
    own = [jid for jid in jobs if jid == job_id]
    assert len(own) == 1

    dispatches = []

    def fake_dispatch(_payload):
        dispatches.append(1)
        return {"success": True, "room_name": "r", "dispatch_id": "d1"}

    from app.backend.services import livekit_cloud_dispatch

    monkeypatch.setattr(livekit_cloud_dispatch, "is_cloud_voice_enabled", lambda: True)
    monkeypatch.setattr(livekit_cloud_dispatch, "dispatch_screening_call", fake_dispatch)

    scheduler.execute_scheduled_call(first_id, gen)
    assert len(dispatches) == 1
