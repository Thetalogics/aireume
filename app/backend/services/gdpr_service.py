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
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

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


def get_retention_config(tenant_id: Optional[int] = None, db: Optional[Session] = None) -> Dict[str, int]:
    """Get retention configuration, optionally tenant-specific.

    Falls back to DEFAULT_RETENTION_DAYS if no tenant config exists.
    """
    if tenant_id and db:
        try:
            from app.backend.models.db_models import TenantConfig
            config = db.query(TenantConfig).filter(TenantConfig.tenant_id == tenant_id).first()
            if config and config.retention_policy:
                custom = json.loads(config.retention_policy)
                return {**DEFAULT_RETENTION_DAYS, **custom}
        except Exception as e:
            logger.warning("Failed to load tenant retention config: %s", e)

    return DEFAULT_RETENTION_DAYS.copy()


def hard_delete_candidate(db: Session, candidate_id: int, tenant_id: int, reason: str = "gdpr_request") -> Dict[str, Any]:
    """Execute right-to-be-forgotten: permanently delete candidate and all associated data.

    This is irreversible. All screening results, voice sessions, and resume data
    are deleted. An audit log entry is preserved (without PII) for compliance.

    Returns summary of deleted records.
    """
    from app.backend.models.db_models import (
        Candidate, ScreeningResult, VoiceScreeningSession,
        FieldAuditLog, AuditLog, TrainingExample,
    )

    deleted = {"candidate": False, "screening_results": 0, "voice_sessions": 0, "resume_text": False}

    try:
        # Get candidate for audit info (before deletion)
        candidate = db.query(Candidate).filter(
            Candidate.id == candidate_id,
            Candidate.tenant_id == tenant_id,
        ).first()

        if not candidate:
            return {"error": "Candidate not found", **deleted}

        # Store anonymized audit info
        candidate_hash = hashlib.sha256(f"{candidate.email}|{candidate_id}".encode()).hexdigest()[:16]

        if candidate.resume_file_key or candidate.resume_pdf_key:
            from app.backend.models.db_models import PendingObjectDeletion
            from app.backend.services.object_storage import ObjectStorageService
            storage_ok = True
            for key in (candidate.resume_file_key, candidate.resume_pdf_key):
                if not key:
                    continue
                deleted_ok = False
                try:
                    if ObjectStorageService.is_available():
                        deleted_ok = bool(ObjectStorageService.delete(key))
                    else:
                        deleted_ok = False
                except Exception as exc:
                    storage_ok = False
                    deleted["object_storage_error"] = str(exc)
                    deleted_ok = False
                if not deleted_ok:
                    storage_ok = False
                    db.add(PendingObjectDeletion(
                        tenant_id=tenant_id,
                        storage_key=key,
                        candidate_id=candidate_id,
                        attempts=1,
                        last_error=deleted.get("object_storage_error") or "delete_failed",
                    ))
            deleted["object_storage_complete"] = storage_ok
            if not storage_ok:
                try:
                    from app.backend.services.metrics import GDPR_DELETION_FAILURE_TOTAL, GDPR_DELETION_RETRY_TOTAL
                    GDPR_DELETION_FAILURE_TOTAL.labels(reason="object_storage").inc()
                    GDPR_DELETION_RETRY_TOTAL.inc()
                except Exception:
                    pass
                db.commit()
                return {
                    "error": "object_storage_delete_incomplete",
                    "deleted": False,
                    **deleted,
                }

        # TrainingExample.screening_result_id has no ON DELETE CASCADE.
        # Remove those rows first so result deletion can CASCADE the ledger.
        results = db.query(ScreeningResult).filter(
            ScreeningResult.candidate_id == candidate_id
        ).all()
        result_ids = [r.id for r in results]
        if result_ids:
            db.query(TrainingExample).filter(
                TrainingExample.screening_result_id.in_(result_ids)
            ).delete(synchronize_session=False)
        for r in results:
            db.delete(r)
            deleted["screening_results"] += 1

        # Delete voice screening sessions
        sessions = db.query(VoiceScreeningSession).filter(
            VoiceScreeningSession.candidate_id == candidate_id
        ).all()
        for s in sessions:
            db.delete(s)
            deleted["voice_sessions"] += 1

        # Delete candidate record (cascade should handle remaining)
        db.delete(candidate)
        deleted["candidate"] = True
        deleted["resume_text"] = True

        # Create audit log entry (no PII)
        audit = AuditLog(
            actor_user_id=None,
            actor_email="system",
            tenant_id=tenant_id,
            action="gdpr.right_to_be_forgotten",
            resource_type="candidate",
            resource_id=candidate_id,
            details=json.dumps({
                "reason": reason,
                "candidate_hash": candidate_hash,
                "deleted": deleted,
            }),
        )
        db.add(audit)
        db.commit()

        logger.info("GDPR hard delete completed for candidate %d (tenant %d): %s",
                     candidate_id, tenant_id, deleted)
        return deleted

    except Exception as e:
        db.rollback()
        logger.error("GDPR hard delete failed for candidate %d: %s", candidate_id, e)
        return {"error": str(e), **deleted}


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

    summary = {"anonymized": 0, "deleted": 0, "errors": 0}

    try:
        query = db.query(Candidate).filter(
            Candidate.created_at < cutoff,
            Candidate.status != "anonymized",
        )
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
