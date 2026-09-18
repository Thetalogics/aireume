"""Helper functions for analysis routes — extracted from analyze.py."""

import hashlib
import json
import os
import asyncio
import logging
import time
import concurrent.futures
from collections import defaultdict
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import update, func
from sqlalchemy.exc import SQLAlchemyError

from app.backend.db.database import get_db, SessionLocal
from app.backend.middleware.auth import get_current_user
from app.backend.middleware.rbac import require_active_recruiter
from app.backend.services.audit_service import log_field_change, log_tenant_event
from app.backend.services.jd_quality_scorer import score_jd_quality
from app.backend.models.db_models import ScreeningResult, User, Candidate, JdCache, Tenant, SubscriptionPlan, OutcomeSkillPattern, SkillTrendSnapshot, TeamSkillProfile, RoleTemplate, ScreeningProject, ScreeningProjectCandidate, Requisition, RequisitionCandidate
from app.backend.models.schemas import (
    AnalysisResponse, BatchAnalysisResponse, BatchAnalysisResult,
    BatchFailedItem, BatchStreamEvent,
    DuplicateCandidateInfo,
    RescoreRequest,
)
from app.backend.services.constants import (
    GENERIC_SOFT_SKILLS, MUST_HAVE_CUES, NICE_TO_HAVE_CUES,
    JOB_FUNCTION_SKILL_TAXONOMY,
    RECOMMENDATION_THRESHOLDS,
)
from app.backend.services.skill_proficiency_service import (
    estimate_skill_proficiency as _estimate_skill_proficiency,
)
from app.backend.services.parser_service import parse_resume, extract_jd_text, enrich_parsed_resume_async
from app.backend.services.doc_converter import convert_to_pdf
from app.backend.services.gap_detector import analyze_gaps
from app.backend.services.hybrid_pipeline import (
    run_hybrid_pipeline,
    astream_hybrid_pipeline,
    parse_jd_rules,
    shutdown_background_tasks,
    _background_llm_narrative,
    register_background_task,
)
# RecruiterAutoTrigger feeds into the unified interview system (/api/interviews/*).
# It creates deep interview sessions via the recruiter orchestrator, which is
# also used by the unified routes/interviews.py. No functional change needed.
from app.backend.services.recruiter.auto_trigger import RecruiterAutoTrigger
from app.backend.services.fit_scorer import compute_fit_score, scalar_breakdown_score
from app.backend.services.interview_kit_generator import refresh_interview_questions_in_analysis
from app.backend.services.weight_mapper import convert_to_new_schema
from app.backend.services.skill_matcher import JD_CACHE_VERSION
from app.backend.routes.subscription import _ensure_monthly_reset, _get_plan_limits, record_usage
from app.backend.services.billing.quota import check_quota
from app.backend.services.outcome_service import compute_skill_patterns
from app.backend.services.team_service import get_team_profile
from app.backend.services.skill_trend_service import get_skill_trends

log = logging.getLogger("aria.analysis")



async def _schedule_auto_trigger(
    tenant_id: int,
    candidate_id: int,
    screening_result_id: int,
    new_status: str,
) -> None:
    """Fire-and-forget auto-trigger evaluation using a fresh DB session."""
    db = SessionLocal()
    try:
        trigger = RecruiterAutoTrigger(db)
        await trigger.evaluate_trigger(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            screening_result_id=screening_result_id,
            new_status=new_status,
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError, RuntimeError, SQLAlchemyError) as e:
        log.warning(
            "Auto-trigger evaluation failed: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
    finally:
        db.close()


ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.rtf', '.odt')

# Maximum JD size (50KB)
MAX_JD_SIZE = 50 * 1024  # 50KB

# Maximum scoring_weights size (4KB)
MAX_SCORING_WEIGHTS_SIZE = 4 * 1024  # 4KB

# ─── File content (magic bytes) validation ─────────────────────────────────────

FILE_SIGNATURES = {
    '.pdf':  [b'%PDF'],
    '.docx': [b'PK\x03\x04'],          # ZIP-based format
    '.doc':  [b'\xd0\xcf\x11\xe0'],   # OLE2 Compound Document
    '.odt':  [b'PK\x03\x04'],           # ZIP-based format (like DOCX)
    '.rtf':  [b'{\\rtf'],
    '.txt':  None,                        # No signature check for plain text
}

# PDF resource limits
MAX_PDF_PAGES = 500
PARSE_TIMEOUT_SECONDS = 30


def _validate_file_content(content: bytes, filename: str) -> None:
    """Verify that file content matches its extension via magic-byte signatures.

    Additional layers beyond the existing extension allowlist:
      1. Magic-byte check — the first bytes of the file must match the
         expected signature for the declared extension.
      2. For .txt files — heuristic check that content is not binary.

    Raises HTTPException(400) on validation failure.
    """
    from app.backend.services.file_scan_service import UnsafeFileError, validate_and_scan
    try:
        validate_and_scan(content, filename)
    except UnsafeFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ext = os.path.splitext(filename.lower())[1]
    signatures = FILE_SIGNATURES.get(ext)

    # Extension not in signature table — skip content check
    if signatures is None and ext != '.txt':
        return

    # ── .txt: heuristic binary detection ────────────────────────────────────
    if ext == '.txt':
        if not content:
            return  # empty file is acceptable for .txt
        sample = content[:1000]
        non_printable = sum(
            1 for b in sample
            if b < 0x20 and b not in (0x09, 0x0A, 0x0D)  # TAB, LF, CR
        )
        if len(sample) and non_printable / len(sample) > 0.30:
            log.warning("File signature mismatch for %s: expected %s format", filename, ext)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file content: '{filename}' does not appear to be a valid {ext} file",
            )
        return

    # ── Magic-byte check for binary formats ─────────────────────────────────
    # Empty files or files shorter than the shortest signature automatically fail
    if not content:
        log.warning("File signature mismatch for %s: expected %s format", filename, ext)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file content: '{filename}' does not appear to be a valid {ext} file",
        )

    min_sig_len = min(len(s) for s in signatures)
    if len(content) < min_sig_len:
        log.warning("File signature mismatch for %s: expected %s format", filename, ext)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file content: '{filename}' does not appear to be a valid {ext} file",
        )

    for sig in signatures:
        if content.startswith(sig):
            # For PDFs, additionally check page count to prevent resource exhaustion
            if ext == '.pdf':
                try:
                    import pdfplumber, io
                    with pdfplumber.open(io.BytesIO(content)) as _pdf:
                        if len(_pdf.pages) > MAX_PDF_PAGES:
                            raise HTTPException(
                                status_code=400,
                                detail=f"PDF exceeds maximum {MAX_PDF_PAGES} pages",
                            )
                except HTTPException:
                    raise
                except Exception as e:
                    log.warning(
                        "PDF page-count check failed for %s: %s", filename, e,
                        extra={"error_code": "VALIDATION_ERROR"},
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid file content: '{filename}' does not appear to be a valid {ext} file",
                    )
            return  # signature matches

    log.warning("File signature mismatch for %s: expected %s format", filename, ext)
    raise HTTPException(
        status_code=400,
        detail=f"Invalid file content: '{filename}' does not appear to be a valid {ext} file",
    )

# ─── Batch processing concurrency control ───────────────────────────────────────

_BATCH_SEMAPHORE = asyncio.Semaphore(int(os.getenv("BATCH_MAX_CONCURRENT", "30")))
MAX_BATCH_SIZE = 50

# ─── Per-tenant quota locks (prevents double-spend on concurrent requests) ──────

_tenant_quota_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _get_tenant_lock(tenant_id: int) -> asyncio.Lock:
    """Return the asyncio.Lock for this tenant, creating one on first access."""
    return _tenant_quota_locks[tenant_id]


# ─── JSON serialization helper ────────────────────────────────────────────────

def persist_resume_file_bytes(
    candidate,
    file_content: bytes | None,
    filename: str | None = None,
    converted_pdf_content: bytes | None = None,
) -> None:
    """Store resume bytes in object storage. Production never falls back to BYTEA."""
    from app.backend.services.object_storage import ObjectStorageService

    production = os.getenv("ENVIRONMENT", "").lower() == "production"
    use_object_storage = ObjectStorageService.is_available()

    if file_content:
        stored = False
        if use_object_storage:
            key = ObjectStorageService.build_key(candidate.tenant_id, candidate.id, filename or "resume")
            if ObjectStorageService.upload(key, file_content):
                candidate.resume_file_key = key
                candidate.resume_file_data = None
                stored = True
        if not stored:
            if production:
                raise HTTPException(
                    status_code=503,
                    detail="File storage is unavailable. Try again later.",
                )
            candidate.resume_file_data = file_content

    if converted_pdf_content:
        stored_pdf = False
        if use_object_storage:
            pdf_key = ObjectStorageService.build_key(
                candidate.tenant_id, candidate.id, filename or "resume", suffix="converted.pdf",
            )
            if ObjectStorageService.upload(pdf_key, converted_pdf_content, content_type="application/pdf"):
                candidate.resume_pdf_key = pdf_key
                candidate.resume_converted_pdf_data = None
                stored_pdf = True
        if not stored_pdf:
            if production:
                raise HTTPException(
                    status_code=503,
                    detail="File storage is unavailable. Try again later.",
                )
            candidate.resume_converted_pdf_data = converted_pdf_content


def _json_default(obj):
    """Handle non-serializable types for json.dumps (datetime, date, Decimal, bytes)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        import base64
        return base64.b64encode(obj).decode("ascii")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _populate_denormalized_columns(sr: ScreeningResult, result: dict) -> None:
    """Populate denormalized score columns on ScreeningResult from pipeline result dict.

    The pipeline computes deterministic_score, core_skill_score, domain_match_score,
    eligibility_status, and eligibility_reason but stores them only inside the
    analysis_result JSON blob.  Several consumers (voice screening UI, recruiter
    context engine, auto-trigger, project lists) read these columns directly
    without parsing the JSON blob, so they must be populated.
    """
    if not isinstance(result, dict):
        return
    try:
        # Keep deterministic_score distinct from blended fit_score / final_score.
        if "deterministic_score" in result:
            sr.deterministic_score = result.get("deterministic_score")

        skill_analysis = result.get("skill_analysis", {})
        if isinstance(skill_analysis, dict):
            sr.core_skill_score = skill_analysis.get("core_match_ratio")

        candidate_domain = result.get("candidate_domain", {})
        if isinstance(candidate_domain, dict):
            sr.domain_match_score = candidate_domain.get("confidence")

        eligibility = result.get("eligibility", {})
        if isinstance(eligibility, dict):
            sr.eligibility_status = eligibility.get("eligible")
            sr.eligibility_reason = eligibility.get("reason")
    except (ValueError, TypeError, KeyError) as e:
        log.warning(
            "Non-critical: Failed to populate denormalized columns: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )


def _should_preserve_analysis_scores(
    existing: ScreeningResult | None,
    resume_text: str,
    jd_text: str,
) -> bool:
    """Same candidate + same JD + unchanged inputs → keep prior analysis scores."""
    if existing is None:
        return False
    if existing.deterministic_score is None:
        return False
    return (
        (existing.resume_text or "").strip() == (resume_text or "").strip()
        and (existing.jd_text or "").strip() == (jd_text or "").strip()
    )


def _restore_preserved_scores(
    pipeline_result: dict,
    *,
    previous_analysis_result: str | None,
    previous_deterministic_score=None,
) -> dict:
    """Restore fit scores from a captured prior analysis JSON (never from mutated row)."""
    try:
        prior = json.loads(previous_analysis_result or "{}")
    except json.JSONDecodeError:
        prior = {}
    restored = dict(pipeline_result)
    for key in ("fit_score", "deterministic_score", "final_recommendation", "overall_score"):
        if prior.get(key) is not None:
            restored[key] = prior[key]
    if previous_deterministic_score is not None and restored.get("deterministic_score") is None:
        restored["deterministic_score"] = previous_deterministic_score
    return restored


def _is_auditable_decision(pipeline_result: dict | None) -> bool:
    if not isinstance(pipeline_result, dict) or not pipeline_result:
        return False
    return any(
        pipeline_result.get(k) is not None
        for k in ("fit_score", "deterministic_score", "final_recommendation", "overall_score")
    )


def _upsert_screening_result(
    db: Session,
    tenant_id: int,
    candidate_id: int,
    role_template_id: int | None,
    resume_text: str,
    jd_text: str,
    parsed_data: str,
    analysis_result: str,
    narrative_status: str | None = None,
    pipeline_result: dict | None = None,
    requisition_id: int | None = None,
) -> ScreeningResult:
    """Insert or update a ScreeningResult, respecting the unique constraint."""
    q = db.query(ScreeningResult).filter(
        ScreeningResult.tenant_id == tenant_id,
        ScreeningResult.candidate_id == candidate_id,
    )
    if requisition_id is not None:
        q = q.filter(ScreeningResult.requisition_id == requisition_id)
    else:
        q = q.filter(ScreeningResult.role_template_id == role_template_id)
    existing = q.first()

    if existing:
        previous_analysis_result = existing.analysis_result
        previous_deterministic_score = existing.deterministic_score
        preserve_scores = _should_preserve_analysis_scores(existing, resume_text, jd_text)
        final_pipeline = dict(pipeline_result) if pipeline_result is not None else None
        if final_pipeline is not None and preserve_scores:
            final_pipeline = _restore_preserved_scores(
                final_pipeline,
                previous_analysis_result=previous_analysis_result,
                previous_deterministic_score=previous_deterministic_score,
            )
        if final_pipeline is not None:
            persisted_analysis = json.dumps(final_pipeline, default=_json_default)
        else:
            persisted_analysis = analysis_result
        try:
            existing.resume_text = resume_text
            existing.jd_text = jd_text
            existing.parsed_data = parsed_data
            existing.analysis_result = persisted_analysis
            existing.is_active = True
            existing.version_number = (existing.version_number or 1) + 1
            existing.status_updated_at = datetime.now(timezone.utc)
            if requisition_id is not None:
                existing.requisition_id = requisition_id
            if narrative_status is not None:
                existing.narrative_status = narrative_status
            if final_pipeline is not None:
                _populate_denormalized_columns(existing, final_pipeline)
            db.flush()
            if final_pipeline is not None and _is_auditable_decision(final_pipeline):
                _write_ai_decision_log(
                    db, existing, final_pipeline, decision_type="REANALYSIS", required=True,
                )
            db.commit()
            db.refresh(existing)
            return existing
        except Exception:
            db.rollback()
            raise

    final_pipeline = dict(pipeline_result) if pipeline_result is not None else None
    persisted_analysis = (
        json.dumps(final_pipeline, default=_json_default) if final_pipeline is not None else analysis_result
    )
    new_result = ScreeningResult(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        role_template_id=role_template_id,
        requisition_id=requisition_id,
        resume_text=resume_text,
        jd_text=jd_text,
        parsed_data=parsed_data,
        analysis_result=persisted_analysis,
    )
    if final_pipeline is not None:
        _populate_denormalized_columns(new_result, final_pipeline)
    if narrative_status is not None:
        new_result.narrative_status = narrative_status

    db.add(new_result)
    db.flush()
    if final_pipeline is not None and _is_auditable_decision(final_pipeline):
        _write_ai_decision_log(
            db, new_result, final_pipeline, decision_type="INITIAL_ANALYSIS", required=True,
        )
    db.commit()
    db.refresh(new_result)
    return new_result


def _write_ai_decision_log(
    db: Session,
    result: ScreeningResult,
    pipeline_result: dict | None,
    *,
    decision_type: str = "INITIAL_ANALYSIS",
    actor_id: int | None = None,
    required: bool = True,
) -> None:
    """Persist an auditable AI decision record (GDPR Art. 22 / EU AI Act).

    Required writes share the caller's transaction: failure rolls back the
    screening mutation. Set required=False only for best-effort side channels.
    """
    from app.backend.services.ai_decision_log_service import write_log_from_pipeline

    write_log_from_pipeline(
        db,
        result,
        pipeline_result,
        decision_type=decision_type,
        actor_id=actor_id,
        required=required,
    )


def _apply_skill_overrides(jd_analysis: dict, overrides: dict | None) -> dict:
    """Apply user-specified skill overrides to jd_analysis in-place.

    Preserves original skills as ``original_required_skills`` /
    ``original_nice_to_have_skills`` and sets ``skill_overrides_applied``
    so downstream consumers can detect overrides.

    Supports proficiency-aware overrides where each skill can be a dict:
        {"skill": "Python", "proficiency": "advanced"}
    Proficiency data is stored separately in
    ``jd_analysis["skill_proficiency_requirements"]`` for downstream scoring.
    """
    if not overrides:
        return jd_analysis if isinstance(jd_analysis, dict) else {}
    if not isinstance(jd_analysis, dict):
        jd_analysis = {}

    proficiency_map: dict[str, str] = {}

    def _extract_skills(skill_list: list) -> list[str]:
        """Normalise a skill list that may contain strings or proficiency dicts."""
        result: list[str] = []
        for item in skill_list:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and "skill" in item:
                result.append(item["skill"])
                prof = item.get("proficiency")
                if isinstance(prof, str) and prof.lower() in (
                    "basic", "intermediate", "advanced", "expert",
                ):
                    proficiency_map[item["skill"].lower()] = prof.lower()
        return result

    if "required_skills" in overrides and isinstance(overrides["required_skills"], list):
        jd_analysis["original_required_skills"] = jd_analysis.get("required_skills", [])
        jd_analysis["required_skills"] = _extract_skills(overrides["required_skills"])
    if "nice_to_have_skills" in overrides and isinstance(overrides["nice_to_have_skills"], list):
        jd_analysis["original_nice_to_have_skills"] = jd_analysis.get("nice_to_have_skills", [])
        jd_analysis["nice_to_have_skills"] = _extract_skills(overrides["nice_to_have_skills"])
    jd_analysis["skill_overrides_applied"] = True

    # Store proficiency requirements separately for downstream scoring
    if proficiency_map:
        jd_analysis["skill_proficiency_requirements"] = proficiency_map
    else:
        jd_analysis.pop("skill_proficiency_requirements", None)

    log.info("Skill overrides applied: required=%d, nice_to_have=%d, proficiency_entries=%d",
             len(overrides.get("required_skills", [])),
             len(overrides.get("nice_to_have_skills", [])),
             len(proficiency_map))
    return jd_analysis


def _persist_skill_overrides_to_template(
    db: Session,
    template_id: Optional[int],
    tenant_id: int,
    parsed_skill_overrides: Optional[dict],
) -> None:
    """Persist recruiter skill overrides to the RoleTemplate and global Skill registry.

    Saves the override lists on the template so they are reused for future candidates,
    then upserts any new skill names into the global Skill DB registry so they are
    available for extraction across the system.
    """
    if not template_id or not parsed_skill_overrides:
        return
    try:
        template = db.query(RoleTemplate).filter(
            RoleTemplate.id == template_id,
            RoleTemplate.tenant_id == tenant_id,
        ).first()
        if not template:
            return
        template.required_skills_override = json.dumps(
            parsed_skill_overrides.get("required_skills", [])
        )
        template.nice_to_have_skills_override = json.dumps(
            parsed_skill_overrides.get("nice_to_have_skills", [])
        )
        db.commit()
        log.info("Persisted tenant-scoped skill overrides to template %s", template_id)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
        log.warning(
            "Failed to persist skill overrides to template: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )


# ─── JD cache helpers ─────────────────────────────────────────────────────────

def _get_or_cache_jd(db: Session, job_description: str, tenant_id: int | None = None) -> dict:
    """Parse the JD or return the cached result. Shared across all workers via DB.

    Cached entries are automatically invalidated when JD_CACHE_VERSION changes,
    ensuring stale skill-extraction results are never reused after logic updates.
    Cache keys include tenant_id so tenant skill overrides cannot leak across tenants.
    """
    jd_hash = hashlib.md5(f"{tenant_id or 0}:{job_description}".encode()).hexdigest()
    cached = db.query(JdCache).filter(JdCache.hash == jd_hash).first()
    if cached:
        try:
            parsed = json.loads(cached.result_json)
            if parsed.get("_cache_version") == JD_CACHE_VERSION:
                return parsed
            log.info("JD cache invalidated (version mismatch: cached=%s current=%s)",
                     parsed.get("_cache_version"), JD_CACHE_VERSION)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
            log.warning(
                "Non-critical: Failed to parse cached JD JSON, re-parsing: %s", e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
    jd_analysis = parse_jd_rules(job_description)
    jd_analysis["_cache_version"] = JD_CACHE_VERSION
    jd_analysis.setdefault("_profile_source", "rules")
    try:
        db.merge(JdCache(hash=jd_hash, result_json=json.dumps(jd_analysis, default=_json_default)))
        db.commit()
    except (TypeError, ValueError, SQLAlchemyError) as e:
        log.warning(
            "Non-critical: Failed to cache JD analysis: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        try:
            db.rollback()
        except SQLAlchemyError as rollback_err:
            log.warning(
                "Non-critical: Rollback also failed: %s", rollback_err,
                extra={"error_code": "DB_ERROR"},
            )
    return jd_analysis


def _link_to_project(
    db: Session,
    project_id: int,
    tenant_id: int,
    candidate_id: int,
    screening_result_id: int,
    added_by: int,
) -> None:
    """Link a candidate + screening result to a ScreeningProject.

    Non-critical: failures are logged and ignored so analysis never fails
    because of project linking.
    """
    try:
        project = db.query(ScreeningProject).filter(
            ScreeningProject.id == project_id,
            ScreeningProject.tenant_id == tenant_id,
        ).first()
        if not project:
            log.warning("Cannot link to project %s: not found for tenant %s", project_id, tenant_id)
            return

        existing = db.query(ScreeningProjectCandidate).filter(
            ScreeningProjectCandidate.project_id == project_id,
            ScreeningProjectCandidate.candidate_id == candidate_id,
        ).first()
        if existing:
            existing.screening_result_id = screening_result_id
        else:
            db.add(ScreeningProjectCandidate(
                project_id=project_id,
                candidate_id=candidate_id,
                screening_result_id=screening_result_id,
                status="pending",
                added_by=added_by,
            ))
        db.commit()
    except (ValueError, TypeError, KeyError, SQLAlchemyError) as e:
        log.warning(
            "Non-critical: Failed to link candidate to project %s: %s", project_id, e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            log.warning(
                "Non-critical: Rollback failed after project link error",
                extra={"error_code": "DB_ERROR"},
            )


def _resolve_requisition(
    db: Session,
    requisition_id: int | None,
    tenant_id: int,
    job_description: str,
    parsed_skill_overrides: dict | None,
    weights: dict | None,
    current_user: Any | None = None,
) -> tuple[int | None, str, dict | None, dict | None, int | None]:
    """Load requisition context — JD, skills, intake gate, legacy template id."""
    if not requisition_id:
        return None, job_description, parsed_skill_overrides, weights, None
    from app.backend.services.requisition_service import (
        get_calibrated_skills_for_matching,
        get_or_create_tenant_settings,
        intake_gate_blocks,
        intake_gate_message,
        ensure_legacy_role_template,
    )

    req = db.query(Requisition).filter(
        Requisition.id == requisition_id,
        Requisition.tenant_id == tenant_id,
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Requisition not found")
    ensure_legacy_role_template(db, req)
    settings = get_or_create_tenant_settings(db, tenant_id)
    if intake_gate_blocks(settings, req, db, user=current_user):
        raise HTTPException(
            status_code=400,
            detail={
                "message": intake_gate_message(settings, req, db, user=current_user),
                "error_code": "INTAKE_GATE_BLOCKED",
                "requisition_id": requisition_id,
            },
        )
    jd = req.jd_text or job_description
    skills = get_calibrated_skills_for_matching(req)
    overrides = dict(parsed_skill_overrides or {})
    overrides.setdefault("required_skills", skills.get("required_skills") or [])
    overrides.setdefault("nice_to_have_skills", skills.get("nice_to_have_skills") or [])
    if not weights and req.scoring_weights:
        try:
            weights = json.loads(req.scoring_weights)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            log.warning(
                "Non-critical: Invalid requisition scoring_weights JSON: %s", e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
    return requisition_id, jd, overrides, weights, req.legacy_role_template_id


def _enforce_screening_mode(db, tenant_id: int, requisition_id: int | None) -> None:
    from app.backend.services.requisition_service import get_or_create_tenant_settings
    settings = get_or_create_tenant_settings(db, tenant_id)
    mode = getattr(settings, "screening_mode", None) or "requisition_required"
    if mode == "requisition_required" and not requisition_id:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Your workspace requires screening through a requisition. Select an opening before analyzing.",
                "error_code": "REQUISITION_REQUIRED",
            },
        )


def _finalize_analyze_context(
    db,
    tenant_id: int,
    job_description: str,
    weights: dict | None,
    parsed_skill_overrides: dict | None,
    requisition_id: int | None,
    template_id: int | None,
    current_user: Any | None = None,
) -> tuple[str, dict | None, dict | None, int | None, int | None]:
    """Apply requisition resolution and legacy template bridge."""
    _enforce_screening_mode(db, tenant_id, requisition_id)
    req_id, jd, overrides, wts, legacy_tpl = _resolve_requisition(
        db, requisition_id, tenant_id, job_description, parsed_skill_overrides, weights,
        current_user=current_user,
    )
    tpl = template_id
    if legacy_tpl and not tpl:
        tpl = legacy_tpl
    return jd, overrides, wts, req_id, tpl


def _link_to_requisition(
    db: Session,
    requisition_id: int,
    tenant_id: int,
    candidate_id: int,
    screening_result_id: int,
    added_by: int,
) -> None:
    try:
        req = db.query(Requisition).filter(
            Requisition.id == requisition_id,
            Requisition.tenant_id == tenant_id,
        ).first()
        if not req:
            return
        existing = db.query(RequisitionCandidate).filter(
            RequisitionCandidate.requisition_id == requisition_id,
            RequisitionCandidate.candidate_id == candidate_id,
        ).first()
        if existing:
            existing.screening_result_id = screening_result_id
        else:
            db.add(RequisitionCandidate(
                requisition_id=requisition_id,
                candidate_id=candidate_id,
                screening_result_id=screening_result_id,
                added_by=added_by,
            ))
        db.commit()
    except (ValueError, TypeError, KeyError, SQLAlchemyError) as e:
        log.warning(
            "Non-critical: Failed to link candidate to requisition %s: %s", requisition_id, e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            log.warning(
                "Non-critical: Rollback failed after requisition link error",
                extra={"error_code": "DB_ERROR"},
            )


def _sync_pipeline_status_for_result(db: Session, screening_result_id: int, new_status: str) -> None:
    """Keep requisition pipeline in lockstep with screening result status."""
    allowed = {"pending", "shortlisted", "rejected", "in-review", "hired"}
    if not screening_result_id or new_status not in allowed:
        return
    rows = db.query(RequisitionCandidate).filter(
        RequisitionCandidate.screening_result_id == screening_result_id
    ).all()
    for rc in rows:
        rc.pipeline_status = new_status


# ─── Candidate deduplication & profile storage ────────────────────────────────

def _build_duplicate_info(db: Session, candidate: Candidate) -> DuplicateCandidateInfo:
    """Build the DuplicateCandidateInfo payload from an existing Candidate row."""
    last_result = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.candidate_id == candidate.id)
        .order_by(ScreeningResult.timestamp.desc())
        .first()
    )
    result_count = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.candidate_id == candidate.id)
        .count()
    )
    skills_snapshot = []
    if candidate.parsed_skills:
        try:
            skills_snapshot = json.loads(candidate.parsed_skills)[:10]
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            log.warning(
                "Non-critical: Failed to parse skills snapshot for candidate %s: %s", candidate.id, e,
                extra={"error_code": "VALIDATION_ERROR"},
            )

    return DuplicateCandidateInfo(
        id=candidate.id,
        name=candidate.name,
        email=candidate.email,
        current_role=candidate.current_role,
        current_company=candidate.current_company,
        total_years_exp=candidate.total_years_exp,
        skills_snapshot=skills_snapshot,
        result_count=result_count,
        last_analyzed=last_result.timestamp.isoformat() if last_result and last_result.timestamp else None,
        profile_quality=candidate.profile_quality,
    )


_SNAPSHOT_JSON_MAX = 500_000  # bytes of UTF-8 JSON; keeps row size bounded


def _parser_snapshot_json(parsed_data: dict) -> str | None:
    """Serialize full parser output so DB retains every field (not only pattern-derived columns)."""
    try:
        s = json.dumps(parsed_data, ensure_ascii=False, default=_json_default)
        return s[:_SNAPSHOT_JSON_MAX]
    except (TypeError, ValueError):
        return None


def _store_candidate_profile(
    candidate: Candidate,
    parsed_data: dict,
    gap_analysis: dict,
    file_hash: str,
    profile_quality: str,
    file_content: bytes | None = None,
    filename: str | None = None,
    converted_pdf_content: bytes | None = None,
    db: Session | None = None,
) -> None:
    """Write parsed profile data into the Candidate row."""
    # Track old storage bytes for incremental update
    old_raw_bytes = len((candidate.raw_resume_text or "").encode("utf-8")) if candidate.raw_resume_text else 0
    old_snapshot_bytes = len((candidate.parser_snapshot_json or "").encode("utf-8")) if candidate.parser_snapshot_json else 0

    work_exp = parsed_data.get("work_experience", [])
    candidate.resume_file_hash   = file_hash
    if filename:
        candidate.resume_filename = filename

    persist_resume_file_bytes(candidate, file_content, filename, converted_pdf_content)
    candidate.raw_resume_text    = parsed_data.get("raw_text", "")[:100000]  # cap at 100k chars
    candidate.parser_snapshot_json = _parser_snapshot_json(parsed_data)
    candidate.parsed_skills      = json.dumps(parsed_data.get("skills", []), default=_json_default)
    candidate.parsed_education   = json.dumps(parsed_data.get("education", []), default=_json_default)
    candidate.parsed_work_exp    = json.dumps(work_exp, default=_json_default)
    candidate.gap_analysis_json  = json.dumps(gap_analysis, default=_json_default)

    # Incrementally update tenant storage_used_bytes
    if db is not None:
        try:
            new_raw_bytes = len((candidate.raw_resume_text or "").encode("utf-8"))
            new_snapshot_bytes = len((candidate.parser_snapshot_json or "").encode("utf-8"))
            delta = (new_raw_bytes + new_snapshot_bytes) - (old_raw_bytes + old_snapshot_bytes)
            if delta != 0:
                from app.backend.models.db_models import Tenant as _Tenant
                tenant_row = db.query(_Tenant).filter(_Tenant.id == candidate.tenant_id).first()
                if tenant_row:
                    tenant_row.storage_used_bytes = (tenant_row.storage_used_bytes or 0) + delta
        except (TypeError, ValueError, SQLAlchemyError) as e:
            log.warning(
                "Failed to update storage_used_bytes for candidate %s: %s", candidate.id, e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )

    # Truncate current_role and current_company to 255 chars to prevent DB truncation errors
    _raw_role = work_exp[0].get("title", "") if work_exp else None
    _raw_company = work_exp[0].get("company", "") if work_exp else None
    if _raw_role and len(_raw_role) > 255:
        log.warning("Truncating current_role from %d to 255 chars", len(_raw_role))
        _raw_role = _raw_role[:255]
    if _raw_company and len(_raw_company) > 255:
        log.warning("Truncating current_company from %d to 255 chars", len(_raw_company))
        _raw_company = _raw_company[:255]
    candidate.current_role       = _raw_role
    candidate.current_company    = _raw_company

    candidate.total_years_exp    = gap_analysis.get("total_years", 0)
    candidate.profile_quality    = profile_quality
    candidate.profile_updated_at = datetime.now(timezone.utc)
    if not candidate.name:
        candidate.name = parsed_data.get("contact_info", {}).get("name")
    if not candidate.email:
        candidate.email = parsed_data.get("contact_info", {}).get("email")
    if not candidate.phone:
        candidate.phone = parsed_data.get("contact_info", {}).get("phone")


def _get_or_create_candidate(
    db: Session,
    parsed_data: dict,
    tenant_id: int,
    file_hash: str | None = None,
    gap_analysis: dict | None = None,
    profile_quality: str = "medium",
    action: str | None = None,
    file_content: bytes | None = None,
    filename: str | None = None,
    converted_pdf_content: bytes | None = None,
    resume_text: str | None = None,
) -> tuple[int, bool]:
    """
    4-layer deduplication. Returns (candidate_id, is_duplicate).

    action values:
      None / unrecognised  → deduplicate, return duplicate_info in result
      "use_existing"       → load stored profile, skip re-parse (caller's responsibility)
      "update_profile"     → update existing candidate's stored profile
      "create_new"         → skip all dedup, always create new row
    """
    contact = parsed_data.get("contact_info", {})
    email   = contact.get("email")
    name    = contact.get("name")
    phone   = contact.get("phone")

    existing: Candidate | None = None

    if action != "create_new":
        # Layer 1 — email match
        if email:
            existing = db.query(Candidate).filter(
                Candidate.email    == email,
                Candidate.tenant_id == tenant_id,
            ).first()

        # Layer 2 — file hash match
        if existing is None and file_hash:
            existing = db.query(Candidate).filter(
                Candidate.resume_file_hash == file_hash,
                Candidate.tenant_id        == tenant_id,
            ).first()

        # Layer 2b — content hash match (normalized resume text)
        if existing is None and resume_text:
            try:
                from app.backend.services.dedup_service import compute_resume_hash
                content_hash = compute_resume_hash(resume_text)
                if content_hash:
                    existing = db.query(Candidate).filter(
                        Candidate.resume_file_hash == content_hash,
                        Candidate.tenant_id        == tenant_id,
                    ).first()
            except (ValueError, TypeError, OSError, RuntimeError, SQLAlchemyError) as e:
                log.warning(
                    "Non-critical: content hash match failed: %s", e,
                    extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
                )

        # Layer 3 — name + phone
        if existing is None and name and phone:
            existing = db.query(Candidate).filter(
                Candidate.name      == name,
                Candidate.phone     == phone,
                Candidate.tenant_id == tenant_id,
            ).first()

    if existing is not None:
        # Update profile when explicitly requested
        if action == "update_profile" and gap_analysis is not None:
            _store_candidate_profile(existing, parsed_data, gap_analysis, file_hash or "", profile_quality, file_content, filename, converted_pdf_content, db=db)
        return existing.id, True

    # Create new candidate
    candidate = Candidate(
        tenant_id=tenant_id,
        name=name,
        email=email,
        phone=phone,
    )
    db.add(candidate)
    db.flush()  # get the new id

    if gap_analysis is not None:
        _store_candidate_profile(candidate, parsed_data, gap_analysis, file_hash or "", profile_quality, file_content, filename, converted_pdf_content, db=db)

    return candidate.id, False


# ─── Misc helpers ─────────────────────────────────────────────────────────────

def _fallback_result(gap_analysis: dict) -> dict:
    return {
        "fit_score": None, "job_role": None,
        "strengths": [], "weaknesses": [],
        "employment_gaps": gap_analysis.get("employment_gaps", []),
        "education_analysis": None,
        "risk_signals": [{"type": "analysis", "severity": "low",
                          "description": "Automated analysis unavailable — manual review required"}],
        "final_recommendation": "Pending",
        "score_breakdown": {}, "matched_skills": [], "missing_skills": [],
        "risk_level": None, "interview_questions": None,
        "required_skills_count": 0, "work_experience": [], "contact_info": {},
        "jd_analysis": {}, "candidate_profile": {}, "skill_analysis": {},
        "edu_timeline_analysis": {}, "explainability": {}, "adjacent_skills": [],
        "pipeline_errors": ["Pipeline unavailable"],
        "analysis_quality": "low", "narrative_pending": False,
        "deterministic_score": None,
        "decision_explanation": None,
        "jd_domain": None,
        "candidate_domain": None,
        "eligibility": None,
        "deterministic_features": None,
    }


def _resolve_jd(
    job_description: str | None,
    job_file_bytes: bytes | None,
    job_filename: str | None,
) -> str:
    if job_file_bytes and job_filename:
        try:
            extracted = extract_jd_text(job_file_bytes, job_filename)
            if extracted.strip():
                return extracted
        except (ValueError, TypeError, OSError, RuntimeError, KeyError) as e:
            log.warning(
                "Non-critical: JD file extraction failed: %s", e,
                extra={"error_code": "IO_ERROR" if isinstance(e, OSError) else "VALIDATION_ERROR"},
            )
    if not (job_description and job_description.strip()):
        raise HTTPException(status_code=400, detail="Job description (text or file) is required")
    return job_description


def _check_jd_length(job_description: str) -> None:
    """Reject JD that is too short to produce meaningful analysis."""
    if len(job_description.split()) < 80:
        raise HTTPException(
            status_code=400,
            detail=(
                "Job description is too brief (under 80 words). "
                "Please include the role title, required skills, and years of experience "
                "for accurate matching."
            ),
        )


def _check_jd_size(job_description: str) -> None:
    """Reject JD that exceeds maximum size limit."""
    if job_description and len(job_description.encode('utf-8')) > MAX_JD_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Job description exceeds maximum size of 50KB"
        )


def _build_phase3_context(
    db: Session,
    tenant_id: int,
    jd_analysis: dict,
    team_id: Optional[str] = None,
) -> Optional[dict]:
    """Build Phase 3 scoring context (outcome patterns, skill trends, team gaps).

    Returns None if no Phase 3 data is available, so scoring falls back to
    the default behaviour.  Failures are caught and logged — never break scoring.
    """
    try:
        all_jd_skills = [s.lower().strip() for s in (
            jd_analysis.get("required_skills", []) +
            jd_analysis.get("nice_to_have_skills", [])
        ) if isinstance(s, str)]

        # 1. Outcome patterns
        outcome_patterns = []
        if all_jd_skills:
            patterns = db.query(OutcomeSkillPattern).filter(
                OutcomeSkillPattern.tenant_id == tenant_id,
                OutcomeSkillPattern.skill_name.in_(all_jd_skills),
            ).all()
            for p in patterns:
                outcome_patterns.append({
                    "skill": p.skill_name,
                    "success_rate": (p.present_in_hired_pct / 100) if p.present_in_hired_pct else 0.5,
                    "sample_size": p.total_outcomes or 0,
                })

        # 2. Skill trends
        skill_trends = []
        if all_jd_skills:
            latest_date = db.query(func.max(SkillTrendSnapshot.period_date)).filter(
                SkillTrendSnapshot.tenant_id == tenant_id,
            ).scalar()
            if latest_date:
                snapshots = db.query(SkillTrendSnapshot).filter(
                    SkillTrendSnapshot.tenant_id == tenant_id,
                    SkillTrendSnapshot.period_date == latest_date,
                    SkillTrendSnapshot.skill_name.in_(all_jd_skills),
                ).all()
                for snap in snapshots:
                    skill_trends.append({
                        "skill": snap.skill_name,
                        "direction": snap.trend_direction or "stable",
                        "growth_pct": snap.growth_pct or 0,
                    })

        # 3. Team gaps
        team_gaps = []
        if team_id:
            try:
                profile = get_team_profile(db, int(team_id), tenant_id)
                if profile and profile.skills_json:
                    team_skills_raw = json.loads(profile.skills_json)
                    team_skill_names = set(
                        e.get("skill", "").lower().strip()
                        for e in team_skills_raw
                        if e.get("skill")
                    )
                    team_gaps = [s for s in all_jd_skills if s not in team_skill_names]
            except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as exc:
                log.warning(
                    "team_gaps query failed in _build_phase3_context: %s", exc,
                    extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "VALIDATION_ERROR"},
                )

        # Only return context if there's something useful
        if outcome_patterns or skill_trends or team_gaps:
            return {
                "team_gaps": team_gaps,
                "skill_trends": skill_trends,
                "outcome_patterns": outcome_patterns,
            }
        return None
    except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
        log.warning(
            "Phase 3 context retrieval failed: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        return None


# ─── JD Parse Preview helpers ────────────────────────────────────────────────

def _enrich_skills_with_confidence(
    skills: list[str],
    jd_text: str,
    is_nice_to_have: bool = False,
    seniority: str = "mid",
) -> list[dict]:
    """Post-process skill list to add confidence/source and proficiency metadata
    based on linguistic cues in the original JD text.

    Heuristic rules:
      - Nice-to-have near preferred/bonus/plus cues  → high / preferred_section
      - Required near must-have/required/essential    → high / explicit_requirement
      - Inferred from Qualifications/Requirements hdr → medium / qualifications_section
      - Default                                         → medium / inferred
    """
    jd_lower = jd_text.lower()

    # Pre-compute section boundaries for "Qualifications" / "Requirements" headers
    _SECTION_HEADERS = [
        "qualifications", "requirements", "what you'll need",
        "what we're looking for", "job requirements",
        "minimum qualifications", "basic qualifications",
        "preferred qualifications", "desired qualifications",
    ]
    section_ranges: list[tuple[int, int]] = []
    for hdr in _SECTION_HEADERS:
        idx = jd_lower.find(hdr)
        if idx >= 0:
            # Section extends to the next header-like line or end of text
            end = len(jd_text)
            for nxt in _SECTION_HEADERS:
                nxt_idx = jd_lower.find(nxt, idx + len(hdr))
                if nxt_idx > idx and nxt_idx < end:
                    end = nxt_idx
            section_ranges.append((idx, end))

    def _skill_in_section(skill_name: str) -> bool:
        """Check if the skill appears within a known section header range."""
        s_lower = skill_name.lower()
        for start, end in section_ranges:
            if s_lower in jd_lower[start:end]:
                return True
        return False

    enriched = []
    for skill in skills:
        skill_lower = skill.lower()

        # Determine surrounding context (±120 chars around the skill mention)
        pos = jd_lower.find(skill_lower)
        context = ""
        if pos >= 0:
            ctx_start = max(0, pos - 120)
            ctx_end = min(len(jd_text), pos + len(skill_lower) + 120)
            context = jd_lower[ctx_start:ctx_end].lower()

        if is_nice_to_have:
            # Nice-to-have: check for preferred/bonus cues
            if any(cue in context for cue in NICE_TO_HAVE_CUES):
                confidence, source = "high", "preferred_section"
            elif _skill_in_section(skill):
                confidence, source = "medium", "qualifications_section"
            else:
                confidence, source = "medium", "inferred"
        else:
            # Required: check for must-have/required/essential cues
            if any(cue in context for cue in MUST_HAVE_CUES):
                confidence, source = "high", "explicit_requirement"
            elif _skill_in_section(skill):
                confidence, source = "medium", "qualifications_section"
            else:
                confidence, source = "medium", "inferred"

        proficiency = _estimate_skill_proficiency(
            skill, seniority, jd_text, is_nice_to_have=is_nice_to_have,
        )
        enriched.append({
            "skill": skill,
            "confidence": confidence,
            "source": source,
            "proficiency_expected": proficiency,
        })

    return enriched



def _get_excluded_skills(
    jd_text: str,
    required_skills: list[str],
    nice_to_have_skills: list[str],
) -> list[str]:
    """Return skills that were detected in the JD but excluded because they
    are generic soft skills (per GENERIC_SOFT_SKILLS constant)."""
    jd_lower = jd_text.lower()
    excluded: list[str] = []
    for soft in GENERIC_SOFT_SKILLS:
        if soft in jd_lower and soft not in (s.lower() for s in required_skills) and soft not in (s.lower() for s in nice_to_have_skills):
            excluded.append(soft)
    return excluded


def _get_suggested_additions(
    job_function: str,
    required_skills: list[str],
    nice_to_have_skills: list[str],
    role_title: str,
) -> list[str]:
    """Return common skills for the detected job_function that are not already
    in required_skills or nice_to_have_skills.

    Tries O*NET first; falls back to JOB_FUNCTION_SKILL_TAXONOMY mapping.
    """
    existing = {s.lower() for s in required_skills + nice_to_have_skills}

    # ── Attempt O*NET lookup ───────────────────────────────────────────────
    try:
        from app.backend.services.onet.onet_validator import ONETValidator
        validator = ONETValidator()
        if validator.available and role_title:
            occ = validator.resolve_occupation(role_title)
            if occ:
                occ_skills = validator.get_expected_skills(occ["soc_code"])
                hot_skills = [
                    s["skill_name"]
                    for s in occ_skills
                    if s.get("is_hot_technology") or s.get("is_in_demand")
                ]
                suggestions = [
                    s for s in hot_skills
                    if s.lower() not in existing
                ]
                if suggestions:
                    return suggestions[:8]
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
        log.debug(
            "O*NET lookup failed, falling back to taxonomy: %s", e,
            extra={"error_code": "UPSTREAM_ERROR"},
        )

    # ── Fallback: JOB_FUNCTION_SKILL_TAXONOMY mapping ──────────────────────
    taxonomy = JOB_FUNCTION_SKILL_TAXONOMY.get(job_function, {})
    core = taxonomy.get("core_skills", [])
    adjacent = taxonomy.get("adjacent_skills", [])
    candidates = core + adjacent
    suggestions = [s for s in candidates if s.lower() not in existing]
    return suggestions[:8]


def _enrich_skills_with_market_data(
    skills_list: list, role_title: str
) -> tuple[list, dict]:
    """Enrich skills with O*NET market intelligence (hot/demand flags, category).

    Returns (enriched_skills_list, market_summary).
    If O*NET is unavailable, market fields are set to None and market_summary
    contains an error message.
    """
    try:
        from app.backend.services.onet.onet_validator import ONETValidator

        validator = ONETValidator()
        if not validator.available or not role_title:
            raise RuntimeError("O*NET unavailable or no role title")

        # Extract skill names from the enriched skill objects
        skill_names = [
            s["skill"] for s in skills_list if isinstance(s, dict) and s.get("skill")
        ]

        # Batch validate against the role title's occupation
        batch_result = validator.validate_skills_batch(skill_names, role_title)

        # Build a commodity_title lookup from occupation skills
        # (validate_skills_batch doesn't include commodity_title)
        commodity_lookup: dict[str, str] = {}
        soc_code = batch_result.get("soc_code")
        if soc_code:
            occ_skills = validator.get_expected_skills(soc_code)
            for occ_s in occ_skills:
                commodity_lookup[occ_s["skill_name"].lower()] = (
                    occ_s.get("commodity_title") or "Unclassified"
                )

        # Index validation results by skill name (case-insensitive)
        validated_lookup: dict[str, dict] = {}
        for v in batch_result.get("validated", []):
            if v.get("skill"):
                validated_lookup[v["skill"].lower()] = v

        # Enrich each skill with market flags
        hot_count = 0
        in_demand_count = 0
        rare_skills: list[str] = []

        for skill_obj in skills_list:
            skill_name = skill_obj.get("skill", "")
            key = skill_name.lower()

            v = validated_lookup.get(key)
            if v and v.get("recognized"):
                skill_obj["is_hot"] = v["is_hot"]
                skill_obj["is_in_demand"] = v["is_in_demand"]
                skill_obj["category"] = commodity_lookup.get(key, "Unclassified")

                if v["is_hot"]:
                    hot_count += 1
                if v["is_in_demand"]:
                    in_demand_count += 1
            else:
                # Not found in O*NET
                skill_obj["is_hot"] = False
                skill_obj["is_in_demand"] = False
                skill_obj["category"] = "Unclassified"
                rare_skills.append(skill_name)

        # Compute market alignment ratio
        total = max(len(skills_list), 1)
        demand_ratio = in_demand_count / total
        if demand_ratio > 0.7:
            alignment = "high"
        elif demand_ratio >= 0.4:
            alignment = "medium"
        else:
            alignment = "low"

        market_summary = {
            "hot_skills_count": hot_count,
            "in_demand_count": in_demand_count,
            "rare_skills": rare_skills,
            "market_alignment": alignment,
        }

        return skills_list, market_summary

    except (ImportError, OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
        log.warning(
            "O*NET market data unavailable: %s", e,
            extra={"error_code": "UPSTREAM_ERROR"},
        )
        # O*NET unavailable — set all market fields to None
        for skill_obj in skills_list:
            skill_obj["is_hot"] = None
            skill_obj["is_in_demand"] = None
            skill_obj["category"] = None

        market_summary = {"error": "Market data unavailable"}
        return skills_list, market_summary



def _check_scoring_weights_size(scoring_weights: str | None) -> None:
    """Reject scoring_weights that exceeds maximum size limit."""
    if scoring_weights and len(scoring_weights.encode('utf-8')) > MAX_SCORING_WEIGHTS_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Scoring weights exceed maximum size of 4KB"
        )


def _parse_user_scoring_weights(scoring_weights: str | None) -> dict | None:
    if not scoring_weights:
        return None
    try:
        weights = json.loads(scoring_weights)
    except json.JSONDecodeError:
        return None
    from app.backend.services.scoring_weights import ScoringWeightError, canonicalize_scoring_weights
    try:
        return canonicalize_scoring_weights(weights, strict=True)
    except ScoringWeightError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_optional_analyze_payloads(
    scoring_weights: str | None,
    skill_overrides: str | None = None,
) -> None:
    """Run payload-size checks before usage is incremented."""
    _check_scoring_weights_size(scoring_weights)
    if skill_overrides and len(skill_overrides.encode("utf-8")) > MAX_SCORING_WEIGHTS_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Skill overrides exceed maximum size of 4KB",
        )


def _assert_custom_weights_allowed(db: Session, tenant_id: int) -> None:
    """Reject custom scoring weights when the tenant's plan does not include them."""
    from app.backend.services.feature_flag_service import is_feature_enabled
    from app.backend.services.plan_entitlement_service import plan_feature_detail

    if not is_feature_enabled(db, tenant_id, "custom_weights"):
        detail = plan_feature_detail(db, tenant_id, "custom_weights")
        raise HTTPException(
            status_code=403,
            detail={
                "detail": detail.get("upgrade_hint") or "Custom scoring weights are not available on your plan",
                "error_code": "PLAN_FEATURE_LOCKED",
                "feature": "custom_weights",
                "plan": detail.get("plan"),
            },
        )


def _assert_custom_weights_allowed_if_provided(
    db: Session, tenant_id: int, scoring_weights: str | None
) -> None:
    if scoring_weights:
        _assert_custom_weights_allowed(db, tenant_id)


async def _parse_resume_with_doc_conversion(content: bytes, filename: str) -> tuple[dict, bytes | None]:
    """Parse resume with automatic DOC-to-PDF conversion for better accuracy.

    Returns:
        (parsed_data, converted_pdf_bytes or None)
    """
    ext = os.path.splitext(filename.lower())[1]
    pdf_bytes = None

    if ext == ".doc":
        pdf_bytes = await asyncio.to_thread(convert_to_pdf, content, filename)
        if pdf_bytes:
            log.info("DOC converted to PDF (%d bytes), parsing from PDF for better accuracy", len(pdf_bytes))
            parsed_data = await asyncio.wait_for(
                asyncio.to_thread(parse_resume, pdf_bytes, "converted.pdf"),
                timeout=PARSE_TIMEOUT_SECONDS,
            )
            await enrich_parsed_resume_async(parsed_data, filename)
            _reject_injected_text(parsed_data.get("raw_text") or "")
            return parsed_data, pdf_bytes
        log.warning("DOC-to-PDF conversion failed for %s, falling back to legacy parser", filename)

    try:
        parsed_data = await asyncio.wait_for(
            asyncio.to_thread(parse_resume, content, filename),
            timeout=PARSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=400,
            detail="Resume parsing timed out — file may be too complex or contain too many pages",
        )
    except Exception as exc:
        # pdfminer.PDFSyntaxError and similar parser failures are not ValueError
        if "pdfminer" in type(exc).__module__ or type(exc).__name__ in {
            "PDFSyntaxError", "PDFException", "PdfminerException",
        }:
            raise ValueError("Could not parse this PDF") from exc
        raise
    await enrich_parsed_resume_async(parsed_data, filename)
    _reject_injected_text(parsed_data.get("raw_text") or "")
    return parsed_data, pdf_bytes


def _reject_injected_text(text: str) -> None:
    from app.backend.services.guardrail_service import detect_prompt_injection
    is_inj, confidence, _matches = detect_prompt_injection(text or "")
    if is_inj and confidence >= 0.75:
        raise HTTPException(
            status_code=400,
            detail="Document failed security screening and cannot be processed",
        )


def _is_parse_failure_result(raw: object) -> bool:
    """True when resume parse failed and the pipeline returned a low-quality error dict."""
    if not isinstance(raw, dict):
        return True
    errors = raw.get("pipeline_errors") or []
    return bool(errors) and raw.get("analysis_quality") == "low"


async def _process_single_resume(
    content: bytes,
    filename: str,
    job_description: str,
    scoring_weights: dict | None,
    db: Session | None = None,
    skill_overrides: dict | None = None,
) -> dict:
    """Core analysis logic — parse in thread, run Python scoring, return result.

    Returns Python scoring results with a fallback narrative. The caller
    (batch endpoint) is responsible for spawning a background LLM task
    after persisting the ScreeningResult to DB.
    """
    # Parse resume in thread pool (blocks event loop otherwise for large PDFs)
    pdf_bytes = None
    try:
        parsed_data, pdf_bytes = await _parse_resume_with_doc_conversion(content, filename)
    except ValueError as e:
        # Scanned PDF or unreadable file — return graceful error
        return {
            **_fallback_result({}),
            "pipeline_errors": [str(e)],
            "analysis_quality": "low",
        }
    except HTTPException as e:
        log.warning(
            "Resume parse rejected for %s: %s", filename, e.detail,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        return {
            **_fallback_result({}),
            "pipeline_errors": [str(e.detail)],
            "analysis_quality": "low",
        }
    except (OSError, TypeError, RuntimeError, KeyError) as e:
        log.warning(
            "Resume parse error for %s: %s", filename, e,
            extra={"error_code": "IO_ERROR" if isinstance(e, OSError) else "VALIDATION_ERROR"},
        )
        return {
            **_fallback_result({}),
            "pipeline_errors": [f"Parse error: {str(e)}"],
        }

    work_exp     = parsed_data.get("work_experience", [])
    gap_analysis = analyze_gaps(work_exp)

    # Cached JD parse
    jd_analysis = None
    if db is not None:
        try:
            jd_analysis = _get_or_cache_jd(db, job_description)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
            log.warning(
                "Non-critical: JD cache fetch failed: %s", e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )

    if skill_overrides:
        _apply_skill_overrides(jd_analysis, skill_overrides)

    try:
        from app.backend.services.hybrid_pipeline import (
            _run_python_phase,
            _build_fallback_narrative,
            _merge_llm_into_result,
        )
        result = _run_python_phase(
            resume_text=parsed_data["raw_text"],
            job_description=job_description,
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=scoring_weights,
            jd_analysis=jd_analysis,
        )
        # Preserve internal _scores before merge (needed for background LLM spawn)
        _scores = result.get("_scores", {})
        fallback = _build_fallback_narrative(result, result.get("skill_analysis", {}))
        result = _merge_llm_into_result(result, fallback)
        result["_scores"] = _scores
        result["narrative_pending"] = True
        log.info("Fast batch path for %s: fit_score=%s (LLM deferred)",
                 filename, result.get("fit_score"))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError, RuntimeError) as e:
        log.warning(
            "Pipeline error for %s: %s", filename, e,
            extra={"error_code": "UPSTREAM_ERROR" if isinstance(e, (OSError, RuntimeError)) else "VALIDATION_ERROR"},
        )
        result = _fallback_result(gap_analysis)
        result["pipeline_errors"] = [f"Pipeline error: {str(e)}"]

    result["_parsed_data"]  = parsed_data
    result["_gap_analysis"] = gap_analysis
    result["_pdf_bytes"]    = pdf_bytes  # DOC-to-PDF conversion result (if applicable)
    return result



# ─── Single resume analysis (non-streaming, JSON response) ────────────────────

def _check_and_increment_usage(db: Session, tenant_id: int, user_id: int, quantity: int = 1) -> tuple[bool, str]:
    """Check usage limits and increment counter atomically. Returns (allowed, message).
    
    Uses atomic UPDATE to prevent race conditions:
    - For limited plans: UPDATE ... SET count = count + 1 WHERE count + quantity <= limit
    - Checks affected rows to determine if limit was reached
    - Uses SAVEPOINT to isolate quota-check failure from the rest of the session
      (avoids full rollback that would lose other pending session state)
    """
    from app.backend.services.plan_entitlement_service import get_tenant_plan

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return False, "Tenant not found"
    
    plan = get_tenant_plan(db, tenant_id)
    
    analyses_limit = None
    if plan:
        limits = _get_plan_limits(plan)
        analyses_limit = limits.get("analyses_per_month", 20)
    
    # If unlimited, just increment without check
    if analyses_limit is None or analyses_limit < 0:
        _ensure_monthly_reset(tenant)
        success = record_usage(db, tenant_id, user_id, "resume_analysis", quantity)
        if not success:
            return False, "Failed to record usage"
        return True, ""
    
    # Atomic increment with limit check — the ONLY write path for the counter.
    # _ensure_monthly_reset is applied inside a SAVEPOINT so that a quota
    # failure only rolls back the savepoint, not the entire session.
    savepoint = db.begin_nested()
    try:
        _ensure_monthly_reset(tenant)
        db.flush()  # Apply reset to DB so atomic UPDATE sees current count
        
        result = db.execute(
            update(Tenant)
            .where(
                Tenant.id == tenant_id,
                Tenant.analyses_count_this_month + quantity <= analyses_limit
            )
            .values(
                analyses_count_this_month=Tenant.analyses_count_this_month + quantity
            )
            .execution_options(synchronize_session=False)
        )
        
        # Check if the update affected any rows
        if result.rowcount == 0:
            savepoint.rollback()
            # Re-read tenant for accurate remaining count (post-savepoint rollback)
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                _ensure_monthly_reset(tenant)
                remaining = analyses_limit - tenant.analyses_count_this_month
                return False, f"Monthly analysis limit exceeded. Remaining: {remaining}, Requested: {quantity}. Please upgrade your plan."
            return False, "Monthly analysis limit exceeded. Please upgrade your plan."
        
        savepoint.commit()
    except (SQLAlchemyError, OSError, RuntimeError, ValueError, TypeError):
        savepoint.rollback()
        raise
    
    # Log the usage
    from app.backend.models.db_models import UsageLog
    usage_log = UsageLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action="resume_analysis",
        quantity=quantity,
        details=None,
    )
    db.add(usage_log)
    db.commit()
    
    # ── Check usage thresholds (non-blocking) ──────────────────────────────────
    try:
        if analyses_limit and analyses_limit > 0:
            from app.backend.services.usage_alert_service import usage_alert_service
            # Re-read tenant for current count after commit
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                usage_alert_service.check_and_alert(
                    db, tenant_id, "analyses_per_month",
                    tenant.analyses_count_this_month, analyses_limit,
                )
    except (OSError, RuntimeError, ValueError, TypeError, SQLAlchemyError) as e:
        log.warning(
            "Usage alert check failed: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "UPSTREAM_ERROR"},
        )
    
    return True, ""


def _release_analysis_quota(db: Session, tenant_id: int, quantity: int = 1) -> None:
    db.execute(
        update(Tenant)
        .where(
            Tenant.id == tenant_id,
            Tenant.analyses_count_this_month >= quantity,
        )
        .values(analyses_count_this_month=Tenant.analyses_count_this_month - quantity)
        .execution_options(synchronize_session=False)
    )


def release_job_analysis_quota(db: Session, job) -> None:
    """Release a reserved analysis unit when a queued job is cancelled or permanently failed."""
    from sqlalchemy.orm.attributes import flag_modified

    cfg = dict(job.job_config or {})
    if not cfg.get("quota_reserved") or cfg.get("quota_released"):
        return
    _release_analysis_quota(db, job.tenant_id, 1)
    cfg["quota_released"] = True
    job.job_config = cfg
    flag_modified(job, "job_config")


def require_explicit_use_existing_candidate(
    db: Session,
    tenant_id: int,
    action: str | None,
    candidate_id: int | None,
    resume_file_hash: str | None = None,
) -> None:
    from fastapi import HTTPException
    from app.backend.models.db_models import Candidate

    if action != "use_existing":
        return
    if not candidate_id:
        raise HTTPException(
            status_code=409,
            detail="use_existing requires an explicit candidate_id",
        )
    existing = (
        db.query(Candidate)
        .filter(Candidate.id == candidate_id, Candidate.tenant_id == tenant_id)
        .first()
    )
    if not existing:
        raise HTTPException(
            status_code=409,
            detail="Candidate not found for use_existing",
        )
    if resume_file_hash is None:
        return
    stored = (existing.resume_file_hash or "").strip().lower()
    incoming = resume_file_hash.strip().lower()
    if not stored or stored != incoming:
        raise HTTPException(
            status_code=409,
            detail="use_existing candidate does not match the uploaded resume",
        )


# ─── Batch resume analysis ────────────────────────────────────────────────────

async def _process_with_semaphore(
    content: bytes,
    filename: str,
    job_description: str,
    scoring_weights: dict | None,
    db: Session | None = None,
    skill_overrides: dict | None = None,
) -> dict:
    """Wrap resume processing with semaphore for concurrency control."""
    async with _BATCH_SEMAPHORE:
        return await _process_single_resume(
            content, filename, job_description, scoring_weights, db, skill_overrides,
        )


def _spawn_background_narrative(
    result: dict,
    screening_result_id: int,
    tenant_id: int,
) -> None:
    """Build llm_context from Python result and spawn background LLM narrative task."""
    llm_context = {
        "jd_analysis":       result.get("jd_analysis", {}),
        "candidate_profile": result.get("candidate_profile", {}),
        "skill_analysis":    result.get("skill_analysis", {}),
        "scores": {
            **result.get("_scores", {}),
            "fit_score":            result.get("fit_score"),
            "final_recommendation": result.get("final_recommendation"),
        },
        "score_rationales":  result.get("score_rationales", {}),
        "risk_summary":      result.get("risk_summary", {}),
        "skill_depth":       result.get("skill_depth", {}),
    }
    # Strip internal keys for background task
    python_result = {k: v for k, v in result.items() if not k.startswith("_")}

    task = asyncio.create_task(
        _background_llm_narrative(
            screening_result_id=screening_result_id,
            tenant_id=tenant_id,
            llm_context=llm_context,
            python_result=python_result,
        )
    )
    register_background_task(task)

