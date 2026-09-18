"""
Queue Manager - Scalable job queue system for resume analysis

This module provides a robust, scalable queue system with:
- Priority-based job scheduling
- Automatic retry with exponential backoff
- Worker health monitoring
- Deduplication
- Graceful shutdown
- Metrics collection
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm.attributes import flag_modified

from app.backend.db import database as _database
from app.backend.db.database import Base
from app.backend.models.db_models import Tenant, Candidate, Requisition

logger = logging.getLogger(__name__)


class AnalysisQuotaExceeded(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================================================================
# Job Models (SQLAlchemy models for queue tables)
# ============================================================================
# The ORM table definitions now live in app.backend.models.db_models so that
# all schema is colocated and discoverable by Alembic/tooling. They are
# re-exported here for backward compatibility with existing imports.

from app.backend.models.db_models import (  # noqa: F401
    AnalysisJob,
    AnalysisResult,
    DeadLetterJob,
    AnalysisArtifact,
    JobMetrics,
)

CACHEABLE_JOB_STATUSES = ("queued", "processing", "retrying", "completed")
TERMINAL_NON_CACHEABLE_STATUSES = ("failed", "cancelled", "dead_letter")


def terminal_fingerprint(status: str, job_id, original_hash: str) -> str:
    return hashlib.sha256(f"terminal:{status}:{job_id}:{original_hash}".encode("utf-8")).hexdigest()


def archive_terminal_fingerprint(job: AnalysisJob, status: str) -> None:
    cfg = dict(job.job_config or {})
    original = cfg.get("canonical_input_hash") or job.input_hash
    if original:
        cfg["canonical_input_hash"] = original
        job.job_config = cfg
        flag_modified(job, "job_config")
        job.input_hash = terminal_fingerprint(status, job.id, original)


# ============================================================================
# Queue Manager
# ============================================================================

class QueueManager:
    """
    Manages the analysis job queue with priority scheduling, retries, and monitoring.
    """
    
    def __init__(self):
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.worker_version = "2.0.0"
        self.is_running = False
        self.current_job_id: Optional[uuid.UUID] = None
        
        # Configuration
        self.max_concurrent_jobs = max(1, min(32, int(os.getenv("QUEUE_MAX_CONCURRENT", "10"))))
        self._job_semaphore = asyncio.Semaphore(self.max_concurrent_jobs)
        self.poll_interval_seconds = int(os.getenv("QUEUE_POLL_INTERVAL", "2"))
        self.heartbeat_interval_seconds = float(os.getenv("QUEUE_HEARTBEAT_INTERVAL", "30"))
        self.stale_job_timeout_seconds = int(os.getenv("QUEUE_STALE_TIMEOUT", "600"))  # 10 min
        self._heartbeat_tasks: set[asyncio.Task] = set()
        self._stop_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        
        # Retry configuration
        self.retry_delays = [60, 300, 900]  # 1min, 5min, 15min
        
        # Metrics
        self.jobs_processed = 0
        self.jobs_failed = 0
        self.jobs_retried = 0
        
        logger.info(f"QueueManager initialized: worker_id={self.worker_id}, max_concurrent={self.max_concurrent_jobs}")
    
    def compute_hash(self, *inputs: str) -> str:
        """Compute SHA-256 hash of inputs for deduplication."""
        combined = "|".join(str(i) for i in inputs)
        return hashlib.sha256(combined.encode()).hexdigest()
    
    async def enqueue_job(
        self,
        tenant_id: int,
        resume_text: str,
        resume_filename: str,
        jd_text: str,
        candidate_id: Optional[int] = None,
        user_id: Optional[int] = None,
        priority: int = 5,
        job_config: Optional[Dict[str, Any]] = None,
        parsed_resume_cache: Optional[Dict[str, Any]] = None,
        requisition_id: Optional[int] = None,
        scoring_weights: Optional[Dict[str, Any]] = None,
        skill_overrides: Optional[Dict[str, Any]] = None,
        role_template_id: Optional[int] = None,
        db: Optional[Session] = None,
        reuse_artifact_id: Optional[uuid.UUID] = None,
    ) -> uuid.UUID:
        """
        Add a new analysis job to the queue.
        
        Returns:
            job_id: UUID of the created job
        
        Raises:
            IntegrityError: If duplicate job already exists
        """
        from app.backend.services.screening_command import (
            ALGORITHM_VERSION_DEFAULT,
            ArtifactUnavailable,
            CrossTenantRequisitionError,
            ScreeningCommand,
            analysis_fingerprint,
            build_screening_command,
            command_to_dict,
        )

        owns_session = db is None
        if owns_session:
            db = _database.SessionLocal()
        try:
            resume_hash = self.compute_hash(resume_text)
            jd_hash = self.compute_hash(jd_text)
            cmd = build_screening_command(
                db,
                tenant_id=tenant_id,
                user_id=user_id or 0,
                resume_hash=resume_hash,
                jd_hash=jd_hash,
                candidate_id=candidate_id,
                requisition_id=requisition_id,
                role_template_id=role_template_id,
                scoring_weights=scoring_weights,
                skill_overrides=skill_overrides,
                algorithm_version=ALGORITHM_VERSION_DEFAULT,
            )
            input_hash = analysis_fingerprint(cmd)

            existing = db.query(AnalysisJob).filter(
                AnalysisJob.input_hash == input_hash,
                AnalysisJob.tenant_id == tenant_id,
                AnalysisJob.status.in_(CACHEABLE_JOB_STATUSES),
            ).first()

            if existing:
                logger.info("Duplicate job found: %s status=%s", existing.id, existing.status)
                return existing.id

            new_job_id = uuid.uuid4()
            quota_operation_id = str(new_job_id)
            from app.backend.routes.analyze_helpers import _ensure_monthly_reset, _get_plan_limits
            from app.backend.services.plan_entitlement_service import get_tenant_plan
            from app.backend.services.reliability.quota_reservation import (
                QuotaLimitExceeded,
                reserve_analysis_quota,
            )

            tenant = db.get(Tenant, tenant_id)
            if tenant is None:
                raise AnalysisQuotaExceeded("Tenant not found")
            _ensure_monthly_reset(tenant)
            db.flush()
            plan = get_tenant_plan(db, tenant_id)
            analyses_limit = (
                _get_plan_limits(plan).get("analyses_per_month", 20)
                if plan is not None
                else 20
            )
            try:
                reservation = reserve_analysis_quota(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    quantity=1,
                    operation_id=quota_operation_id,
                    analyses_limit=analyses_limit,
                )
            except QuotaLimitExceeded as exc:
                db.rollback()
                raise AnalysisQuotaExceeded(str(exc)) from exc

            reserved_config = dict(job_config or {})
            reserved_config["quota_reserved"] = True
            reserved_config["quota_operation_id"] = quota_operation_id
            reserved_config["command"] = command_to_dict(cmd)
            job_config = reserved_config
            
            if reuse_artifact_id is not None:
                artifact = db.query(AnalysisArtifact).filter(
                    AnalysisArtifact.id == reuse_artifact_id,
                    AnalysisArtifact.tenant_id == tenant_id,
                ).first()
                if not artifact:
                    raise ArtifactUnavailable("Analysis artifact is unavailable")
            else:
                artifact = AnalysisArtifact(
                    tenant_id=tenant_id,
                    resume_filename=resume_filename,
                    resume_size_bytes=len(resume_text.encode()),
                    resume_hash=resume_hash,
                    resume_text=resume_text,
                    resume_text_length=len(resume_text),
                    jd_hash=jd_hash,
                    jd_text=jd_text,
                    jd_size_bytes=len(jd_text.encode()),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                    parsed_resume_cache=parsed_resume_cache,
                )
                db.add(artifact)
                db.flush()

            cmd.artifact_id = str(artifact.id)
            job_config["command"] = command_to_dict(cmd)
            
            # Create job
            job = AnalysisJob(
                id=new_job_id,
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                user_id=user_id,
                resume_hash=resume_hash,
                jd_hash=jd_hash,
                input_hash=input_hash,
                priority=priority,
                artifact_id=artifact.id,
                job_config=job_config,
            )
            db.add(job)
            db.flush()
            reservation.job_id = str(job.id)
            if owns_session:
                db.commit()
            else:
                db.flush()
            
            logger.info(f"Job enqueued: {job.id}, priority={priority}, tenant={tenant_id}")
            return job.id

        except IntegrityError as e:
            db.rollback()
            logger.warning("Duplicate job detected: %s", e)
            existing = db.query(AnalysisJob).filter(
                AnalysisJob.input_hash == input_hash,
                AnalysisJob.tenant_id == tenant_id,
                AnalysisJob.status.in_(CACHEABLE_JOB_STATUSES),
            ).first()
            if existing:
                return existing.id
            poison = db.query(AnalysisJob).filter(AnalysisJob.input_hash == input_hash).first()
            if poison and poison.status in TERMINAL_NON_CACHEABLE_STATUSES:
                archive_terminal_fingerprint(poison, poison.status)
                db.commit()
                return await self.enqueue_job(
                    tenant_id=tenant_id,
                    resume_text=resume_text,
                    resume_filename=resume_filename,
                    jd_text=jd_text,
                    candidate_id=candidate_id,
                    user_id=user_id,
                    priority=priority,
                    job_config=job_config,
                    parsed_resume_cache=parsed_resume_cache,
                    requisition_id=requisition_id,
                    scoring_weights=scoring_weights,
                    skill_overrides=skill_overrides,
                    role_template_id=role_template_id,
                    db=db if not owns_session else None,
                    reuse_artifact_id=reuse_artifact_id,
                )
            raise
        finally:
            if owns_session:
                db.close()
    
    async def get_next_job(self, db: Session) -> Optional[AnalysisJob]:
        """
        Get the next job to process based on priority and queue time.
        
        Uses SELECT FOR UPDATE SKIP LOCKED for concurrent worker safety.
        """
        # Query for next available job
        job = db.execute(
            select(AnalysisJob)
            .where(
                and_(
                    AnalysisJob.status.in_(['queued', 'retrying']),
                    or_(
                        AnalysisJob.next_retry_at.is_(None),
                        AnalysisJob.next_retry_at <= datetime.now(timezone.utc)
                    )
                )
            )
            .order_by(AnalysisJob.priority.asc(), AnalysisJob.queued_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        
        if job:
            # Claim the job and issue a 10-minute lease
            job.status = 'processing'
            job.worker_id = self.worker_id
            job.started_at = datetime.now(timezone.utc)
            job.worker_heartbeat = datetime.now(timezone.utc)
            job.leased_until = datetime.now(timezone.utc) + timedelta(seconds=self.stale_job_timeout_seconds)
            db.commit()
            
            logger.info(f"Claimed job: {job.id}, priority={job.priority}, retry_count={job.retry_count}")
            try:
                from app.backend.services.metrics import QUEUE_WAIT_SECONDS
                if job.queued_at and job.started_at:
                    wait = (job.started_at - job.queued_at).total_seconds()
                    if wait >= 0:
                        QUEUE_WAIT_SECONDS.observe(wait)
            except Exception:
                pass
        
        return job
    
    async def update_heartbeat(self, job_id: uuid.UUID, db: Session):
        """Update worker heartbeat and renew the processing lease."""
        now = datetime.now(timezone.utc)
        db.execute(
            update(AnalysisJob)
            .where(
                AnalysisJob.id == job_id,
                AnalysisJob.status == "processing",
                AnalysisJob.worker_id == self.worker_id,
            )
            .values(
                worker_heartbeat=now,
                leased_until=now + timedelta(seconds=self.stale_job_timeout_seconds),
            )
        )
        db.commit()

    def _owns_lease(self, db: Session, job: AnalysisJob) -> bool:
        fresh = db.query(AnalysisJob).filter(AnalysisJob.id == job.id).first()
        if fresh is None:
            return False
        if fresh.worker_id != self.worker_id or fresh.status != "processing":
            return False
        lease = fresh.leased_until
        if lease is None:
            return True
        now = datetime.now(timezone.utc)
        if getattr(lease, "tzinfo", None) is None:
            lease = lease.replace(tzinfo=timezone.utc)
        return lease >= now

    async def process_job(self, job: AnalysisJob, db: Session) -> bool:
        """
        Process a single analysis job via the shared screening pipeline.
        
        Returns:
            True if successful, False if failed
        """
        start_time = time.time()
        stop = asyncio.Event()

        async def _beat():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval_seconds)
                    break
                except asyncio.TimeoutError:
                    hb_db = _database.SessionLocal()
                    try:
                        await self.update_heartbeat(job.id, hb_db)
                    finally:
                        hb_db.close()

        beat_task = asyncio.create_task(_beat())
        self._heartbeat_tasks.add(beat_task)
        try:
            return await self._process_job_body(job, db, start_time)
        finally:
            stop.set()
            beat_task.cancel()
            self._heartbeat_tasks.discard(beat_task)
            with contextlib.suppress(asyncio.CancelledError):
                await beat_task

    async def _process_job_body(self, job: AnalysisJob, db: Session, start_time: float) -> bool:
        job_id = job.id
        try:
            if not self._owns_lease(db, job):
                logger.warning("lease_lost before process job_id=%s", job.id)
                db.rollback()
                return False
            artifact = db.query(AnalysisArtifact).filter(AnalysisArtifact.id == job.artifact_id).first()
            if not artifact:
                raise ValueError(f"Artifact not found: {job.artifact_id}")
            
            logger.info(f"Processing job {job.id}: {artifact.resume_filename}")
            
            job.processing_stage = 'parsing'
            job.progress_percent = 10
            db.commit()
            
            from app.backend.services.queue_analysis_service import complete_queue_job

            if not self._owns_lease(db, job):
                logger.warning("lease_lost before result commit job_id=%s", job.id)
                db.rollback()
                return False
            completed = await complete_queue_job(
                job.id,
                db,
                expected_worker_id=self.worker_id,
            )
            if not completed:
                return False
            db.refresh(job)
            
            total_time_ms = int((time.time() - start_time) * 1000)
            queue_wait_ms = int((job.started_at - job.queued_at).total_seconds() * 1000) if job.started_at and job.queued_at else 0
            
            metrics = JobMetrics(
                job_id=job.id,
                tenant_id=job.tenant_id,
                queue_wait_time_ms=queue_wait_ms,
                total_time_ms=total_time_ms,
                stage_timings={'total_analysis': total_time_ms},
                worker_id=self.worker_id,
                worker_version=self.worker_version,
                retry_attempts=job.retry_count,
            )
            db.add(metrics)
            db.commit()
            
            self.jobs_processed += 1
            try:
                from app.backend.services.metrics import QUEUE_PROCESSING_SECONDS
                QUEUE_PROCESSING_SECONDS.observe(max(total_time_ms / 1000.0, 0))
            except Exception:
                pass
            logger.info(f"Job completed: {job.id}, time={total_time_ms}ms")
            return True
            
        except Exception as e:
            db.rollback()
            job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            if job is None:
                logger.error("Job failed then disappeared: %s", job_id, exc_info=True)
                return False
            if not self._owns_lease(db, job):
                logger.warning(
                    "lease_lost while handling failure job_id=%s worker_id=%s",
                    job_id,
                    self.worker_id,
                )
                db.rollback()
                return False
            logger.error(f"Job failed: {job.id}, error={str(e)}", exc_info=True)
            
            # Determine if we should retry
            from app.backend.services.reliability.retry import classify_failure

            decision = classify_failure(e)
            should_retry = decision.should_retry and job.retry_count < job.max_retries
            job.last_error_category = decision.category.value
            job.last_error_at = datetime.now(timezone.utc)
            
            if should_retry:
                # Calculate next retry time with exponential backoff
                retry_delay = self.retry_delays[min(job.retry_count, len(self.retry_delays) - 1)]
                if decision.retry_after_seconds is not None:
                    retry_delay = min(
                        max(0.0, decision.retry_after_seconds),
                        float(max(self.retry_delays)),
                    )
                next_retry = datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
                
                job.status = 'retrying'
                job.retry_count += 1
                job.next_retry_at = next_retry
                job.error_message = str(e)
                job.error_type = type(e).__name__
                
                self.jobs_retried += 1
                try:
                    from app.backend.services.metrics import QUEUE_RETRY_TOTAL
                    QUEUE_RETRY_TOTAL.labels(reason="job_error").inc()
                except Exception:
                    pass
                logger.info(f"Job will retry: {job.id}, attempt={job.retry_count}/{job.max_retries}, next_retry={next_retry}")
            else:
                # Max retries exceeded — move to dead letter queue
                job.status = 'failed'
                job.failed_at = datetime.now(timezone.utc)
                job.error_message = str(e)
                job.error_type = type(e).__name__
                
                self.jobs_failed += 1
                logger.error(f"Job permanently failed: {job.id}, retries exhausted")
                from app.backend.routes.analyze_helpers import release_job_analysis_quota
                release_job_analysis_quota(db, job)
                try:
                    await self.move_to_dead_letter(
                        db,
                        job,
                        "retries exhausted",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                except Exception as dlq_exc:
                    logger.error("Failed to move job %s to DLQ: %s", job.id, dlq_exc)
            
            db.commit()
            
            # Save failure metrics
            total_time_ms = int((time.time() - start_time) * 1000)
            metrics = JobMetrics(
                job_id=job.id,
                tenant_id=job.tenant_id,
                total_time_ms=total_time_ms,
                error_stage=job.processing_stage,
                retry_attempts=job.retry_count,
                worker_id=self.worker_id,
                worker_version=self.worker_version,
            )
            db.add(metrics)
            db.commit()
            
            return False
    
    async def recover_stale_jobs(self, db: Session):
        """
        Recover jobs that have been processing for too long (worker died).

        Uses a conditional UPDATE so concurrent recoveries increment retry_count
        at most once per job (second UPDATE matches 0 rows).
        """
        stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=self.stale_job_timeout_seconds)
        now = datetime.now(timezone.utc)
        stale_cond = or_(
            AnalysisJob.worker_heartbeat < stale_threshold,
            and_(AnalysisJob.leased_until.isnot(None), AnalysisJob.leased_until < now),
        )

        stale_jobs = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.status == "processing", stale_cond)
            .all()
        )

        recovered = 0
        for job in stale_jobs:
            logger.warning(
                "Recovering stale job: %s, worker=%s, last_heartbeat=%s",
                job.id,
                job.worker_id,
                job.worker_heartbeat,
            )
            if job.retry_count < job.max_retries:
                values = {
                    "status": "retrying",
                    "retry_count": job.retry_count + 1,
                    "next_retry_at": now + timedelta(seconds=60),
                    "worker_id": None,
                }
            else:
                values = {
                    "status": "failed",
                    "failed_at": now,
                    "error_message": "Worker timeout - job abandoned",
                    "error_type": "WorkerTimeout",
                }
            result = db.execute(
                update(AnalysisJob)
                .where(
                    AnalysisJob.id == job.id,
                    AnalysisJob.status == "processing",
                    AnalysisJob.retry_count == job.retry_count,
                    stale_cond,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                continue
            recovered += 1
            try:
                from app.backend.services.metrics import LEASE_RECOVERY_TOTAL

                LEASE_RECOVERY_TOTAL.inc()
            except Exception:
                pass
            db.expire(job)
            if values["status"] == "failed":
                db.refresh(job)
                from app.backend.routes.analyze_helpers import release_job_analysis_quota
                release_job_analysis_quota(db, job)
                await self.move_to_dead_letter(
                    db,
                    job,
                    "Worker timeout - job abandoned",
                    error_type="WorkerTimeout",
                    error_message="Worker timeout - job abandoned",
                )

        if recovered:
            db.commit()
            logger.info("Recovered %s stale jobs", recovered)
        from app.backend.services.reliability.quota_reservation import reconcile_expired_quota_reservations
        try:
            reconcile_expired_quota_reservations(db)
        except Exception:
            db.rollback()
            try:
                from app.backend.services.metrics import QUOTA_RECONCILE_FAILURE_TOTAL

                QUOTA_RECONCILE_FAILURE_TOTAL.inc()
            except Exception:
                pass
            raise

    # ─── Dead Letter Queue Operations ───────────────────────────────────────────

    async def move_to_dead_letter(
        self,
        db: Session,
        job: AnalysisJob,
        failure_reason: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        error_trace: Optional[str] = None,
    ) -> DeadLetterJob:
        """
        Move a failed job to the dead letter queue after all retries exhausted.
        """
        original_input_hash = (job.job_config or {}).get("canonical_input_hash") or job.input_hash
        existing = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).first()
        if existing:
            archive_terminal_fingerprint(job, "dead_letter")
            job.status = "dead_letter"
            job.failed_at = job.failed_at or datetime.now(timezone.utc)
            db.commit()
            return existing

        dlq_job = DeadLetterJob(
            original_job_id=job.id,
            tenant_id=job.tenant_id,
            candidate_id=job.candidate_id,
            user_id=job.user_id,
            job_type=job.job_type,
            resume_hash=job.resume_hash,
            jd_hash=job.jd_hash,
            input_hash=original_input_hash,
            artifact_id=job.artifact_id,
            job_config=job.job_config,
            failure_reason=failure_reason,
            failure_type=error_type,
            last_error_message=error_message,
            last_error_trace=error_trace,
            retry_count=job.retry_count,
            original_created_at=job.created_at,
            status='pending',
        )

        db.add(dlq_job)
        job.status = 'dead_letter'
        job.failed_at = datetime.now(timezone.utc)
        archive_terminal_fingerprint(job, "dead_letter")
        
        db.commit()
        logger.warning("Moved job %s to dead letter queue: %s", job.id, failure_reason)
        try:
            from app.backend.services.metrics import DLQ_DEPTH, DLQ_MOVED_TOTAL
            from app.backend.services.observability import capture_message

            DLQ_MOVED_TOTAL.inc()
            depth = db.query(func.count(DeadLetterJob.id)).filter(
                DeadLetterJob.status == "pending"
            ).scalar() or 0
            DLQ_DEPTH.set(depth)
            threshold = int(os.getenv("DLQ_ALERT_THRESHOLD", "10"))
            if depth >= threshold:
                capture_message(
                    f"DLQ depth alert: {depth} pending dead-letter jobs (threshold={threshold})"
                )
        except (ValueError, TypeError, ImportError) as metric_exc:
            logger.warning("DLQ metrics skipped: %s", metric_exc)
        return dlq_job

    async def get_dead_letter_jobs(
        self,
        db: Session,
        tenant_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[DeadLetterJob]:
        """Get dead letter jobs with optional filters."""
        query = db.query(DeadLetterJob)
        
        if tenant_id:
            query = query.filter(DeadLetterJob.tenant_id == tenant_id)
        if status:
            query = query.filter(DeadLetterJob.status == status)
        
        return query.order_by(DeadLetterJob.failed_at.desc()).limit(limit).all()

    async def retry_dead_letter_job(self, db: Session, dlq_job_id: uuid.UUID) -> Optional[AnalysisJob]:
        """Retry a dead letter job by creating a new analysis job."""
        from app.backend.services.screening_command import ArtifactUnavailable, command_from_dict

        dlq_job = db.query(DeadLetterJob).filter(DeadLetterJob.id == dlq_job_id).first()
        
        if not dlq_job:
            return None
        
        if dlq_job.status != 'pending':
            raise ValueError(f"Cannot retry dead letter job with status: {dlq_job.status}")

        artifact = None
        if dlq_job.artifact_id is not None:
            artifact = db.query(AnalysisArtifact).filter(
                AnalysisArtifact.id == dlq_job.artifact_id,
                AnalysisArtifact.tenant_id == dlq_job.tenant_id,
            ).first()
        if not artifact:
            raise ArtifactUnavailable("Analysis artifact is unavailable")

        cmd_data = (dlq_job.job_config or {}).get("command") or {}
        cmd = command_from_dict(cmd_data) if cmd_data else None
        retry_config = dict(dlq_job.job_config or {})
        retry_config.pop("quota_released", None)
        new_id = await self.enqueue_job(
            tenant_id=dlq_job.tenant_id,
            resume_text=artifact.resume_text,
            resume_filename=artifact.resume_filename,
            jd_text=artifact.jd_text,
            candidate_id=dlq_job.candidate_id,
            user_id=dlq_job.user_id,
            job_config=retry_config,
            parsed_resume_cache=artifact.parsed_resume_cache,
            requisition_id=cmd.requisition_id if cmd else None,
            scoring_weights=cmd.scoring_weights if cmd else None,
            skill_overrides=cmd.skill_overrides if cmd else None,
            role_template_id=cmd.role_template_id if cmd else None,
            db=db,
            reuse_artifact_id=artifact.id,
        )
        if not new_id:
            raise ArtifactUnavailable("Analysis artifact is unavailable")

        new_job = db.query(AnalysisJob).filter(AnalysisJob.id == new_id).first()
        dlq_job.status = 'reprocessed'
        dlq_job.reprocessed_at = datetime.now(timezone.utc)
        dlq_job.reprocessed_job_id = new_job.id
        db.commit()
        logger.info(f"Retried dead letter job {dlq_job_id} as new job {new_job.id}")
        return new_job

    async def purge_expired_artifacts(self, db: Session) -> int:
        """Delete expired artifacts that are not referenced by pending DLQ or live jobs."""
        now = datetime.now(timezone.utc)
        live_ids = {
            row[0]
            for row in db.query(AnalysisJob.artifact_id).filter(
                AnalysisJob.artifact_id.isnot(None),
                AnalysisJob.status.in_(["queued", "processing", "retrying", "completed"]),
            ).all()
        }
        pending_dlq_ids = {
            row[0]
            for row in db.query(DeadLetterJob.artifact_id).filter(
                DeadLetterJob.status == "pending",
                DeadLetterJob.artifact_id.isnot(None),
            ).all()
        }
        keep = live_ids | pending_dlq_ids
        deleted = 0
        expired = db.query(AnalysisArtifact).filter(
            AnalysisArtifact.expires_at.isnot(None),
            AnalysisArtifact.expires_at < now,
        ).all()
        for art in expired:
            if art.id in keep:
                continue
            db.delete(art)
            deleted += 1
        db.commit()
        return deleted

    async def _process_claimed_job(self, job_id: uuid.UUID) -> None:
        async with self._job_semaphore:
            db = _database.SessionLocal()
            try:
                job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one_or_none()
                if not job:
                    return
                self.current_job_id = job.id
                try:
                    await self.process_job(job, db)
                finally:
                    self.current_job_id = None
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                await asyncio.sleep(5)
            finally:
                db.close()

    async def worker_loop(self):
        """Main worker loop - processes up to max_concurrent_jobs in parallel."""
        logger.info(f"Worker loop started: {self.worker_id}")
        self.is_running = True
        self._stop_event.clear()
        in_flight: set[asyncio.Task] = set()
        shutdown_timeout = float(os.getenv("QUEUE_SHUTDOWN_TIMEOUT", "30"))
        claim_db = _database.SessionLocal()
        try:
            while self.is_running:
                while self.is_running and len(in_flight) < self.max_concurrent_jobs:
                    job = await self.get_next_job(claim_db)
                    if not job:
                        break
                    task = asyncio.create_task(self._process_claimed_job(job.id))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)
                if in_flight:
                    stop_wait = asyncio.create_task(self._stop_event.wait())
                    done, _ = await asyncio.wait(
                        in_flight | {stop_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_wait in done:
                        break
                    stop_wait.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stop_wait
                else:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
            if in_flight:
                _, pending = await asyncio.wait(in_flight, timeout=shutdown_timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        finally:
            claim_db.close()
            self.is_running = False

        logger.info(f"Worker loop stopped: {self.worker_id}")
    
    def start(self):
        """Start the queue worker."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self.worker_loop())
    
    def stop(self):
        """Stop the queue worker gracefully."""
        logger.info(f"Stopping worker: {self.worker_id}")
        self.is_running = False
        self._stop_event.set()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics."""
        return {
            "worker_id": self.worker_id,
            "is_running": self.is_running,
            "current_job_id": str(self.current_job_id) if self.current_job_id else None,
            "jobs_processed": self.jobs_processed,
            "jobs_failed": self.jobs_failed,
            "jobs_retried": self.jobs_retried,
        }


# ============================================================================
# Global queue manager instance
# ============================================================================

_queue_manager: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    """Get or create the global queue manager instance."""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = QueueManager()
    return _queue_manager


async def start_queue_worker():
    """Start the background queue worker."""
    manager = get_queue_manager()
    manager.start()
    logger.info("Queue worker started")


async def stop_queue_worker():
    """Stop the background queue worker."""
    manager = get_queue_manager()
    manager.stop()
    if manager._worker_task is not None:
        try:
            await manager._worker_task
        finally:
            manager._worker_task = None
    logger.info("Queue worker stopped")
