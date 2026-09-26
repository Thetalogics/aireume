"""GDPR data retention and right-to-be-forgotten service.

Provides:
- Configurable retention periods per data category
- Automated cleanup of expired candidate data
- Right-to-be-forgotten (hard delete) implementation
- Data export for portability (GDPR Article 20)
- Audit trail for all deletion operations
"""

import logging
import json
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

# Default retention periods (in days) per data category
DEFAULT_RETENTION_DAYS = {
    "candidate_data": 730,       # 2 years
    "screening_results": 730,    # 2 years
    "voice_screening": 365,      # 1 year
    "audit_logs": 2555,          # 7 years (legal compliance)
    "jd_cache": 180,             # 6 months
    "resume_text": 730,          # 2 years
    "analysis_results": 730,     # 2 years
}

# Fields to anonymize (replace with placeholder) vs hard-delete
PII_FIELDS_TO_ANONYMIZE = [
    "name", "email", "phone", "address", "linkedin_url",
    "github_url", "website", "current_company", "current_title",
]

OBJECT_DELETION_MAX_ATTEMPTS = 10
OBJECT_DELETION_OVERDUE_HOURS = 24
OBJECT_DELETION_LEASE_MINUTES = 15


def get_retention_config(tenant_id: Optional[int] = None, db: Optional[Session] = None) -> Dict[str, int]:
    """Get retention configuration, optionally tenant-specific.

    Falls back to DEFAULT_RETENTION_DAYS if no tenant config exists.
    """
    if tenant_id and db:
        try:
            from app.backend.models.db_models import DataRetentionPolicy
            policy = db.query(DataRetentionPolicy).filter(
                DataRetentionPolicy.tenant_id == tenant_id
            ).first()
            if policy:
                return {
                    **DEFAULT_RETENTION_DAYS,
                    "candidate_data": policy.candidate_retention_days,
                    "screening_results": policy.screening_result_retention_days,
                    "voice_screening": policy.voice_transcript_retention_days,
                    "analysis_results": policy.screening_result_retention_days,
                }
        except Exception as e:
            logger.warning("Failed to load tenant retention config: %s", e)

    return DEFAULT_RETENTION_DAYS.copy()


def _sanitize_delete_error(error: str | Exception | None) -> str:
    text = str(error or "delete_failed")
    text = text.replace("\r", " ").replace("\n", " ")
    return text[:500]


def _next_object_delete_retry(attempts: int) -> datetime:
    delay_minutes = min(24 * 60, 2 ** min(max(attempts, 0), 8))
    return datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)


def _delete_or_queue_object_keys(
    db: Session,
    *,
    tenant_id: int,
    candidate_id: int,
    keys: List[str | None],
    workflow_type: str = "object_delete",
    erasure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete object-storage keys or create retry rows for failed deletes."""
    from app.backend.models.db_models import PendingObjectDeletion
    from app.backend.services.object_storage import ObjectStorageService

    summary: Dict[str, Any] = {
        "object_storage_complete": True,
        "object_storage_deleted": 0,
        "object_storage_queued": 0,
    }
    for key in [k for k in keys if k]:
        deleted_ok = False
        last_error = "delete_failed"
        try:
            if ObjectStorageService.is_available():
                deleted_ok = bool(ObjectStorageService.delete(key))
            else:
                last_error = "object_storage_unavailable"
        except Exception as exc:
            last_error = str(exc)
            summary["object_storage_error"] = last_error

        if deleted_ok:
            summary["object_storage_deleted"] += 1
            continue

        summary["object_storage_complete"] = False
        summary["object_storage_queued"] += 1
        pending = (
            db.query(PendingObjectDeletion)
            .filter(
                PendingObjectDeletion.tenant_id == tenant_id,
                PendingObjectDeletion.storage_key == key,
            )
            .first()
        )
        if pending:
            pending.candidate_id = candidate_id
            pending.attempts = (pending.attempts or 0) + 1
            pending.status = "pending"
            pending.workflow_type = workflow_type
            pending.workflow_status = "object_pending"
            pending.next_retry_at = _next_object_delete_retry(pending.attempts)
            pending.last_error = _sanitize_delete_error(last_error)
            pending.erasure_reason = erasure_reason or pending.erasure_reason
            pending.completed_at = None
            pending.finalized_at = None
            pending.dead_lettered_at = None
            pending.lease_owner = None
            pending.lease_expires_at = None
        else:
            attempts = 1
            try:
                with db.begin_nested():
                    db.add(PendingObjectDeletion(
                        tenant_id=tenant_id,
                        storage_key=key,
                        candidate_id=candidate_id,
                        status="pending",
                        workflow_type=workflow_type,
                        workflow_status="object_pending",
                        attempts=attempts,
                        next_retry_at=_next_object_delete_retry(attempts),
                        last_error=_sanitize_delete_error(last_error),
                        erasure_reason=erasure_reason,
                    ))
                    db.flush()
            except IntegrityError:
                # A concurrent erasure request queued the same object key.
                pending = db.query(PendingObjectDeletion).filter(
                    PendingObjectDeletion.tenant_id == tenant_id,
                    PendingObjectDeletion.storage_key == key,
                ).one()
                pending.candidate_id = candidate_id
                pending.status = "pending"
                pending.workflow_type = workflow_type
                pending.workflow_status = "object_pending"
                pending.erasure_reason = erasure_reason or pending.erasure_reason
                pending.completed_at = None
                pending.finalized_at = None
                pending.dead_lettered_at = None

    return summary


def _delete_candidate_database_records(
    db: Session,
    *,
    candidate_id: int,
    tenant_id: int,
    reason: str,
    delete_objects: bool,
    commit: bool,
) -> Dict[str, Any]:
    from app.backend.models.db_models import (
        AuditLog, Candidate, CandidateNote, Comment, FieldAuditLog,
        HiringOutcome, InterviewEvaluation, OverallAssessment, ScreeningResult,
        TrainingExample, TranscriptAnalysis, VoiceScreeningSession,
    )

    deleted = {
        "candidate": False,
        "screening_results": 0,
        "voice_sessions": 0,
        "candidate_notes": 0,
        "transcripts": 0,
        "hiring_outcomes": 0,
        "resume_text": False,
    }
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == tenant_id,
    ).first()

    if not candidate:
        return {"error": "Candidate not found", **deleted}

    candidate_hash = hashlib.sha256(f"{candidate.email}|{candidate_id}".encode()).hexdigest()[:16]

    if delete_objects and (candidate.resume_file_key or candidate.resume_pdf_key):
        storage = _delete_or_queue_object_keys(
            db,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            keys=[candidate.resume_file_key, candidate.resume_pdf_key],
            workflow_type="hard_delete",
            erasure_reason=reason,
        )
        deleted.update(storage)
        if not storage["object_storage_complete"]:
            try:
                from app.backend.services.metrics import GDPR_DELETION_FAILURE_TOTAL, GDPR_DELETION_RETRY_TOTAL
                GDPR_DELETION_FAILURE_TOTAL.labels(reason="object_storage").inc()
                GDPR_DELETION_RETRY_TOTAL.inc()
            except Exception:
                pass
            if commit:
                db.commit()
            return {
                "error": "object_storage_delete_incomplete",
                "deleted": False,
                **deleted,
            }

    results = db.query(ScreeningResult).filter(
        ScreeningResult.candidate_id == candidate_id,
        ScreeningResult.tenant_id == tenant_id,
    ).all()
    result_ids = [r.id for r in results]
    if result_ids:
        deleted["hiring_outcomes"] += db.query(HiringOutcome).filter(
            HiringOutcome.screening_result_id.in_(result_ids)
        ).delete(synchronize_session=False)
        db.query(Comment).filter(
            Comment.result_id.in_(result_ids)
        ).delete(synchronize_session=False)
        db.query(InterviewEvaluation).filter(
            InterviewEvaluation.result_id.in_(result_ids)
        ).delete(synchronize_session=False)
        db.query(OverallAssessment).filter(
            OverallAssessment.result_id.in_(result_ids)
        ).delete(synchronize_session=False)
        db.query(TrainingExample).filter(
            TrainingExample.screening_result_id.in_(result_ids)
        ).delete(synchronize_session=False)
        db.query(FieldAuditLog).filter(
            FieldAuditLog.tenant_id == tenant_id,
            FieldAuditLog.entity_type == "screening_result",
            FieldAuditLog.entity_id.in_(result_ids),
        ).delete(synchronize_session=False)
    for result in results:
        db.delete(result)
        deleted["screening_results"] += 1

    deleted["hiring_outcomes"] += db.query(HiringOutcome).filter(
        HiringOutcome.tenant_id == tenant_id,
        HiringOutcome.candidate_id == candidate_id,
    ).delete(synchronize_session=False)
    deleted["candidate_notes"] = db.query(CandidateNote).filter(
        CandidateNote.tenant_id == tenant_id,
        CandidateNote.candidate_id == candidate_id,
    ).delete(synchronize_session=False)
    deleted["transcripts"] = db.query(TranscriptAnalysis).filter(
        TranscriptAnalysis.tenant_id == tenant_id,
        TranscriptAnalysis.candidate_id == candidate_id,
    ).delete(synchronize_session=False)
    db.query(FieldAuditLog).filter(
        FieldAuditLog.tenant_id == tenant_id,
        FieldAuditLog.entity_type == "candidate",
        FieldAuditLog.entity_id == candidate_id,
    ).delete(synchronize_session=False)
    db.query(AuditLog).filter(
        AuditLog.tenant_id == tenant_id,
        or_(
            and_(AuditLog.resource_type == "candidate", AuditLog.resource_id == candidate_id),
            and_(AuditLog.resource_type == "screening_result", AuditLog.resource_id.in_(result_ids)),
        ),
    ).update(
        {AuditLog.resource_id: None, AuditLog.details: json.dumps({"redacted": True, "reason": "gdpr_erasure"})},
        synchronize_session=False,
    )
    db.query(Candidate).filter(
        Candidate.tenant_id == tenant_id,
        Candidate.merged_into_id == candidate_id,
    ).update({Candidate.merged_into_id: None}, synchronize_session=False)

    sessions = db.query(VoiceScreeningSession).filter(
        VoiceScreeningSession.candidate_id == candidate_id
    ).all()
    for session in sessions:
        db.delete(session)
        deleted["voice_sessions"] += 1

    db.delete(candidate)
    deleted["candidate"] = True
    deleted["resume_text"] = True

    audit = AuditLog(
        actor_user_id=None,
        actor_email="system",
        tenant_id=tenant_id,
        action="gdpr.right_to_be_forgotten",
        resource_type="candidate",
        resource_id=None,
        details=json.dumps({
            "reason": reason,
            "candidate_hash": candidate_hash,
            "deleted": deleted,
        }),
    )
    db.add(audit)
    if commit:
        db.commit()
    return deleted


def _finalize_completed_hard_delete_workflows(db: Session) -> int:
    from app.backend.models.db_models import Candidate, PendingObjectDeletion

    candidates = (
        db.query(PendingObjectDeletion.tenant_id, PendingObjectDeletion.candidate_id)
        .filter(
            PendingObjectDeletion.workflow_type == "hard_delete",
            PendingObjectDeletion.workflow_status == "database_pending",
            PendingObjectDeletion.candidate_id.isnot(None),
        )
        .distinct()
        .all()
    )
    finalized = 0
    for tenant_id, candidate_id in candidates:
        candidate = db.query(Candidate).filter(
            Candidate.tenant_id == tenant_id,
            Candidate.id == candidate_id,
        ).with_for_update(skip_locked=True).first()
        if candidate is None:
            still_exists = db.query(Candidate.id).filter(
                Candidate.tenant_id == tenant_id,
                Candidate.id == candidate_id,
            ).scalar()
            if still_exists is not None:
                continue
        rows = db.query(PendingObjectDeletion).filter(
            PendingObjectDeletion.tenant_id == tenant_id,
            PendingObjectDeletion.candidate_id == candidate_id,
            PendingObjectDeletion.workflow_type == "hard_delete",
        ).all()
        if any(row.status == "pending" for row in rows):
            continue
        if any(row.status == "dead_letter" for row in rows):
            for row in rows:
                if row.workflow_status != "completed":
                    row.workflow_status = "dead_letter"
            continue

        reason = next((row.erasure_reason for row in rows if row.erasure_reason), "gdpr_request")
        result = _delete_candidate_database_records(
            db,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            reason=reason,
            delete_objects=False,
            commit=False,
        )
        now = datetime.now(timezone.utc)
        if result.get("error") and result["error"] != "Candidate not found":
            for row in rows:
                row.last_error = _sanitize_delete_error(result["error"])
            continue
        for row in rows:
            row.workflow_status = "completed"
            row.finalized_at = now
        finalized += 1
    return finalized


def _claim_pending_object_deletions(db: Session, *, limit: int, now: datetime) -> tuple[str, List[int]]:
    """Atomically lease due outbox rows so only one replica performs a retry."""
    from app.backend.models.db_models import PendingObjectDeletion

    owner = uuid.uuid4().hex
    lease_until = now + timedelta(minutes=OBJECT_DELETION_LEASE_MINUTES)
    rows = (
        db.query(PendingObjectDeletion)
        .filter(
            PendingObjectDeletion.status == "pending",
            (PendingObjectDeletion.next_retry_at.is_(None) | (PendingObjectDeletion.next_retry_at <= now)),
            (PendingObjectDeletion.lease_expires_at.is_(None) | (PendingObjectDeletion.lease_expires_at <= now)),
        )
        .order_by(PendingObjectDeletion.next_retry_at.asc(), PendingObjectDeletion.created_at.asc(), PendingObjectDeletion.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    for row in rows:
        row.lease_owner = owner
        row.lease_expires_at = lease_until
    db.commit()
    return owner, [row.id for row in rows]


def process_pending_object_deletions(db: Session, *, limit: int = 100) -> Dict[str, int]:
    """Retry GDPR object-storage deletes recorded in the durable outbox."""
    from app.backend.models.db_models import PendingObjectDeletion
    from app.backend.services.object_storage import ObjectStorageService

    now = datetime.now(timezone.utc)
    summary = {
        "processed": 0,
        "deleted": 0,
        "remaining": 0,
        "errors": 0,
        "dead_lettered": 0,
        "overdue": 0,
        "finalized": 0,
    }
    overdue_cutoff = now - timedelta(hours=OBJECT_DELETION_OVERDUE_HOURS)
    summary["overdue"] = db.query(PendingObjectDeletion).filter(
        PendingObjectDeletion.status == "pending",
        PendingObjectDeletion.created_at < overdue_cutoff,
    ).count()
    try:
        from app.backend.services.metrics import GDPR_DELETION_DEAD_LETTER, GDPR_DELETION_OVERDUE

        GDPR_DELETION_OVERDUE.set(summary["overdue"])
        dead_letter_count = db.query(PendingObjectDeletion).filter(
            PendingObjectDeletion.status == "dead_letter",
        ).count()
        GDPR_DELETION_DEAD_LETTER.set(dead_letter_count)
    except Exception:
        pass

    if not ObjectStorageService.is_available():
        summary["remaining"] = db.query(PendingObjectDeletion).filter(
            PendingObjectDeletion.status == "pending",
        ).count()
        summary["finalized"] = _finalize_completed_hard_delete_workflows(db)
        db.commit()
        return summary

    owner, row_ids = _claim_pending_object_deletions(db, limit=limit, now=now)
    for row_id in row_ids:
        row = db.query(PendingObjectDeletion).filter(
            PendingObjectDeletion.id == row_id,
            PendingObjectDeletion.status == "pending",
            PendingObjectDeletion.lease_owner == owner,
        ).first()
        if row is None:
            continue
        summary["processed"] += 1
        try:
            deleted_ok = ObjectStorageService.delete(row.storage_key)
            db.refresh(row)
            if row.lease_owner != owner:
                continue
            if deleted_ok:
                row.status = "completed"
                if row.workflow_type == "hard_delete":
                    row.workflow_status = "database_pending"
                row.completed_at = datetime.now(timezone.utc)
                summary["deleted"] += 1
            else:
                row.attempts = (row.attempts or 0) + 1
                row.last_error = "delete_failed"
                if row.attempts >= OBJECT_DELETION_MAX_ATTEMPTS:
                    row.status = "dead_letter"
                    row.workflow_status = "dead_letter"
                    row.dead_lettered_at = datetime.now(timezone.utc)
                    summary["dead_lettered"] += 1
                else:
                    row.next_retry_at = _next_object_delete_retry(row.attempts)
                    summary["remaining"] += 1
        except Exception as exc:
            db.refresh(row)
            if row.lease_owner != owner:
                continue
            row.attempts = (row.attempts or 0) + 1
            row.last_error = _sanitize_delete_error(exc)
            summary["errors"] += 1
            if row.attempts >= OBJECT_DELETION_MAX_ATTEMPTS:
                row.status = "dead_letter"
                row.workflow_status = "dead_letter"
                row.dead_lettered_at = datetime.now(timezone.utc)
                summary["dead_lettered"] += 1
            else:
                row.next_retry_at = _next_object_delete_retry(row.attempts)
                summary["remaining"] += 1
        finally:
            if row.lease_owner == owner:
                row.lease_owner = None
                row.lease_expires_at = None
            db.commit()

    summary["finalized"] = _finalize_completed_hard_delete_workflows(db)
    db.commit()
    return summary


def hard_delete_candidate(db: Session, candidate_id: int, tenant_id: int, reason: str = "gdpr_request") -> Dict[str, Any]:
    """Execute right-to-be-forgotten: permanently delete candidate and all associated data.

    This is irreversible. All screening results, voice sessions, and resume data
    are deleted. An audit log entry is preserved (without PII) for compliance.

    Returns summary of deleted records.
    """
    try:
        deleted = _delete_candidate_database_records(
            db,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            reason=reason,
            delete_objects=True,
            commit=True,
        )
        logger.info("GDPR hard delete completed for candidate %d (tenant %d): %s",
                     candidate_id, tenant_id, deleted)
        return deleted

    except Exception as e:
        db.rollback()
        logger.error("GDPR hard delete failed for candidate %d: %s", candidate_id, e)
        return {
            "error": str(e),
            "candidate": False,
            "screening_results": 0,
            "voice_sessions": 0,
            "candidate_notes": 0,
            "transcripts": 0,
            "hiring_outcomes": 0,
            "resume_text": False,
        }


def anonymize_candidate(db: Session, candidate_id: int, tenant_id: int, reason: str = "retention_expiry") -> Dict[str, Any]:
    """Anonymize a candidate's PII while preserving aggregate analytics.

    Replaces PII fields with placeholders but keeps screening results
    for statistical/bias auditing purposes.
    """
    from app.backend.models.db_models import Candidate, AuditLog

    anonymized = {"fields": 0}

    try:
        candidate = db.query(Candidate).filter(
            Candidate.id == candidate_id,
            Candidate.tenant_id == tenant_id,
        ).first()

        if not candidate:
            return {"error": "Candidate not found", **anonymized}

        candidate_hash = hashlib.sha256(f"{candidate.email or 'unknown'}|{candidate_id}".encode()).hexdigest()[:16]
        markers = {
            value for value in (
                candidate.name, candidate.email, candidate.phone,
                getattr(candidate, "linkedin_url", None),
                getattr(candidate, "github_url", None),
            ) if value
        }

        for field in PII_FIELDS_TO_ANONYMIZE:
            if hasattr(candidate, field):
                setattr(candidate, field, f"[ANONYMIZED_{candidate_hash}]")
                anonymized["fields"] += 1

        if candidate.resume_file_key or candidate.resume_pdf_key:
            storage = _delete_or_queue_object_keys(
                db,
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                keys=[candidate.resume_file_key, candidate.resume_pdf_key],
            )
            anonymized.update(storage)
            candidate.resume_file_key = None
            candidate.resume_pdf_key = None

        candidate.raw_resume_text = None
        candidate.parser_snapshot_json = None
        candidate.ai_professional_summary = None
        from app.backend.models.db_models import ScreeningResult, CandidateNote, Comment, TranscriptAnalysis, VoiceScreeningSession
        results = db.query(ScreeningResult).filter(ScreeningResult.candidate_id == candidate_id).all()
        marker_fields = ("resume_text", "parsed_data", "analysis_result", "narrative_json", "jd_text")
        for result in results:
            for field in marker_fields:
                value = getattr(result, field, None)
                if isinstance(value, str):
                    cleaned = value
                    for marker in markers:
                        cleaned = cleaned.replace(marker, "[ANONYMIZED]")
                    setattr(result, field, "[ANONYMIZED]" if cleaned != value or any(m in (value or "") for m in markers) else "[ANONYMIZED]")
                    anonymized["fields"] += 1
            comments = db.query(Comment).filter(Comment.result_id == result.id).all()
            for comment in comments:
                comment.text = "[ANONYMIZED]"
                anonymized["fields"] += 1
        notes = db.query(CandidateNote).filter(CandidateNote.candidate_id == candidate_id).all()
        for note in notes:
            note.text = "[ANONYMIZED]"
            anonymized["fields"] += 1
        transcripts = db.query(TranscriptAnalysis).filter(TranscriptAnalysis.candidate_id == candidate_id).all()
        for row in transcripts:
            row.transcript_text = "[ANONYMIZED]"
            if getattr(row, "analysis_result", None):
                row.analysis_result = "{}"
            anonymized["fields"] += 1
        sessions = db.query(VoiceScreeningSession).filter(VoiceScreeningSession.candidate_id == candidate_id).all()
        for session in sessions:
            if getattr(session, "transcript_json", None):
                session.transcript_json = "[]"
                anonymized["fields"] += 1
        # Mark as anonymized
        if hasattr(candidate, "status"):
            candidate.status = "anonymized"

        # Audit log
        audit = AuditLog(
            actor_user_id=None,
            actor_email="system",
            tenant_id=tenant_id,
            action="gdpr.anonymize",
            resource_type="candidate",
            resource_id=candidate_id,
            details=json.dumps({
                "reason": reason,
                "candidate_hash": candidate_hash,
                "fields_anonymized": anonymized["fields"],
            }),
        )
        db.add(audit)
        db.commit()

        logger.info("GDPR anonymization completed for candidate %d (tenant %d)", candidate_id, tenant_id)
        return anonymized

    except Exception as e:
        db.rollback()
        logger.error("GDPR anonymization failed for candidate %d: %s", candidate_id, e)
        return {"error": str(e), **anonymized}


def cleanup_expired_data(db: Session, tenant_id: Optional[int] = None) -> Dict[str, int]:
    """Find and anonymize/delete candidates whose data has exceeded retention period.

    This should be called by a scheduled job (e.g. APScheduler) daily.

    Returns summary of processed records.
    """
    from app.backend.models.db_models import Candidate

    config = get_retention_config(tenant_id, db)
    retention_days = config.get("candidate_data", 730)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    summary = {"anonymized": 0, "deleted": 0, "errors": 0, "object_deletes_retried": 0}

    try:
        query = db.query(Candidate).filter(
            Candidate.created_at < cutoff,
        )
        if hasattr(Candidate, "status"):
            query = query.filter(Candidate.status != "anonymized")
        if tenant_id:
            query = query.filter(Candidate.tenant_id == tenant_id)

        expired = query.all()

        for candidate in expired:
            try:
                # Anonymize rather than hard-delete (preserves analytics)
                result = anonymize_candidate(db, candidate.id, candidate.tenant_id, reason="retention_expiry")
                if "error" in result:
                    summary["errors"] += 1
                else:
                    summary["anonymized"] += 1
            except Exception as e:
                logger.error("Failed to process expired candidate %d: %s", candidate.id, e)
                summary["errors"] += 1

        logger.info("Retention cleanup completed: %s", summary)
        return summary

    except Exception as e:
        logger.error("Retention cleanup failed: %s", e)
        return {"error": str(e), **summary}


def cleanup_expired_data_for_all_tenants(db: Session) -> Dict[str, int]:
    """Run retention cleanup using each tenant's persisted retention policy."""
    from app.backend.models.db_models import DataRetentionPolicy, Tenant

    summary = {
        "tenants_processed": 0,
        "anonymized": 0,
        "deleted": 0,
        "errors": 0,
        "object_deletes_retried": 0,
    }
    try:
        tenant_ids = [
            row[0]
            for row in db.query(Tenant.id)
            .filter(Tenant.deleted_at.is_(None))
            .all()
        ]
        for policy_tenant_id, in db.query(DataRetentionPolicy.tenant_id).all():
            if policy_tenant_id not in tenant_ids:
                tenant_ids.append(policy_tenant_id)

        for tenant_id in tenant_ids:
            result = cleanup_expired_data(db, tenant_id=tenant_id)
            summary["tenants_processed"] += 1
            for key in ("anonymized", "deleted", "errors", "object_deletes_retried"):
                summary[key] += int(result.get(key, 0) or 0)
            if result.get("error"):
                summary["errors"] += 1
        return summary
    except Exception as exc:
        logger.error("Tenant retention cleanup failed: %s", exc)
        return {"error": str(exc), **summary}


def export_candidate_data(db: Session, candidate_id: int, tenant_id: int) -> Dict[str, Any]:
    """Export all candidate data for GDPR Article 20 (data portability).

    Returns a dict with all candidate information, screening results,
    and voice screening sessions.
    """
    from app.backend.models.db_models import (
        Candidate, ScreeningResult, VoiceScreeningSession, CandidateNote, Comment,
        RequisitionCandidate,
    )

    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == tenant_id,
    ).first()

    if not candidate:
        return {"error": "Candidate not found"}

    export = {
        "export_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "current_title": candidate.current_role,
            "current_company": candidate.current_company,
            "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        },
        "screening_results": [],
        "comments": [],
        "notes": [],
        "requisition_associations": [],
        "voice_sessions": [],
    }

    results = db.query(ScreeningResult).filter(
        ScreeningResult.candidate_id == candidate_id
    ).all()

    for r in results:
        analysis = {}
        try:
            analysis = json.loads(r.analysis_result or "{}")
        except (TypeError, json.JSONDecodeError):
            analysis = {}
        created = getattr(r, "timestamp", None) or getattr(r, "created_at", None)
        export["screening_results"].append({
            "id": r.id,
            "fit_score": analysis.get("fit_score"),
            "deterministic_score": getattr(r, "deterministic_score", None),
            "eligibility_status": getattr(r, "eligibility_status", None),
            "matched_skills": getattr(r, "matched_skills", None),
            "missing_skills": getattr(r, "missing_skills", None),
            "final_recommendation": getattr(r, "final_recommendation", None) or getattr(r, "status", None),
            "created_at": created.isoformat() if created else None,
        })
        comments = db.query(Comment).filter(Comment.result_id == r.id).all()
        for comment in comments:
            export["comments"].append({
                "result_id": r.id,
                "text": comment.text,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
            })

    notes = db.query(CandidateNote).filter(CandidateNote.candidate_id == candidate_id).all()
    for note in notes:
        export["notes"].append({"text": note.text})

    associations = db.query(RequisitionCandidate).filter(
        RequisitionCandidate.candidate_id == candidate_id
    ).all()
    for assoc in associations:
        export["requisition_associations"].append({
            "requisition_id": assoc.requisition_id,
            "pipeline_status": assoc.pipeline_status,
        })

    sessions = db.query(VoiceScreeningSession).filter(
        VoiceScreeningSession.candidate_id == candidate_id
    ).all()

    for s in sessions:
        export["voice_sessions"].append({
            "id": s.id,
            "status": s.status,
            "overall_score": s.overall_score,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return export
