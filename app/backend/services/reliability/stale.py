"""Optimistic generation checks for background writeback."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    ScreeningResult,
    TranscriptAnalysis,
    VoiceScreeningSession,
)
from app.backend.services.reliability.errors import ErrorCategory
from app.backend.services.reliability.voice_attempt import (
    VOICE_COMPLETION_REJECTED_STATUSES,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteOutcome:
    stale: bool
    rowcount: int


@dataclass(frozen=True)
class VoiceCompletionOutcome:
    session: VoiceScreeningSession
    accepted: bool = False
    duplicate: bool = False
    stale: bool = False


def discard_stale(
    *,
    job_type: str,
    job_id: str,
    target_id: int,
    expected_version: int,
    current_version: int | None,
    tenant_id: int | None,
) -> dict:
    payload = {
        "job_type": job_type,
        "job_id": str(job_id),
        "target_id": target_id,
        "expected_version": expected_version,
        "current_version": current_version,
        "tenant_id": tenant_id,
        "stale": True,
        "category": ErrorCategory.STALE_VERSION.value,
    }
    try:
        log.info("stale_job_discarded %s", json.dumps(payload))
    except Exception:
        log.info(
            "stale_job_discarded job_type=%s target_id=%s expected=%s current=%s",
            job_type,
            target_id,
            expected_version,
            current_version,
        )
    try:
        from app.backend.services.metrics import JOB_STALE_DISCARD_TOTAL

        JOB_STALE_DISCARD_TOTAL.labels(job_type=job_type).inc()
    except Exception:
        pass
    return payload


def apply_narrative_if_generation(
    db: Session,
    *,
    screening_result_id: int,
    tenant_id: int,
    expected_generation: int,
    narrative: dict,
    status: str,
    error: str | None = None,
    merge_analysis: dict | None = None,
) -> WriteOutcome:
    values = {
        "narrative_json": json.dumps(narrative, default=str),
        "narrative_status": status,
        "narrative_error": error,
    }
    if merge_analysis is not None:
        values["analysis_result"] = json.dumps(merge_analysis, default=str)
    result = db.execute(
        update(ScreeningResult)
        .where(
            ScreeningResult.id == screening_result_id,
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.analysis_generation == expected_generation,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        current = (
            db.query(ScreeningResult.analysis_generation)
            .filter(
                ScreeningResult.id == screening_result_id,
                ScreeningResult.tenant_id == tenant_id,
            )
            .scalar()
        )
        discard_stale(
            job_type="llm_narrative",
            job_id=f"narrative-{screening_result_id}-{expected_generation}",
            target_id=screening_result_id,
            expected_version=expected_generation,
            current_version=current,
            tenant_id=tenant_id,
        )
        return WriteOutcome(stale=True, rowcount=0)
    return WriteOutcome(stale=False, rowcount=1)


def apply_transcript_if_generation(
    db: Session,
    *,
    transcript_analysis_id: int,
    tenant_id: int,
    expected_generation: int,
    analysis_result: dict,
    transcript_text: str | None = None,
) -> WriteOutcome:
    values = {"analysis_result": json.dumps(analysis_result, default=str)}
    if transcript_text is not None:
        values["transcript_text"] = transcript_text
    result = db.execute(
        update(TranscriptAnalysis)
        .where(
            TranscriptAnalysis.id == transcript_analysis_id,
            TranscriptAnalysis.tenant_id == tenant_id,
            TranscriptAnalysis.analysis_generation == expected_generation,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        return WriteOutcome(stale=False, rowcount=result.rowcount)
    current = (
        db.query(TranscriptAnalysis.analysis_generation)
        .filter(
            TranscriptAnalysis.id == transcript_analysis_id,
            TranscriptAnalysis.tenant_id == tenant_id,
        )
        .scalar()
    )
    discard_stale(
        job_type="transcript_analysis",
        job_id=f"transcript-{transcript_analysis_id}-{expected_generation}",
        target_id=transcript_analysis_id,
        expected_version=expected_generation,
        current_version=current,
        tenant_id=tenant_id,
    )
    return WriteOutcome(stale=True, rowcount=0)


def claim_voice_completion(
    db: Session,
    *,
    session_id: int,
    expected_generation: int,
    event_id: str,
) -> VoiceCompletionOutcome:
    session = (
        db.query(VoiceScreeningSession)
        .filter(VoiceScreeningSession.id == session_id)
        .with_for_update()
        .one()
    )
    if session.result_generation != expected_generation:
        discard_stale(
            job_type="voice_completion",
            job_id=event_id,
            target_id=session_id,
            expected_version=expected_generation,
            current_version=session.result_generation,
            tenant_id=session.tenant_id,
        )
        return VoiceCompletionOutcome(session=session, stale=True)
    if session.status in VOICE_COMPLETION_REJECTED_STATUSES:
        discard_stale(
            job_type="voice_completion",
            job_id=event_id,
            target_id=session_id,
            expected_version=expected_generation,
            current_version=session.result_generation,
            tenant_id=session.tenant_id,
        )
        return VoiceCompletionOutcome(session=session, stale=True)
    if session.completion_event_id == event_id:
        return VoiceCompletionOutcome(session=session, duplicate=True)
    if session.completion_event_id is not None:
        discard_stale(
            job_type="voice_completion",
            job_id=event_id,
            target_id=session_id,
            expected_version=expected_generation,
            current_version=session.result_generation,
            tenant_id=session.tenant_id,
        )
        return VoiceCompletionOutcome(session=session, stale=True)
    session.completion_event_id = event_id
    return VoiceCompletionOutcome(session=session, accepted=True)
