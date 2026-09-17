"""Explicit tenant-scoped candidate merge (AUD-040).

Duplicate hierarchy (never auto-merge):
1. exact tenant + verified/stored email
2. exact tenant + resume file hash
3. exact tenant + strong phone AND name match
4. name similarity alone is ambiguous and requires human review
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    ATSSyncLog,
    AIDecisionLog,
    AnalysisJob,
    AnalysisResult,
    Candidate,
    CandidateConsent,
    CandidateMergeEvent,
    CandidateNote,
    DeadLetterJob,
    HiringOutcome,
    PendingObjectDeletion,
    RecruiterInterviewSession,
    RecruiterScorecard,
    RequisitionCandidate,
    ScreeningProjectCandidate,
    ScreeningResult,
    TranscriptAnalysis,
    User,
    VoiceScreeningSession,
)
from app.backend.services.audit_service import log_audit

IDENTITY_FIELDS = (
    "email",
    "phone",
    "name",
    "current_role",
    "current_company",
)


class MergeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _norm_name(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _norm_phone(value: Optional[str]) -> str:
    return re.sub(r"\D+", "", value or "")


def classify_duplicate(left: Candidate, right: Candidate) -> str:
    """Return match class; never implies an automatic merge."""
    if left.tenant_id != right.tenant_id:
        return "none"
    if left.email and right.email and left.email.lower() == right.email.lower():
        return "exact_email"
    if left.resume_file_hash and right.resume_file_hash and left.resume_file_hash == right.resume_file_hash:
        return "exact_resume_hash"
    phone_match = bool(_norm_phone(left.phone) and _norm_phone(left.phone) == _norm_phone(right.phone))
    name_match = bool(_norm_name(left.name) and _norm_name(left.name) == _norm_name(right.name))
    if phone_match and name_match:
        return "strong_phone_name"
    if name_match and not phone_match:
        return "ambiguous"
    return "none"


def _move_simple(db: Session, model, source_id: int, target_id: int) -> None:
    db.query(model).filter(model.candidate_id == source_id).update(
        {model.candidate_id: target_id}, synchronize_session=False
    )


def _move_unique_pair(db: Session, model, pair_attr: str, source_id: int, target_id: int) -> None:
    target_keys = {
        getattr(row, pair_attr)
        for row in db.query(model).filter(model.candidate_id == target_id).all()
    }
    for row in db.query(model).filter(model.candidate_id == source_id).all():
        key = getattr(row, pair_attr)
        if key in target_keys:
            db.delete(row)
        else:
            row.candidate_id = target_id


def _apply_identity(source: Candidate, target: Candidate) -> dict[str, Any]:
    conflicts: dict[str, Any] = {}
    for field in IDENTITY_FIELDS:
        src = getattr(source, field)
        dst = getattr(target, field)
        if src and dst and str(src).strip() != str(dst).strip():
            conflicts[field] = {"kept": dst, "discarded": src}
        elif src and not dst:
            setattr(target, field, src)
    if source.resume_file_hash and not target.resume_file_hash:
        target.resume_file_hash = source.resume_file_hash
    elif source.resume_file_hash and target.resume_file_hash and source.resume_file_hash != target.resume_file_hash:
        conflicts["resume_file_hash"] = {"kept": target.resume_file_hash, "discarded": source.resume_file_hash}
    if source.resume_file_key and not target.resume_file_key:
        target.resume_file_key = source.resume_file_key
    return conflicts


def merge_candidates(
    db: Session,
    tenant_id: int,
    source_candidate_id: int,
    target_candidate_id: int,
    actor_user_id: int,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    if source_candidate_id == target_candidate_id:
        raise MergeError("same_id", "Source and target must be different candidates")

    source = db.get(Candidate, source_candidate_id)
    target = db.get(Candidate, target_candidate_id)
    if source is None or target is None:
        raise MergeError("not_found", "Candidate not found")
    if source.tenant_id != tenant_id or target.tenant_id != tenant_id:
        raise MergeError("cross_tenant", "Cross-tenant merge is not allowed")
    if source.tenant_id != target.tenant_id:
        raise MergeError("cross_tenant", "Cross-tenant merge is not allowed")
    if target.merged_into_id:
        raise MergeError("target_not_canonical", "Target is not a canonical candidate")
    if source.merged_into_id == target.id:
        event = CandidateMergeEvent(
            tenant_id=tenant_id,
            source_candidate_id=source.id,
            target_candidate_id=target.id,
            actor_user_id=actor_user_id,
            reason=reason,
            already_merged=True,
        )
        db.add(event)
        db.flush()
        return {
            "source_candidate_id": source.id,
            "target_candidate_id": target.id,
            "canonical_id": target.id,
            "already_merged": True,
            "conflicts": {},
            "merge_event_id": event.id,
        }
    if source.merged_into_id:
        raise MergeError("already_merged", "Source was already merged into a different candidate")

    source_email = source.email
    source_hash = source.resume_file_hash
    conflicts = _apply_identity(source, target)
    source.email = None
    source.resume_file_hash = None
    if source_email and target.email and source_email != target.email:
        conflicts.setdefault("email", {"kept": target.email, "discarded": source_email})
    if source_hash and target.resume_file_hash and source_hash != target.resume_file_hash:
        conflicts.setdefault("resume_file_hash", {"kept": target.resume_file_hash, "discarded": source_hash})

    _move_simple(db, ScreeningResult, source.id, target.id)
    _move_unique_pair(db, RequisitionCandidate, "requisition_id", source.id, target.id)
    _move_unique_pair(db, ScreeningProjectCandidate, "project_id", source.id, target.id)
    _move_simple(db, CandidateNote, source.id, target.id)
    _move_simple(db, TranscriptAnalysis, source.id, target.id)
    _move_simple(db, VoiceScreeningSession, source.id, target.id)
    _move_simple(db, RecruiterInterviewSession, source.id, target.id)
    _move_simple(db, RecruiterScorecard, source.id, target.id)
    _move_simple(db, ATSSyncLog, source.id, target.id)
    _move_simple(db, HiringOutcome, source.id, target.id)
    _move_simple(db, AIDecisionLog, source.id, target.id)
    _move_simple(db, AnalysisJob, source.id, target.id)
    _move_simple(db, AnalysisResult, source.id, target.id)
    _move_simple(db, PendingObjectDeletion, source.id, target.id)
    _move_simple(db, DeadLetterJob, source.id, target.id)
    _move_unique_pair(db, CandidateConsent, "consent_type", source.id, target.id)

    source.merged_into_id = target.id
    event = CandidateMergeEvent(
        tenant_id=tenant_id,
        source_candidate_id=source.id,
        target_candidate_id=target.id,
        actor_user_id=actor_user_id,
        reason=reason,
        conflicts_json=json.dumps(conflicts, default=str) if conflicts else None,
        already_merged=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()

    actor = db.get(User, actor_user_id)
    if actor:
        log_audit(
            db,
            actor=actor,
            action="candidate.merge",
            resource_type="candidate",
            resource_id=target.id,
            details={
                "source_candidate_id": source.id,
                "target_candidate_id": target.id,
                "reason": reason,
                "conflicts": list(conflicts.keys()),
            },
            tenant_id=tenant_id,
        )
    return {
        "source_candidate_id": source.id,
        "target_candidate_id": target.id,
        "canonical_id": target.id,
        "already_merged": False,
        "conflicts": conflicts,
        "merge_event_id": event.id,
    }
