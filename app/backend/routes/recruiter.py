"""
AI Recruiter routes.

Endpoints:
  POST /api/recruiter/sessions                       — Initiate an AI recruiter interview
  GET  /api/recruiter/sessions                       — List AI recruiter sessions for tenant
  GET  /api/recruiter/sessions/{session_id}          — Get session detail
  GET  /api/recruiter/sessions/{session_id}/transcript — Get session transcript questions
  GET  /api/recruiter/sessions/{session_id}/scorecard — Get session scorecard
  POST /api/recruiter/sessions/{session_id}/cancel   — Cancel a session
  POST /api/recruiter/sessions/{session_id}/retry    — Retry a failed session
  GET  /api/recruiter/config                         — Get auto-trigger config
  PUT  /api/recruiter/config                         — Update auto-trigger config
  GET  /api/recruiter/candidates/{candidate_id}/sessions — List sessions for a candidate
  GET  /api/recruiter/analytics                      — Aggregated analytics
  POST /api/recruiter/sessions/export                — Export sessions as CSV
"""
import csv
import io
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import SQLAlchemyError

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_internal_service
from app.backend.models.db_models import (
    Candidate,
    RecruiterAutoTriggerConfig,
    RecruiterInterviewQuestion,
    RecruiterInterviewSession,
    RecruiterScorecard,
    RoleTemplate,
    ScreeningResult,
    User,
    VoiceScreeningSession,
    VoiceTranscriptEntry,
)
from app.backend.models.schemas import (
    RecruiterAnalyticsOut,
    RecruiterAutoTriggerConfigOut,
    RecruiterAutoTriggerConfigUpdate,
    RecruiterQuestionOut,
    RecruiterScorecardOut,
    RecruiterSessionCreate,
    RecruiterSessionDetail,
    RecruiterSessionOut,
)
from app.backend.services.recruiter.orchestrator import RecruiterOrchestrator

logger = logging.getLogger("aria.recruiter")

# NOTE: This module is maintained for backward compatibility.
# The unified interview API is at /api/interviews/* (see routes/interviews.py).
# New features should be added to interviews.py, not here.

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
internal_router = APIRouter(prefix="/api/recruiter", tags=["recruiter-internal"])

# Feature flag for the AI Recruiter module
RECRUITER_ENABLED = os.getenv("RECRUITER_INTERVIEW_ENABLED", "true").lower() == "true"


def require_recruiter_enabled() -> None:
    """Raise 404 if the AI Recruiter feature is disabled."""
    if not RECRUITER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI Recruiter feature is not enabled",
        )


# Apply the feature flag to every route in this module
router.dependencies.append(Depends(require_recruiter_enabled))

# Roles allowed to initiate / cancel / retry AI recruiter sessions
_RECRUITER_ADMIN_ROLES = {"admin", "recruiter"}

# Cancellable lifecycle statuses
_CANCELLABLE_STATUSES = {"pending_strategy", "strategy_ready", "scheduled"}


def _load_json(raw: Optional[str], default: Any = None) -> Any:
    """Safely load a JSON string; return default on empty/invalid input."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _normalize_scorecard_collection(
    raw: Optional[str],
    *,
    list_key: str,
    default: Optional[dict] = None,
) -> dict:
    """Parse a scorecard JSON field; legacy rows may store a bare list."""
    parsed = _load_json(raw, default=default if default is not None else {})
    if isinstance(parsed, list):
        return {list_key: parsed}
    if isinstance(parsed, dict):
        return parsed
    return default if default is not None else {}


def _require_recruiter_or_admin(current_user: User) -> None:
    """Raise 403 if the user is not a recruiter or admin."""
    if current_user.role not in _RECRUITER_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recruiter or admin access required",
        )


def _require_admin(current_user: User) -> None:
    """Raise 403 if the user is not an admin."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


# ─── Session Management ───────────────────────────────────────────────────────

@router.post(
    "/sessions",
    response_model=RecruiterSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_recruiter_session(
    body: RecruiterSessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Initiate a new AI recruiter interview session."""
    _require_recruiter_or_admin(current_user)

    # Verify candidate belongs to the current tenant
    candidate = db.execute(
        select(Candidate).where(
            Candidate.id == body.candidate_id,
            Candidate.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found or not in your tenant",
        )

    # Build config from both flat fields and legacy interview_config_json
    config = (body.interview_config_json or {}).copy()
    if body.duration_minutes is not None:
        config.setdefault("duration_minutes", body.duration_minutes)
    config.setdefault("focus_areas", body.focus_areas)
    config.setdefault("phone_number", body.phone_number)
    config.setdefault("scheduled_at", body.scheduled_at)
    config.setdefault("timezone", body.timezone)

    orchestrator = RecruiterOrchestrator(db)
    try:
        session_id = await orchestrator.initiate_interview(
            tenant_id=current_user.tenant_id,
            candidate_id=body.candidate_id,
            jd_id=body.jd_id,
            screening_result_id=body.screening_result_id,
            trigger_type=body.trigger_type or "manual",
            config=config,
            created_by=current_user.id,
        )
    except ValueError as exc:
        logger.warning("Failed to initiate recruiter interview: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session creation failed",
        )

    return RecruiterSessionOut.model_validate(session)


@router.get("/sessions")
def list_recruiter_sessions(
    status: Optional[str] = Query(None),
    candidate_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List AI recruiter sessions for the current tenant."""
    base_query = select(RecruiterInterviewSession).where(
        RecruiterInterviewSession.tenant_id == current_user.tenant_id
    ).options(
        selectinload(RecruiterInterviewSession.candidate),
        selectinload(RecruiterInterviewSession.jd),
        selectinload(RecruiterInterviewSession.voice_session),
    )

    if status:
        base_query = base_query.where(RecruiterInterviewSession.status == status)
    if candidate_id is not None:
        base_query = base_query.where(
            RecruiterInterviewSession.candidate_id == candidate_id
        )

    count_query = select(func.count()).select_from(base_query.subquery())
    total = db.execute(count_query).scalar() or 0

    sessions = db.execute(
        base_query.order_by(RecruiterInterviewSession.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    results = []
    for s in sessions:
        out = RecruiterSessionOut.model_validate(s)
        out.candidate_name = s.candidate.name if s.candidate else None
        out.jd_title = s.jd.name if s.jd else None
        if s.voice_session:
            out.scheduled_at = s.voice_session.scheduled_at
            out.phone_number = s.voice_session.phone_number
        results.append(out)

    return {
        "sessions": results,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/sessions/{session_id}", response_model=RecruiterSessionDetail)
def get_recruiter_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full detail for an AI recruiter session, including strategy and config."""
    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
        .options(
            selectinload(RecruiterInterviewSession.candidate),
            selectinload(RecruiterInterviewSession.jd),
            selectinload(RecruiterInterviewSession.voice_session),
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    base_out = RecruiterSessionOut.model_validate(session)
    if session.voice_session:
        base_out.scheduled_at = session.voice_session.scheduled_at
        base_out.phone_number = session.voice_session.phone_number
    data = {
        **base_out.model_dump(),
        "interview_strategy_json": _load_json(
            session.interview_strategy_json, default={}
        ),
        "interview_config_json": _load_json(
            session.interview_config_json, default={}
        ),
        "candidate_name": session.candidate.name if session.candidate else None,
        "jd_title": session.jd.name if session.jd else None,
    }
    return RecruiterSessionDetail.model_validate(data)


@router.get("/sessions/{session_id}/transcript")
def get_recruiter_transcript(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the ordered list of questions for a session."""
    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    questions = db.execute(
        select(RecruiterInterviewQuestion)
        .where(RecruiterInterviewQuestion.session_id == session_id)
        .order_by(RecruiterInterviewQuestion.sequence_number.asc())
    ).scalars().all()

    return {
        "questions": [RecruiterQuestionOut.model_validate(q) for q in questions]
    }


@router.get("/sessions/{session_id}/scorecard", response_model=RecruiterScorecardOut)
def get_recruiter_scorecard(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the scorecard for a completed AI recruiter session."""
    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
        .options(selectinload(RecruiterInterviewSession.scorecard))
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    scorecard = session.scorecard
    if scorecard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scorecard not yet generated",
        )

    # Build a plain dict from the ORM object, then parse JSON-string fields
    # before Pydantic validation. The ORM stores evidence fields as JSON text.
    json_fields = {
        "technical_evidence",
        "behavioral_evidence",
        "communication_evidence",
        "cultural_fit_evidence",
        "motivation_evidence",
        "risk_signals_validated",
        "gaps_explained",
    }
    data = {
        k: getattr(scorecard, k)
        for k in RecruiterScorecardOut.model_fields.keys()
        if k not in json_fields
    }
    data["technical_evidence"] = _load_json(scorecard.technical_evidence, default={}) or {}
    data["behavioral_evidence"] = _load_json(scorecard.behavioral_evidence, default={}) or {}
    data["communication_evidence"] = _load_json(scorecard.communication_evidence, default={}) or {}
    data["cultural_fit_evidence"] = _load_json(scorecard.cultural_fit_evidence, default={}) or {}
    data["motivation_evidence"] = _load_json(scorecard.motivation_evidence, default={}) or {}
    data["risk_signals_validated"] = _normalize_scorecard_collection(
        scorecard.risk_signals_validated,
        list_key="signals",
    )
    data["gaps_explained"] = _normalize_scorecard_collection(
        scorecard.gaps_explained,
        list_key="items",
    )

    return RecruiterScorecardOut.model_validate(data)


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=RecruiterSessionOut,
)
async def cancel_recruiter_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a recruiter session that has not yet started."""
    _require_recruiter_or_admin(current_user)

    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel session in '{session.status}' status",
        )

    orchestrator = RecruiterOrchestrator(db)
    await orchestrator.cancel_interview(session_id)

    db.refresh(session)
    return RecruiterSessionOut.model_validate(session)


@router.post(
    "/sessions/{session_id}/retry",
    response_model=RecruiterSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def retry_recruiter_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retry a failed AI recruiter interview by creating a new session."""
    _require_recruiter_or_admin(current_user)

    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry session in '{session.status}' status",
        )

    orchestrator = RecruiterOrchestrator(db)
    try:
        new_session_id = await orchestrator.retry_interview(session_id)
    except ValueError as exc:
        logger.warning("Failed to retry recruiter interview: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    new_session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == new_session_id,
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if new_session is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retry session creation failed",
        )

    return RecruiterSessionOut.model_validate(new_session)


# ─── Auto-Trigger Configuration ───────────────────────────────────────────────

@router.get("/config", response_model=RecruiterAutoTriggerConfigOut)
def get_recruiter_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the tenant's AI recruiter auto-trigger configuration."""
    _require_admin(current_user)

    config = db.execute(
        select(RecruiterAutoTriggerConfig).where(
            RecruiterAutoTriggerConfig.tenant_id == current_user.tenant_id
        )
    ).scalar_one_or_none()

    if config is None:
        config = RecruiterAutoTriggerConfig(
            id=str(uuid.uuid4()),
            tenant_id=current_user.tenant_id,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

    data = {
        **RecruiterAutoTriggerConfigOut.model_validate(config).model_dump(),
        "focus_areas": _load_json(config.focus_areas, default=[]),
        "auto_status_mapping_json": _load_json(config.auto_status_mapping_json, default={}),
        "evaluator_model_json": _load_json(config.evaluator_model_json, default={}),
    }
    return RecruiterAutoTriggerConfigOut.model_validate(data)


@router.put("/config", response_model=RecruiterAutoTriggerConfigOut)
def update_recruiter_config(
    body: RecruiterAutoTriggerConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the tenant's AI recruiter auto-trigger configuration."""
    _require_admin(current_user)

    config = db.execute(
        select(RecruiterAutoTriggerConfig).where(
            RecruiterAutoTriggerConfig.tenant_id == current_user.tenant_id
        )
    ).scalar_one_or_none()

    if config is None:
        config = RecruiterAutoTriggerConfig(
            id=str(uuid.uuid4()),
            tenant_id=current_user.tenant_id,
        )
        db.add(config)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "focus_areas" and isinstance(value, list):
            value = json.dumps(value)
        if field == "auto_status_mapping_json" and isinstance(value, dict):
            value = json.dumps(value)
        if field == "evaluator_model_json" and isinstance(value, dict):
            value = json.dumps(value)
        setattr(config, field, value)

    db.commit()
    db.refresh(config)

    data = {
        **RecruiterAutoTriggerConfigOut.model_validate(config).model_dump(),
        "focus_areas": _load_json(config.focus_areas, default=[]),
        "auto_status_mapping_json": _load_json(config.auto_status_mapping_json, default={}),
        "evaluator_model_json": _load_json(config.evaluator_model_json, default={}),
    }
    return RecruiterAutoTriggerConfigOut.model_validate(data)


# ─── Candidate Sessions ───────────────────────────────────────────────────────

@router.get(
    "/candidates/{candidate_id}/sessions",
    response_model=List[RecruiterSessionOut],
)
def list_candidate_recruiter_sessions(
    candidate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all AI recruiter sessions for a specific candidate."""
    candidate = db.execute(
        select(Candidate).where(
            Candidate.id == candidate_id,
            Candidate.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()

    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found or not in your tenant",
        )

    sessions = db.execute(
        select(RecruiterInterviewSession)
        .where(
            RecruiterInterviewSession.tenant_id == current_user.tenant_id,
            RecruiterInterviewSession.candidate_id == candidate_id,
        )
        .options(selectinload(RecruiterInterviewSession.voice_session))
        .order_by(RecruiterInterviewSession.created_at.desc())
    ).scalars().all()

    results = []
    for s in sessions:
        out = RecruiterSessionOut.model_validate(s)
        if s.voice_session:
            out.scheduled_at = s.voice_session.scheduled_at
            out.phone_number = s.voice_session.phone_number
        results.append(out)
    return results


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics", response_model=RecruiterAnalyticsOut)
def get_recruiter_analytics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return aggregated analytics for AI recruiter sessions."""
    tenant_id = current_user.tenant_id

    session_stats = db.execute(
        select(
            func.count(RecruiterInterviewSession.id).label("total"),
            func.count(RecruiterInterviewSession.id)
            .filter(RecruiterInterviewSession.status == "completed")
            .label("completed"),
            func.count(RecruiterInterviewSession.id)
            .filter(RecruiterInterviewSession.status == "failed")
            .label("failed"),
            func.count(RecruiterInterviewSession.id)
            .filter(RecruiterInterviewSession.status == "cancelled")
            .label("cancelled"),
            func.avg(RecruiterInterviewSession.duration_seconds)
            .filter(RecruiterInterviewSession.duration_seconds.isnot(None))
            .label("avg_duration"),
        ).where(RecruiterInterviewSession.tenant_id == tenant_id)
    ).one()

    total = session_stats.total or 0
    completed = session_stats.completed or 0
    failed = session_stats.failed or 0
    cancelled = session_stats.cancelled or 0
    avg_duration = round(session_stats.avg_duration, 1) if session_stats.avg_duration else None

    # Status distribution
    status_rows = db.execute(
        select(
            RecruiterInterviewSession.status,
            func.count(RecruiterInterviewSession.id).label("cnt"),
        )
        .where(RecruiterInterviewSession.tenant_id == tenant_id)
        .group_by(RecruiterInterviewSession.status)
    ).all()
    sessions_by_status = {r.status: r.cnt for r in status_rows}

    # Recommendation distribution and average overall score from scorecards
    recommendation_rows = db.execute(
        select(
            RecruiterScorecard.recommendation,
            func.count(RecruiterScorecard.id).label("cnt"),
        )
        .where(
            RecruiterScorecard.tenant_id == tenant_id,
            RecruiterScorecard.recommendation.isnot(None),
        )
        .group_by(RecruiterScorecard.recommendation)
    ).all()
    recommendation_distribution = {r.recommendation: r.cnt for r in recommendation_rows}

    avg_score_row = db.execute(
        select(
            func.avg(RecruiterScorecard.overall_score)
            .filter(RecruiterScorecard.overall_score.isnot(None))
            .label("avg_score")
        ).where(RecruiterScorecard.tenant_id == tenant_id)
    ).scalar_one_or_none()
    average_overall_score = round(avg_score_row, 1) if avg_score_row else None

    # Score distribution buckets
    scorecard_rows = db.execute(
        select(RecruiterScorecard.overall_score)
        .where(
            RecruiterScorecard.tenant_id == tenant_id,
            RecruiterScorecard.overall_score.isnot(None),
        )
    ).scalars().all()

    score_distribution = {
        "0-49": 0,
        "50-69": 0,
        "70-84": 0,
        "85-100": 0,
    }
    for score in scorecard_rows:
        if score < 50:
            score_distribution["0-49"] += 1
        elif score < 70:
            score_distribution["50-69"] += 1
        elif score < 85:
            score_distribution["70-84"] += 1
        else:
            score_distribution["85-100"] += 1

    # Sessions created this calendar month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sessions_this_month = db.execute(
        select(func.count(RecruiterInterviewSession.id))
        .where(
            RecruiterInterviewSession.tenant_id == tenant_id,
            RecruiterInterviewSession.created_at >= month_start,
        )
    ).scalar() or 0

    return RecruiterAnalyticsOut(
        tenant_id=tenant_id,
        total_sessions=total,
        completed_sessions=completed,
        failed_sessions=failed,
        cancelled_sessions=cancelled,
        average_duration_seconds=avg_duration,
        average_overall_score=average_overall_score,
        recommendation_distribution=recommendation_distribution,
        sessions_by_status=sessions_by_status,
        score_distribution=score_distribution,
        sessions_this_month=sessions_this_month,
    )


# ─── Export ───────────────────────────────────────────────────────────────────

@router.post("/sessions/export")
def export_recruiter_sessions(
    format: str = Query("csv"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export AI recruiter sessions as CSV."""
    if format.lower() != "csv":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV export is supported",
        )

    sessions = db.execute(
        select(RecruiterInterviewSession)
        .where(RecruiterInterviewSession.tenant_id == current_user.tenant_id)
        .options(
            selectinload(RecruiterInterviewSession.candidate),
            selectinload(RecruiterInterviewSession.jd),
        )
        .order_by(RecruiterInterviewSession.created_at.desc())
    ).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Session ID",
        "Candidate",
        "Email",
        "Job Title",
        "Status",
        "Trigger Type",
        "Started At",
        "Ended At",
        "Duration (s)",
        "Created At",
    ])

    for s in sessions:
        writer.writerow([
            s.id,
            s.candidate.name if s.candidate else "",
            s.candidate.email if s.candidate else "",
            s.jd.name if s.jd else "",
            s.status,
            s.trigger_type,
            str(s.started_at) if s.started_at else "",
            str(s.ended_at) if s.ended_at else "",
            s.duration_seconds or "",
            str(s.created_at) if s.created_at else "",
        ])

    output.seek(0)
    filename = f"recruiter_sessions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Internal Callback (Voice Agent → Backend) ──────────────────────────────

async def _generate_scorecard_background(
    session_id: str,
    *,
    expected_voice_generation: int,
) -> None:
    """Background task to generate scorecard after interview completes.

    Runs after the HTTP response is sent so the voice agent gets an immediate 200.
    Creates its own DB session to avoid leaking the request-scoped session.
    """
    from app.backend.db.database import SessionLocal

    db = SessionLocal()
    try:
        orchestrator = RecruiterOrchestrator(db)
        await orchestrator.on_interview_completed(
            session_id,
            expected_voice_generation=expected_voice_generation,
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        logger.error(
            "Scorecard generation failed for session %s: %s",
            session_id, e, exc_info=True,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        db.rollback()
        session = db.execute(
            select(RecruiterInterviewSession).where(
                RecruiterInterviewSession.id == session_id
            )
        ).scalar_one_or_none()
        if session and session.status != "completed":
            session.status = "completed"
            db.commit()
    except (OSError, RuntimeError, SQLAlchemyError) as e:
        logger.error(
            "Scorecard generation failed for session %s: %s",
            session_id, e, exc_info=True,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "IO_ERROR"},
        )
        # Roll back any partial changes from the orchestrator
        db.rollback()
        # Keep session as completed — scorecard can be retried via the
        # retry endpoint or a future re-trigger.
        session = db.execute(
            select(RecruiterInterviewSession).where(
                RecruiterInterviewSession.id == session_id
            )
        ).scalar_one_or_none()
        if session and session.status != "completed":
            session.status = "completed"
            db.commit()
    finally:
        db.close()


@internal_router.post("/internal/complete")
async def on_recruiter_interview_complete(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_service),
):
    """
    Internal callback from voice agent when recruiter interview finishes.

    NOT authenticated via user JWT — uses internal service-to-service trust
    (both containers on same Docker network).

    Payload from voice agent::

        {
            "session_id": str,      # recruiter session UUID or voice session ID
            "result": {
                "duration_seconds": int,
                "questions_asked": int,
                "consent_recorded": bool,
                "transcript": [...],           # speaker turns
                "questions_responses": [...],  # structured Q&A
                "completion_reason": str
            }
        }
    """
    body = await request.json()
    session_id = body.get("session_id")
    result = body.get("result", {})
    expected_generation = body.get("expected_generation")
    event_id = body.get("event_id")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    if not isinstance(expected_generation, int) or not event_id:
        raise HTTPException(
            status_code=400,
            detail="expected_generation and event_id are required",
        )

    # ── Resolve session ────────────────────────────────────────────────────
    # The voice agent may send either the RecruiterInterviewSession UUID or
    # the VoiceScreeningSession integer ID (depending on dispatch path).
    # Try UUID first, then fall back to voice_session_id lookup.
    session = db.execute(
        select(RecruiterInterviewSession).where(
            RecruiterInterviewSession.id == session_id
        )
    ).scalar_one_or_none()

    if session is None:
        try:
            voice_sid = int(session_id)
            session = db.execute(
                select(RecruiterInterviewSession).where(
                    RecruiterInterviewSession.voice_session_id == voice_sid
                )
            ).scalar_one_or_none()
        except (ValueError, TypeError):
            pass

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.voice_session_id:
        raise HTTPException(status_code=409, detail="Voice session identity required")

    from app.backend.services.reliability.stale import claim_voice_completion

    claim = claim_voice_completion(
        db,
        session_id=session.voice_session_id,
        expected_generation=expected_generation,
        event_id=str(event_id),
    )
    if claim.stale:
        db.rollback()
        return {"status": "stale", "session_id": session.id}
    if claim.duplicate:
        db.rollback()
        return {"status": "ok", "session_id": session.id, "message": "Already completed"}

    # Idempotent: already completed with a scorecard → return early
    if session.status == "completed" and session.scorecard is not None:
        return {"status": "ok", "session_id": session.id, "message": "Already completed"}

    # ── Update session with call data ───────────────────────────────────────
    session.status = "completed"
    session.ended_at = datetime.now(timezone.utc)
    duration = result.get("duration_seconds")
    if duration is not None:
        session.duration_seconds = duration
        if not session.started_at:
            session.started_at = session.ended_at - timedelta(seconds=duration)

    db.commit()

    # ── Store / update questions with candidate responses ───────────────────
    questions_responses = result.get("questions_responses", [])
    if questions_responses:
        existing_questions = {
            q.question_text: q
            for q in db.execute(
                select(RecruiterInterviewQuestion).where(
                    RecruiterInterviewQuestion.session_id == session.id
                )
            ).scalars().all()
        }

        for idx, qr in enumerate(questions_responses):
            if not isinstance(qr, dict):
                continue

            is_follow_up = bool(qr.get("is_follow_up", False))
            candidate_response = qr.get("response") or qr.get("answer", "")
            question_text = (
                (qr.get("follow_up_text") or "").strip()
                if is_follow_up
                else (qr.get("question") or "").strip()
            )
            if not question_text:
                continue

            existing = existing_questions.get(question_text)

            if existing:
                existing.candidate_response = candidate_response
                existing.response_duration_seconds = qr.get("response_duration")
                if is_follow_up:
                    existing.is_follow_up = True
            else:
                parent_question_id = None
                if is_follow_up:
                    parent_text = (qr.get("question") or "").strip()
                    parent = existing_questions.get(parent_text)
                    if parent:
                        parent_question_id = parent.id

                new_q = RecruiterInterviewQuestion(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    sequence_number=idx + 1,
                    category=qr.get("stage") or qr.get("category", "technical"),
                    question_text=question_text,
                    question_context=qr.get("context", ""),
                    candidate_response=candidate_response,
                    response_duration_seconds=qr.get("response_duration"),
                    is_follow_up=is_follow_up,
                    parent_question_id=parent_question_id,
                )
                db.add(new_q)
                existing_questions[question_text] = new_q

    # ── Store transcript as VoiceTranscriptEntry records ────────────────────
    # The orchestrator's CommunicationEvaluator reads from this table.
    transcript = result.get("transcript", [])
    if transcript and session.voice_session_id:
        existing_count = db.execute(
            select(func.count()).select_from(
                select(VoiceTranscriptEntry).where(
                    VoiceTranscriptEntry.session_id == session.voice_session_id
                ).subquery()
            )
        ).scalar() or 0

        if existing_count == 0:
            started_at = session.started_at or session.ended_at
            for entry in transcript:
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("timestamp")
                if ts is None:
                    abs_ts = started_at
                elif isinstance(ts, (int, float)):
                    # Heuristic: large numbers are Unix timestamps (seconds)
                    if ts > 1e10:
                        abs_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    else:
                        abs_ts = started_at + timedelta(seconds=ts)
                else:
                    abs_ts = started_at

                db.add(VoiceTranscriptEntry(
                    session_id=session.voice_session_id,
                    speaker=entry.get("speaker", "candidate"),
                    text=entry.get("text", ""),
                    timestamp=abs_ts,
                ))

    db.commit()

    # ── Trigger scorecard generation as background task ─────────────────────
    background_tasks.add_task(
        _generate_scorecard_background,
        session_id=session.id,
        expected_voice_generation=expected_generation,
    )

    return {"status": "ok", "session_id": session.id}
