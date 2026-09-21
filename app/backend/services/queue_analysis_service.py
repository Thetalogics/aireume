"""
Queue analysis integration — file upload enqueue + ScreeningResult persistence.

Bridges the job queue with the main hybrid analysis pipeline so background
jobs produce the same ScreeningResult records as SSE batch/stream analysis.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.backend.db.database import SessionLocal
from app.backend.services.queue_manager import get_queue_manager

log = logging.getLogger("aria.queue_analysis")

MAX_INLINE_BYTES = 2 * 1024 * 1024  # 2 MB — store in parsed_resume_cache


def _json_default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def prepare_file_for_queue(
    content: bytes,
    filename: str,
    jd_text: str,
    tenant_id: int,
    user_id: int,
    *,
    scoring_weights: dict | None = None,
    skill_overrides: dict | None = None,
    template_id: int | None = None,
    requisition_id: int | None = None,
    priority: int = 7,
) -> dict[str, Any]:
    """Parse resume file and enqueue a background analysis job."""
    from app.backend.routes.analyze import _parse_resume_with_doc_conversion

    parsed_data, _pdf_bytes = await _parse_resume_with_doc_conversion(content, filename)
    resume_text = parsed_data.get("raw_text") or ""
    if not resume_text.strip():
        raise ValueError(f"Could not extract text from {filename}")

    file_hash = hashlib.md5(content).hexdigest()
    cache: dict[str, Any] = {
        "parsed_data": parsed_data,
        "file_hash": file_hash,
        "filename": filename,
    }
    if len(content) <= MAX_INLINE_BYTES:
        cache["file_content_b64"] = base64.b64encode(content).decode("ascii")

    job_config = {
        "scoring_weights": scoring_weights,
        "skill_overrides": skill_overrides,
        "template_id": template_id,
        "filename": filename,
    }

    queue_manager = get_queue_manager()
    job_id = await queue_manager.enqueue_job(
        tenant_id=tenant_id,
        resume_text=resume_text,
        resume_filename=filename,
        jd_text=jd_text,
        user_id=user_id,
        priority=priority,
        job_config=job_config,
        parsed_resume_cache=cache,
        requisition_id=requisition_id,
        scoring_weights=scoring_weights,
        skill_overrides=skill_overrides,
        role_template_id=template_id,
    )

    return {
        "job_id": str(job_id),
        "filename": filename,
        "status": "queued",
    }


async def complete_queue_job(job_id, db: Session, *, expected_worker_id: str) -> bool:
    """
    Process a queued job end-to-end: score, persist ScreeningResult, spawn LLM.
    Returns True on success.
    """
    from datetime import datetime, timezone

    from app.backend.models.db_models import AnalysisArtifact, AnalysisJob, AnalysisResult
    from app.backend.routes.analyze import (
        _process_single_resume,
        _spawn_background_narrative,
    )
    from app.backend.services.screening_command import command_from_dict, execute_screening
    import time

    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    artifact = db.query(AnalysisArtifact).filter(AnalysisArtifact.id == job.artifact_id).first()
    if not artifact:
        raise ValueError(f"Artifact not found for job {job_id}")

    start_time = time.time()
    job_config = job.job_config or {}
    cmd = command_from_dict(job_config["command"]) if job_config.get("command") else None
    scoring_weights = (cmd.scoring_weights if cmd else None) or job_config.get("scoring_weights")
    skill_overrides = (cmd.skill_overrides if cmd else None) or job_config.get("skill_overrides")
    filename = job_config.get("filename") or artifact.resume_filename

    cache = artifact.parsed_resume_cache or {}
    content: bytes
    if cache.get("file_content_b64"):
        content = base64.b64decode(cache["file_content_b64"])
    else:
        content = (artifact.resume_text or "").encode("utf-8")

    job.processing_stage = "scoring"
    job.progress_percent = 30
    db.commit()

    raw = await _process_single_resume(
        content=content,
        filename=filename,
        job_description=artifact.jd_text,
        scoring_weights=scoring_weights,
        db=db,
        skill_overrides=skill_overrides,
    )

    if raw.get("pipeline_errors"):
        raise ValueError("; ".join(raw["pipeline_errors"]))

    parsed_data = raw.pop("_parsed_data", cache.get("parsed_data") or {})
    gap_analysis = raw.pop("_gap_analysis", {})
    raw.pop("_pdf_bytes", None)

    file_hash = cache.get("file_hash") or hashlib.md5(content).hexdigest()

    if cmd is None:
        from app.backend.services.screening_command import build_screening_command
        cmd = build_screening_command(
            db,
            tenant_id=job.tenant_id,
            user_id=job.user_id or 0,
            resume_hash=job.resume_hash,
            jd_hash=job.jd_hash,
            candidate_id=job.candidate_id,
            artifact_id=str(job.artifact_id) if job.artifact_id else None,
            scoring_weights=scoring_weights,
            skill_overrides=skill_overrides,
            role_template_id=job_config.get("template_id"),
        )

    # Revalidate ownership under a row lock at the authoritative write boundary.
    db.commit()
    job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.id == job_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    lease = job.leased_until
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    existing_result = db.query(AnalysisResult).filter(AnalysisResult.job_id == job.id).first()
    if job.status == "completed" and existing_result is not None:
        db.rollback()
        return True
    if (
        job.status != "processing"
        or job.worker_id != expected_worker_id
        or (lease is not None and lease < datetime.now(timezone.utc))
    ):
        db.rollback()
        log.warning(
            "lease_lost before domain write job_id=%s worker_id=%s current_owner=%s",
            job_id,
            expected_worker_id,
            job.worker_id,
        )
        try:
            from app.backend.services.metrics import LEASE_LOST_BEFORE_COMMIT_TOTAL

            LEASE_LOST_BEFORE_COMMIT_TOTAL.inc()
        except Exception:
            pass
        return False

    if existing_result:
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.result_id = existing_result.id
        job.progress_percent = 100
        job.processing_stage = "complete"
        from app.backend.routes.analyze_helpers import consume_job_analysis_quota

        consume_job_analysis_quota(db, job)
        db.commit()
        return True

    db_result, _ = execute_screening(
        db,
        cmd,
        resume_text=parsed_data.get("raw_text", artifact.resume_text or ""),
        jd_text=artifact.jd_text,
        parsed_data=parsed_data,
        pipeline_result=raw,
        file_hash=file_hash,
        filename=filename,
        file_content=content,
        gap_analysis=gap_analysis,
        commit=False,
    )
    screening_result_id = db_result.id

    job_config["screening_result_id"] = screening_result_id
    job_config["filename"] = filename
    job.job_config = job_config
    job.candidate_id = db_result.candidate_id

    analysis_result = AnalysisResult(
        job_id=job.id,
        tenant_id=job.tenant_id,
        candidate_id=db_result.candidate_id,
        fit_score=raw.get("fit_score") or 0,
        final_recommendation=raw.get("final_recommendation") or "Pending",
        risk_level=raw.get("risk_level"),
        analysis_data=raw,
        parsed_resume=parsed_data,
        parsed_jd={},
        narrative_status="pending",
        processing_time_ms=int((time.time() - start_time) * 1000),
        artifact_id=artifact.id,
    )
    db.add(analysis_result)
    db.flush()

    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    job.result_id = analysis_result.id
    job.progress_percent = 100
    job.processing_stage = "complete"
    from app.backend.routes.analyze_helpers import consume_job_analysis_quota

    consume_job_analysis_quota(db, job)
    db.commit()

    _spawn_background_narrative(
        raw,
        screening_result_id,
        job.tenant_id,
        db_result.analysis_generation,
        screening_decision_id=db_result.current_decision_id,
    )
    log.info(
        "Queue job %s completed → screening_result_id=%s generation=%s retry_count=%s worker_id=%s",
        job_id,
        screening_result_id,
        db_result.analysis_generation,
        job.retry_count,
        expected_worker_id,
        extra={
            "tenant_id": job.tenant_id,
            "job_id": str(job.id),
            "candidate_id": db_result.candidate_id,
            "screening_result_id": screening_result_id,
            "analysis_generation": db_result.analysis_generation,
            "retry_count": job.retry_count,
            "worker_id": expected_worker_id,
        },
    )
    return True
