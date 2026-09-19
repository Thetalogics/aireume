"""PostgreSQL reliability proofs. Independent sessions. No process mutex."""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.backend.models.db_models import (
    AnalysisJob,
    AnalysisResult,
    Candidate,
    QuotaReservation,
    ScreeningResult,
    Tenant,
    TranscriptAnalysis,
    VoiceScreeningSession,
    VoiceTenantConfig,
    VoiceTranscriptEntry,
)
from app.backend.services.queue_manager import QueueManager
from app.backend.services.reliability.quota_reservation import (
    reconcile_expired_quota_reservations,
)
from app.backend.services.reliability.stale import apply_narrative_if_generation
from app.backend.tests.reliability_env import require_postgres

pytestmark = [pytest.mark.timeout(60)]


@pytest.fixture(scope="module")
def pg_engine():
    url = require_postgres()
    engine = create_engine(url, pool_size=20, max_overflow=40)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


@pytest.fixture()
def PgSession(pg_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


def _tenant(db) -> Tenant:
    t = Tenant(
        name="r1",
        slug=f"rel-{uuid.uuid4().hex[:16]}",
        subscription_status="active",
        analyses_count_this_month=0,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _quiesce_active_jobs(db) -> None:
    """Keep queue-claim proofs repeatable on a reused local integration DB."""
    db.query(AnalysisJob).filter(
        AnalysisJob.status.in_(["queued", "processing", "retrying"])
    ).update(
        {"status": "cancelled", "worker_id": None},
        synchronize_session=False,
    )
    db.commit()


def _quota_job(db, tenant_id: int, status: str) -> AnalysisJob:
    token = uuid.uuid4().hex
    job = AnalysisJob(
        tenant_id=tenant_id,
        resume_hash=token,
        jd_hash=token[::-1],
        input_hash=uuid.uuid4().hex,
        status=status,
    )
    db.add(job)
    db.flush()
    return job


def _expired_reservation(
    db,
    tenant_id: int,
    *,
    job: AnalysisJob | None = None,
    quantity: int = 1,
) -> QuotaReservation:
    row = QuotaReservation(
        operation_id=str(job.id) if job else uuid.uuid4().hex,
        tenant_id=tenant_id,
        quantity=quantity,
        status="pending",
        job_id=str(job.id) if job else None,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(row)
    db.flush()
    return row


def _voice_session(
    db,
    tenant_id: int,
    *,
    status: str = "scheduled",
    generation: int = 1,
) -> VoiceScreeningSession:
    candidate = Candidate(
        tenant_id=tenant_id,
        name=f"Voice {uuid.uuid4().hex[:8]}",
    )
    db.add(candidate)
    db.flush()
    session = VoiceScreeningSession(
        tenant_id=tenant_id,
        candidate_id=candidate.id,
        phone_number="+15555550199",
        direction="outbound",
        status=status,
        result_generation=generation,
    )
    db.add(session)
    db.flush()
    return session


def test_r1_stale_narrative_rejected(PgSession):
    db = PgSession()
    try:
        t = _tenant(db)
        row = ScreeningResult(
            tenant_id=t.id,
            resume_text="r",
            jd_text="j",
            parsed_data="{}",
            analysis_result='{"keep": true}',
            analysis_generation=2,
            narrative_json='{"gen":2}',
            narrative_status="ready",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out = apply_narrative_if_generation(
            db,
            screening_result_id=row.id,
            tenant_id=t.id,
            expected_generation=1,
            narrative={"fit_summary": "stale"},
            status="ready",
        )
        db.commit()
        db.refresh(row)
        assert out.stale is True
        assert row.narrative_json == '{"gen":2}'
    finally:
        db.close()


def test_r2_concurrent_claim_one_owner(PgSession):
    db = PgSession()
    try:
        _quiesce_active_jobs(db)
        t = _tenant(db)
        job = AnalysisJob(
            tenant_id=t.id,
            resume_hash="a" * 64,
            jd_hash="b" * 64,
            input_hash=uuid.uuid4().hex,
            status="queued",
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    owners = []
    errors = []
    barrier = threading.Barrier(2)

    def worker(wid: str):
        sess = PgSession()
        mgr = QueueManager()
        mgr.worker_id = wid
        try:
            barrier.wait(timeout=10)
            claimed = __import__("asyncio").run(mgr.get_next_job(sess))
            owners.append(None if claimed is None else claimed.worker_id)
        except Exception as exc:
            errors.append(exc)
            owners.append(None)
        finally:
            sess.close()

    threads = [threading.Thread(target=worker, args=(f"w-{i}",)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert not errors, errors
    claimed_ids = [o for o in owners if o]
    assert len(claimed_ids) == 1
    check = PgSession()
    try:
        row = check.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
        assert row.status == "processing"
        assert row.worker_id in claimed_ids
    finally:
        check.close()


def test_r3_expired_lease_reclaim(PgSession):
    db = PgSession()
    try:
        t = _tenant(db)
        now = datetime.now(timezone.utc)
        job = AnalysisJob(
            tenant_id=t.id,
            resume_hash="c" * 64,
            jd_hash="d" * 64,
            input_hash=uuid.uuid4().hex,
            status="processing",
            worker_id="dead-worker",
            worker_heartbeat=now - timedelta(hours=2),
            leased_until=now - timedelta(minutes=1),
            retry_count=0,
            max_retries=3,
        )
        db.add(job)
        db.commit()
        job_id = job.id
        mgr = QueueManager()
        mgr.stale_job_timeout_seconds = 60
        __import__("asyncio").run(mgr.recover_stale_jobs(db))
        row = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
        assert row.status == "retrying"
        assert row.worker_id is None
    finally:
        db.close()


def test_r4_active_lease_not_reclaimed(PgSession):
    db = PgSession()
    try:
        t = _tenant(db)
        now = datetime.now(timezone.utc)
        job = AnalysisJob(
            tenant_id=t.id,
            resume_hash="e" * 64,
            jd_hash="f" * 64,
            input_hash=uuid.uuid4().hex,
            status="processing",
            worker_id="alive",
            worker_heartbeat=now,
            leased_until=now + timedelta(minutes=10),
            retry_count=0,
            max_retries=3,
        )
        db.add(job)
        db.commit()
        job_id = job.id
        mgr = QueueManager()
        __import__("asyncio").run(mgr.recover_stale_jobs(db))
        row = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
        assert row.status == "processing"
        assert row.worker_id == "alive"
    finally:
        db.close()


def test_r8_quota_concurrency_one_success(PgSession):
    from sqlalchemy import update

    db = PgSession()
    try:
        t = _tenant(db)
        t.analyses_count_this_month = 19
        db.commit()
        tid = t.id
    finally:
        db.close()

    results = []
    barrier = threading.Barrier(2)

    def worker():
        sess = PgSession()
        try:
            barrier.wait(timeout=10)
            result = sess.execute(
                update(Tenant)
                .where(Tenant.id == tid, Tenant.analyses_count_this_month + 1 <= 20)
                .values(analyses_count_this_month=Tenant.analyses_count_this_month + 1)
            )
            sess.commit()
            results.append(result.rowcount == 1)
        finally:
            sess.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_r12_lost_lease_skips_commit(PgSession):
    db = PgSession()
    try:
        t = _tenant(db)
        now = datetime.now(timezone.utc)
        job = AnalysisJob(
            tenant_id=t.id,
            resume_hash="g" * 64,
            jd_hash="h" * 64,
            input_hash=uuid.uuid4().hex,
            status="processing",
            worker_id="other",
            leased_until=now + timedelta(minutes=5),
        )
        db.add(job)
        db.commit()
        mgr = QueueManager()
        mgr.worker_id = "this-worker"
        assert mgr._owns_lease(db, job) is False
    finally:
        db.close()


def test_p7_transcript_stale_write(PgSession):
    from app.backend.services.reliability.stale import apply_transcript_if_generation

    db = PgSession()
    try:
        tenant = _tenant(db)
        row = TranscriptAnalysis(
            tenant_id=tenant.id,
            transcript_text="generation two",
            analysis_result='{"generation":2}',
            analysis_generation=2,
        )
        db.add(row)
        db.commit()
        outcome = apply_transcript_if_generation(
            db,
            transcript_analysis_id=row.id,
            tenant_id=tenant.id,
            expected_generation=1,
            analysis_result={"generation": 1},
        )
        db.commit()
        db.refresh(row)
        assert outcome.stale is True
        assert row.analysis_result == '{"generation":2}'
    finally:
        db.close()


def test_p8_voice_completion_stale_and_duplicate(PgSession):
    from app.backend.services.reliability.stale import claim_voice_completion

    db = PgSession()
    try:
        tenant = _tenant(db)
        candidate = Candidate(tenant_id=tenant.id, name="Voice Candidate")
        db.add(candidate)
        db.flush()
        session = VoiceScreeningSession(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            phone_number="+15555550125",
            status="in_progress",
            result_generation=2,
        )
        db.add(session)
        db.commit()
        stale = claim_voice_completion(
            db,
            session_id=session.id,
            expected_generation=1,
            event_id="old-event",
        )
        accepted = claim_voice_completion(
            db,
            session_id=session.id,
            expected_generation=2,
            event_id="new-event",
        )
        duplicate = claim_voice_completion(
            db,
            session_id=session.id,
            expected_generation=2,
            event_id="new-event",
        )
        assert stale.stale is True
        assert accepted.accepted is True
        assert duplicate.duplicate is True
    finally:
        db.rollback()
        db.close()


def test_p10_quota_transaction_crash_rolls_back(PgSession):
    from app.backend.services.reliability.quota_reservation import reserve_analysis_quota

    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant_id = tenant.id
        reserve_analysis_quota(
            db,
            tenant_id=tenant_id,
            user_id=None,
            quantity=1,
            operation_id="quota-crash",
            analyses_limit=10,
        )
        db.rollback()
        db.expire_all()
        assert db.get(Tenant, tenant_id).analyses_count_this_month == 0
        assert (
            db.query(QuotaReservation)
            .filter_by(tenant_id=tenant_id, operation_id="quota-crash")
            .count()
            == 0
        )
    finally:
        db.close()


def test_p11_quota_retry_reuses_one_reservation(PgSession):
    from app.backend.services.reliability.quota_reservation import reserve_analysis_quota

    db = PgSession()
    try:
        tenant = _tenant(db)
        first = reserve_analysis_quota(
            db,
            tenant_id=tenant.id,
            user_id=None,
            quantity=1,
            operation_id="quota-retry",
            analyses_limit=10,
        )
        db.commit()
        second = reserve_analysis_quota(
            db,
            tenant_id=tenant.id,
            user_id=None,
            quantity=1,
            operation_id="quota-retry",
            analyses_limit=10,
        )
        db.commit()
        db.refresh(tenant)
        assert first.id == second.id
        assert tenant.analyses_count_this_month == 1
    finally:
        db.close()


def test_p9_quota_limit_concurrency_one_reservation(PgSession):
    from app.backend.services.reliability.quota_reservation import (
        QuotaLimitExceeded,
        reserve_analysis_quota,
    )

    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant_id = tenant.id
    finally:
        db.close()

    barrier = threading.Barrier(2)
    outcomes = []

    def reserve(operation_id):
        session = PgSession()
        try:
            barrier.wait(timeout=10)
            reserve_analysis_quota(
                session,
                tenant_id=tenant_id,
                user_id=None,
                quantity=1,
                operation_id=operation_id,
                analyses_limit=1,
            )
            session.commit()
            outcomes.append("accepted")
        except QuotaLimitExceeded:
            session.rollback()
            outcomes.append("rejected")
        finally:
            session.close()

    threads = [
        threading.Thread(target=reserve, args=(f"quota-race-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["accepted", "rejected"]

    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 1
        assert (
            check.query(QuotaReservation)
            .filter_by(tenant_id=tenant_id, status="pending")
            .count()
            == 1
        )
    finally:
        check.close()


@pytest.mark.asyncio
async def test_p6_delayed_narrative_spawn_is_stale(PgSession, monkeypatch):
    from app.backend.db import database as database_module
    from app.backend.services import hybrid_pipeline

    db = PgSession()
    try:
        tenant = _tenant(db)
        row = ScreeningResult(
            tenant_id=tenant.id,
            resume_text="r",
            jd_text="j",
            parsed_data="{}",
            analysis_result='{"generation":1}',
            analysis_generation=1,
            narrative_status="pending",
        )
        db.add(row)
        db.commit()
        row_id, tenant_id = row.id, tenant.id
    finally:
        db.close()

    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def delayed(_context):
        provider_started.set()
        await release_provider.wait()
        return {"fit_summary": "generation one"}

    monkeypatch.setattr(database_module, "SessionLocal", PgSession)
    monkeypatch.setattr(hybrid_pipeline, "explain_with_llm", delayed)
    monkeypatch.setattr(
        "app.backend.services.background_enrichment.schedule_post_narrative_enrichment",
        lambda *_args, **_kwargs: None,
    )
    task = asyncio.create_task(
        hybrid_pipeline._background_llm_narrative(
            screening_result_id=row_id,
            tenant_id=tenant_id,
            llm_context={},
            python_result={},
            expected_analysis_generation=1,
        )
    )
    await provider_started.wait()
    newer = PgSession()
    try:
        current = newer.get(ScreeningResult, row_id)
        current.analysis_generation = 2
        current.analysis_result = '{"generation":2}'
        current.narrative_json = '{"generation":2}'
        current.narrative_status = "ready"
        newer.commit()
    finally:
        newer.close()
    release_provider.set()
    await task

    check = PgSession()
    try:
        current = check.get(ScreeningResult, row_id)
        assert current.analysis_generation == 2
        assert current.analysis_result == '{"generation":2}'
        assert current.narrative_json == '{"generation":2}'
    finally:
        check.close()


@pytest.mark.asyncio
async def test_p4_lost_lease_cannot_write_domain_result(PgSession, monkeypatch):
    from app.backend.services.queue_analysis_service import complete_queue_job

    db = PgSession()
    try:
        _quiesce_active_jobs(db)
        tenant = _tenant(db)
        manager = QueueManager()
        manager.worker_id = "worker-a"
        job_id = await manager.enqueue_job(
            tenant_id=tenant.id,
            resume_text="Candidate Python",
            resume_filename="lease.pdf",
            jd_text="Need Python",
            db=db,
            parsed_resume_cache={
                "parsed_data": {
                    "raw_text": "Candidate Python",
                    "contact_info": {"name": "Lease Candidate"},
                    "skills": ["python"],
                }
            },
        )
        db.commit()
        job = await manager.get_next_job(db)
        assert job.id == job_id

        async def provider_then_lease_transfer(**_kwargs):
            transfer = PgSession()
            try:
                current = transfer.get(AnalysisJob, job_id)
                current.worker_id = "worker-b"
                current.leased_until = datetime.now(timezone.utc) + timedelta(minutes=5)
                transfer.commit()
            finally:
                transfer.close()
            return {
                "fit_score": 80,
                "final_recommendation": "Hire",
                "_parsed_data": {
                    "raw_text": "Candidate Python",
                    "contact_info": {"name": "Lease Candidate"},
                    "skills": ["python"],
                },
                "_gap_analysis": {},
            }

        monkeypatch.setattr(
            "app.backend.routes.analyze._process_single_resume",
            provider_then_lease_transfer,
        )
        completed = await complete_queue_job(
            job_id,
            db,
            expected_worker_id="worker-a",
        )
        assert completed is False
        db.expire_all()
        current = db.get(AnalysisJob, job_id)
        assert current.worker_id == "worker-b"
        assert current.status == "processing"
        assert (
            db.query(ScreeningResult)
            .filter(ScreeningResult.tenant_id == tenant.id)
            .count()
            == 0
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_f1_enqueue_failure_rolls_back_quota_and_reservation(PgSession, monkeypatch):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant_id = tenant.id
        manager = QueueManager()
        original_add = db.add

        def fail_on_job(instance, *args, **kwargs):
            if isinstance(instance, AnalysisJob):
                raise RuntimeError("injected failure before enqueue")
            return original_add(instance, *args, **kwargs)

        monkeypatch.setattr(db, "add", fail_on_job)
        with pytest.raises(RuntimeError, match="injected failure"):
            await manager.enqueue_job(
                tenant_id=tenant_id,
                resume_text="failure resume",
                resume_filename="failure.pdf",
                jd_text="failure jd",
                db=db,
            )
    finally:
        db.close()

    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 0
        assert (
            check.query(QuotaReservation)
            .filter(QuotaReservation.tenant_id == tenant_id)
            .count()
            == 0
        )
    finally:
        check.close()


@pytest.mark.asyncio
async def test_p8_voice_assessment_cannot_cross_generation(PgSession, monkeypatch):
    from app.backend.services.voice_screening_service import process_completed_call

    db = PgSession()
    try:
        tenant = _tenant(db)
        candidate = Candidate(tenant_id=tenant.id, name="Delayed Voice")
        db.add(candidate)
        db.flush()
        session = VoiceScreeningSession(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            phone_number="+15555550126",
            status="completed",
            result_generation=1,
        )
        db.add(session)
        db.flush()
        db.add(
            VoiceTranscriptEntry(
                session_id=session.id,
                speaker="candidate",
                text="Generation one answer",
                timestamp=datetime.now(timezone.utc),
            )
        )
        db.commit()
        session_id = session.id

        provider_started = asyncio.Event()
        release_provider = asyncio.Event()

        async def delayed_assessment(**_kwargs):
            provider_started.set()
            await release_provider.wait()
            return {"overall_score": 99, "overall_recommendation": "hire"}

        monkeypatch.setattr(
            "app.backend.services.voice_screening_service.generate_post_call_assessment",
            delayed_assessment,
        )
        task = asyncio.create_task(
            process_completed_call(db, session_id, expected_generation=1)
        )
        await provider_started.wait()
        newer = PgSession()
        try:
            current = newer.get(VoiceScreeningSession, session_id)
            current.result_generation = 2
            current.status = "in_progress"
            current.assessment_json = '{"generation":2}'
            newer.commit()
        finally:
            newer.close()
        release_provider.set()
        assert await task is False
        db.expire_all()
        current = db.get(VoiceScreeningSession, session_id)
        assert current.result_generation == 2
        assert current.status == "in_progress"
        assert current.assessment_json == '{"generation":2}'
    finally:
        db.close()


def test_p12_abandoned_reservation_released_once(PgSession):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 3
        reservation = _expired_reservation(db, tenant.id)
        db.commit()
        reservation_id = reservation.id
        tenant_id = tenant.id

        assert reconcile_expired_quota_reservations(db) == 1
        assert reconcile_expired_quota_reservations(db) == 0
        db.expire_all()
        assert db.get(Tenant, tenant_id).analyses_count_this_month == 2
        assert db.get(QuotaReservation, reservation_id).status == "released"
    finally:
        db.close()


@pytest.mark.parametrize("job_status", ["queued", "processing", "retrying"])
def test_p12_active_operation_keeps_reservation(PgSession, job_status):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 1
        job = _quota_job(db, tenant.id, job_status)
        reservation = _expired_reservation(db, tenant.id, job=job)
        db.commit()

        assert reconcile_expired_quota_reservations(db) == 0
        db.refresh(tenant)
        db.refresh(reservation)
        assert tenant.analyses_count_this_month == 1
        assert reservation.status == "pending"
        other = PgSession()
        try:
            other.query(QuotaReservation).filter(
                QuotaReservation.id == reservation.id
            ).with_for_update(nowait=True).one()
            other.rollback()
        finally:
            other.close()
    finally:
        db.close()


def test_p12_successful_operation_keeps_pending_reservation(PgSession):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 1
        job = _quota_job(db, tenant.id, "completed")
        db.add(
            AnalysisResult(
                job_id=job.id,
                tenant_id=tenant.id,
                fit_score=80,
                final_recommendation="hire",
                analysis_data={
                    "fit_score": 80,
                    "strengths": [],
                    "weaknesses": [],
                    "matched_skills": [],
                },
                parsed_resume={},
                parsed_jd={},
            )
        )
        reservation = _expired_reservation(db, tenant.id, job=job)
        db.commit()

        assert reconcile_expired_quota_reservations(db) == 0
        db.refresh(tenant)
        db.refresh(reservation)
        assert tenant.analyses_count_this_month == 1
        assert reservation.status == "pending"
    finally:
        db.close()


def test_p12_terminal_failed_operation_released_once(PgSession):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 1
        job = _quota_job(db, tenant.id, "failed")
        reservation = _expired_reservation(db, tenant.id, job=job)
        db.commit()

        assert reconcile_expired_quota_reservations(db) == 1
        assert reconcile_expired_quota_reservations(db) == 0
        db.expire_all()
        assert tenant.analyses_count_this_month == 0
        assert reservation.status == "released"
    finally:
        db.close()


def test_p12_concurrent_reconcilers_release_once(PgSession):
    from app.backend.services.metrics import QUOTA_RECONCILE_RELEASE_TOTAL

    setup = PgSession()
    try:
        tenant = _tenant(setup)
        tenant.analyses_count_this_month = 1
        reservation = _expired_reservation(setup, tenant.id)
        setup.commit()
        tenant_id = tenant.id
        reservation_id = reservation.id
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    releases: list[int] = []
    errors: list[Exception] = []
    metric_before = QUOTA_RECONCILE_RELEASE_TOTAL._value.get()

    def reconcile():
        session = PgSession()
        try:
            barrier.wait(timeout=10)
            releases.append(reconcile_expired_quota_reservations(session))
        except Exception as exc:
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=reconcile) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sorted(releases) == [0, 1]
    assert QUOTA_RECONCILE_RELEASE_TOTAL._value.get() - metric_before == 1
    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 0
        assert check.get(QuotaReservation, reservation_id).status == "released"
    finally:
        check.close()


def test_p12_reconciliation_rollback_is_atomic(PgSession, monkeypatch):
    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 1
        reservation = _expired_reservation(db, tenant.id)
        db.commit()
        tenant_id = tenant.id
        reservation_id = reservation.id

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            reconcile_expired_quota_reservations(db)
        db.rollback()
    finally:
        db.close()

    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 1
        reservation = check.get(QuotaReservation, reservation_id)
        assert reservation.status == "pending"
        reservation.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        check.commit()
    finally:
        check.close()


def test_p12_reconciliation_is_tenant_isolated(PgSession):
    db = PgSession()
    try:
        tenant_a = _tenant(db)
        tenant_b = _tenant(db)
        tenant_a.analyses_count_this_month = 1
        tenant_b.analyses_count_this_month = 1
        reservation_a = _expired_reservation(db, tenant_a.id)
        reservation_b = QuotaReservation(
            operation_id=uuid.uuid4().hex,
            tenant_id=tenant_b.id,
            quantity=1,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(reservation_b)
        db.commit()

        assert reconcile_expired_quota_reservations(db) == 1
        db.expire_all()
        assert tenant_a.analyses_count_this_month == 0
        assert reservation_a.status == "released"
        assert tenant_b.analyses_count_this_month == 1
        assert reservation_b.status == "pending"
    finally:
        db.close()


def test_p12_zero_usage_does_not_release_or_underflow(PgSession):
    db = PgSession()
    try:
        tenant = _tenant(db)
        reservation = _expired_reservation(db, tenant.id)
        db.commit()

        assert reconcile_expired_quota_reservations(db) == 0
        db.expire_all()
        assert tenant.analyses_count_this_month == 0
        assert reservation.status == "pending"
    finally:
        db.close()


def test_v2_voice_attempt_invalidation_increments_once_and_resets_outputs(PgSession):
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="failed", generation=3)
        session.completion_event_id = "old-event"
        session.assessment_json = '{"old":true}'
        session.transcript_json = '{"old":true}'
        session.duration_seconds = 10
        session.call_sid = "old-call"
        session.error_log = "old failure"

        generation = invalidate_voice_attempt(
            session,
            next_status="scheduled",
            clear_attempt_outputs=True,
        )
        db.commit()
        db.refresh(session)

        assert generation == 4
        assert session.result_generation == 4
        assert session.status == "scheduled"
        assert session.completion_event_id is None
        assert session.assessment_json is None
        assert session.transcript_json is None
        assert session.duration_seconds is None
        assert session.call_sid is None
        assert session.error_log is None
    finally:
        db.close()


def test_v7_cancelled_lifecycle_rejects_completion_claim(PgSession):
    from app.backend.services.reliability.stale import claim_voice_completion

    db = PgSession()
    try:
        tenant = _tenant(db)
        candidate = Candidate(tenant_id=tenant.id, name="Cancelled Candidate")
        db.add(candidate)
        db.flush()
        session = VoiceScreeningSession(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            phone_number="+15555550131",
            status="cancelled",
            result_generation=2,
        )
        db.add(session)
        db.commit()

        outcome = claim_voice_completion(
            db,
            session_id=session.id,
            expected_generation=2,
            event_id="cancelled-event",
        )

        assert outcome.stale is True
        assert outcome.accepted is False
        assert session.completion_event_id is None
        assert session.status == "cancelled"
    finally:
        db.rollback()
        db.close()


def test_q1_q3_cancelled_job_quota_release_is_successful_and_idempotent(PgSession):
    from app.backend.routes.analyze_helpers import release_job_analysis_quota

    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 1
        job = _quota_job(db, tenant.id, "cancelled")
        reservation = _expired_reservation(db, tenant.id, job=job)
        job.job_config = {
            "quota_reserved": True,
            "quota_operation_id": reservation.operation_id,
        }
        db.commit()

        assert release_job_analysis_quota(db, job) is True
        db.commit()
        assert release_job_analysis_quota(db, job) is False
        db.commit()

        db.expire_all()
        assert db.get(Tenant, tenant.id).analyses_count_this_month == 0
        assert db.get(QuotaReservation, reservation.id).status == "released"
        assert db.get(AnalysisJob, job.id).job_config["quota_released"] is True
    finally:
        db.close()


def test_q2_direct_terminal_quota_underflow_preserves_pending_and_marker(PgSession):
    from app.backend.routes.analyze_helpers import release_job_analysis_quota

    db = PgSession()
    try:
        tenant = _tenant(db)
        tenant.analyses_count_this_month = 0
        job = _quota_job(db, tenant.id, "failed")
        reservation = _expired_reservation(db, tenant.id, job=job)
        job.job_config = {
            "quota_reserved": True,
            "quota_operation_id": reservation.operation_id,
        }
        db.commit()

        assert release_job_analysis_quota(db, job) is False
        db.commit()

        db.expire_all()
        assert db.get(Tenant, tenant.id).analyses_count_this_month == 0
        pending = db.get(QuotaReservation, reservation.id)
        assert pending.status == "pending"
        assert not db.get(AnalysisJob, job.id).job_config.get("quota_released")
        pending.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
    finally:
        db.close()


def test_q5_direct_quota_release_rollback_is_atomic(PgSession, monkeypatch):
    from app.backend.routes.analyze_helpers import release_job_analysis_quota

    setup = PgSession()
    try:
        tenant = _tenant(setup)
        tenant.analyses_count_this_month = 1
        job = _quota_job(setup, tenant.id, "failed")
        reservation = _expired_reservation(setup, tenant.id, job=job)
        job.job_config = {
            "quota_reserved": True,
            "quota_operation_id": reservation.operation_id,
        }
        setup.commit()
        tenant_id, job_id, reservation_id = tenant.id, job.id, reservation.id
    finally:
        setup.close()

    release = PgSession()
    try:
        job = release.get(AnalysisJob, job_id)
        assert release_job_analysis_quota(release, job) is True
        monkeypatch.setattr(
            release,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("injected commit failure")),
        )
        with pytest.raises(RuntimeError, match="injected commit failure"):
            release.commit()
        release.rollback()
    finally:
        release.close()

    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 1
        pending = check.get(QuotaReservation, reservation_id)
        assert pending.status == "pending"
        assert not check.get(AnalysisJob, job_id).job_config.get("quota_released")
        pending.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        check.commit()
    finally:
        check.close()


def test_q4_terminal_release_and_reconciler_decrement_once(PgSession):
    from app.backend.routes.analyze_helpers import release_job_analysis_quota

    setup = PgSession()
    try:
        tenant = _tenant(setup)
        tenant.analyses_count_this_month = 1
        job = _quota_job(setup, tenant.id, "failed")
        reservation = _expired_reservation(setup, tenant.id, job=job)
        job.job_config = {
            "quota_reserved": True,
            "quota_operation_id": reservation.operation_id,
        }
        setup.commit()
        tenant_id, job_id, reservation_id = tenant.id, job.id, reservation.id
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    errors = []
    results = []

    def terminal_release():
        db = PgSession()
        try:
            barrier.wait(timeout=10)
            results.append(release_job_analysis_quota(db, db.get(AnalysisJob, job_id)))
            db.commit()
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    def reconcile():
        db = PgSession()
        try:
            barrier.wait(timeout=10)
            results.append(
                reconcile_expired_quota_reservations(db, commit=True) == 1
            )
        except Exception as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [threading.Thread(target=terminal_release), threading.Thread(target=reconcile)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sum(results) == 1
    check = PgSession()
    try:
        assert check.get(Tenant, tenant_id).analyses_count_this_month == 0
        assert check.get(QuotaReservation, reservation_id).status == "released"
        assert check.get(AnalysisJob, job_id).job_config["quota_released"] is True
    finally:
        check.close()


def test_v1_cancelled_session_old_scheduler_callback_does_not_dispatch(
    PgSession, monkeypatch
):
    from app.backend.services import livekit_cloud_dispatch
    from app.backend.services import voice_call_scheduler as scheduler
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id)
    session_id = session.id
    invalidate_voice_attempt(session, next_status="cancelled")
    db.commit()
    db.close()

    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(livekit_cloud_dispatch, "is_cloud_voice_enabled", lambda: True)
    monkeypatch.setattr(
        livekit_cloud_dispatch,
        "dispatch_screening_call",
        lambda payload: calls.append(payload) or {"success": True},
    )
    scheduler.execute_scheduled_call(session_id, 1)
    assert calls == []


def test_v3_bulk_cancel_invalidates_each_generation(PgSession, monkeypatch):
    from types import SimpleNamespace
    from app.backend.models.schemas import BulkCancelRequest
    from app.backend.routes.voice import bulk_cancel_sessions

    db = PgSession()
    try:
        tenant = _tenant(db)
        first = _voice_session(db, tenant.id)
        second = _voice_session(db, tenant.id)
        db.commit()
        monkeypatch.setattr(
            "app.backend.services.voice_call_scheduler.cancel_pending_retries",
            lambda _session_id: None,
        )

        result = bulk_cancel_sessions(
            BulkCancelRequest(session_ids=[first.id, second.id]),
            SimpleNamespace(tenant_id=tenant.id),
            db,
        )

        assert result == {"cancelled": 2, "skipped": 0}
        db.refresh(first)
        db.refresh(second)
        assert (first.status, first.result_generation) == ("cancelled", 2)
        assert (second.status, second.result_generation) == ("cancelled", 2)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_v4_consent_denial_invalidates_active_generation(PgSession):
    from app.backend.routes.interviews import record_candidate_consent

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="in_progress")
        db.commit()

        await record_candidate_consent(session.id, {"consent": "denied"}, db)

        db.refresh(session)
        assert session.consent_status == "denied"
        assert session.status == "cancelled"
        assert session.result_generation == 2
    finally:
        db.close()


@pytest.mark.asyncio
async def test_v5_manual_retry_increments_generation_once(PgSession, monkeypatch):
    from types import SimpleNamespace
    from app.backend.routes.interviews import retry_interview_session

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="failed")
        session.interview_depth = "quick"
        session.completion_event_id = "old"
        db.commit()
        scheduled = []
        monkeypatch.setattr(
            "app.backend.services.voice_call_scheduler.schedule_voice_call",
            lambda session_id, scheduled_at, **kwargs: scheduled.append(
                (session_id, kwargs["expected_generation"])
            ),
        )

        await retry_interview_session(
            session.id,
            SimpleNamespace(tenant_id=tenant.id),
            db,
        )

        db.refresh(session)
        assert (session.result_generation, session.retry_count) == (2, 1)
        assert session.completion_event_id is None
        assert scheduled == [(session.id, 2)]
    finally:
        db.close()


def test_v6_automatic_retry_increments_and_binds_generation(PgSession, monkeypatch):
    from app.backend.services import voice_call_scheduler as scheduler

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="failed", generation=5)
        db.add(
            VoiceTenantConfig(
                tenant_id=tenant.id,
                max_retries=3,
                retry_intervals=[0],
                timezone="UTC",
            )
        )
        db.commit()
        session_id = session.id
    finally:
        db.close()

    jobs = []
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(
        scheduler.voice_scheduler,
        "add_job",
        lambda _func, **kwargs: jobs.append(kwargs),
    )
    scheduler.process_voice_retries()

    check = PgSession()
    try:
        current = check.get(VoiceScreeningSession, session_id)
        assert (current.result_generation, current.retry_count) == (6, 1)
        own_job = next(job for job in jobs if job["args"][0] == session_id)
        assert own_job["id"] == f"voice_call_{session_id}_6"
        assert own_job["args"] == [session_id, 6]
    finally:
        check.close()


@pytest.mark.asyncio
async def test_v8_old_callback_after_manual_retry_is_stale(PgSession, monkeypatch):
    from types import SimpleNamespace
    from app.backend.routes.interviews import retry_interview_session
    from app.backend.services.reliability.stale import claim_voice_completion

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="failed")
        session.interview_depth = "quick"
        db.commit()
        monkeypatch.setattr(
            "app.backend.services.voice_call_scheduler.schedule_voice_call",
            lambda *_args, **_kwargs: True,
        )
        await retry_interview_session(
            session.id,
            SimpleNamespace(tenant_id=tenant.id),
            db,
        )

        old = claim_voice_completion(
            db, session_id=session.id, expected_generation=1, event_id="old"
        )
        new = claim_voice_completion(
            db, session_id=session.id, expected_generation=2, event_id="new"
        )
        assert old.stale is True
        assert new.accepted is True
    finally:
        db.rollback()
        db.close()


def test_v9_old_callback_after_automatic_retry_is_stale(PgSession):
    from app.backend.services.reliability.stale import claim_voice_completion
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="failed")
        invalidate_voice_attempt(
            session, next_status="scheduled", clear_attempt_outputs=True
        )
        session.retry_count += 1
        db.commit()

        old = claim_voice_completion(
            db, session_id=session.id, expected_generation=1, event_id="old-auto"
        )
        new = claim_voice_completion(
            db, session_id=session.id, expected_generation=2, event_id="new-auto"
        )
        assert old.stale is True
        assert new.accepted is True
    finally:
        db.rollback()
        db.close()


def test_v10_internal_patch_rejects_cancelled_same_generation(PgSession):
    from fastapi import HTTPException
    from app.backend.routes.voice import VoiceSessionUpdate, update_voice_session

    db = PgSession()
    try:
        tenant = _tenant(db)
        session = _voice_session(db, tenant.id, status="cancelled", generation=2)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            update_voice_session(
                session.id,
                VoiceSessionUpdate(expected_generation=2, status="in_progress"),
                db,
                None,
            )
        assert exc.value.status_code == 409
        db.refresh(session)
        assert session.status == "cancelled"
    finally:
        db.close()


def test_v11_old_scheduler_generation_after_retry_does_not_dispatch(
    PgSession, monkeypatch
):
    from app.backend.services import livekit_cloud_dispatch
    from app.backend.services import voice_call_scheduler as scheduler
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id, status="failed")
    session_id = session.id
    invalidate_voice_attempt(session, next_status="scheduled", clear_attempt_outputs=True)
    db.commit()
    db.close()

    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(livekit_cloud_dispatch, "is_cloud_voice_enabled", lambda: True)
    monkeypatch.setattr(
        livekit_cloud_dispatch,
        "dispatch_screening_call",
        lambda payload: calls.append(payload) or {"success": True},
    )
    scheduler.execute_scheduled_call(session_id, 1)
    assert calls == []


def test_v12_cancel_after_ringing_claim_skips_provider(PgSession, monkeypatch):
    from app.backend.services import livekit_cloud_dispatch
    from app.backend.services import voice_call_scheduler as scheduler
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id)
    session_id = session.id
    db.commit()
    db.close()
    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(livekit_cloud_dispatch, "is_cloud_voice_enabled", lambda: True)
    monkeypatch.setattr(
        livekit_cloud_dispatch,
        "dispatch_screening_call",
        lambda payload: calls.append(payload) or {"success": True},
    )

    def cancel_before_provider(_session_id, _generation):
        cancel_db = PgSession()
        current = cancel_db.get(VoiceScreeningSession, session_id)
        invalidate_voice_attempt(current, next_status="cancelled")
        cancel_db.commit()
        cancel_db.close()
        return False

    monkeypatch.setattr(
        scheduler, "_dispatch_attempt_is_current", cancel_before_provider
    )
    scheduler.execute_scheduled_call(session_id, 1)
    assert calls == []


def test_v13_post_provider_old_generation_write_is_rejected(PgSession, monkeypatch):
    from app.backend.services import livekit_cloud_dispatch
    from app.backend.services import voice_call_scheduler as scheduler
    from app.backend.services.reliability.voice_attempt import invalidate_voice_attempt

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id)
    session_id = session.id
    db.commit()
    db.close()
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(livekit_cloud_dispatch, "is_cloud_voice_enabled", lambda: True)

    def dispatch_then_cancel(_payload):
        cancel_db = PgSession()
        current = cancel_db.get(VoiceScreeningSession, session_id)
        invalidate_voice_attempt(current, next_status="cancelled")
        cancel_db.commit()
        cancel_db.close()
        return {"success": True}

    monkeypatch.setattr(
        livekit_cloud_dispatch, "dispatch_screening_call", dispatch_then_cancel
    )
    scheduler.execute_scheduled_call(session_id, 1)
    check = PgSession()
    try:
        current = check.get(VoiceScreeningSession, session_id)
        assert (current.status, current.result_generation) == ("cancelled", 2)
    finally:
        check.close()


def test_v14_scheduler_registration_failure_is_recovered(PgSession, monkeypatch):
    from app.backend.services import voice_call_scheduler as scheduler

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id, status="pending")
    session_id = session.id
    db.commit()
    db.close()
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    monkeypatch.setattr(
        scheduler.voice_scheduler,
        "add_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(scheduler.VoiceScheduleRegistrationError):
        scheduler.schedule_voice_call(
            session_id,
            datetime.now(timezone.utc) + timedelta(minutes=5),
            expected_generation=1,
        )

    check = PgSession()
    assert check.get(VoiceScreeningSession, session_id).status == "scheduled"
    check.close()
    registrations = []
    monkeypatch.setattr(scheduler.voice_scheduler, "get_jobs", lambda: [])
    monkeypatch.setattr(
        scheduler.voice_scheduler,
        "add_job",
        lambda _func, **kwargs: registrations.append(kwargs),
    )
    scheduler.reconcile_scheduled_voice_calls()
    assert any(job["args"] == [session_id, 1] for job in registrations)


def test_v15_scheduler_reconciliation_is_idempotent(PgSession, monkeypatch):
    from app.backend.services import voice_call_scheduler as scheduler

    db = PgSession()
    tenant = _tenant(db)
    session = _voice_session(db, tenant.id)
    session.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    session_id = session.id
    db.commit()
    db.close()
    monkeypatch.setattr(scheduler, "SessionLocal", PgSession)
    jobs = {}
    registrations = []

    class Job:
        def __init__(self, job_id):
            self.id = job_id

        def remove(self):
            jobs.pop(self.id, None)

    def add_job(_func, **kwargs):
        registrations.append(kwargs["id"])
        jobs[kwargs["id"]] = kwargs

    monkeypatch.setattr(
        scheduler.voice_scheduler,
        "get_jobs",
        lambda: [Job(job_id) for job_id in list(jobs)],
    )
    monkeypatch.setattr(scheduler.voice_scheduler, "add_job", add_job)
    scheduler.reconcile_scheduled_voice_calls()
    scheduler.reconcile_scheduled_voice_calls()
    own_id = f"voice_call_{session_id}_1"
    assert registrations.count(own_id) == 1
    assert jobs[own_id]["args"] == [session_id, 1]
