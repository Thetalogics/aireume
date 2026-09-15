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

from app.backend.db.database import SessionLocal, Base
from app.backend.models.db_models import Tenant, Candidate

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
        self.max_concurrent_jobs = int(os.getenv("QUEUE_MAX_CONCURRENT", "10"))
        self.poll_interval_seconds = int(os.getenv("QUEUE_POLL_INTERVAL", "2"))
        self.heartbeat_interval_seconds = int(os.getenv("QUEUE_HEARTBEAT_INTERVAL", "30"))
        self.stale_job_timeout_seconds = int(os.getenv("QUEUE_STALE_TIMEOUT", "600"))  # 10 min
        
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
    ) -> uuid.UUID:
        """
        Add a new analysis job to the queue.
        
        Returns:
            job_id: UUID of the created job
        
        Raises:
            IntegrityError: If duplicate job already exists
        """
        db = SessionLocal()
        try:
            # Compute hashes for deduplication
            resume_hash = self.compute_hash(resume_text)
            jd_hash = self.compute_hash(jd_text)
            input_hash = self.compute_hash(resume_hash, jd_hash, str(tenant_id))
            
            # Check if identical job already exists and is not failed
            existing = db.query(AnalysisJob).filter(
                AnalysisJob.input_hash == input_hash,
                AnalysisJob.tenant_id == tenant_id,
                AnalysisJob.status.in_(['queued', 'processing', 'completed', 'retrying'])
            ).first()
            
            if existing:
                if existing.status == 'completed':
                    logger.info(f"Duplicate job found (completed): {existing.id}")
                    return existing.id
                else:
                    logger.info(f"Duplicate job found (in progress): {existing.id}, status={existing.status}")
                    return existing.id

            from app.backend.routes.analyze_helpers import _check_and_increment_usage
            allowed, message = _check_and_increment_usage(db, tenant_id, user_id or 0, 1)
            if not allowed:
                raise AnalysisQuotaExceeded(message or "Monthly analysis quota exceeded")

            reserved_config = dict(job_config or {})
            reserved_config["quota_reserved"] = True
            job_config = reserved_config
            
            # Create artifact
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
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),  # Keep for 30 days
                parsed_resume_cache=parsed_resume_cache,
            )
            db.add(artifact)
            db.flush()
            
            # Create job
            job = AnalysisJob(
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
            db.commit()
            
            logger.info(f"Job enqueued: {job.id}, priority={priority}, tenant={tenant_id}")
            return job.id
            
        except IntegrityError as e:
            db.rollback()
            logger.warning(f"Duplicate job detected: {e}")
            # Return existing job ID
            existing = db.query(AnalysisJob).filter(AnalysisJob.input_hash == input_hash).first()
            return existing.id if existing else None
        finally:
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
            job.leased_until = datetime.now(timezone.utc) + timedelta(minutes=10)
            db.commit()
            
            logger.info(f"Claimed job: {job.id}, priority={job.priority}, retry_count={job.retry_count}")
        
        return job
    
    async def update_heartbeat(self, job_id: uuid.UUID, db: Session):
        """Update worker heartbeat to show job is still being processed."""
        db.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .values(worker_heartbeat=datetime.now(timezone.utc))
        )
        db.commit()
    
    async def process_job(self, job: AnalysisJob, db: Session) -> bool:
        """
        Process a single analysis job via the shared screening pipeline.
        
        Returns:
            True if successful, False if failed
        """
        start_time = time.time()
        
        try:
            artifact = db.query(AnalysisArtifact).filter(AnalysisArtifact.id == job.artifact_id).first()
            if not artifact:
                raise ValueError(f"Artifact not found: {job.artifact_id}")
            
            logger.info(f"Processing job {job.id}: {artifact.resume_filename}")
            
            job.processing_stage = 'parsing'
            job.progress_percent = 10
            db.commit()
            
            from app.backend.services.queue_analysis_service import complete_queue_job
            
            await complete_queue_job(job.id, db)
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
            logger.info(f"Job completed: {job.id}, time={total_time_ms}ms")
            return True
            
        except Exception as e:
            logger.error(f"Job failed: {job.id}, error={str(e)}", exc_info=True)
            
            # Determine if we should retry
            should_retry = job.retry_count < job.max_retries
            
            if should_retry:
                # Calculate next retry time with exponential backoff
                retry_delay = self.retry_delays[min(job.retry_count, len(self.retry_delays) - 1)]
                next_retry = datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
                
                job.status = 'retrying'
                job.retry_count += 1
                job.next_retry_at = next_retry
                job.error_message = str(e)
                job.error_type = type(e).__name__
                
                self.jobs_retried += 1
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
        """
        stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=self.stale_job_timeout_seconds)
        
        stale_jobs = db.query(AnalysisJob).filter(
            AnalysisJob.status == 'processing',
            AnalysisJob.worker_heartbeat < stale_threshold
        ).all()

        now = datetime.now(timezone.utc)
        leased_jobs = db.query(AnalysisJob).filter(
            AnalysisJob.status == "processing",
            AnalysisJob.leased_until != None,
            AnalysisJob.leased_until < now,
        ).all()
        seen_ids = {job.id for job in stale_jobs}
        for job in leased_jobs:
            if job.id not in seen_ids:
                stale_jobs.append(job)
                seen_ids.add(job.id)
        
        for job in stale_jobs:
            logger.warning(f"Recovering stale job: {job.id}, worker={job.worker_id}, last_heartbeat={job.worker_heartbeat}")
            
            if job.retry_count < job.max_retries:
                job.status = 'retrying'
                job.retry_count += 1
                job.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                job.worker_id = None
            else:
                job.status = 'failed'
                job.failed_at = datetime.now(timezone.utc)
                job.error_message = "Worker timeout - job abandoned"
                job.error_type = "WorkerTimeout"
                from app.backend.routes.analyze_helpers import release_job_analysis_quota
                release_job_analysis_quota(db, job)
        
        if stale_jobs:
            db.commit()
            logger.info(f"Recovered {len(stale_jobs)} stale jobs")

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
        dlq_job = DeadLetterJob(
            original_job_id=job.id,
            tenant_id=job.tenant_id,
            candidate_id=job.candidate_id,
            user_id=job.user_id,
            job_type=job.job_type,
            resume_hash=job.resume_hash,
            jd_hash=job.jd_hash,
            input_hash=job.input_hash,
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
        
        # Mark original job as dead-lettered
        job.status = 'dead_letter'
        job.failed_at = datetime.now(timezone.utc)
        
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
        dlq_job = db.query(DeadLetterJob).filter(DeadLetterJob.id == dlq_job_id).first()
        
        if not dlq_job:
            return None
        
        if dlq_job.status != 'pending':
            raise ValueError(f"Cannot retry dead letter job with status: {dlq_job.status}")
        
        # Create new job from dead letter job
        new_job = AnalysisJob(
            tenant_id=dlq_job.tenant_id,
            candidate_id=dlq_job.candidate_id,
            user_id=dlq_job.user_id,
            job_type=dlq_job.job_type,
            resume_hash=dlq_job.resume_hash,
            jd_hash=dlq_job.jd_hash,
            input_hash=dlq_job.input_hash,
            job_config=dlq_job.job_config,
            priority=5,
            status='queued',
        )
        
        db.add(new_job)
        
        # Update dead letter job status
        dlq_job.status = 'reprocessed'
        dlq_job.reprocessed_at = datetime.now(timezone.utc)
        dlq_job.reprocessed_job_id = new_job.id
        
        db.commit()
        logger.info(f"Retried dead letter job {dlq_job_id} as new job {new_job.id}")
        
        return new_job

    async def worker_loop(self):
        """Main worker loop - processes jobs from the queue."""
        logger.info(f"Worker loop started: {self.worker_id}")
        self.is_running = True
        
        last_heartbeat = time.time()
        last_recovery = time.time()
        
        while self.is_running:
            db = SessionLocal()
            try:
                # Periodic stale job recovery (every 5 minutes)
                if time.time() - last_recovery > 300:
                    await self.recover_stale_jobs(db)
                    last_recovery = time.time()
                
                # Get next job
                job = await self.get_next_job(db)
                
                if job:
                    self.current_job_id = job.id
                    
                    # Process with periodic heartbeats
                    success = await self.process_job(job, db)
                    
                    self.current_job_id = None
                else:
                    # No jobs available, wait before polling again
                    await asyncio.sleep(self.poll_interval_seconds)
                
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Back off on error
            finally:
                db.close()
        
        logger.info(f"Worker loop stopped: {self.worker_id}")
    
    def start(self):
        """Start the queue worker."""
        asyncio.create_task(self.worker_loop())
    
    def stop(self):
        """Stop the queue worker gracefully."""
        logger.info(f"Stopping worker: {self.worker_id}")
        self.is_running = False
    
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
    logger.info("Queue worker stopped")
