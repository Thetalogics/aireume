"""Focused closure tests for Phase 1 reliability gaps."""
from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.backend.models.db_models import (
    QuotaReservation,
    ScreeningResult,
    Tenant,
    TranscriptAnalysis,
    VoiceScreeningSession,
)


def test_narrative_generation_is_required_at_both_scheduling_boundaries():
    from app.backend.routes.analyze_helpers import _spawn_background_narrative
    from app.backend.services.background_enrichment import (
        background_interview_kit,
        background_voice_strategy,
        schedule_post_narrative_enrichment,
    )
    from app.backend.services.hybrid_pipeline import _background_llm_narrative

    assert (
        inspect.signature(_spawn_background_narrative)
        .parameters["expected_generation"]
        .default
        is inspect.Parameter.empty
    )
    assert (
        inspect.signature(_background_llm_narrative)
        .parameters["expected_analysis_generation"]
        .default
        is inspect.Parameter.empty
    )
    for function in (
        schedule_post_narrative_enrichment,
        background_interview_kit,
        background_voice_strategy,
    ):
        assert (
            inspect.signature(function).parameters["expected_generation"].default
            is inspect.Parameter.empty
        )


@pytest.mark.asyncio
async def test_delayed_generation_one_narrative_cannot_write_generation_two(
    db, seed_subscription_plans
):
    from app.backend.services.hybrid_pipeline import _background_llm_narrative

    tenant = Tenant(name="delayed-narrative", slug=f"delayed-{uuid.uuid4().hex[:10]}")
    db.add(tenant)
    db.flush()
    row = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result='{"generation": 1}',
        narrative_json='{"generation": 1}',
        narrative_status="pending",
        analysis_generation=1,
    )
    db.add(row)
    db.commit()
    row_id = row.id
    tenant_id = tenant.id

    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def delayed_provider(_context):
        provider_started.set()
        await release_provider.wait()
        return {"fit_summary": "old generation"}

    with patch(
        "app.backend.services.hybrid_pipeline.explain_with_llm",
        new=AsyncMock(side_effect=delayed_provider),
    ):
        task = asyncio.create_task(
            _background_llm_narrative(
                screening_result_id=row_id,
                tenant_id=tenant_id,
                llm_context={},
                python_result={},
                expected_analysis_generation=1,
            )
        )
        await provider_started.wait()
        current = db.get(ScreeningResult, row_id)
        current.analysis_generation = 2
        current.analysis_result = '{"generation": 2}'
        current.narrative_json = '{"generation": 2}'
        current.narrative_status = "ready"
        db.commit()
        release_provider.set()
        await task

    db.expire_all()
    current = db.get(ScreeningResult, row_id)
    assert current.analysis_generation == 2
    assert json.loads(current.narrative_json)["generation"] == 2
    assert json.loads(current.analysis_result)["generation"] == 2


def test_transcript_write_is_generation_guarded(db, seed_subscription_plans):
    from app.backend.services.reliability.stale import (
        apply_transcript_if_generation,
    )

    tenant = Tenant(name="transcript-stale", slug=f"tx-{uuid.uuid4().hex[:10]}")
    db.add(tenant)
    db.flush()
    row = TranscriptAnalysis(
        tenant_id=tenant.id,
        transcript_text="new transcript",
        analysis_result='{"generation": 2}',
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
    assert json.loads(row.analysis_result)["generation"] == 2

    outcome = apply_transcript_if_generation(
        db,
        transcript_analysis_id=row.id,
        tenant_id=tenant.id,
        expected_generation=2,
        analysis_result={"generation": 2, "updated": True},
    )
    db.commit()
    db.refresh(row)
    assert outcome.stale is False
    assert json.loads(row.analysis_result)["updated"] is True


def test_sync_analysis_final_write_is_generation_guarded(db, seed_subscription_plans):
    from app.backend.services.screening_command import apply_screening_pipeline_result

    tenant = Tenant(name="sync-stale", slug=f"sync-{uuid.uuid4().hex[:10]}")
    db.add(tenant)
    db.flush()
    row = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result='{"generation":2}',
        analysis_generation=2,
    )
    db.add(row)
    db.commit()

    outcome = apply_screening_pipeline_result(
        db,
        row,
        {"generation": 1, "fit_score": 10},
        expected_generation=1,
    )
    assert outcome is None
    db.expire_all()
    current = db.get(ScreeningResult, row.id)
    assert current.analysis_generation == 2
    assert json.loads(current.analysis_result)["generation"] == 2


def test_voice_completion_claim_is_stale_and_duplicate_safe(db, seed_subscription_plans):
    from app.backend.services.reliability.stale import claim_voice_completion

    tenant = Tenant(name="voice-stale", slug=f"voice-{uuid.uuid4().hex[:10]}")
    db.add(tenant)
    db.flush()
    from app.backend.models.db_models import Candidate

    candidate = Candidate(tenant_id=tenant.id, name="Candidate")
    db.add(candidate)
    db.flush()
    session = VoiceScreeningSession(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        phone_number="+15555550123",
        result_generation=2,
        status="in_progress",
    )
    db.add(session)
    db.commit()

    stale = claim_voice_completion(
        db,
        session_id=session.id,
        expected_generation=1,
        event_id="evt-old",
    )
    assert stale.stale is True
    db.refresh(session)
    assert session.status == "in_progress"

    accepted = claim_voice_completion(
        db,
        session_id=session.id,
        expected_generation=2,
        event_id="evt-new",
    )
    assert accepted.accepted is True
    duplicate = claim_voice_completion(
        db,
        session_id=session.id,
        expected_generation=2,
        event_id="evt-new",
    )
    assert duplicate.duplicate is True


def test_voice_callback_duplicate_and_out_of_order_are_idempotent(
    client, db, seed_subscription_plans
):
    from app.backend.models.db_models import Candidate

    tenant = Tenant(name="voice-callback", slug=f"voice-cb-{uuid.uuid4().hex[:10]}")
    db.add(tenant)
    db.flush()
    candidate = Candidate(tenant_id=tenant.id, name="Candidate")
    db.add(candidate)
    db.flush()
    session = VoiceScreeningSession(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        phone_number="+15555550124",
        result_generation=1,
        status="in_progress",
        interview_depth="quick",
    )
    db.add(session)
    db.commit()
    headers = {"X-Internal-Secret": "test-internal-service-secret"}
    payload = {
        "session_id": session.id,
        "expected_generation": 1,
        "event_id": "voice-event-1",
        "result": {"duration_seconds": 10},
    }

    first = client.post("/api/interviews/internal/complete", json=payload, headers=headers)
    duplicate = client.post("/api/interviews/internal/complete", json=payload, headers=headers)
    assert first.status_code == 200
    assert duplicate.json()["duplicate"] is True

    db.expire_all()
    current = db.get(VoiceScreeningSession, session.id)
    current.result_generation = 2
    current.completion_event_id = None
    current.status = "in_progress"
    current.duration_seconds = 20
    db.commit()
    stale = client.post("/api/interviews/internal/complete", json=payload, headers=headers)
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    db.expire_all()
    current = db.get(VoiceScreeningSession, session.id)
    assert current.result_generation == 2
    assert current.status == "in_progress"
    assert current.duration_seconds == 20


def test_quota_reservation_and_increment_are_atomic_and_idempotent(
    db, seed_subscription_plans
):
    from app.backend.services.reliability.quota_reservation import reserve_analysis_quota

    tenant = Tenant(
        name="quota-atomic",
        slug=f"quota-{uuid.uuid4().hex[:10]}",
        analyses_count_this_month=0,
    )
    db.add(tenant)
    db.commit()

    first = reserve_analysis_quota(
        db,
        tenant_id=tenant.id,
        user_id=None,
        quantity=1,
        operation_id="operation-one",
        analyses_limit=1,
    )
    db.commit()
    second = reserve_analysis_quota(
        db,
        tenant_id=tenant.id,
        user_id=None,
        quantity=1,
        operation_id="operation-one",
        analyses_limit=1,
    )
    db.commit()

    db.refresh(tenant)
    assert first.id == second.id
    assert tenant.analyses_count_this_month == 1
    assert (
        db.query(QuotaReservation)
        .filter_by(tenant_id=tenant.id, operation_id="operation-one")
        .count()
        == 1
    )


def test_quota_transaction_rollback_leaves_no_increment_or_reservation(
    db, seed_subscription_plans
):
    from app.backend.services.reliability.quota_reservation import reserve_analysis_quota

    tenant = Tenant(
        name="quota-rollback",
        slug=f"quota-rb-{uuid.uuid4().hex[:10]}",
        analyses_count_this_month=0,
    )
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id

    reserve_analysis_quota(
        db,
        tenant_id=tenant_id,
        user_id=None,
        quantity=1,
        operation_id="operation-crash",
        analyses_limit=10,
    )
    db.rollback()
    db.expire_all()

    assert db.get(Tenant, tenant_id).analyses_count_this_month == 0
    assert (
        db.query(QuotaReservation)
        .filter_by(tenant_id=tenant_id, operation_id="operation-crash")
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_transient_queue_retry_reuses_and_consumes_one_quota_hold(
    db, seed_subscription_plans, monkeypatch
):
    from app.backend.models.db_models import AnalysisJob
    from app.backend.routes.analyze_helpers import consume_job_analysis_quota
    from app.backend.services.queue_manager import QueueManager

    tenant = Tenant(
        name="retry-quota",
        slug=f"retry-quota-{uuid.uuid4().hex[:8]}",
        analyses_count_this_month=0,
    )
    db.add(tenant)
    db.commit()
    manager = QueueManager()
    manager.worker_id = "retry-worker"
    job_id = await manager.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Python engineer",
        resume_filename="retry.pdf",
        jd_text="Need Python",
        user_id=None,
        db=db,
    )
    db.commit()
    job = await manager.get_next_job(db)

    async def transient_provider_failure(*_args, **_kwargs):
        import httpx

        request = httpx.Request("POST", "https://provider.invalid/analyze")
        response = httpx.Response(
            429,
            headers={"Retry-After": "864000"},
            request=request,
        )
        raise httpx.HTTPStatusError(
            "rate limited",
            request=request,
            response=response,
        )

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job",
        transient_provider_failure,
    )
    assert await manager.process_job(job, db) is False
    db.expire_all()
    job = db.get(AnalysisJob, job_id)
    assert job.status == "retrying"
    assert job.retry_count == 1
    retry_now = (
        datetime.now(timezone.utc)
        if job.next_retry_at.tzinfo
        else datetime.now(timezone.utc).replace(tzinfo=None)
    )
    retry_delay = (job.next_retry_at - retry_now).total_seconds()
    assert 0 < retry_delay <= max(manager.retry_delays)

    job.next_retry_at = datetime.now(timezone.utc)
    db.commit()
    job = await manager.get_next_job(db)

    async def successful_retry(_job_id, session, **_kwargs):
        current = session.get(AnalysisJob, _job_id)
        current.status = "completed"
        consume_job_analysis_quota(session, current)
        session.commit()
        return True

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job",
        successful_retry,
    )
    assert await manager.process_job(job, db) is True
    db.expire_all()
    assert db.get(Tenant, tenant.id).analyses_count_this_month == 1
    reservations = (
        db.query(QuotaReservation)
        .filter(QuotaReservation.tenant_id == tenant.id)
        .all()
    )
    assert len(reservations) == 1
    assert reservations[0].status == "consumed"


@pytest.mark.asyncio
async def test_transient_db_write_failure_never_marks_queue_job_succeeded(
    db, seed_subscription_plans, monkeypatch
):
    from app.backend.models.db_models import AnalysisJob
    from app.backend.services.queue_manager import QueueManager

    tenant = Tenant(
        name="db-failure",
        slug=f"db-failure-{uuid.uuid4().hex[:8]}",
    )
    db.add(tenant)
    db.commit()
    manager = QueueManager()
    manager.worker_id = "db-failure-worker"
    job_id = await manager.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Python engineer",
        resume_filename="db-failure.pdf",
        jd_text="Need Python",
        user_id=None,
        db=db,
    )
    db.commit()
    job = await manager.get_next_job(db)

    async def provider_succeeded_then_db_failed(*_args, **_kwargs):
        raise RuntimeError("database deadlock before completion commit")

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job",
        provider_succeeded_then_db_failed,
    )
    assert await manager.process_job(job, db) is False
    db.expire_all()
    current = db.get(AnalysisJob, job_id)
    assert current.status == "retrying"
    assert current.status != "completed"
