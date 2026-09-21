"""
Voice screening routes.

Endpoints:
  GET    /api/voice/settings          — Get tenant's voice bot config
  PUT    /api/voice/settings          — Update tenant's voice bot config
  POST   /api/voice/schedule          — Schedule a voice screening call
  GET    /api/voice/sessions          — List voice sessions for tenant
  GET    /api/voice/sessions/{id}     — Get session detail with transcript
  GET    /api/voice/sessions/analytics — Session analytics summary
  POST   /api/voice/sessions/bulk-cancel — Cancel multiple sessions
  GET    /api/voice/sessions/export   — Export sessions as CSV
  GET    /api/voice/next-slot         — Next available call slot
  GET    /api/voice/status            — Cloud voice availability
"""
import csv
import io
import json
import logging

import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session, selectinload

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_internal_service, require_admin
from app.backend.models.db_models import (
    User, VoiceTenantConfig, VoiceScreeningSession, VoiceTranscriptEntry, Candidate, RoleTemplate,
    ScreeningResult, Tenant,
)
from app.backend.models.schemas import (
    VoiceTenantConfigUpdate,
    VoiceTenantConfigOut,
    InterviewOpeningSuggestRequest,
    InterviewOpeningSuggestResponse,
    VoiceScreeningSessionOut,
    VoiceTranscriptEntryOut,
    ScheduleVoiceCallRequest,
    ScheduleVoiceCallResponse,
    RescheduleVoiceCallRequest,
    BulkCancelRequest,
)
from app.backend.services.reliability.voice_attempt import (
    VOICE_COMPLETION_REJECTED_STATUSES,
    VOICE_TERMINAL_STATUSES,
    can_transition_voice_status,
    invalidate_voice_attempt,
)

logger = logging.getLogger(__name__)

# NOTE: This module is maintained for backward compatibility.
# The unified interview API is at /api/interviews/* (see routes/interviews.py).
# New features should be added to interviews.py, not here.

router = APIRouter(prefix="/api/voice", tags=["voice-screening"])
internal_router = APIRouter(prefix="/api/voice", tags=["voice-internal"])

# ─── Default consent script ───────────────────────────────────────────────────

DEFAULT_CONSENT_SCRIPT = (
    "Before we begin, I need to let you know that this call is being recorded "
    "for hiring evaluation purposes. Your responses will be used to assess your "
    "fit for the position. Do you consent to proceed with this recorded screening?"
)


# ─── Settings ─────────────────────────────────────────────────────────────────

def _guard_opening_admin_fields(user: User, update_data: dict) -> None:
    from app.backend.services.interview_opening_service import guard_opening_admin_fields

    guard_opening_admin_fields(user, update_data)


@router.get("/settings", response_model=VoiceTenantConfigOut)
def get_voice_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current tenant's voice screening bot configuration."""
    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == user.tenant_id)
    ).scalar_one_or_none()

    if config is None:
        # Auto-create default config for this tenant
        config = VoiceTenantConfig(tenant_id=user.tenant_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    return config


@router.put("/settings", response_model=VoiceTenantConfigOut)
def update_voice_settings(
    body: VoiceTenantConfigUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current tenant's voice screening bot configuration."""
    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == user.tenant_id)
    ).scalar_one_or_none()

    if config is None:
        config = VoiceTenantConfig(tenant_id=user.tenant_id)
        db.add(config)

    # Apply only non-None fields from the update body
    update_data = body.model_dump(exclude_unset=True)
    _guard_opening_admin_fields(user, update_data)
    if update_data.get("use_custom_interview_opening"):
        from app.backend.services.interview_opening_service import validate_opening_template

        issues = validate_opening_template(update_data.get("interview_opening_script"))
        if issues:
            raise HTTPException(status_code=422, detail=issues[0])
    for field, value in update_data.items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)
    return config


@router.post("/settings/suggest-opening", response_model=InterviewOpeningSuggestResponse)
async def suggest_interview_opening(
    body: InterviewOpeningSuggestRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: draft a custom interview opening with AI assist."""
    from app.backend.services.interview_opening_service import (
        load_tenant_opening_config,
        suggest_interview_opening_draft,
    )

    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == user.tenant_id)
    ).scalar_one_or_none()
    opening = load_tenant_opening_config(db, user.tenant_id)
    company_about = body.company_about or opening.get("company_about_blurb")
    try:
        script = await suggest_interview_opening_draft(
            company_name=opening["company_name"],
            bot_name=opening["bot_name"],
            company_about=company_about,
            tone=body.tone or "professional",
        )
    except HTTPException:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        logger.warning(
            "suggest-opening failed for tenant %s: %s", user.tenant_id, exc,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        from app.backend.services.interview_opening_service import default_opening_text

        script = default_opening_text(
            candidate_name="there",
            role_title="open role",
            company_name=opening["company_name"],
            bot_name=opening["bot_name"],
        )
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        logger.exception(
            "suggest-opening failed for tenant %s: %s", user.tenant_id, exc,
            extra={"error_code": "LLM_ERROR"},
        )
        from app.backend.services.interview_opening_service import default_opening_text

        script = default_opening_text(
            candidate_name="there",
            role_title="open role",
            company_name=opening["company_name"],
            bot_name=opening["bot_name"],
        )
    return InterviewOpeningSuggestResponse(script=script)


# ─── Voice availability ───────────────────────────────────────────────────────

@router.get("/status")
def get_voice_status():
    """Report whether cloud voice screening can accept new calls."""
    from app.backend.services.livekit_cloud_dispatch import (
        get_voice_unavailability_reason,
        is_cloud_voice_enabled,
    )

    reason = get_voice_unavailability_reason()
    return {
        "cloud_voice_enabled": is_cloud_voice_enabled(),
        "available": reason is None,
        "message": reason,
    }


# ─── Schedule Call ────────────────────────────────────────────────────────────

@router.post("/schedule", response_model=ScheduleVoiceCallResponse)
def schedule_voice_call(
    body: ScheduleVoiceCallRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Schedule a voice screening call for a candidate."""
    from app.backend.services.livekit_cloud_dispatch import get_voice_unavailability_reason

    unavailable = get_voice_unavailability_reason()
    if unavailable:
        raise HTTPException(status_code=503, detail=unavailable)

    # Verify candidate belongs to this tenant
    candidate = db.execute(
        select(Candidate).where(
            Candidate.id == body.candidate_id,
            Candidate.tenant_id == user.tenant_id,
        )
    ).scalar_one_or_none()

    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found or not in your tenant")

    from app.backend.services.candidate_processing_policy import (
        PROCESSING_VOICE_INTERVIEW,
        enforce_candidate_processing_policy,
    )
    enforce_candidate_processing_policy(
        db,
        tenant_id=user.tenant_id,
        candidate_id=body.candidate_id,
        processing_type=PROCESSING_VOICE_INTERVIEW,
    )

    # Ensure config exists
    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == user.tenant_id)
    ).scalar_one_or_none()
    if config is None:
        config = VoiceTenantConfig(tenant_id=user.tenant_id)
        db.add(config)
        db.commit()

    from app.backend.services.voice_call_scheduler import schedule_voice_call as register_voice_job
    from app.backend.services.voice_schedule_idempotency import operation_key, schedule_once

    def _create():
        row = VoiceScreeningSession(
            tenant_id=user.tenant_id,
            candidate_id=body.candidate_id,
            jd_id=body.jd_id,
            phone_number=body.phone_number,
            direction="outbound",
            status="scheduled",
            scheduled_at=body.scheduled_at,
        )
        db.add(row)
        return row

    def _register(row: VoiceScreeningSession):
        register_voice_job(
            row.id,
            body.scheduled_at,
            expected_generation=row.result_generation,
        )

    session, _replayed = schedule_once(
        db,
        tenant_id=user.tenant_id,
        endpoint="POST:/api/voice/schedule",
        key=operation_key(request, getattr(body, "operation_id", None)),
        payload={
            "candidate_id": body.candidate_id,
            "jd_id": body.jd_id,
            "phone_number": body.phone_number,
            "scheduled_at": str(body.scheduled_at),
        },
        create_session=_create,
        register_scheduler=_register,
    )

    return ScheduleVoiceCallResponse(
        session_id=session.id,
        status=session.status,
        scheduled_at=session.scheduled_at,
        phone_number=session.phone_number,
    )


# ─── Sessions List ────────────────────────────────────────────────────────────

@router.get("/sessions")
def list_voice_sessions(
    candidate_id: int = None,
    status: str = None,
    search: str = None,
    jd_id: int = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List voice screening sessions for the current tenant."""
    query = (
        select(VoiceScreeningSession)
        .where(VoiceScreeningSession.tenant_id == user.tenant_id)
        .options(
            selectinload(VoiceScreeningSession.candidate),
            selectinload(VoiceScreeningSession.jd),
        )
    )

    if candidate_id is not None:
        query = query.where(VoiceScreeningSession.candidate_id == candidate_id)
    if status is not None:
        query = query.where(VoiceScreeningSession.status == status)
    if jd_id is not None:
        query = query.where(VoiceScreeningSession.jd_id == jd_id)
    if search and len(search) >= 2:
        search_term = f"%{search}%"
        query = query.join(Candidate, VoiceScreeningSession.candidate_id == Candidate.id).where(
            or_(
                Candidate.name.ilike(search_term),
                Candidate.email.ilike(search_term),
                VoiceScreeningSession.phone_number.ilike(search_term),
            )
        )

    query = query.order_by(VoiceScreeningSession.created_at.desc()).limit(limit).offset(offset)
    sessions = db.execute(query).scalars().all()

    # Heal stale rows: calls that finished but never got status=completed
    stale_fixed = False
    for s in sessions:
        if s.status != "in_progress":
            continue
        if s.assessment_json or (s.duration_seconds and s.duration_seconds > 0):
            s.status = "completed"
            s.ended_at = s.ended_at or s.started_at or datetime.now(timezone.utc)
            stale_fixed = True
    if stale_fixed:
        db.commit()

    # Compute call_count per candidate in batch
    candidate_ids = list({s.candidate_id for s in sessions})
    call_counts = {}
    if candidate_ids:
        count_rows = db.execute(
            select(
                VoiceScreeningSession.candidate_id,
                func.count(VoiceScreeningSession.id).label("cnt"),
            )
            .where(
                VoiceScreeningSession.tenant_id == user.tenant_id,
                VoiceScreeningSession.candidate_id.in_(candidate_ids),
            )
            .group_by(VoiceScreeningSession.candidate_id)
        ).all()
        call_counts = {r.candidate_id: r.cnt for r in count_rows}

    # Compute match_score per (candidate, jd) in batch
    match_scores = {}
    jd_pairs = list({(s.candidate_id, s.jd_id) for s in sessions if s.jd_id})
    if jd_pairs:
        cand_ids_for_score = list({p[0] for p in jd_pairs})
        jd_ids_for_score = list({p[1] for p in jd_pairs})
        score_rows = db.execute(
            select(
                ScreeningResult.candidate_id,
                ScreeningResult.role_template_id,
                ScreeningResult.deterministic_score,
            )
            .where(
                ScreeningResult.tenant_id == user.tenant_id,
                ScreeningResult.candidate_id.in_(cand_ids_for_score),
                ScreeningResult.role_template_id.in_(jd_ids_for_score),
                ScreeningResult.is_active == True,
            )
        ).all()
        for r in score_rows:
            match_scores[(r.candidate_id, r.role_template_id)] = r.deterministic_score

    results = []
    for s in sessions:
        out = VoiceScreeningSessionOut.model_validate(s)
        out.candidate_name = s.candidate.name if s.candidate else None
        out.candidate_email = s.candidate.email if s.candidate else None
        out.jd_title = s.jd.name if s.jd else None
        out.call_count = call_counts.get(s.candidate_id, 0)
        out.match_score = match_scores.get((s.candidate_id, s.jd_id)) if s.jd_id else None
        results.append(out)
    return results


# ─── Session Analytics ────────────────────────────────────────────────────────

@router.get("/sessions/analytics")
def get_session_analytics(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return aggregate analytics for voice screening sessions."""
    tenant_id = user.tenant_id

    rows = db.execute(
        select(
            func.count(VoiceScreeningSession.id).label("total"),
            func.count(VoiceScreeningSession.id).filter(
                VoiceScreeningSession.status == "completed"
            ).label("completed"),
            func.avg(VoiceScreeningSession.duration_seconds).filter(
                VoiceScreeningSession.duration_seconds.isnot(None)
            ).label("avg_duration"),
        ).where(VoiceScreeningSession.tenant_id == tenant_id)
    ).one()

    total = rows.total or 0
    completed = rows.completed or 0
    connection_rate = round((completed / total * 100) if total > 0 else 0, 1)
    avg_duration = int(rows.avg_duration) if rows.avg_duration else 0

    # Status breakdown
    status_rows = db.execute(
        select(
            VoiceScreeningSession.status,
            func.count(VoiceScreeningSession.id).label("cnt"),
        )
        .where(VoiceScreeningSession.tenant_id == tenant_id)
        .group_by(VoiceScreeningSession.status)
    ).all()
    status_breakdown = {r.status: r.cnt for r in status_rows}

    # Today's count
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.execute(
        select(func.count(VoiceScreeningSession.id))
        .where(
            VoiceScreeningSession.tenant_id == tenant_id,
            VoiceScreeningSession.created_at >= today_start,
        )
    ).scalar() or 0

    return {
        "total": total,
        "completed": completed,
        "connection_rate": connection_rate,
        "avg_duration_seconds": avg_duration,
        "status_breakdown": status_breakdown,
        "today_count": today_count,
    }


# ─── Bulk Cancel ──────────────────────────────────────────────────────────────

@router.post("/sessions/bulk-cancel")
def bulk_cancel_sessions(
    body: BulkCancelRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel multiple voice screening sessions at once."""
    sessions = db.execute(
        select(VoiceScreeningSession).where(
            VoiceScreeningSession.id.in_(body.session_ids),
            VoiceScreeningSession.tenant_id == user.tenant_id,
        )
    ).scalars().all()

    cancelled = 0
    skipped = 0
    for session in sessions:
        if session.status in VOICE_TERMINAL_STATUSES:
            skipped += 1
            continue
        from app.backend.services.voice_call_scheduler import cancel_pending_retries
        cancel_pending_retries(session.id)
        invalidate_voice_attempt(session, next_status="cancelled")
        cancelled += 1

    db.commit()
    return {"cancelled": cancelled, "skipped": skipped}


# ─── CSV Export ───────────────────────────────────────────────────────────────

@router.get("/sessions/export")
def export_sessions_csv(
    status: str = None,
    search: str = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export voice screening sessions as a CSV file."""
    query = (
        select(VoiceScreeningSession)
        .where(VoiceScreeningSession.tenant_id == user.tenant_id)
        .options(
            selectinload(VoiceScreeningSession.candidate),
            selectinload(VoiceScreeningSession.jd),
        )
        .order_by(VoiceScreeningSession.created_at.desc())
    )

    if status:
        query = query.where(VoiceScreeningSession.status == status)
    if search and len(search) >= 2:
        search_term = f"%{search}%"
        query = query.join(Candidate, VoiceScreeningSession.candidate_id == Candidate.id).where(
            or_(
                Candidate.name.ilike(search_term),
                Candidate.email.ilike(search_term),
            )
        )

    sessions = db.execute(query).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Candidate", "Phone", "Status", "Direction", "Job", "Scheduled", "Duration (s)", "Retries", "Created"])
    for s in sessions:
        writer.writerow([
            s.candidate.name if s.candidate else "",
            s.phone_number,
            s.status,
            s.direction,
            s.jd.name if s.jd else "",
            str(s.scheduled_at) if s.scheduled_at else "",
            s.duration_seconds or "",
            s.retry_count,
            str(s.created_at) if s.created_at else "",
        ])

    output.seek(0)
    filename = f"voice_sessions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Session Detail ───────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}")
def get_voice_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a voice screening session detail with transcript entries."""
    session = db.execute(
        select(VoiceScreeningSession)
        .where(
            VoiceScreeningSession.id == session_id,
            VoiceScreeningSession.tenant_id == user.tenant_id,
        )
        .options(
            selectinload(VoiceScreeningSession.candidate),
            selectinload(VoiceScreeningSession.jd),
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Voice session not found")

    # Load transcript entries
    entries = db.execute(
        select(VoiceTranscriptEntry)
        .where(VoiceTranscriptEntry.session_id == session_id)
        .order_by(VoiceTranscriptEntry.timestamp.asc())
    ).scalars().all()

    result = VoiceScreeningSessionOut.model_validate(session)
    result.candidate_name = session.candidate.name if session.candidate else None
    result.candidate_email = session.candidate.email if session.candidate else None
    result.jd_title = session.jd.name if session.jd else None
    result_dict = result.model_dump()
    result_dict["transcript"] = [VoiceTranscriptEntryOut.model_validate(e) for e in entries]

    return result_dict


# ─── Next Available Slot ─────────────────────────────────────────────────────

@router.get("/next-slot")
def get_next_available_slot(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the next available call slot based on business hours config."""
    import zoneinfo

    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == user.tenant_id)
    ).scalar_one_or_none()

    if config is None:
        # Default: next hour on the hour
        now = datetime.now(timezone.utc)
        suggested = now + timedelta(hours=1)
        return {"suggested_at": suggested.isoformat()}

    # Resolve tenant timezone
    tz_name = config.timezone or "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    try:
        start_h, start_m = map(int, config.business_hours_start.split(":"))
        allowed_days = config.allowed_days or [1, 2, 3, 4, 5]
        if isinstance(allowed_days, str):
            try:
                allowed_days = json.loads(allowed_days)
            except (json.JSONDecodeError, TypeError):
                allowed_days = [1, 2, 3, 4, 5]
    except (ValueError, AttributeError):
        now = datetime.now(timezone.utc)
        suggested = now + timedelta(hours=1)
        return {"suggested_at": suggested.isoformat()}

    # Calculate "tomorrow" in the TENANT's timezone to avoid day-boundary mismatch
    now_local = datetime.now(tz)
    tomorrow_local = (now_local + timedelta(days=1)).replace(
        hour=start_h, minute=start_m, second=0, microsecond=0,
    )

    # Find next allowed day (check day-of-week in tenant's timezone)
    candidate = tomorrow_local
    for _ in range(14):
        if candidate.isoweekday() in allowed_days:
            return {"suggested_at": candidate.astimezone(timezone.utc).isoformat()}
        candidate += timedelta(days=1)

    # Fallback
    return {"suggested_at": tomorrow_local.astimezone(timezone.utc).isoformat()}


# ─── Internal Endpoints (service-to-service, no auth) ─────────────────────────
# These are called by the voice-agent container over the internal Docker network.
# They are NOT exposed through Nginx (only /api/voice/* is proxied).

@internal_router.get("/internal/config/{tenant_id}")
def get_voice_config_internal(
    tenant_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_service),
):
    """Get voice tenant config — called by voice-agent (internal, secret-guarded)."""
    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == tenant_id)
    ).scalar_one_or_none()

    if config is None:
        return {}

    tenant = db.get(Tenant, tenant_id)
    company_name = (tenant.brand_name or tenant.name if tenant else None) or "the company"

    return {
        "bot_name": config.bot_name,
        "bot_voice_gender": config.bot_voice_gender,
        "greeting_style": config.greeting_style,
        "call_duration_max": config.call_duration_max,
        "call_duration_min": config.call_duration_min,
        "consent_script": config.consent_script,
        "interview_opening_script": config.interview_opening_script,
        "use_custom_interview_opening": bool(config.use_custom_interview_opening),
        "company_name": company_name,
        "outbound_phone_number": config.outbound_phone_number,
        "caller_id_name": config.caller_id_name,
        "assessment_detail_level": config.assessment_detail_level,
        "follow_up_aggressiveness": config.follow_up_aggressiveness,
    }


@internal_router.get("/internal/candidate/{tenant_id}/{candidate_id}")
def get_candidate_internal(
    tenant_id: int,
    candidate_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_service),
):
    """Get candidate info — called by voice-agent (internal, secret-guarded)."""
    candidate = db.execute(
        select(Candidate).where(
            Candidate.id == candidate_id,
            Candidate.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return {
        "id": candidate.id,
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
    }


class VoiceSessionUpdate(BaseModel):
    """Typed, allow-listed fields for internal voice session updates."""
    expected_generation: int
    status: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    transcript_json: Optional[dict] = None
    assessment_json: Optional[dict] = None
    duration_seconds: Optional[int] = None
    retry_count: Optional[int] = None
    consent_recorded: Optional[bool] = None
    call_sid: Optional[str] = None
    error_log: Optional[str] = None


@internal_router.patch("/sessions/{session_id}")
def update_voice_session(
    session_id: int,
    body: VoiceSessionUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_service),
):
    """Update a voice screening session — called by voice-agent (internal, secret-guarded)."""
    session = db.execute(
        select(VoiceScreeningSession)
        .where(
            VoiceScreeningSession.id == session_id,
            VoiceScreeningSession.result_generation == body.expected_generation,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if session is None:
        existing = db.get(VoiceScreeningSession, session_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Voice session not found")
        raise HTTPException(status_code=409, detail="Voice session generation is stale")

    if session.status in VOICE_COMPLETION_REJECTED_STATUSES:
        raise HTTPException(status_code=409, detail="Voice session is no longer mutable")
    if body.status is not None and not can_transition_voice_status(session.status, body.status):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid voice session transition: {session.status} -> {body.status}",
        )

    for field_name, value in body.model_dump(
        exclude_unset=True,
        exclude={"expected_generation"},
    ).items():
        setattr(session, field_name, value)

    db.commit()
    db.refresh(session)
    return {"id": session.id, "status": session.status}


# ─── Reschedule / Cancel ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/reschedule")
def reschedule_voice_session(
    session_id: int,
    body: RescheduleVoiceCallRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reschedule a voice screening call to a new time."""
    from app.backend.services.livekit_cloud_dispatch import get_voice_unavailability_reason

    unavailable = get_voice_unavailability_reason()
    if unavailable:
        raise HTTPException(status_code=503, detail=unavailable)

    session = db.execute(
        select(VoiceScreeningSession).where(
            VoiceScreeningSession.id == session_id,
            VoiceScreeningSession.tenant_id == user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Voice session not found")

    if session.status not in ("scheduled", "no_answer", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reschedule session in '{session.status}' status",
        )

    # Cancel any existing APScheduler jobs for this session
    from app.backend.services.voice_call_scheduler import cancel_pending_retries, schedule_voice_call
    cancel_pending_retries(session_id)

    # Update session
    invalidate_voice_attempt(
        session,
        next_status="scheduled",
        clear_attempt_outputs=True,
    )
    session.scheduled_at = body.scheduled_at
    session.phone_number = body.phone_number or session.phone_number
    if body.jd_id is not None:
        session.jd_id = body.jd_id
    db.commit()

    # Schedule the new call
    schedule_voice_call(
        session_id,
        body.scheduled_at,
        expected_generation=session.result_generation,
    )

    return {
        "session_id": session.id,
        "status": session.status,
        "scheduled_at": session.scheduled_at,
        "message": "Call rescheduled successfully",
    }


@router.post("/sessions/{session_id}/cancel")
def cancel_voice_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a voice screening call and all pending retries."""
    session = db.execute(
        select(VoiceScreeningSession).where(
            VoiceScreeningSession.id == session_id,
            VoiceScreeningSession.tenant_id == user.tenant_id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Voice session not found")

    if session.status in VOICE_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel session in '{session.status}' status",
        )

    # Cancel any pending APScheduler jobs
    from app.backend.services.voice_call_scheduler import cancel_pending_retries
    cancel_pending_retries(session_id)

    invalidate_voice_attempt(session, next_status="cancelled")
    db.commit()

    return {"session_id": session.id, "status": "cancelled", "message": "Call cancelled"}
