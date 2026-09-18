"""Deterministic graceful-shutdown semantics for the PostgreSQL-backed worker."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.backend.models.db_models import AnalysisJob, Tenant
from app.backend.services.queue_manager import QueueManager


async def _queued_job(db, mgr: QueueManager, slug: str):
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.commit()
    return await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text=f"{slug} resume",
        resume_filename=f"{slug}.pdf",
        jd_text=f"{slug} jd",
    )


@pytest.mark.asyncio
async def test_shutdown_without_active_jobs_is_immediate(monkeypatch):
    mgr = QueueManager()
    mgr.poll_interval_seconds = 60
    monkeypatch.setattr(mgr, "get_next_job", lambda _db: asyncio.sleep(0, result=None))
    task = asyncio.create_task(mgr.worker_loop())
    await asyncio.sleep(0)
    mgr.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_public_stop_api_waits_for_worker_loop(monkeypatch):
    from app.backend.services import queue_manager as module

    mgr = QueueManager()
    mgr.poll_interval_seconds = 60
    monkeypatch.setattr(mgr, "get_next_job", lambda _db: asyncio.sleep(0, result=None))
    monkeypatch.setattr(module, "_queue_manager", mgr)

    await module.start_queue_worker()
    await asyncio.sleep(0)
    await asyncio.wait_for(module.stop_queue_worker(), timeout=1)

    assert mgr._worker_task is None
    assert mgr.is_running is False


@pytest.mark.asyncio
async def test_registered_analysis_tasks_are_cancelled_and_awaited():
    from app.backend.services.hybrid_pipeline import (
        _background_tasks,
        register_background_task,
        shutdown_background_tasks,
    )

    started = asyncio.Event()

    async def long_enrichment():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(long_enrichment())
    register_background_task(task)
    await started.wait()
    await shutdown_background_tasks(timeout=1)

    assert task.done()
    assert task.cancelled()
    assert not _background_tasks


@pytest.mark.asyncio
async def test_short_active_job_drains_before_shutdown(
    monkeypatch, db, seed_subscription_plans
):
    mgr = QueueManager()
    mgr.max_concurrent_jobs = 1
    mgr._job_semaphore = asyncio.Semaphore(1)
    job_id = await _queued_job(db, mgr, "shutdown-short")
    completed = asyncio.Event()

    async def complete(job_id, session, **_kwargs):
        job = session.get(AnalysisJob, job_id)
        job.status = "completed"
        session.commit()
        completed.set()
        return True

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job", complete
    )
    task = asyncio.create_task(mgr.worker_loop())
    await asyncio.wait_for(completed.wait(), timeout=2)
    mgr.stop()
    await asyncio.wait_for(task, timeout=2)
    db.expire_all()
    assert db.get(AnalysisJob, job_id).status == "completed"


@pytest.mark.asyncio
async def test_long_job_is_cancelled_without_false_completion_and_reclaimable(
    monkeypatch, db, seed_subscription_plans
):
    monkeypatch.setenv("QUEUE_SHUTDOWN_TIMEOUT", "0.05")
    mgr = QueueManager()
    mgr.max_concurrent_jobs = 1
    mgr._job_semaphore = asyncio.Semaphore(1)
    mgr.heartbeat_interval_seconds = 0.01
    mgr.stale_job_timeout_seconds = 1
    job_id = await _queued_job(db, mgr, "shutdown-long")
    started = asyncio.Event()

    async def never_complete(_job_id, _session, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job",
        never_complete,
    )
    task = asyncio.create_task(mgr.worker_loop())
    await asyncio.wait_for(started.wait(), timeout=2)
    mgr.stop()
    await asyncio.wait_for(task, timeout=2)
    assert not mgr._heartbeat_tasks

    db.expire_all()
    job = db.get(AnalysisJob, job_id)
    assert job.status == "processing"
    assert job.completed_at is None
    job.leased_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    replacement = QueueManager()
    await replacement.recover_stale_jobs(db)
    db.expire_all()
    assert db.get(AnalysisJob, job_id).status == "retrying"


@pytest.mark.asyncio
async def test_stop_signal_prevents_claiming_another_job(
    monkeypatch, db, seed_subscription_plans
):
    monkeypatch.setenv("QUEUE_SHUTDOWN_TIMEOUT", "0.05")
    mgr = QueueManager()
    mgr.max_concurrent_jobs = 1
    mgr._job_semaphore = asyncio.Semaphore(1)
    first = await _queued_job(db, mgr, "shutdown-one")
    second = await _queued_job(db, mgr, "shutdown-two")
    started = asyncio.Event()

    async def never_complete(_job_id, _session, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.backend.services.queue_analysis_service.complete_queue_job",
        never_complete,
    )
    task = asyncio.create_task(mgr.worker_loop())
    await asyncio.wait_for(started.wait(), timeout=2)
    mgr.stop()
    await asyncio.wait_for(task, timeout=2)
    db.expire_all()
    assert db.get(AnalysisJob, first).status == "processing"
    assert db.get(AnalysisJob, second).status == "queued"
