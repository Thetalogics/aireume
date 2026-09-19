"""Voice attempt identity and reliability-sensitive lifecycle rules."""
from __future__ import annotations

from app.backend.models.db_models import VoiceScreeningSession

VOICE_TERMINAL_STATUSES = frozenset(
    {"completed", "cancelled", "ended", "escalated", "voicemail"}
)
VOICE_RETRYABLE_STATUSES = frozenset({"failed", "no_answer", "pending"})
VOICE_DISPATCHABLE_STATUSES = frozenset({"scheduled"})
VOICE_COMPLETION_REJECTED_STATUSES = frozenset(
    {"cancelled", "ended", "escalated", "voicemail"}
)

_ALLOWED_TRANSITIONS = {
    "scheduled": frozenset({"ringing", "in_progress", "cancelled", "failed", "pending"}),
    "ringing": frozenset({"in_progress", "cancelled", "failed", "pending"}),
    "in_progress": frozenset({"completed", "cancelled", "failed"}),
    "pending": frozenset({"scheduled", "cancelled", "escalated"}),
    "failed": frozenset({"scheduled", "cancelled", "escalated"}),
    "no_answer": frozenset({"scheduled", "cancelled", "escalated"}),
    "voicemail": frozenset({"cancelled"}),
}


def can_transition_voice_status(current: str, target: str) -> bool:
    """Return whether a same-generation attempt may make this transition."""
    return current == target or target in _ALLOWED_TRANSITIONS.get(current, ())


def invalidate_voice_attempt(
    session: VoiceScreeningSession,
    *,
    next_status: str,
    clear_attempt_outputs: bool = False,
) -> int:
    """Invalidate the current attempt; caller owns the database transaction."""
    session.result_generation = (session.result_generation or 1) + 1
    session.completion_event_id = None
    session.status = next_status
    if clear_attempt_outputs:
        session.assessment_json = None
        session.transcript_json = None
        session.duration_seconds = None
        session.started_at = None
        session.ended_at = None
        session.call_sid = None
        session.error_log = None
    return session.result_generation
