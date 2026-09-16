# app/backend/tests/test_audit_phase_c_screening.py
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.backend.db.database import SessionLocal
from app.backend.models.db_models import (
    AnalysisArtifact,
    AnalysisJob,
    AnalysisResult,
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
    CrossTenantRequisitionError,
    ScreeningCommand,
    analysis_fingerprint,
    command_from_dict,
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

