"""
Voice Call Scheduler — APScheduler integration for voice screening calls.

Handles:
  - Scheduling outbound calls at specific times
  - Business hours enforcement (timezone-aware)
  - 3-tier retry logic (24h → 48h → escalate)
  - Callback cancellation (inbound call cancels pending retries)
  - Escalation notification

Follows the same APScheduler pattern as scheduler.py.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, update

from app.backend.db.database import SessionLocal
from app.backend.models.db_models import (
    VoiceTenantConfig, VoiceScreeningSession, Candidate,
)
from app.backend.services.reliability.voice_attempt import (
    VOICE_DISPATCHABLE_STATUSES,
    VOICE_RETRYABLE_STATUSES,
    invalidate_voice_attempt,
)

logger = logging.getLogger(__name__)

# Enable APScheduler internal logging so misfired jobs are visible
logging.getLogger("apscheduler").setLevel(logging.INFO)

# Shared scheduler instance (started by main.py lifespan)
# Explicitly use UTC so all run_date comparisons are consistent
voice_scheduler = BackgroundScheduler(timezone="UTC")

# Voice Agent dispatch URL
VOICE_AGENT_URL = os.environ.get("VOICE_AGENT_URL", "http://voice-agent:8002")

# Advisory lock state — ensures only ONE uvicorn worker runs the scheduler
# across all workers in a multi-process deployment (e.g., --workers 3).
_scheduler_lock_session = None
_scheduler_lock_acquired = False


class VoiceScheduleRegistrationError(RuntimeError):
    """The durable DB intent exists, but in-memory registration failed."""


def _voice_job_id(session_id: int, generation: int) -> str:
    return f"voice_call_{session_id}_{generation}"


def _conditional_voice_update(
    session_id: int,
    expected_generation: int,
    *,
    expected_statuses: set[str] | frozenset[str],
    values: dict,
) -> bool:
    """Apply a generation/status-bound update in a short owned transaction."""
    db = SessionLocal()
    try:
        result = db.execute(
            update(VoiceScreeningSession)
            .where(
                VoiceScreeningSession.id == session_id,
                VoiceScreeningSession.result_generation == expected_generation,
                VoiceScreeningSession.status.in_(expected_statuses),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount == 1
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _dispatch_attempt_is_current(session_id: int, expected_generation: int) -> bool:
    """Recheck authority immediately before the provider call."""
    db = SessionLocal()
    try:
        return (
            db.execute(
                select(VoiceScreeningSession.id).where(
                    VoiceScreeningSession.id == session_id,
                    VoiceScreeningSession.result_generation == expected_generation,
                    VoiceScreeningSession.status == "ringing",
                )
            ).scalar_one_or_none()
            is not None
        )
    finally:
        db.close()


# ─── Business Hours Enforcement ───────────────────────────────────────────────

def is_within_business_hours(
    dt: datetime,
    config: VoiceTenantConfig,
) -> bool:
    """
    Check if a datetime falls within the tenant's business hours.

    Uses tenant's timezone, business_hours_start/end, and allowed_days.
    """
    import zoneinfo

    tz_name = config.timezone or "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    local_dt = dt.astimezone(tz)

    # Check day of week (Monday=1 .. Sunday=7)
    allowed_days = config.allowed_days or [1, 2, 3, 4, 5]
    if isinstance(allowed_days, str):
        try:
            allowed_days = json.loads(allowed_days)
        except (json.JSONDecodeError, TypeError):
            allowed_days = [1, 2, 3, 4, 5]

    if local_dt.isoweekday() not in allowed_days:
        return False

    # Check time of day
    try:
        start_h, start_m = map(int, (config.business_hours_start or "09:00").split(":"))
        end_h, end_m = map(int, (config.business_hours_end or "18:00").split(":"))
    except (ValueError, AttributeError):
        start_h, start_m = 9, 0
        end_h, end_m = 18, 0

    current_minutes = local_dt.hour * 60 + local_dt.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    return start_minutes <= current_minutes <= end_minutes


def adjust_to_business_hours(
    dt: datetime,
    config: VoiceTenantConfig,
) -> datetime:
    """
    If dt is outside business hours, move it to the next valid slot.
    """
    import zoneinfo

    tz_name = config.timezone or "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    if is_within_business_hours(dt, config):
        return dt

    # Move to next day's start
    local_dt = dt.astimezone(tz)
    next_day = (local_dt + timedelta(days=1)).replace(
        hour=int((config.business_hours_start or "09:00").split(":")[0]),
        minute=int((config.business_hours_start or "09:00").split(":")[1]),
        second=0,
        microsecond=0,
    )

    # Skip non-allowed days
    allowed_days = config.allowed_days or [1, 2, 3, 4, 5]
    if isinstance(allowed_days, str):
        try:
            allowed_days = json.loads(allowed_days)
        except (json.JSONDecodeError, TypeError):
            allowed_days = [1, 2, 3, 4, 5]

    for _ in range(7):
        if next_day.isoweekday() in allowed_days:
            return next_day.astimezone(timezone.utc)
        next_day += timedelta(days=1)

    return next_day.astimezone(timezone.utc)


# ─── Call Execution ────────────────────────────────────────────────────────────

def execute_scheduled_call(session_id: int, expected_generation: int):
    """
    Execute a scheduled voice screening call.

    Called by APScheduler at the scheduled time.
    Dispatches via LiveKit Cloud when LIVEKIT_CLOUD_VOICE=1, otherwise
    the self-hosted voice-agent HTTP /dispatch endpoint.
    """
    from app.backend.services.livekit_cloud_dispatch import (
        dispatch_screening_call,
        is_cloud_voice_enabled,
        normalize_voice_dispatch_error,
    )

    claimed = _conditional_voice_update(
        session_id,
        expected_generation,
        expected_statuses=VOICE_DISPATCHABLE_STATUSES,
        values={"status": "ringing", "started_at": datetime.now(timezone.utc)},
    )
    if not claimed:
        logger.info(
            "Stale or non-dispatchable voice job ignored: session=%d generation=%d",
            session_id,
            expected_generation,
        )
        return

    db = SessionLocal()
    try:
        session = db.execute(
            select(VoiceScreeningSession).where(
                VoiceScreeningSession.id == session_id,
                VoiceScreeningSession.result_generation == expected_generation,
                VoiceScreeningSession.status == "ringing",
            )
        ).scalar_one_or_none()

        if session is None:
            logger.info(
                "Voice job lost authority while loading context: session=%d generation=%d",
                session_id,
                expected_generation,
            )
            return

        logger.info(
            "Executing voice screening call: session=%d candidate=%d phone=%s",
            session_id, session.candidate_id, session.phone_number,
        )

        # Fetch candidate name for context
        candidate = db.execute(
            select(Candidate).where(Candidate.id == session.candidate_id)
        ).scalar_one_or_none()
        candidate_name = candidate.name if candidate else "Candidate"

        # Get JD title if available
        jd_title = None
        if session.jd_id:
            try:
                from app.backend.models.db_models import RoleTemplate
                jd = db.execute(
                    select(RoleTemplate).where(RoleTemplate.id == session.jd_id)
                ).scalar_one_or_none()
                jd_title = jd.name if jd else None
            except Exception:
                pass

        # Load associated recruiter interview config (if any) to preserve duration/depth
        interview_config = None
        try:
            from app.backend.models.db_models import RecruiterInterviewSession

            recruiter_session = db.execute(
                select(RecruiterInterviewSession).where(
                    RecruiterInterviewSession.voice_session_id == session.id
                )
            ).scalar_one_or_none()
            if recruiter_session and recruiter_session.interview_config_json:
                interview_config = json.loads(recruiter_session.interview_config_json)
        except Exception:
            interview_config = None

        # Resolve interview kit for kit-driven voice flow
        interview_kit_payload: dict = {"questions": [], "kit_source": "empty"}
        try:
            from app.backend.services.interview_kit_loader import resolve_interview_kit_for_voice

            interview_kit_payload = resolve_interview_kit_for_voice(db, session)
            logger.info(
                "Interview kit for session=%d: source=%s questions=%d screening_result=%s",
                session_id,
                interview_kit_payload.get("kit_source"),
                len(interview_kit_payload.get("questions") or []),
                interview_kit_payload.get("screening_result_id"),
            )
        except Exception as kit_err:
            logger.warning("Failed to load interview kit for session %d: %s", session_id, kit_err)

        dispatch_payload = {
            "session_id": session.id,
            "phone_number": session.phone_number,
            "candidate_name": candidate_name,
            "tenant_id": session.tenant_id,
            "candidate_id": session.candidate_id,
            "jd_title": jd_title,
            "jd_must_have_skills": [],
            "depth": session.interview_depth or "quick",
            "interview_config": interview_config or {},
            "interview_kit": interview_kit_payload,
            "screening_result_id": interview_kit_payload.get("screening_result_id"),
            "result_generation": expected_generation,
        }
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Failed to prepare scheduled call %d: %s", session_id, e, exc_info=True)
        _conditional_voice_update(
            session_id,
            expected_generation,
            expected_statuses={"ringing"},
            values={"status": "failed", "error_log": str(e)},
        )
        return
    finally:
        db.close()

    if not _dispatch_attempt_is_current(session_id, expected_generation):
        logger.info(
            "Voice job cancelled before provider dispatch: session=%d generation=%d",
            session_id,
            expected_generation,
        )
        return

    try:
        if is_cloud_voice_enabled():
            result = dispatch_screening_call(dispatch_payload)
        else:
            resp = httpx.post(
                f"{VOICE_AGENT_URL}/dispatch",
                json=dispatch_payload,
                timeout=60.0,
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("success"):
            values = {"status": "in_progress", "error_log": None}
            logger.info(
                "Call dispatched: session=%d room=%s dispatch_id=%s",
                session_id,
                result.get("room_name"),
                result.get("dispatch_id"),
            )
        else:
            values = {
                "status": "failed",
                "error_log": normalize_voice_dispatch_error(result.get("message")),
            }
            logger.error("Dispatch failed for session %d: %s", session_id, result.get("message"))

        if not _conditional_voice_update(
            session_id,
            expected_generation,
            expected_statuses={"ringing"},
            values=values,
        ):
            logger.info(
                "Discarded post-provider write for stale voice generation: "
                "session=%d generation=%d",
                session_id,
                expected_generation,
            )

    except httpx.ConnectError as e:
        next_status = "failed" if is_cloud_voice_enabled() else "pending"
        error = (
            normalize_voice_dispatch_error(str(e))
            if is_cloud_voice_enabled()
            else f"Voice agent unreachable: {e}"
        )
        if is_cloud_voice_enabled():
            logger.error("LiveKit Cloud dispatch failed for session %d: %s", session_id, e)
        else:
            logger.warning(
                "Voice agent unreachable at %s — session %d set to pending for retry: %s",
                VOICE_AGENT_URL, session_id, e,
            )
        _conditional_voice_update(
            session_id,
            expected_generation,
            expected_statuses={"ringing"},
            values={"status": next_status, "error_log": error},
        )
    except httpx.HTTPStatusError as e:
        logger.error(
            "Voice agent returned HTTP %d for session %d: %s",
            e.response.status_code, session_id, e,
        )
        _conditional_voice_update(
            session_id,
            expected_generation,
            expected_statuses={"ringing"},
            values={
                "status": "failed",
                "error_log": f"Voice agent HTTP {e.response.status_code}: {e.response.text[:200]}",
            },
        )
    except Exception as e:
        logger.error("Failed to execute scheduled call %d: %s", session_id, e, exc_info=True)
        try:
            _conditional_voice_update(
                session_id,
                expected_generation,
                expected_statuses={"ringing"},
                values={"status": "failed", "error_log": str(e)},
            )
        except Exception:
            pass


# ─── Retry Logic ──────────────────────────────────────────────────────────────

def process_voice_retries():
    """
    Process all due voice call retries.

    Checks for sessions in 'pending', 'no_answer' or 'failed' status where:
    - retry_count < max_retries
    - Next retry time has elapsed (based on retry_intervals)
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Find sessions needing retry
        sessions = db.execute(
            select(VoiceScreeningSession).where(
                VoiceScreeningSession.status.in_(["pending", "no_answer", "failed"]),
                VoiceScreeningSession.direction == "outbound",
            )
        ).scalars().all()

        retried = 0
        escalated = 0

        for session in sessions:
            config = db.execute(
                select(VoiceTenantConfig).where(
                    VoiceTenantConfig.tenant_id == session.tenant_id
                )
            ).scalar_one_or_none()

            if config is None:
                continue

            max_retries = config.max_retries or 3
            retry_intervals = config.retry_intervals or [24, 48]
            if isinstance(retry_intervals, str):
                try:
                    retry_intervals = json.loads(retry_intervals)
                except (json.JSONDecodeError, TypeError):
                    retry_intervals = [24, 48]

            if session.retry_count >= max_retries:
                # All retries exhausted — escalate
                invalidate_voice_attempt(session, next_status="escalated")
                db.commit()
                if config.escalation_contact_id:
                    logger.warning(
                        "Session %d: all retries exhausted, escalating to contact %d",
                        session.id, config.escalation_contact_id,
                    )
                # Send escalation notification
                try:
                    from app.backend.services.voice_screening_service import _notify_escalation
                    candidate = db.execute(
                        select(Candidate).where(Candidate.id == session.candidate_id)
                    ).scalar_one_or_none()
                    _notify_escalation(db, session, candidate, config)
                except Exception as notify_err:
                    logger.warning("Failed to send escalation notification: %s", notify_err)
                escalated += 1
                continue

            # Check if enough time has elapsed since last attempt
            interval_hours = retry_intervals[min(session.retry_count, len(retry_intervals) - 1)]
            last_attempt = session.updated_at or session.created_at
            if last_attempt and last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            if last_attempt and (now - last_attempt).total_seconds() < interval_hours * 3600:
                continue  # Not yet time to retry

            # A retry is a new attempt with a new immutable generation.
            invalidate_voice_attempt(
                session,
                next_status="scheduled",
                clear_attempt_outputs=True,
            )
            session.retry_count += 1
            retry_time = now + timedelta(minutes=5)  # Immediate re-schedule
            adjusted_time = adjust_to_business_hours(retry_time, config)
            session.scheduled_at = adjusted_time
            generation = session.result_generation
            db.commit()

            try:
                _register_scheduled_job(session.id, generation, adjusted_time)
            except VoiceScheduleRegistrationError:
                logger.exception(
                    "Retry registration deferred to reconciliation: "
                    "session=%d generation=%d",
                    session.id,
                    generation,
                )

            logger.info(
                "Session %d: retry %d/%d scheduled for %s",
                session.id, session.retry_count, max_retries, adjusted_time.isoformat(),
            )
            retried += 1

        if retried or escalated:
            logger.info("Voice retry processing: %d retried, %d escalated", retried, escalated)

    except Exception as e:
        logger.error("Voice retry processing failed: %s", e, exc_info=True)
    finally:
        db.close()


def cancel_pending_retries(session_id: int):
    """
    Cancel all pending retries for a session.

    Called when a candidate calls back (inbound callback cancels remaining retries).
    """
    db = SessionLocal()
    try:
        session = db.execute(
            select(VoiceScreeningSession).where(VoiceScreeningSession.id == session_id)
        ).scalar_one_or_none()

        if session is None:
            return

        # Remove any scheduled APScheduler jobs for this session
        for job in voice_scheduler.get_jobs():
            if f"voice_call_{session_id}" in job.id or f"voice_retry_{session_id}" in job.id:
                job.remove()
                logger.info("Cancelled scheduled job %s", job.id)

        logger.info("Cancelled all pending retries for session %d", session_id)

    except Exception as e:
        logger.error("Failed to cancel retries for session %d: %s", session_id, e)
    finally:
        db.close()


# ─── Scheduling API ───────────────────────────────────────────────────────────

def _register_scheduled_job(
    session_id: int,
    expected_generation: int,
    call_time: datetime,
) -> None:
    try:
        voice_scheduler.add_job(
            execute_scheduled_call,
            trigger="date",
            run_date=call_time,
            args=[session_id, expected_generation],
            id=_voice_job_id(session_id, expected_generation),
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception as exc:
        logger.error(
            "Voice scheduler registration failed: session=%d generation=%d",
            session_id,
            expected_generation,
            exc_info=True,
        )
        raise VoiceScheduleRegistrationError(
            f"Could not register voice session {session_id} generation "
            f"{expected_generation}"
        ) from exc


def schedule_voice_call(
    session_id: int,
    scheduled_at: Optional[datetime] = None,
    *,
    expected_generation: int,
) -> bool:
    """
    Schedule a voice screening call.

    If scheduled_at is None, schedules for now (immediate).
    When a recruiter explicitly provides scheduled_at, the time is respected
    as-is — business hours adjustment only applies to auto/immediate scheduling
    and retries, never to a deliberate user choice.
    """
    db = SessionLocal()
    try:
        session = db.execute(
            select(VoiceScreeningSession).where(
                VoiceScreeningSession.id == session_id,
                VoiceScreeningSession.result_generation == expected_generation,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if session is None:
            raise VoiceScheduleRegistrationError(
                f"Voice session {session_id} generation {expected_generation} is stale or missing"
            )

        config = db.execute(
            select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == session.tenant_id)
        ).scalar_one_or_none()

        # Determine call time
        if scheduled_at:
            # Recruiter explicitly chose a time — respect it without adjustment
            call_time = scheduled_at
        else:
            # Immediate / auto — apply business hours guard rail
            call_time = datetime.now(timezone.utc)
            if config:
                call_time = adjust_to_business_hours(call_time, config)

        # Ensure call_time is timezone-aware (APScheduler needs aware datetimes)
        if call_time.tzinfo is None:
            call_time = call_time.replace(tzinfo=timezone.utc)

        # Update session
        session.scheduled_at = call_time
        session.status = "scheduled"
        db.commit()

        # DB commit above is durable truth; registration may be reconciled later.
        _register_scheduled_job(session_id, expected_generation, call_time)

        logger.info(
            "Voice call scheduled: session=%d time=%s (utc=%s) phone=%s",
            session_id, call_time.isoformat(),
            call_time.astimezone(timezone.utc).isoformat(),
            session.phone_number,
        )
        return True

    except VoiceScheduleRegistrationError:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Failed to schedule voice call %d: %s", session_id, e, exc_info=True)
        raise VoiceScheduleRegistrationError(
            f"Could not persist voice schedule for session {session_id}"
        ) from e
    finally:
        db.close()


# ─── Scheduler Lifecycle ──────────────────────────────────────────────────────

def reconcile_scheduled_voice_calls():
    """
    Re-register any scheduled voice calls that were lost due to container restart.

    APScheduler uses in-memory job storage, so all jobs are lost when the backend
    restarts. This function scans the DB for sessions in 'scheduled' status with a
    future scheduled_at and re-adds them to APScheduler.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        pending = db.execute(
            select(VoiceScreeningSession).where(
                VoiceScreeningSession.status == "scheduled",
                VoiceScreeningSession.scheduled_at.isnot(None),
                VoiceScreeningSession.direction == "outbound",
            )
        ).scalars().all()

        recovered = 0
        expired = 0
        existing_jobs = {
            job.id: job
            for job in voice_scheduler.get_jobs()
            if job.id.startswith("voice_call_")
        }

        for session in pending:
            scheduled_at = session.scheduled_at
            # Ensure timezone-aware
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

            generation = session.result_generation or 1
            job_id = _voice_job_id(session.id, generation)
            prefix = f"voice_call_{session.id}_"
            for stale_id, stale_job in list(existing_jobs.items()):
                if stale_id.startswith(prefix) and stale_id != job_id:
                    stale_job.remove()
                    existing_jobs.pop(stale_id, None)

            if job_id in existing_jobs:
                continue

            if scheduled_at > now:
                # Future job — re-register with APScheduler
                _register_scheduled_job(session.id, generation, scheduled_at)
                recovered += 1
            else:
                # Past-due job — fire immediately (within grace period)
                grace = timedelta(minutes=10)
                if (now - scheduled_at) <= grace:
                    _register_scheduled_job(
                        session.id,
                        generation,
                        now + timedelta(seconds=10),
                    )
                    recovered += 1
                    logger.info(
                        "Session %d: past-due by %s, firing immediately",
                        session.id, now - scheduled_at,
                    )
                else:
                    # Too old — mark as failed
                    session.status = "failed"
                    session.error_log = "Scheduled call missed due to server restart"
                    expired += 1

        if expired:
            db.commit()

        if recovered or expired:
            logger.info(
                "Voice call recovery: %d re-scheduled, %d expired (total pending: %d)",
                recovered, expired, len(pending),
            )
        else:
            logger.info("Voice call recovery: no pending calls found")

    except Exception as e:
        logger.error("Failed to reconcile scheduled voice calls: %s", e, exc_info=True)
    finally:
        db.close()


def recover_pending_calls():
    """Startup compatibility wrapper for durable scheduled-row reconciliation."""
    return reconcile_scheduled_voice_calls()


def start_voice_scheduler():
    """Start the voice call scheduler with periodic retry processing.

    Uses a PostgreSQL advisory lock to ensure only ONE uvicorn worker runs
    the APScheduler across all workers in a multi-process deployment.
    Without this, --workers 3 would create 3 independent schedulers causing
    lost jobs and duplicate call firings.
    """
    global _scheduler_lock_session, _scheduler_lock_acquired

    if voice_scheduler.running:
        return

    # Try to acquire advisory lock (non-blocking)
    try:
        _scheduler_lock_session = SessionLocal()
        dialect_name = _scheduler_lock_session.get_bind().dialect.name
        if dialect_name != "postgresql":
            logger.warning(
                "Voice scheduler distributed leadership unavailable for %s; "
                "using explicit single-process fallback",
                dialect_name,
            )
            _scheduler_lock_acquired = True
            _scheduler_lock_session.close()
            _scheduler_lock_session = None
        else:
            result = _scheduler_lock_session.execute(
                __import__("sqlalchemy").text("SELECT pg_try_advisory_lock(987654)")
            ).scalar()
            if result:
                _scheduler_lock_acquired = True
                logger.info("Advisory lock acquired — this worker is the scheduler leader")
            else:
                _scheduler_lock_acquired = False
                _scheduler_lock_session.close()
                _scheduler_lock_session = None
                logger.info(
                    "Advisory lock held by another worker — skipping scheduler startup"
                )
                return
    except Exception as e:
        dialect_name = None
        if _scheduler_lock_session is not None:
            try:
                dialect_name = _scheduler_lock_session.get_bind().dialect.name
            except Exception:
                pass
        if _scheduler_lock_session:
            _scheduler_lock_session.close()
            _scheduler_lock_session = None
        _scheduler_lock_acquired = False
        if dialect_name == "postgresql":
            logger.error(
                "PostgreSQL advisory lock failed; voice scheduler will not start",
                exc_info=True,
            )
            return
        logger.warning(
            "Could not inspect scheduler lock for non-PostgreSQL test DB (%s); "
            "using explicit single-process fallback",
            e,
        )
        _scheduler_lock_acquired = True

    # Process retries every 15 minutes
    voice_scheduler.add_job(
        process_voice_retries,
        trigger="date",  # Run once at startup to catch any due retries
        id="voice_retry_initial",
        replace_existing=True,
    )

    voice_scheduler.add_job(
        process_voice_retries,
        trigger="interval",
        minutes=15,
        id="voice_retry_periodic",
        replace_existing=True,
        misfire_grace_time=300,
    )
    voice_scheduler.add_job(
        reconcile_scheduled_voice_calls,
        trigger="interval",
        minutes=2,
        id="voice_schedule_reconcile_periodic",
        replace_existing=True,
        misfire_grace_time=300,
    )

    from app.backend.services.livekit_cloud_dispatch import is_cloud_voice_enabled

    dispatch_target = (
        "livekit-cloud"
        if is_cloud_voice_enabled()
        else VOICE_AGENT_URL
    )
    voice_scheduler.start()
    logger.info(
        "Voice call scheduler started (tz=%s, retry check: every 15 min, dispatch: %s)",
        voice_scheduler.timezone,
        dispatch_target,
    )

    # Recover any pending calls lost due to container restart
    recover_pending_calls()


def stop_voice_scheduler():
    """Gracefully stop the voice call scheduler and release advisory lock."""
    global _scheduler_lock_session, _scheduler_lock_acquired

    if voice_scheduler.running:
        voice_scheduler.shutdown(wait=False)
        logger.info("Voice call scheduler stopped")

    if _scheduler_lock_acquired and _scheduler_lock_session:
        try:
            _scheduler_lock_session.execute(
                __import__("sqlalchemy").text("SELECT pg_advisory_unlock(987654)")
            )
            _scheduler_lock_session.close()
            logger.info("Advisory lock released")
        except Exception:
            pass
        _scheduler_lock_session = None
        _scheduler_lock_acquired = False
