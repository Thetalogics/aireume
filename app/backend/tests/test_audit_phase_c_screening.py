# app/backend/tests/test_audit_phase_c_screening.py
import asyncio
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.backend.db.database import SessionLocal
from app.backend.models.db_models import (
    AnalysisArtifact,
    AnalysisJob,
    AnalysisResult,
    Candidate,
    DeadLetterJob,
    Requisition,
    RequisitionCandidate,
    ScreeningResult,
    Tenant,
    User,
)
from app.backend.services.queue_analysis_service import complete_queue_job
from app.backend.services.queue_manager import QueueManager, get_queue_manager
from app.backend.services.screening_command import (
    ArtifactUnavailable,
    CandidateNotFoundError,
    CrossTenantRequisitionError,
    ScreeningCommand,
    analysis_fingerprint,
    build_screening_command,
    command_from_dict,
    execute_screening,
)


def _cmd(**kwargs):
    base = dict(
        schema_version="v1",
        tenant_id=1,
        user_id=9,
        resume_hash="r" * 64,
        jd_hash="j" * 64,
        algorithm_version="1.0",
    )
    base.update(kwargs)
    return ScreeningCommand(**base)


def test_same_inputs_same_fingerprint():
    a = analysis_fingerprint(_cmd(scoring_weights={"a": 1, "b": 2}))
    b = analysis_fingerprint(_cmd(scoring_weights={"b": 2, "a": 1}, user_id=99, candidate_id=3))
    assert a == b
    assert len(a) == 64


def test_weights_change_fingerprint():
    assert analysis_fingerprint(_cmd(scoring_weights={"a": 1})) != analysis_fingerprint(
        _cmd(scoring_weights={"a": 2})
    )


def test_criteria_version_change_fingerprint():
    assert analysis_fingerprint(_cmd(requisition_id=5, criteria_version=1)) != analysis_fingerprint(
        _cmd(requisition_id=5, criteria_version=2)
    )


def test_skill_override_change_fingerprint():
    assert analysis_fingerprint(_cmd(skill_overrides={"python": True})) != analysis_fingerprint(
        _cmd(skill_overrides={"python": False})
    )


def test_filename_priority_excluded():
    # user_id / candidate_id / artifact_id must not affect hash
    assert analysis_fingerprint(_cmd(artifact_id="aaa")) == analysis_fingerprint(_cmd(artifact_id="bbb"))


@pytest.mark.asyncio
async def test_enqueue_persists_command_and_fingerprint(db, seed_subscription_plans):
    tenant = Tenant(name="C1", slug="c1-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    req = Requisition(tenant_id=tenant.id, title="Eng", jd_text="", status="open", current_criteria_version=3, created_by=1)
    db.add(req)
    db.commit()
    db.refresh(req)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        requisition_id=req.id,
        scoring_weights={"skills": 0.5},
        skill_overrides={"python": True},
        role_template_id=None,
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    cmd = command_from_dict(job.job_config["command"])
    assert cmd.requisition_id == req.id
    assert cmd.criteria_version == 3
    assert job.input_hash == analysis_fingerprint(cmd)


@pytest.mark.asyncio
async def test_weights_change_creates_new_job(db, seed_subscription_plans):
    tenant = Tenant(name="C2", slug="c2-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    a = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, scoring_weights={"w": 1})
    b = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, scoring_weights={"w": 2})
    assert a != b


@pytest.mark.asyncio
async def test_same_config_dedupes(db, seed_subscription_plans):
    tenant = Tenant(name="C3", slug="c3-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    a = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1)
    b = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="other.pdf", jd_text="J", user_id=99)
    assert a == b


@pytest.mark.asyncio
async def test_cross_tenant_requisition_rejected(db, seed_subscription_plans):
    t1 = Tenant(name="T1", slug="c-t1")
    t2 = Tenant(name="T2", slug="c-t2")
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    req = Requisition(tenant_id=t2.id, title="X", jd_text="", status="open", created_by=1)
    db.add(req)
    db.commit()
    db.refresh(req)
    mgr = get_queue_manager()
    with pytest.raises(CrossTenantRequisitionError):
        await mgr.enqueue_job(tenant_id=t1.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, requisition_id=req.id)
    assert db.query(AnalysisJob).filter(AnalysisJob.tenant_id == t1.id).count() == 0


@pytest.mark.asyncio
async def test_complete_queue_job_links_requisition_and_is_idempotent(db, seed_subscription_plans, monkeypatch):
    tenant = Tenant(name="C4", slug="c4-complete")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    user = User(
        tenant_id=tenant.id,
        email="c4@test.com",
        hashed_password="x",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    req = Requisition(
        tenant_id=tenant.id,
        title="Eng",
        jd_text="Need python",
        status="open",
        current_criteria_version=1,
        created_by=user.id,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    async def _fake_process(*args, **kwargs):
        return {"fit_score": 70, "final_recommendation": "Hire"}

    monkeypatch.setattr("app.backend.routes.analyze._process_single_resume", _fake_process)
    monkeypatch.setattr("app.backend.routes.analyze._spawn_background_narrative", lambda *a, **k: None)

    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=user.id,
        requisition_id=req.id,
        parsed_resume_cache={
            "parsed_data": {
                "raw_text": "Alice engineer python",
                "contact_info": {"name": "Alice", "email": "alice@c4.test"},
                "skills": ["python"],
            },
            "file_hash": "c4hash",
            "filename": "a.pdf",
        },
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()

    ok = await complete_queue_job(job.id, db)
    assert ok is True

    result = db.query(ScreeningResult).filter(ScreeningResult.tenant_id == tenant.id).one()
    assert result.requisition_id == req.id

    rc = db.query(RequisitionCandidate).filter(
        RequisitionCandidate.requisition_id == req.id,
        RequisitionCandidate.candidate_id == result.candidate_id,
    ).one()
    assert rc.screening_result_id == result.id

    ok2 = await complete_queue_job(job.id, db)
    assert ok2 is True
    assert db.query(AnalysisResult).filter(AnalysisResult.job_id == job.id).count() == 1


def test_queue_max_concurrent_clamped(monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONCURRENT", "100")
    assert QueueManager().max_concurrent_jobs == 32
    monkeypatch.setenv("QUEUE_MAX_CONCURRENT", "0")
    assert QueueManager().max_concurrent_jobs == 1
    monkeypatch.setenv("QUEUE_MAX_CONCURRENT", "10")
    assert QueueManager().max_concurrent_jobs == 10


async def _enqueue_named(mgr, tenant_id, name: str):
    return await mgr.enqueue_job(
        tenant_id=tenant_id,
        resume_text=f"Resume {name}",
        resume_filename=f"{name}.pdf",
        jd_text=f"JD {name}",
        user_id=1,
    )


@pytest.mark.asyncio
async def test_idle_worker_does_not_fill_empty_claim_slots(monkeypatch):
    monkeypatch.setenv("QUEUE_MAX_CONCURRENT", "4")
    claim_calls = []

    async def stub_get_next_job(session):
        claim_calls.append(1)
        return None

    mgr = QueueManager()
    mgr.poll_interval_seconds = 0.2
    monkeypatch.setattr(mgr, "get_next_job", stub_get_next_job)
    loop_task = asyncio.create_task(mgr.worker_loop())
    try:
        await asyncio.sleep(0.05)
        assert len(claim_calls) == 1
    finally:
        mgr.stop()
        await asyncio.wait_for(loop_task, timeout=5)


@pytest.mark.asyncio
async def test_two_jobs_overlap_with_concurrency_2(monkeypatch, db, seed_subscription_plans):
    monkeypatch.setenv("QUEUE_MAX_CONCURRENT", "2")
    started = []
    currently = 0
    peak = 0
    two_running = asyncio.Event()
    third_started = asyncio.Event()
    release_first = asyncio.Event()
    release_rest = asyncio.Event()

    async def slow_complete(job_id, session):
        nonlocal currently, peak
        currently += 1
        peak = max(peak, currently)
        started.append(job_id)
        if currently >= 2:
            two_running.set()
        if len(started) >= 3:
            third_started.set()
        if len(started) == 1:
            await asyncio.wait_for(release_first.wait(), timeout=5)
        else:
            await asyncio.wait_for(release_rest.wait(), timeout=5)
        currently -= 1
        return True

    monkeypatch.setattr("app.backend.services.queue_analysis_service.complete_queue_job", slow_complete)
    mgr = QueueManager()
    mgr.poll_interval_seconds = 0.02
    assert mgr.max_concurrent_jobs == 2

    tenant = Tenant(name="C-conc", slug="c-conc-overlap")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    a = await _enqueue_named(mgr, tenant.id, "one")
    b = await _enqueue_named(mgr, tenant.id, "two")
    c = await _enqueue_named(mgr, tenant.id, "three")
    assert len({a, b, c}) == 3

    pending_ids = [a, b, c]

    async def stub_get_next_job(session):
        if not pending_ids:
            return None
        jid = pending_ids.pop(0)
        job = session.query(AnalysisJob).filter(AnalysisJob.id == jid).one()
        job.status = "processing"
        job.worker_id = mgr.worker_id
        job.started_at = datetime.now(timezone.utc)
        job.worker_heartbeat = datetime.now(timezone.utc)
        job.leased_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        session.commit()
        return job

    monkeypatch.setattr(mgr, "get_next_job", stub_get_next_job)
    loop_task = asyncio.create_task(mgr.worker_loop())
    try:
        await asyncio.wait_for(two_running.wait(), timeout=5)
        assert peak == 2
        assert len(started) == 2
        release_first.set()
        await asyncio.wait_for(third_started.wait(), timeout=5)
        assert peak == 2
        assert len(started) == 3
        release_rest.set()
    finally:
        mgr.stop()
        release_first.set()
        release_rest.set()
        await asyncio.wait_for(loop_task, timeout=5)
    assert set(started) == {a, b, c}
    assert peak == 2


@pytest.mark.asyncio
async def test_heartbeat_renews_lease_during_process(monkeypatch, db, seed_subscription_plans):
    monkeypatch.setenv("QUEUE_HEARTBEAT_INTERVAL", "0.05")
    async def slow_complete(job_id, session):
        await asyncio.sleep(0.2)
        return True

    monkeypatch.setattr("app.backend.services.queue_analysis_service.complete_queue_job", slow_complete)
    mgr = QueueManager()
    mgr.heartbeat_interval_seconds = 0.05

    tenant = Tenant(name="C-hb", slug="c-hb-lease")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    job_id = await _enqueue_named(mgr, tenant.id, "hb")
    job = await mgr.get_next_job(db)
    assert job is not None and job.id == job_id
    claimed_hb = job.worker_heartbeat
    claimed_lease = job.leased_until

    await mgr.process_job(job, db)
    db.expire_all()
    refreshed = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    assert refreshed.worker_heartbeat > claimed_hb
    assert refreshed.leased_until > claimed_lease


@pytest.mark.asyncio
async def test_heartbeat_task_stops_after_process(monkeypatch, db, seed_subscription_plans):
    seen_during = []

    async def instant_complete(job_id, session):
        seen_during.extend(list(mgr._heartbeat_tasks))
        assert any(not t.done() for t in mgr._heartbeat_tasks)
        return True

    monkeypatch.setattr("app.backend.services.queue_analysis_service.complete_queue_job", instant_complete)
    mgr = QueueManager()

    tenant = Tenant(name="C-hb-stop", slug="c-hb-stop")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    job_id = await _enqueue_named(mgr, tenant.id, "stop")
    job = await mgr.get_next_job(db)
    assert job is not None and job.id == job_id

    await mgr.process_job(job, db)
    leftover = [t for t in mgr._heartbeat_tasks if not t.done()]
    assert leftover == []
    assert mgr._heartbeat_tasks == set()
    assert seen_during


def _processing_job(db, slug, heartbeat, leased_until, retry_count=0):
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    job = AnalysisJob(
        tenant_id=tenant.id,
        status="processing",
        job_type="resume_screening",
        resume_hash="s" * 64,
        jd_hash="t" * 64,
        input_hash=uuid.uuid4().hex,
        retry_count=retry_count,
        max_retries=3,
        worker_heartbeat=heartbeat,
        leased_until=leased_until,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_concurrent_recovery_increments_retry_once(db, seed_subscription_plans):
    now = datetime.now(timezone.utc)
    job = _processing_job(db, "c-stale-once", now - timedelta(hours=2), now - timedelta(hours=1))
    job_id = job.id
    db.expunge(job)
    db2 = SessionLocal()
    try:
        mgr = QueueManager()
        await asyncio.gather(mgr.recover_stale_jobs(db), mgr.recover_stale_jobs(db2))
    finally:
        db2.close()
    db.expire_all()
    refreshed = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    assert refreshed.retry_count == 1
    assert refreshed.status == "retrying"


def test_worker_loop_source_has_no_recover_call():
    src = inspect.getsource(QueueManager.worker_loop)
    assert "recover_stale_jobs" not in src


def test_scheduler_uses_singleton():
    from app.backend.services import scheduler

    src = inspect.getsource(scheduler.recover_stale_jobs)
    assert "get_queue_manager" in src
    assert "QueueManager()" not in src


@pytest.mark.asyncio
async def test_fresh_heartbeat_not_recovered(db, seed_subscription_plans):
    now = datetime.now(timezone.utc)
    job = _processing_job(db, "c-fresh-hb", now, now + timedelta(minutes=10), retry_count=2)
    await QueueManager().recover_stale_jobs(db)
    db.refresh(job)
    assert job.status == "processing"
    assert job.retry_count == 2


@pytest.mark.asyncio
async def test_move_to_dead_letter_copies_artifact_id(db, seed_subscription_plans):
    tenant = Tenant(name="C-dlq-copy", slug="c-dlq-copy")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    assert job.artifact_id is not None
    original_artifact_id = job.artifact_id
    await mgr.move_to_dead_letter(db, job, "retries exhausted")
    dlq = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).one()
    assert dlq.artifact_id == original_artifact_id


@pytest.mark.asyncio
async def test_retry_dead_letter_job_replays_artifact_and_command(db, seed_subscription_plans):
    tenant = Tenant(name="C-dlq-retry", slug="c-dlq-retry")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    req = Requisition(
        tenant_id=tenant.id,
        title="Eng",
        jd_text="Need python",
        status="open",
        current_criteria_version=2,
        created_by=1,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        requisition_id=req.id,
        scoring_weights={"skills": 0.4},
        skill_overrides={"python": True},
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    stored_cmd = command_from_dict(job.job_config["command"])
    artifact_id = job.artifact_id
    await mgr.move_to_dead_letter(db, job, "retries exhausted")
    dlq = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).one()
    new_job = await mgr.retry_dead_letter_job(db, dlq.id)
    assert new_job is not None
    assert new_job.id != job.id
    assert new_job.artifact_id == artifact_id
    replayed = command_from_dict(new_job.job_config["command"])
    assert replayed.requisition_id == stored_cmd.requisition_id
    assert replayed.criteria_version == stored_cmd.criteria_version
    assert replayed.scoring_weights == stored_cmd.scoring_weights
    assert replayed.skill_overrides == stored_cmd.skill_overrides
    assert str(replayed.artifact_id) == str(artifact_id)
    db.refresh(dlq)
    assert dlq.status == "reprocessed"
    assert dlq.reprocessed_job_id == new_job.id


@pytest.mark.asyncio
async def test_retry_dead_letter_strips_quota_released_from_new_job(db, seed_subscription_plans):
    tenant = Tenant(name="C-dlq-quota", slug="c-dlq-quota")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    cfg = dict(job.job_config or {})
    cfg["quota_reserved"] = True
    cfg["quota_released"] = True
    job.job_config = cfg
    db.commit()
    await mgr.move_to_dead_letter(db, job, "retries exhausted")
    dlq = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).one()
    assert dlq.job_config.get("quota_released") is True
    new_job = await mgr.retry_dead_letter_job(db, dlq.id)
    assert new_job is not None
    assert new_job.job_config.get("quota_reserved") is True
    assert new_job.job_config.get("quota_released") is not True


@pytest.mark.asyncio
async def test_retry_missing_artifact_keeps_dlq_pending(db, seed_subscription_plans):
    tenant = Tenant(name="C-dlq-miss", slug="c-dlq-miss")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    await mgr.move_to_dead_letter(db, job, "retries exhausted")
    dlq = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).one()
    artifact = db.query(AnalysisArtifact).filter(AnalysisArtifact.id == dlq.artifact_id).one()
    db.delete(artifact)
    db.commit()
    with pytest.raises(ArtifactUnavailable):
        await mgr.retry_dead_letter_job(db, dlq.id)
    db.refresh(dlq)
    assert dlq.status == "pending"


@pytest.mark.asyncio
async def test_purge_expired_artifacts_skips_pending_dlq_and_live_refs(db, seed_subscription_plans):
    tenant = Tenant(name="C-dlq-purge", slug="c-dlq-purge")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    now = datetime.now(timezone.utc)
    expired = now - timedelta(days=1)

    def _artifact(name: str):
        art = AnalysisArtifact(
            tenant_id=tenant.id,
            resume_filename=f"{name}.pdf",
            resume_size_bytes=4,
            resume_hash=name[0] * 64,
            resume_text=name,
            resume_text_length=len(name),
            jd_hash=name[-1] * 64,
            jd_text=f"jd {name}",
            jd_size_bytes=5,
            expires_at=expired,
        )
        db.add(art)
        db.flush()
        return art

    live_art = _artifact("live")
    dlq_art = _artifact("dlqref")
    orphan_art = _artifact("orphan")
    live_job = AnalysisJob(
        tenant_id=tenant.id,
        status="queued",
        job_type="resume_screening",
        resume_hash="l" * 64,
        jd_hash="m" * 64,
        input_hash=uuid.uuid4().hex,
        artifact_id=live_art.id,
    )
    db.add(live_job)
    db.add(
        DeadLetterJob(
            original_job_id=uuid.uuid4(),
            tenant_id=tenant.id,
            job_type="resume_screening",
            resume_hash="d" * 64,
            jd_hash="e" * 64,
            input_hash=uuid.uuid4().hex,
            artifact_id=dlq_art.id,
            failure_reason="exhausted",
            original_created_at=now,
            status="pending",
        )
    )
    db.commit()
    live_id, dlq_id, orphan_id = live_art.id, dlq_art.id, orphan_art.id
    mgr = QueueManager()
    await mgr.purge_expired_artifacts(db)
    remaining = {row.id for row in db.query(AnalysisArtifact).all()}
    assert live_id in remaining
    assert dlq_id in remaining
    assert orphan_id not in remaining


def _idempotency_test_client(hits):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.backend.middleware.idempotency import IdempotencyMiddleware

    mini = FastAPI()
    mini.add_middleware(IdempotencyMiddleware)

    @mini.post("/api/idem-a")
    async def idem_a(request: Request):
        hits["a"] = hits.get("a", 0) + 1
        body = await request.json()
        return {"echo": body, "hits": hits["a"]}

    @mini.post("/api/idem-b")
    async def idem_b(request: Request):
        hits["b"] = hits.get("b", 0) + 1
        return {"path": "b", "hits": hits["b"]}

    return TestClient(mini)


def test_request_fingerprint_canonical_json_stable():
    from app.backend.middleware.idempotency import request_fingerprint

    a = request_fingerprint("POST", "/api/idem-a", "", "application/json", b'{"b":1,"a":2}')
    b = request_fingerprint("POST", "/api/idem-a", "", "application/json", b'{"a":2,"b":1}')
    assert a == b
    assert len(a) == 64


def test_same_key_same_json_replays(db):
    hits = {}
    client = _idempotency_test_client(hits)
    headers = {"X-Idempotency-Key": "replay-same"}
    payload = {"n": 1}
    first = client.post("/api/idem-a", json=payload, headers=headers)
    second = client.post("/api/idem-a", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers.get("X-Idempotent-Replay") == "true"
    assert second.json() == first.json()
    assert hits["a"] == 1


def test_same_key_different_json_conflicts_without_handler(db):
    hits = {}
    client = _idempotency_test_client(hits)
    headers = {"X-Idempotency-Key": "replay-conflict"}
    first = client.post("/api/idem-a", json={"n": 1}, headers=headers)
    second = client.post("/api/idem-a", json={"n": 2}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "Idempotency key reused with different request"
    assert "X-Idempotent-Replay" not in second.headers
    assert hits["a"] == 1


def test_same_key_different_path_is_independent(db):
    hits = {}
    client = _idempotency_test_client(hits)
    headers = {"X-Idempotency-Key": "replay-path"}
    a = client.post("/api/idem-a", json={"n": 1}, headers=headers)
    b = client.post("/api/idem-b", json={"n": 1}, headers=headers)
    assert a.status_code == 200
    assert b.status_code == 200
    assert "X-Idempotent-Replay" not in b.headers
    assert hits["a"] == 1
    assert hits["b"] == 1


def test_expired_idempotency_row_does_not_replay(db):
    from app.backend.middleware import idempotency as idem
    from app.backend.models.db_models import IdempotencyKey

    fp = idem.request_fingerprint("POST", "/api/idem-a", "", "application/json", b'{"n":1}')
    db.merge(
        IdempotencyKey(
            key="expired-key",
            tenant_id=0,
            endpoint="POST:/api/idem-a",
            request_fingerprint=fp,
            response_status=200,
            response_body={"stale": True},
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    db.commit()
    assert idem._lookup("expired-key", "0", "POST:/api/idem-a", fp) is None


async def _enqueue_pair(db, slug, **kwargs):
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
        **kwargs,
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    return tenant, mgr, job


@pytest.mark.asyncio
async def test_queued_same_fingerprint_returns_same_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-queued")
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="other.pdf",
        jd_text="Need python",
        user_id=2,
        scoring_weights={"skills": 0.5},
    )
    assert again == job.id
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_processing_same_fingerprint_returns_same_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-proc")
    job.status = "processing"
    db.commit()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again == job.id


@pytest.mark.asyncio
async def test_retrying_same_fingerprint_returns_same_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-retry")
    job.status = "retrying"
    db.commit()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again == job.id


@pytest.mark.asyncio
async def test_completed_same_fingerprint_returns_cache_hit(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-done")
    job.status = "completed"
    db.commit()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again == job.id


@pytest.mark.asyncio
async def test_cancelled_same_fingerprint_creates_new_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-cancel")
    db.refresh(tenant)
    before = tenant.analyses_count_this_month
    job.status = "cancelled"
    from app.backend.routes.analyze_helpers import release_job_analysis_quota

    release_job_analysis_quota(db, job)
    db.commit()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again != job.id
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == before


@pytest.mark.asyncio
async def test_failed_same_fingerprint_creates_new_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-fail")
    job.status = "failed"
    db.commit()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again != job.id


@pytest.mark.asyncio
async def test_dead_letter_same_fingerprint_creates_new_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-dlq")
    await mgr.move_to_dead_letter(db, job, "retries exhausted")
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again != job.id


@pytest.mark.asyncio
async def test_concurrent_enqueue_resolves_to_one_cacheable_job(db, seed_subscription_plans):
    tenant = Tenant(name="c-fp-race", slug="c-fp-race")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    ids = await asyncio.gather(
        mgr.enqueue_job(
            tenant_id=tenant.id,
            resume_text="Race resume",
            resume_filename="a.pdf",
            jd_text="Race jd",
            user_id=1,
        ),
        mgr.enqueue_job(
            tenant_id=tenant.id,
            resume_text="Race resume",
            resume_filename="b.pdf",
            jd_text="Race jd",
            user_id=1,
        ),
    )
    assert ids[0] == ids[1]
    cacheable = (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.tenant_id == tenant.id,
            AnalysisJob.status.in_(["queued", "processing", "retrying", "completed"]),
        )
        .all()
    )
    assert len(cacheable) == 1


@pytest.mark.asyncio
async def test_integrity_error_fallback_never_returns_terminal_job(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-poison")
    original_hash = job.input_hash
    job.status = "failed"
    db.commit()
    db.expire_all()
    again = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    assert again != job.id
    poisoned = db.query(AnalysisJob).filter(AnalysisJob.id == job.id).one()
    assert poisoned.input_hash != original_hash
    fresh = db.query(AnalysisJob).filter(AnalysisJob.id == again).one()
    assert fresh.status in ("queued", "processing", "retrying", "completed")
    assert fresh.input_hash == original_hash


@pytest.mark.asyncio
async def test_dedup_does_not_double_reserve_quota(db, seed_subscription_plans):
    tenant, mgr, job = await _enqueue_pair(db, "c-fp-quota")
    db.refresh(tenant)
    used = tenant.analyses_count_this_month
    await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        scoring_weights={"skills": 0.5},
    )
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == used
    assert used >= 1


@pytest.mark.asyncio
async def test_stale_at_max_retries_moves_to_dlq_once(db, seed_subscription_plans):
    now = datetime.now(timezone.utc)
    job = _processing_job(
        db, "c-stale-dlq", now - timedelta(hours=2), now - timedelta(hours=1), retry_count=3
    )
    job.max_retries = 3
    job.job_config = {"quota_reserved": True, "command": {"tenant_id": job.tenant_id, "user_id": 1, "resume_hash": "s" * 64, "jd_hash": "t" * 64, "schema_version": "v1", "algorithm_version": "1.0"}}
    db.commit()
    original_hash = job.input_hash
    original_artifact = job.artifact_id
    db2 = SessionLocal()
    try:
        mgr = QueueManager()
        await asyncio.gather(mgr.recover_stale_jobs(db), mgr.recover_stale_jobs(db2))
    finally:
        db2.close()
    db.expire_all()
    refreshed = db.query(AnalysisJob).filter(AnalysisJob.id == job.id).one()
    assert refreshed.status == "dead_letter"
    assert refreshed.input_hash != original_hash
    dlqs = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).all()
    assert len(dlqs) == 1
    assert dlqs[0].artifact_id == original_artifact
    assert (dlqs[0].job_config or {}).get("command") is not None
    tenant = db.query(Tenant).filter(Tenant.id == job.tenant_id).one()
    mgr = get_queue_manager()
    new_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="new after stale",
        resume_filename="n.pdf",
        jd_text="jd",
        user_id=1,
    )
    assert new_id != job.id


def test_query_param_order_same_fingerprint():
    from app.backend.middleware.idempotency import canonicalize_query, request_fingerprint

    q1 = canonicalize_query("a=1&b=2")
    q2 = canonicalize_query("b=2&a=1")
    assert q1 == q2
    a = request_fingerprint("POST", "/api/idem-a", q1, "application/json", b'{"n":1}')
    b = request_fingerprint("POST", "/api/idem-a", q2, "application/json", b'{"n":1}')
    assert a == b


def test_duplicate_query_params_are_deterministic():
    from app.backend.middleware.idempotency import canonicalize_query, request_fingerprint

    q_ab = canonicalize_query("tag=a&tag=b")
    q_ba = canonicalize_query("tag=b&tag=a")
    q_a = canonicalize_query("tag=a")
    assert q_ab == q_ba
    assert q_ab != q_a
    assert request_fingerprint("GET", "/x", q_ab, "", b"") == request_fingerprint("GET", "/x", q_ba, "", b"")
    assert request_fingerprint("GET", "/x", q_ab, "", b"") != request_fingerprint("GET", "/x", q_a, "", b"")


def test_same_query_order_replays(db):
    hits = {}
    client = _idempotency_test_client(hits)
    headers = {"X-Idempotency-Key": "q-order"}
    first = client.post("/api/idem-a?a=1&b=2", json={"n": 1}, headers=headers)
    second = client.post("/api/idem-a?b=2&a=1", json={"n": 1}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers.get("X-Idempotent-Replay") == "true"
    assert hits["a"] == 1


def test_changed_query_value_conflicts(db):
    hits = {}
    client = _idempotency_test_client(hits)
    headers = {"X-Idempotency-Key": "q-change"}
    first = client.post("/api/idem-a?a=1", json={"n": 1}, headers=headers)
    second = client.post("/api/idem-a?a=2", json={"n": 1}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert hits["a"] == 1


def test_idempotency_tenant_isolation_http(db, monkeypatch):
    from jose import jwt
    from app.backend.middleware.auth import ALGORITHM, SECRET_KEY

    hits = {}
    client = _idempotency_test_client(hits)
    t1 = jwt.encode({"tenant_id": 11, "sub": "1"}, SECRET_KEY, algorithm=ALGORITHM)
    t2 = jwt.encode({"tenant_id": 22, "sub": "2"}, SECRET_KEY, algorithm=ALGORITHM)
    headers1 = {"X-Idempotency-Key": "shared-key", "Authorization": f"Bearer {t1}"}
    headers2 = {"X-Idempotency-Key": "shared-key", "Authorization": f"Bearer {t2}"}
    a = client.post("/api/idem-a", json={"n": 1}, headers=headers1)
    b = client.post("/api/idem-a", json={"n": 1}, headers=headers2)
    assert a.status_code == 200
    assert b.status_code == 200
    assert "X-Idempotent-Replay" not in b.headers
    assert hits["a"] == 2


def test_analyze_endpoint_persists_via_execute_screening():
    import inspect
    from app.backend.routes import analyze as analyze_mod

    endpoint_src = inspect.getsource(analyze_mod.analyze_endpoint)
    helper_src = inspect.getsource(analyze_mod._persist_via_screening_command)
    assert "_persist_via_screening_command(" in endpoint_src
    assert "_upsert_screening_result(" not in endpoint_src
    assert "execute_screening(" in helper_src


@pytest.mark.asyncio
async def test_sync_analyze_http_and_queue_screening_command_parity(
    auth_client, db, seed_subscription_plans, monkeypatch, mock_hybrid_pipeline
):
    from io import BytesIO

    from app.backend.services import screening_command as sc_mod

    tenant = db.query(Tenant).filter(Tenant.slug == "testcorp").one()
    user = db.query(User).filter(User.email == "admin@testcorp.com").one()
    req = Requisition(
        tenant_id=tenant.id,
        title="Eng",
        jd_text="Need python engineers who can ship production services with tests.",
        status="open",
        current_criteria_version=4,
        created_by=user.id,
        legacy_role_template_id=None,
        intake_json=json.dumps({
            "must_haves": ["python"],
            "screen_focus_topics": ["ownership"],
        }),
        intake_status="ready",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    parsed = {
        "raw_text": "Alice engineer python",
        "contact_info": {"name": "Alice Sync", "email": "alice-sync-parity@test.com"},
        "skills": ["python"],
        "education": [],
        "work_experience": [],
    }

    async def _fake_parse(*args, **kwargs):
        return parsed, None

    persist_calls = {"n": 0}
    real_execute = sc_mod.execute_screening

    def _wrapped_execute(*args, **kwargs):
        persist_calls["n"] += 1
        return real_execute(*args, **kwargs)

    monkeypatch.setattr("app.backend.routes.analyze._parse_resume_with_doc_conversion", _fake_parse)
    monkeypatch.setattr("app.backend.routes.analyze._spawn_background_narrative", lambda *a, **k: None)
    monkeypatch.setattr("app.backend.routes.analyze.execute_screening", _wrapped_execute)
    monkeypatch.setattr(sc_mod, "execute_screening", _wrapped_execute)

    jd = (
        "We are looking for an experienced software developer to join our growing team. "
        "The ideal candidate will have strong skills in Python programming, web development, "
        "and database design. Requirements include 3+ years of professional experience with "
        "Python frameworks such as FastAPI or Django, familiarity with SQL and NoSQL databases, "
        "experience with cloud platforms like AWS or Azure, strong understanding of software "
        "design patterns, excellent problem-solving skills, and the ability to work collaboratively "
        "in an agile environment. The role involves building scalable web applications, "
        "integrating with third-party APIs, writing unit tests, and mentoring junior developers."
    )
    resp = auth_client.post(
        "/api/analyze",
        files={
            "resume": (
                "a.docx",
                BytesIO(b"PK\x03\x04" + b"\x00" * 20),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "job_description": jd,
            "requisition_id": str(req.id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert persist_calls["n"] >= 1
    sync_id = body["result_id"]
    sync_result = db.query(ScreeningResult).filter(ScreeningResult.id == sync_id).one()
    assert sync_result.requisition_id == req.id
    rc_sync = db.query(RequisitionCandidate).filter(
        RequisitionCandidate.requisition_id == req.id,
        RequisitionCandidate.candidate_id == sync_result.candidate_id,
    ).one()
    assert rc_sync.screening_result_id == sync_result.id

    async def _fake_process(*args, **kwargs):
        raw = dict(mock_hybrid_pipeline.return_value)
        raw["_parsed_data"] = {
            "raw_text": "Bob engineer python",
            "contact_info": {"name": "Bob Queue", "email": "bob-queue-parity@test.com"},
            "skills": ["python"],
        }
        raw["_gap_analysis"] = {}
        return raw

    monkeypatch.setattr("app.backend.routes.analyze._process_single_resume", _fake_process)

    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Bob engineer python",
        resume_filename="b.pdf",
        jd_text=jd,
        user_id=user.id,
        requisition_id=req.id,
        parsed_resume_cache={
            "parsed_data": {
                "raw_text": "Bob engineer python",
                "contact_info": {"name": "Bob Queue", "email": "bob-queue-parity@test.com"},
                "skills": ["python"],
            },
            "file_hash": "parity-bob",
            "filename": "b.pdf",
        },
    )
    await complete_queue_job(job_id, db)
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
    queue_result = db.query(ScreeningResult).filter(ScreeningResult.candidate_id == job.candidate_id).one()
    assert queue_result.requisition_id == req.id
    qcmd = command_from_dict(job.job_config["command"])
    sync_cmd = build_screening_command(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        resume_hash="ignored",
        jd_hash="ignored",
        requisition_id=req.id,
    )
    assert qcmd.criteria_version == sync_cmd.criteria_version
    assert queue_result.deterministic_score == sync_result.deterministic_score
    rc_q = db.query(RequisitionCandidate).filter(
        RequisitionCandidate.requisition_id == req.id,
        RequisitionCandidate.candidate_id == queue_result.candidate_id,
    ).one()
    assert rc_q.screening_result_id == queue_result.id


def _seed_tenant_with_candidates(db):
    tenant = Tenant(name="CandRes", slug="cand-res")
    other = Tenant(name="CandOther", slug="cand-other")
    db.add_all([tenant, other])
    db.commit()
    db.refresh(tenant)
    db.refresh(other)
    user = User(
        tenant_id=tenant.id,
        email="cand-res@test.com",
        hashed_password="x",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    kept = Candidate(
        tenant_id=tenant.id,
        name="Kept",
        email="kept@test.com",
        raw_resume_text="kept resume",
        parsed_skills='["python"]',
    )
    other_match = Candidate(
        tenant_id=tenant.id,
        name="OtherMatch",
        email="other-match@test.com",
        raw_resume_text="other resume",
        parsed_skills='["python"]',
    )
    foreign = Candidate(
        tenant_id=other.id,
        name="Foreign",
        email="foreign@test.com",
        raw_resume_text="foreign resume",
    )
    db.add_all([user, kept, other_match, foreign])
    db.commit()
    db.refresh(user)
    db.refresh(kept)
    db.refresh(other_match)
    db.refresh(foreign)
    return tenant, user, kept, other_match, foreign


def test_execute_screening_keeps_supplied_candidate_id(db, seed_subscription_plans, monkeypatch):
    tenant, user, kept, other_match, _foreign = _seed_tenant_with_candidates(db)
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("_get_or_create_candidate must not run when candidate_id is set")

    monkeypatch.setattr("app.backend.routes.analyze._get_or_create_candidate", _boom)
    cmd = build_screening_command(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        resume_hash="r",
        jd_hash="j",
        candidate_id=kept.id,
    )
    parsed = {
        "raw_text": "other resume",
        "contact_info": {"name": "OtherMatch", "email": other_match.email},
        "skills": ["python"],
    }
    result, is_dup = execute_screening(
        db,
        cmd,
        resume_text="other resume",
        jd_text="Need python",
        parsed_data=parsed,
        pipeline_result={"fit_score": 77},
        filename="a.pdf",
        action="use_existing",
    )
    assert called["n"] == 0
    assert result.candidate_id == kept.id
    assert cmd.candidate_id == kept.id
    assert db.query(Candidate).filter(Candidate.tenant_id == tenant.id).count() == 2


def test_execute_screening_rejects_cross_tenant_candidate_id(db, seed_subscription_plans):
    tenant, user, _kept, _other, foreign = _seed_tenant_with_candidates(db)
    cmd = build_screening_command(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        resume_hash="r",
        jd_hash="j",
        candidate_id=foreign.id,
    )
    before = db.query(Candidate).count()
    with pytest.raises(CandidateNotFoundError):
        execute_screening(
            db,
            cmd,
            resume_text="x",
            jd_text="Need python",
            parsed_data={"raw_text": "x", "contact_info": {}},
            pipeline_result={"fit_score": 10},
            filename="a.pdf",
        )
    assert db.query(Candidate).count() == before


def test_execute_screening_creates_when_candidate_id_absent(db, seed_subscription_plans):
    tenant, user, kept, _other, _foreign = _seed_tenant_with_candidates(db)
    cmd = build_screening_command(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        resume_hash="r",
        jd_hash="j",
    )
    result, is_dup = execute_screening(
        db,
        cmd,
        resume_text="brand new",
        jd_text="Need python",
        parsed_data={
            "raw_text": "brand new",
            "contact_info": {"name": "Newbie", "email": "newbie-create@test.com"},
            "skills": ["python"],
        },
        pipeline_result={"fit_score": 40},
        filename="n.pdf",
    )
    assert result.candidate_id != kept.id
    assert result.candidate_id != _other.id
    assert is_dup is False
    assert db.query(Candidate).filter(Candidate.tenant_id == tenant.id).count() == 3


def test_use_existing_does_not_increase_candidate_count(
    auth_client, db, seed_subscription_plans, mock_hybrid_pipeline
):
    import hashlib
    from io import BytesIO
    from app.backend.tests.test_usage_enforcement import DOCX_HEADER, LONG_JOB_DESCRIPTION, RESUME_CONTENT
    from app.backend.tests.test_helpers import allow_ad_hoc_screening

    admin = db.query(User).filter(User.email == "admin@testcorp.com").one()
    content = DOCX_HEADER + RESUME_CONTENT
    existing = Candidate(
        tenant_id=admin.tenant_id,
        name="HashMatchCount",
        email="hash-match-count@testcorp.com",
        resume_file_hash=hashlib.md5(content).hexdigest(),
        raw_resume_text="stored resume",
        parsed_skills='["python"]',
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)
    allow_ad_hoc_screening(db, email="admin@testcorp.com")
    before = db.query(Candidate).filter(Candidate.tenant_id == admin.tenant_id).count()
    resp = auth_client.post(
        "/api/analyze",
        files={
            "resume": (
                "test_resume.docx",
                BytesIO(content),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "job_description": LONG_JOB_DESCRIPTION,
            "action": "use_existing",
            "candidate_id": str(existing.id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidate_id"] == existing.id
    assert db.query(Candidate).filter(Candidate.tenant_id == admin.tenant_id).count() == before

