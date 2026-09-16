"""
Queue API - REST endpoints for job queue management and monitoring

Provides endpoints for:
- Submitting analysis jobs
- Checking job status
- Retrieving results
- Queue statistics and monitoring
- Admin operations (retry, cancel, etc.)
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.backend.db.database import get_db
from app.backend.models.db_models import User, Tenant
from app.backend.routes.auth import get_current_user
from app.backend.middleware.auth import require_active_subscription, require_platform_admin, require_feature
from app.backend.services.queue_manager import (
    get_queue_manager,
    AnalysisJob,
    AnalysisResult,
    AnalysisArtifact,
    JobMetrics,
    AnalysisQuotaExceeded,
)
from app.backend.services.screening_command import (
    CandidateNotFoundError,
    CrossTenantRequisitionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["Queue Management"])


def _compute_content_hash(tenant_id: int, candidate_id: Optional[int]) -> str:
    """Generate a deduplication hash for tenant+candidate-based 60s window check."""
    content = f"{tenant_id}:{candidate_id}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ============================================================================
# Job Submission
# ============================================================================

@router.post("/submit")
async def submit_analysis_job(
    resume_text: str,
    resume_filename: str,
    job_description: str,
    candidate_id: Optional[int] = None,
    requisition_id: Optional[int] = None,
    priority: int = 5,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """
    Submit a new resume analysis job to the queue.
    
    Priority levels:
    - 1-2: High priority (premium users, urgent)
    - 3-5: Normal priority (default)
    - 6-10: Low priority (batch jobs, background)
    """
    from app.backend.routes.analyze_helpers import _enforce_screening_mode
    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)
    # Content-hash deduplication: reject identical tenant+candidate jobs within 60s
    content_hash = _compute_content_hash(current_user.tenant_id, candidate_id)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    existing = db.query(AnalysisJob).filter(
        AnalysisJob.content_hash == content_hash,
        AnalysisJob.status.in_(['queued', 'processing']),
        AnalysisJob.queued_at > cutoff,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate job detected within last 60 seconds. Existing job ID: {existing.id}",
        )

    queue_manager = get_queue_manager()
    
    try:
        job_id = await queue_manager.enqueue_job(
            tenant_id=current_user.tenant_id,
            resume_text=resume_text,
            resume_filename=resume_filename,
            jd_text=job_description,
            candidate_id=candidate_id,
            user_id=current_user.id,
            priority=priority,
            requisition_id=requisition_id,
        )

        # Store content_hash on the newly created job
        new_job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if new_job and new_job.content_hash is None:
            new_job.content_hash = content_hash
            db.commit()
        
        return {
            "job_id": str(job_id),
            "status": "queued",
            "message": "Analysis job submitted successfully",
        }
    except HTTPException:
        raise
    except (CrossTenantRequisitionError, CandidateNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except AnalysisQuotaExceeded as e:
        raise HTTPException(status_code=403, detail=e.message) from e
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Failed to submit job: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except OSError as e:
        logger.error(
            "Failed to submit job: %s", e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=502, detail="Upstream failure") from e
    except (SQLAlchemyError, RuntimeError) as e:
        logger.exception(
            "Failed to submit job: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "QUEUE_ERROR"},
        )
        raise HTTPException(status_code=500, detail=f"Failed to submit job: {str(e)}") from e


def _parse_queue_options(
    scoring_weights: str | None,
    skill_overrides: str | None,
    template_id: str | None,
) -> tuple[dict | None, dict | None, int | None]:
    weights = None
    overrides = None
    tpl_id = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid scoring_weights JSON")
    if skill_overrides:
        try:
            overrides = json.loads(skill_overrides)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid skill_overrides JSON")
    if template_id:
        try:
            tpl_id = int(template_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid template_id")
    return weights, overrides, tpl_id


async def _resolve_jd_for_queue(
    jd_text: str | None,
    jd_file: UploadFile | None,
) -> str:
    from app.backend.routes.analyze import _resolve_jd, _check_jd_length

    jd_bytes = None
    jd_name = None
    if jd_file and jd_file.filename:
        jd_bytes = await jd_file.read()
        jd_name = jd_file.filename
    resolved = _resolve_jd(jd_text, jd_bytes, jd_name)
    _check_jd_length(resolved)
    return resolved


@router.post("/submit-file")
async def submit_analysis_file(
    resume_file: UploadFile = File(...),
    jd_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    scoring_weights: str | None = Form(None),
    skill_overrides: str | None = Form(None),
    template_id: str | None = Form(None),
    requisition_id: int | None = Form(None),
    priority: int = Form(7),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Submit a resume file for background queue analysis (multipart)."""
    from app.backend.routes.analyze_helpers import _enforce_screening_mode
    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)
    from app.backend.services.queue_analysis_service import prepare_file_for_queue

    if not resume_file.filename:
        raise HTTPException(status_code=400, detail="Resume filename is required")

    content = await resume_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Resume file is empty")

    job_description = await _resolve_jd_for_queue(jd_text, jd_file)
    weights, overrides, tpl_id = _parse_queue_options(scoring_weights, skill_overrides, template_id)

    try:
        result = await prepare_file_for_queue(
            content,
            resume_file.filename,
            job_description,
            current_user.tenant_id,
            current_user.id,
            scoring_weights=weights,
            skill_overrides=overrides,
            template_id=tpl_id,
            requisition_id=requisition_id,
            priority=priority,
        )
        return {
            **result,
            "message": "Analysis job submitted successfully",
        }
    except (CrossTenantRequisitionError, CandidateNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Could not queue this file. Check the file and try again.")
    except AnalysisQuotaExceeded as e:
        raise HTTPException(status_code=403, detail=e.message) from e
    except (TypeError, json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Failed to submit file job: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except OSError as e:
        logger.error(
            "Failed to submit file job: %s", e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=502, detail="Upstream failure") from e
    except (SQLAlchemyError, RuntimeError) as e:
        logger.exception(
            "Failed to submit file job: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "QUEUE_ERROR"},
        )
        raise HTTPException(status_code=500, detail=f"Failed to submit job: {str(e)}") from e


@router.post("/submit-batch", dependencies=[Depends(require_feature("batch_analysis"))])
async def submit_analysis_batch(
    resume_files: List[UploadFile] = File(...),
    jd_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    scoring_weights: str | None = Form(None),
    skill_overrides: str | None = Form(None),
    template_id: str | None = Form(None),
    requisition_id: int | None = Form(None),
    priority: int = Form(8),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Submit multiple resume files for background queue processing."""
    from app.backend.routes.analyze_helpers import _enforce_screening_mode
    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)
    from app.backend.services.queue_analysis_service import prepare_file_for_queue

    if not resume_files:
        raise HTTPException(status_code=400, detail="At least one resume file is required")
    if len(resume_files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch")

    job_description = await _resolve_jd_for_queue(jd_text, jd_file)
    weights, overrides, tpl_id = _parse_queue_options(scoring_weights, skill_overrides, template_id)
    batch_id = str(uuid.uuid4())
    jobs = []
    errors = []

    for resume_file in resume_files:
        if not resume_file.filename:
            errors.append({"filename": "unknown", "error": "Missing filename"})
            continue
        try:
            content = await resume_file.read()
            if not content:
                errors.append({"filename": resume_file.filename, "error": "Empty file"})
                continue
            result = await prepare_file_for_queue(
                content,
                resume_file.filename,
                job_description,
                current_user.tenant_id,
                current_user.id,
                scoring_weights=weights,
                skill_overrides=overrides,
                template_id=tpl_id,
                requisition_id=requisition_id,
                priority=priority,
            )
            jobs.append({**result, "batch_id": batch_id})
        except (CrossTenantRequisitionError, CandidateNotFoundError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except (ValueError, TypeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, SQLAlchemyError) as e:
            error_code = "VALIDATION_ERROR"
            if isinstance(e, OSError):
                error_code = "IO_ERROR"
            elif isinstance(e, SQLAlchemyError):
                error_code = "DB_ERROR"
            elif isinstance(e, RuntimeError):
                error_code = "QUEUE_ERROR"
            logger.warning(
                "Failed to queue file %s: %s", resume_file.filename, e,
                extra={"error_code": error_code},
            )
            errors.append({"filename": resume_file.filename, "error": str(e)})

    if not jobs:
        raise HTTPException(status_code=400, detail="No jobs could be queued", headers={"X-Errors": json.dumps(errors)})

    return {
        "batch_id": batch_id,
        "total": len(resume_files),
        "queued": len(jobs),
        "failed": len(errors),
        "jobs": jobs,
        "errors": errors,
        "status": "queued",
        "message": f"Queued {len(jobs)} of {len(resume_files)} resumes for background analysis",
    }


# ============================================================================
# Job Status and Results
# ============================================================================

@router.get("/status/{job_id}")
async def get_job_status(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the current status of a job.
    
    Returns:
        - status: queued, processing, completed, failed, retrying
        - progress_percent: 0-100
        - estimated_completion: ISO timestamp (if available)
        - error_message: If failed
    """
    job = db.query(AnalysisJob).filter(
        AnalysisJob.id == job_id,
        AnalysisJob.tenant_id == current_user.tenant_id
    ).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    response = {
        "job_id": str(job.id),
        "status": job.status,
        "progress_percent": job.progress_percent,
        "processing_stage": job.processing_stage,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
    }
    
    # Add timing estimates
    if job.status == 'queued':
        # Estimate based on queue position
        queue_position = db.query(func.count(AnalysisJob.id)).filter(
            AnalysisJob.tenant_id == current_user.tenant_id,
            AnalysisJob.status == 'queued',
            AnalysisJob.priority <= job.priority,
            AnalysisJob.queued_at < job.queued_at
        ).scalar()
        
        response["queue_position"] = queue_position
        response["estimated_wait_seconds"] = queue_position * 30  # Rough estimate
    
    # Add error details if failed
    if job.status in ['failed', 'retrying']:
        response["error_message"] = job.error_message
        response["error_type"] = job.error_type
        if job.status == 'retrying':
            response["next_retry_at"] = job.next_retry_at.isoformat() if job.next_retry_at else None
    
    # Add result reference if completed
    if job.status == 'completed' and job.result_id:
        response["result_id"] = str(job.result_id)
    if job.job_config and job.job_config.get("screening_result_id"):
        response["screening_result_id"] = job.job_config["screening_result_id"]
    if job.job_config and job.job_config.get("filename"):
        response["filename"] = job.job_config["filename"]
    
    return response


@router.get("/result/{job_id}")
async def get_job_result(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the analysis result for a completed job.
    
    Returns the full analysis data including:
    - fit_score, final_recommendation
    - strengths, weaknesses, matched_skills
    - parsed resume and JD
    - AI enhancement status
    """
    # Verify job belongs to user's tenant
    job = db.query(AnalysisJob).filter(
        AnalysisJob.id == job_id,
        AnalysisJob.tenant_id == current_user.tenant_id
    ).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != 'completed':
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed yet. Current status: {job.status}"
        )
    
    # Get result
    result = db.query(AnalysisResult).filter(
        AnalysisResult.job_id == job_id
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    
    return {
        "job_id": str(job_id),
        "result_id": str(result.id),
        "candidate_id": result.candidate_id,
        "screening_result_id": (job.job_config or {}).get("screening_result_id"),
        "fit_score": result.fit_score,
        "final_recommendation": result.final_recommendation,
        "risk_level": result.risk_level,
        "analysis_quality": result.analysis_quality,
        "confidence_score": result.confidence_score,
        "processing_time_ms": result.processing_time_ms,
        "created_at": result.created_at.isoformat(),
        
        # Full analysis data
        "analysis": result.analysis_data,
        "parsed_resume": result.parsed_resume,
        "parsed_jd": result.parsed_jd,
        
        # AI enhancement
        "narrative_status": result.narrative_status,
        "narrative_data": result.narrative_data,
        "ai_enhanced": result.ai_enhanced,
        
        # Metadata
        "model_used": result.model_used,
        "analysis_version": result.analysis_version,
    }


# ============================================================================
# Queue Monitoring
# ============================================================================

@router.get("/stats")
async def get_queue_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get queue statistics for the current tenant.
    
    Returns:
        - Total jobs by status
        - Average processing time
        - Success rate
        - Current queue depth
    """
    tenant_id = current_user.tenant_id
    
    # Count jobs by status
    status_counts = db.query(
        AnalysisJob.status,
        func.count(AnalysisJob.id).label('count')
    ).filter(
        AnalysisJob.tenant_id == tenant_id
    ).group_by(AnalysisJob.status).all()
    
    status_dict = {status: count for status, count in status_counts}
    
    # Get average processing time for completed jobs (last 100)
    avg_time = db.query(
        func.avg(JobMetrics.total_time_ms)
    ).join(
        AnalysisJob, JobMetrics.job_id == AnalysisJob.id
    ).filter(
        AnalysisJob.tenant_id == tenant_id,
        AnalysisJob.status == 'completed'
    ).scalar()
    
    # Calculate success rate
    total_finished = status_dict.get('completed', 0) + status_dict.get('failed', 0)
    success_rate = (status_dict.get('completed', 0) / total_finished * 100) if total_finished > 0 else 0
    
    # Current queue depth
    queue_depth = status_dict.get('queued', 0) + status_dict.get('retrying', 0)
    
    # Jobs in last 24 hours
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    recent_jobs = db.query(func.count(AnalysisJob.id)).filter(
        AnalysisJob.tenant_id == tenant_id,
        AnalysisJob.created_at >= yesterday
    ).scalar()
    
    return {
        "tenant_id": tenant_id,
        "status_counts": status_dict,
        "queue_depth": queue_depth,
        "avg_processing_time_ms": int(avg_time) if avg_time else None,
        "success_rate_percent": round(success_rate, 2),
        "jobs_last_24h": recent_jobs,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List jobs for the current tenant with pagination.
    """
    query = db.query(AnalysisJob).filter(
        AnalysisJob.tenant_id == current_user.tenant_id
    )
    
    if status:
        query = query.filter(AnalysisJob.status == status)
    
    total = query.count()
    
    jobs = query.order_by(
        AnalysisJob.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": [
            {
                "job_id": str(job.id),
                "status": job.status,
                "priority": job.priority,
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "retry_count": job.retry_count,
                "candidate_id": job.candidate_id,
                "result_id": str(job.result_id) if job.result_id else None,
                "screening_result_id": (job.job_config or {}).get("screening_result_id"),
                "filename": (job.job_config or {}).get("filename"),
            }
            for job in jobs
        ]
    }


# ============================================================================
# Admin Operations
# ============================================================================

@router.post("/retry/{job_id}")
async def retry_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Manually retry a failed job.
    """
    job = db.query(AnalysisJob).filter(
        AnalysisJob.id == job_id,
        AnalysisJob.tenant_id == current_user.tenant_id
    ).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ['failed', 'cancelled']:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry job with status: {job.status}"
        )
    
    # Enforce max_retries ceiling
    if job.retry_count >= job.max_retries:
        raise HTTPException(
            status_code=400,
            detail=f"Job has exceeded maximum retries ({job.max_retries}). Cannot retry further."
        )
    
    # Reset job for retry
    job.status = 'queued'
    job.retry_count += 1
    job.leased_until = None
    job.queued_at = datetime.now(timezone.utc)
    job.started_at = None
    job.completed_at = None
    job.failed_at = None
    job.next_retry_at = None
    job.worker_id = None
    job.error_message = None
    job.error_type = None
    
    db.commit()
    
    return {
        "job_id": str(job_id),
        "status": "queued",
        "message": "Job requeued for retry"
    }


@router.delete("/cancel/{job_id}")
async def cancel_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancel a queued or processing job.
    """
    job = db.query(AnalysisJob).filter(
        AnalysisJob.id == job_id,
        AnalysisJob.tenant_id == current_user.tenant_id
    ).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status in ['completed', 'failed']:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status: {job.status}"
        )
    
    job.status = 'cancelled'
    job.completed_at = datetime.now(timezone.utc)
    from app.backend.routes.analyze_helpers import release_job_analysis_quota
    from app.backend.services.queue_manager import archive_terminal_fingerprint
    archive_terminal_fingerprint(job, "cancelled")
    release_job_analysis_quota(db, job)
    db.commit()
    
    return {
        "job_id": str(job_id),
        "status": "cancelled",
        "message": "Job cancelled successfully"
    }


@router.get("/worker/stats")
async def get_worker_stats(
    admin: User = Depends(require_platform_admin),
):
    """
    Get statistics about the queue worker.
    
    Requires platform admin privileges.
    """
    queue_manager = get_queue_manager()
    return queue_manager.get_stats()


@router.get("/metrics/performance")
async def get_performance_metrics(
    days: int = Query(7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get performance metrics over time.
    
    Returns:
        - Average processing time by day
        - Success rate trends
        - Queue depth over time
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Daily aggregates
    daily_stats = db.query(
        func.date(AnalysisJob.created_at).label('date'),
        func.count(AnalysisJob.id).label('total_jobs'),
        func.count(AnalysisJob.id).filter(AnalysisJob.status == 'completed').label('completed'),
        func.count(AnalysisJob.id).filter(AnalysisJob.status == 'failed').label('failed'),
        func.avg(JobMetrics.total_time_ms).label('avg_time_ms'),
    ).outerjoin(
        JobMetrics, AnalysisJob.id == JobMetrics.job_id
    ).filter(
        AnalysisJob.tenant_id == current_user.tenant_id,
        AnalysisJob.created_at >= since
    ).group_by(
        func.date(AnalysisJob.created_at)
    ).order_by(
        func.date(AnalysisJob.created_at)
    ).all()
    
    return {
        "period_days": days,
        "daily_metrics": [
            {
                "date": stat.date.isoformat(),
                "total_jobs": stat.total_jobs,
                "completed": stat.completed,
                "failed": stat.failed,
                "success_rate": (stat.completed / stat.total_jobs * 100) if stat.total_jobs > 0 else 0,
                "avg_processing_time_ms": int(stat.avg_time_ms) if stat.avg_time_ms else None,
            }
            for stat in daily_stats
        ]
    }
