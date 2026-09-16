"""
Resume analysis routes — hybrid pipeline (Python scoring + single LLM narrative).

Key changes vs old LangGraph version:
  - Uses run_hybrid_pipeline / astream_hybrid_pipeline instead of agent_pipeline
  - 3-layer candidate deduplication (email → file hash → name+phone)
  - Full candidate profile stored in Candidate row on every new/updated analysis
  - DB-shared JD cache (all 4 workers share the same parsed JD result)
  - JD minimum content check (< 80 words rejected)
  - asyncio.to_thread for blocking PDF parse
  - Structured JSON logging per analysis
"""

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
from app.backend.middleware.auth import get_current_user, require_feature
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
from app.backend.services.plan_entitlement_service import get_tenant_plan
from app.backend.services.billing.quota import check_quota
from app.backend.services.outcome_service import compute_skill_patterns
from app.backend.services.team_service import get_team_profile
from app.backend.services.skill_trend_service import get_skill_trends

from app.backend.services.screening_command import build_screening_command, execute_screening
from app.backend.routes.analyze_helpers import (
    ALLOWED_EXTENSIONS,
    MAX_BATCH_SIZE,
    MAX_JD_SIZE,
    MAX_SCORING_WEIGHTS_SIZE,
    FILE_SIGNATURES,
    MAX_PDF_PAGES,
    PARSE_TIMEOUT_SECONDS,
    _schedule_auto_trigger,
    _validate_file_content,
    _get_tenant_lock,
    _json_default,
    _populate_denormalized_columns,
    _should_preserve_analysis_scores,
    _restore_preserved_scores,
    _upsert_screening_result,
    _write_ai_decision_log,
    _apply_skill_overrides,
    _persist_skill_overrides_to_template,
    _get_or_cache_jd,
    _link_to_project,
    _resolve_requisition,
    _enforce_screening_mode,
    _finalize_analyze_context,
    _link_to_requisition,
    _build_duplicate_info,
    _parser_snapshot_json,
    _store_candidate_profile,
    _get_or_create_candidate,
    _fallback_result,
    _resolve_jd,
    _check_jd_length,
    _check_jd_size,
    _build_phase3_context,
    _enrich_skills_with_confidence,
    _get_excluded_skills,
    _get_suggested_additions,
    _enrich_skills_with_market_data,
    _check_scoring_weights_size,
    _validate_optional_analyze_payloads,
    _assert_custom_weights_allowed,
    _assert_custom_weights_allowed_if_provided,
    _parse_resume_with_doc_conversion,
    _reject_injected_text,
    _process_single_resume,
    _is_parse_failure_result,
    _check_and_increment_usage,
    require_explicit_use_existing_candidate,
    _process_with_semaphore,
    _spawn_background_narrative,
)

router = APIRouter(prefix="/api", tags=["analysis"])
log    = logging.getLogger("aria.analysis")


def _persist_via_screening_command(
    db,
    *,
    tenant_id: int,
    user_id: int,
    resume_text: str,
    jd_text: str,
    parsed_data: dict,
    pipeline_result: dict | None,
    filename: str,
    file_hash: str | None = None,
    file_content: bytes | None = None,
    gap_analysis: dict | None = None,
    requisition_id: int | None = None,
    role_template_id: int | None = None,
    scoring_weights: dict | None = None,
    skill_overrides: dict | None = None,
    action: str | None = None,
    converted_pdf_content: bytes | None = None,
    candidate_id: int | None = None,
):
    cmd = build_screening_command(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        resume_hash=hashlib.sha256((resume_text or "").encode("utf-8")).hexdigest(),
        jd_hash=hashlib.sha256((jd_text or "").encode("utf-8")).hexdigest(),
        candidate_id=candidate_id,
        requisition_id=requisition_id,
        role_template_id=role_template_id,
        scoring_weights=scoring_weights,
        skill_overrides=skill_overrides,
    )
    db_result, is_dup = execute_screening(
        db,
        cmd,
        resume_text=resume_text or "",
        jd_text=jd_text or "",
        parsed_data=parsed_data or {},
        pipeline_result=pipeline_result or {},
        file_hash=file_hash,
        filename=filename,
        file_content=file_content,
        gap_analysis=gap_analysis,
        action=action,
        converted_pdf_content=converted_pdf_content,
    )
    return db_result, is_dup



# ─── JD Parse Preview Endpoint ────────────────────────────────────────────────

@router.post("/jd/parse-preview")
async def jd_parse_preview(
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    team_id: Optional[str] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Preview parsed JD structure with confidence metadata per skill.

    Accepts either ``job_description`` text or a ``job_file`` upload.
    Reuses the existing JD parser pipeline (with caching via JdCache).
    Returns enriched skill lists with confidence/source, excluded soft skills,
    and suggested additions based on the detected job function.
    """
    # Resolve JD text from form or file
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        if len(jd_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Job description file too large (max 5MB)")
        jd_name = job_file.filename

    jd_text = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(jd_text)
    _check_jd_size(jd_text)

    # Use the same caching logic as /api/analyze
    jd_analysis = _get_or_cache_jd(db, jd_text)

    # Enrich skills with confidence/source metadata
    seniority = jd_analysis.get("seniority", "mid")
    required_skills_enriched = _enrich_skills_with_confidence(
        jd_analysis.get("required_skills", []),
        jd_text,
        is_nice_to_have=False,
        seniority=seniority,
    )
    nice_to_have_enriched = _enrich_skills_with_confidence(
        jd_analysis.get("nice_to_have_skills", []),
        jd_text,
        is_nice_to_have=True,
        seniority=seniority,
    )

    # Compute excluded skills (detected but filtered as generic soft skills)
    excluded_skills = _get_excluded_skills(
        jd_text,
        jd_analysis.get("required_skills", []),
        jd_analysis.get("nice_to_have_skills", []),
    )

    # Compute suggested additions from O*NET or taxonomy fallback
    suggested_additions = _get_suggested_additions(
        job_function=jd_analysis.get("job_function", "other"),
        required_skills=jd_analysis.get("required_skills", []),
        nice_to_have_skills=jd_analysis.get("nice_to_have_skills", []),
        role_title=jd_analysis.get("role_title", ""),
    )

    # Enrich skills with O*NET market intelligence (hot/demand flags, category)
    # Both lists share the same skill objects, so in-place enrichment propagates
    all_enriched_skills = required_skills_enriched + nice_to_have_enriched
    _, market_summary = _enrich_skills_with_market_data(
        all_enriched_skills,
        jd_analysis.get("role_title", ""),
    )

    # Score JD quality (pure Python, no LLM)
    jd_quality = score_jd_quality(jd_text, jd_analysis)

    # ── Phase 3 insights: historical, team context, skill trends ────────────
    tenant_id = current_user.tenant_id
    all_jd_skills = [s.lower().strip() for s in (
        jd_analysis.get("required_skills", []) +
        jd_analysis.get("nice_to_have_skills", [])
    ) if isinstance(s, str)]

    # 1. Historical insights — OutcomeSkillPattern correlations
    historical_insights = {}
    try:
        skill_patterns = db.query(OutcomeSkillPattern).filter(
            OutcomeSkillPattern.tenant_id == tenant_id,
            OutcomeSkillPattern.skill_name.in_(all_jd_skills),
        ).order_by(OutcomeSkillPattern.correlation_score.desc()).limit(20).all()

        patterns_list = []
        for p in skill_patterns:
            # Derive success_rate from present_in_hired_pct (0-100 → 0.0-1.0)
            success_rate = round((p.present_in_hired_pct or 0) / 100, 2) if p.present_in_hired_pct else None
            patterns_list.append({
                "skill": p.skill_name,
                "success_rate": success_rate,
                "sample_size": p.sample_size,
                "correlation": p.correlation_score,
            })
        historical_insights = {"patterns": patterns_list}
    except (ValueError, TypeError, KeyError, SQLAlchemyError) as exc:
        log.warning(
            "historical_insights query failed: %s", exc,
            extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        historical_insights = {"patterns": []}

    # 2. Team context — team has / gaps if team_id provided
    team_context = None
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
                team_has = [
                    s for s in all_jd_skills if s in team_skill_names
                ]
                team_gaps = [
                    s for s in all_jd_skills if s not in team_skill_names
                ]
                team_context = {
                    "team_has": team_has,
                    "team_gaps": team_gaps,
                }
            else:
                team_context = {"team_has": [], "team_gaps": all_jd_skills}
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as exc:
            log.warning(
                "team_context query failed: %s", exc,
                extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
            team_context = {"team_has": [], "team_gaps": []}

    # 3. Skill trends — direction + growth_pct from SkillTrendSnapshot
    skill_trends = []
    try:
        if all_jd_skills:
            # Get latest period date for this tenant
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
    except (ValueError, TypeError, KeyError, SQLAlchemyError) as exc:
        log.warning(
            "skill_trends query failed: %s", exc,
            extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "VALIDATION_ERROR"},
        )
        skill_trends = []

    return {
        "role_title": jd_analysis.get("role_title", ""),
        "seniority": jd_analysis.get("seniority", "mid"),
        "domain": jd_analysis.get("domain", "other"),
        "job_function": jd_analysis.get("job_function", "other"),
        "required_years": jd_analysis.get("required_years", 0),
        "required_skills": required_skills_enriched,
        "nice_to_have_skills": nice_to_have_enriched,
        "excluded_skills": excluded_skills,
        "suggested_additions": suggested_additions,
        "key_responsibilities": jd_analysis.get("key_responsibilities", []),
        "market_summary": market_summary,
        "jd_quality": jd_quality,
        "historical_insights": historical_insights,
        "team_context": team_context,
        "skill_trends": skill_trends,
    }



# ─── Weight Suggestion Endpoint ───────────────────────────────────────────────

@router.post("/analyze/suggest-weights")
async def suggest_weights_endpoint(
    job_description: str = Form(...),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """AI-powered weight suggestion based on job description."""
    if not job_description or len(job_description.strip()) < 50:
        raise HTTPException(status_code=400, detail="Job description too short")

    from app.backend.services.weight_suggester import suggest_weights_for_jd, create_fallback_suggestion

    try:
        suggestion = await suggest_weights_for_jd(job_description)
        if suggestion is None:
            # Return fallback suggestion instead of error
            suggestion = create_fallback_suggestion(job_description)
        return suggestion
    except HTTPException:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
        log.warning(
            "Weight suggestion failed: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except (OSError, RuntimeError) as e:
        log.exception(
            "Weight suggestion failed: %s", e,
            extra={"error_code": "UPSTREAM_ERROR"},
        )
        raise HTTPException(status_code=500, detail="Failed to generate weight suggestions") from e



@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_endpoint(
    resume: UploadFile = File(...),
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    scoring_weights: str = Form(None),
    skill_overrides: str = Form(None),
    action: str = Form(None),   # use_existing | update_profile | create_new | None
    candidate_id: Optional[int] = Form(None),
    template_id: Optional[int] = Form(None),
    requisition_id: Optional[int] = Form(None),
    team_id: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """
    Non-streaming analysis endpoint.
    
    Returns Python scoring results immediately with narrative_pending=True.
    LLM narrative is generated in background and can be polled via
    GET /api/analysis/{id}/narrative.
    """
    # ─── HARD QUOTA CHECK (before any work) ───────────────────────────────────
    quota = check_quota(current_user.tenant_id, db)
    if not quota["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Monthly analysis quota exceeded",
                "used": quota["used"],
                "limit": quota["limit"],
                "plan": quota["plan"],
            },
        )

    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)
    require_explicit_use_existing_candidate(
        db, current_user.tenant_id, action, candidate_id
    )

    # ─── VALIDATE FILES FIRST (before incrementing usage) ─────────────────────
    # Validate file extension
    if not resume.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail=f"Only {ALLOWED_EXTENSIONS} files are allowed")

    # Read and validate resume file size
    content = await resume.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file too large (max 10MB)")

    # Validate file content matches extension (magic bytes)
    _validate_file_content(content, resume.filename)
    require_explicit_use_existing_candidate(
        db,
        current_user.tenant_id,
        action,
        candidate_id,
        resume_file_hash=hashlib.md5(content).hexdigest(),
    )

    # Read and validate JD file if provided
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        if len(jd_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Job description file too large (max 5MB)")
        jd_name = job_file.filename

    # Resolve and validate job description
    job_description = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(job_description)
    _check_jd_size(job_description)
    
    _validate_optional_analyze_payloads(scoring_weights, skill_overrides)
    _assert_custom_weights_allowed_if_provided(db, current_user.tenant_id, scoring_weights)

    # ─── CHECK AND INCREMENT USAGE (after validation) ─────────────────────────
    async with _get_tenant_lock(current_user.tenant_id):
        allowed, message = _check_and_increment_usage(db, current_user.tenant_id, current_user.id, 1)
    if not allowed:
        raise HTTPException(status_code=429, detail=message)

    weights = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
        except json.JSONDecodeError as e:
            log.warning("Non-critical: Invalid scoring_weights JSON, using defaults: %s", e)
    
    # Parse skill_overrides JSON (accepts strings or proficiency dicts)
    parsed_skill_overrides = None
    if skill_overrides:
        try:
            parsed_skill_overrides = json.loads(skill_overrides)
            # Validate structure: must contain lists of strings or proficiency dicts
            if not isinstance(parsed_skill_overrides, dict):
                raise ValueError("skill_overrides must be a JSON object")
            for key in ("required_skills", "nice_to_have_skills"):
                if key in parsed_skill_overrides:
                    if not isinstance(parsed_skill_overrides[key], list):
                        raise ValueError(f"skill_overrides.{key} must be a list")
                    for item in parsed_skill_overrides[key]:
                        if isinstance(item, str):
                            continue  # Plain string — backward compatible
                        if isinstance(item, dict) and isinstance(item.get("skill"), str):
                            prof = item.get("proficiency")
                            if prof is not None and not isinstance(prof, str):
                                raise ValueError(
                                    f"skill_overrides.{key} proficiency must be a string"
                                )
                            continue
                        raise ValueError(
                            f"skill_overrides.{key} items must be strings or "
                            f'{{"skill": "...", "proficiency": "..."}} dicts'
                        )
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Non-critical: Invalid skill_overrides JSON, ignoring: %s", e)
            parsed_skill_overrides = None
    
    # If no explicit weights provided, load tenant default weights
    if not weights:
        try:
            tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
            if tenant and tenant.scoring_weights:
                from app.backend.services.feature_flag_service import is_feature_enabled
                if is_feature_enabled(db, current_user.tenant_id, "custom_weights"):
                    weights = json.loads(tenant.scoring_weights)
                    log.info("Loaded tenant default weights for tenant %s", current_user.tenant_id)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
            log.warning(
                "Non-critical: Failed to load tenant weights, using defaults: %s", e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )

    job_description, parsed_skill_overrides, weights, requisition_id, legacy_tpl = _finalize_analyze_context(
        db, current_user.tenant_id, job_description, weights, parsed_skill_overrides,
        requisition_id, template_id, current_user=current_user,
    )
    template_id = legacy_tpl

    file_hash = hashlib.md5(content).hexdigest()

    # Handle "use_existing" — skip re-analysis if candidate already in DB
    if action == "use_existing":
        if not candidate_id:
            raise HTTPException(
                status_code=409,
                detail="use_existing requires an explicit candidate_id",
            )
        existing = (
            db.query(Candidate)
            .filter(
                Candidate.id == candidate_id,
                Candidate.tenant_id == current_user.tenant_id,
            )
            .first()
        )
        if not existing:
            raise HTTPException(
                status_code=409,
                detail="Candidate not found for use_existing",
            )
        # If found with stored profile, run scoring-only
        if existing and existing.raw_resume_text and existing.parsed_skills:
            parsed_data = {
                "raw_text":       existing.raw_resume_text,
                "skills":         json.loads(existing.parsed_skills or "[]"),
                "education":      json.loads(existing.parsed_education or "[]"),
                "work_experience": json.loads(existing.parsed_work_exp or "[]"),
                "contact_info":   {"name": existing.name, "email": existing.email,
                                   "phone": existing.phone},
            }
            gap_analysis = json.loads(existing.gap_analysis_json or "{}")
            jd_analysis  = _get_or_cache_jd(db, job_description)
            _apply_skill_overrides(jd_analysis, parsed_skill_overrides)

            # Build Phase 3 context for scoring integration
            phase3_context = _build_phase3_context(
                db, current_user.tenant_id, jd_analysis, team_id=team_id,
            )

            persist_kw = dict(
                db=db,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                resume_text=existing.raw_resume_text,
                jd_text=job_description,
                parsed_data=parsed_data,
                filename=resume.filename,
                file_hash=file_hash,
                file_content=content,
                gap_analysis=gap_analysis,
                requisition_id=requisition_id,
                role_template_id=template_id,
                scoring_weights=weights,
                skill_overrides=parsed_skill_overrides,
                action=action,
                candidate_id=existing.id,
            )
            db_result, _ = _persist_via_screening_command(pipeline_result={}, **persist_kw)

            result = await run_hybrid_pipeline(
                resume_text=existing.raw_resume_text,
                job_description=job_description,
                parsed_data=parsed_data,
                gap_analysis=gap_analysis,
                scoring_weights=weights,
                jd_analysis=jd_analysis,
                screening_result_id=db_result.id,
                tenant_id=current_user.tenant_id,
                phase3_context=phase3_context,
                db_session=db,
            )
            db_result, _ = _persist_via_screening_command(pipeline_result=result, **persist_kw)
            
            result["result_id"]      = db_result.id
            result["analysis_id"]    = db_result.id   # Add this line
            result["candidate_id"]   = existing.id
            result["candidate_name"] = existing.name

            if project_id:
                _link_to_project(db, project_id, current_user.tenant_id, existing.id, db_result.id, current_user.id)
            if requisition_id:
                _link_to_requisition(db, requisition_id, current_user.tenant_id, existing.id, db_result.id, current_user.id)

            return result

    t_start = time.time()

    # Parse resume first - handle parse errors gracefully
    try:
        parsed_data, pdf_bytes = await _parse_resume_with_doc_conversion(content, resume.filename)
    except ValueError as e:
        # Scanned PDF or unreadable file — return graceful error
        log.warning(f"Resume parse failed for {resume.filename}: {e}")
        parsed_data = {
            "raw_text": "",
            "skills": [],
            "education": [],
            "work_experience": [],
            "contact_info": {},
        }
        pdf_bytes = None
        # Continue with fallback - will set analysis_quality to "low"
    except HTTPException:
        raise
    except (OSError, TypeError, RuntimeError, KeyError) as e:
        log.warning(
            "Resume parse error for %s: %s", resume.filename, e,
            extra={"error_code": "IO_ERROR" if isinstance(e, OSError) else "VALIDATION_ERROR"},
        )
        parsed_data = {
            "raw_text": "",
            "skills": [],
            "education": [],
            "work_experience": [],
            "contact_info": {},
        }
        pdf_bytes = None

    gap_analysis = analyze_gaps(parsed_data.get("work_experience", []))
    jd_analysis  = _get_or_cache_jd(db, job_description)
    _apply_skill_overrides(jd_analysis, parsed_skill_overrides)

    # Build Phase 3 context for scoring integration
    phase3_context = _build_phase3_context(
        db, current_user.tenant_id, jd_analysis, team_id=team_id,
    )

    persist_kw = dict(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        resume_text=parsed_data.get("raw_text", ""),
        jd_text=job_description,
        parsed_data=parsed_data,
        filename=resume.filename,
        file_hash=file_hash,
        file_content=content,
        gap_analysis=gap_analysis,
        requisition_id=requisition_id,
        role_template_id=template_id,
        scoring_weights=weights,
        skill_overrides=parsed_skill_overrides,
        action=action,
        converted_pdf_content=pdf_bytes,
    )
    db_result, is_dup = _persist_via_screening_command(pipeline_result={}, **persist_kw)
    candidate_id = db_result.candidate_id

    # Run pipeline with background LLM
    result = await run_hybrid_pipeline(
        resume_text=parsed_data["raw_text"],
        job_description=job_description,
        parsed_data=parsed_data,
        gap_analysis=gap_analysis,
        scoring_weights=weights,
        jd_analysis=jd_analysis,
        screening_result_id=db_result.id,
        tenant_id=current_user.tenant_id,
        phase3_context=phase3_context,
        db_session=db,
    )
    db_result, is_dup = _persist_via_screening_command(
        pipeline_result=result, candidate_id=candidate_id, **persist_kw
    )

    # Persist skill overrides to template after successful analysis
    _persist_skill_overrides_to_template(
        db, template_id, current_user.tenant_id, parsed_skill_overrides
    )

    result["result_id"]    = db_result.id
    result["analysis_id"]  = db_result.id   # Add this line
    result["candidate_id"] = candidate_id

    from app.backend.services.requisition_service import compute_parse_confidence, build_skill_evidence
    result["parse_confidence"] = compute_parse_confidence(parsed_data)
    result["skill_evidence"] = build_skill_evidence(
        parsed_data,
        result.get("matched_skills") or result.get("skill_analysis", {}).get("matched_skills"),
    )
    if requisition_id:
        result["requisition_id"] = requisition_id

    if project_id:
        _link_to_project(db, project_id, current_user.tenant_id, candidate_id, db_result.id, current_user.id)
    if requisition_id:
        _link_to_requisition(db, requisition_id, current_user.tenant_id, candidate_id, db_result.id, current_user.id)

    # Resolve name: candidate.name (possibly edited) takes priority over parsed/analysis data
    _cand_row = db.get(Candidate, candidate_id)
    result["candidate_name"] = (
        (_cand_row.name if _cand_row and _cand_row.name else None)
        or (parsed_data.get("contact_info", {}).get("name") or "").strip()
        or (result.get("candidate_profile", {}).get("name") or "").strip()
        or None
    )

    if is_dup and action not in ("update_profile", "create_new"):
        existing = db.get(Candidate, candidate_id)
        if existing:
            result["duplicate_candidate"] = _build_duplicate_info(db, existing).model_dump(mode='json')

    log.info(json.dumps({
        "event":       "analysis_complete",
        "tenant_id":   current_user.tenant_id,
        "filename":    resume.filename,
        "skills_found": len(result.get("matched_skills", [])),
        "fit_score":   result.get("fit_score"),
        "llm_pending": result.get("narrative_pending", False),
        "quality":     result.get("analysis_quality"),
        "total_ms":    int((time.time() - t_start) * 1000),
    }, default=_json_default))

    # Webhook dispatch — never let webhook failure affect analysis
    try:
        from app.backend.services.webhook_service import dispatch_event_background
        from app.backend.db.database import SessionLocal
        dispatch_event_background(SessionLocal, current_user.tenant_id, "analysis.completed", {"result_id": db_result.id})
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        log.warning(
            "Webhook dispatch failed: %s", e,
            extra={"error_code": "UPSTREAM_ERROR"},
        )

    return result



# ─── Single resume analysis (SSE streaming) ───────────────────────────────────

@router.post("/analyze/stream")
async def analyze_stream_endpoint(
    request: Request,
    resume: UploadFile = File(...),
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    scoring_weights: str = Form(None),
    skill_overrides: str = Form(None),
    action: str = Form(None),
    candidate_id: Optional[int] = Form(None),
    template_id: Optional[int] = Form(None),
    requisition_id: Optional[int] = Form(None),
    team_id: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """
    SSE streaming version of /analyze.

    With background LLM processing:
      1. Creates ScreeningResult immediately with Python scores
      2. Yields results with narrative_pending=True and analysis_id for polling
      3. LLM runs in background and writes to DB when done
      4. Frontend polls GET /api/analysis/{id}/narrative for LLM narrative

    Emits:
      data: {"stage": "parsing",  "result": {...Python scores...}}   — within 2s
      data: {"stage": "complete", "result": {...result with analysis_id...}}
      data: [DONE]
    """
    # ─── HARD QUOTA CHECK (before any work) ───────────────────────────────────
    quota = check_quota(current_user.tenant_id, db)
    if not quota["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Monthly analysis quota exceeded",
                "used": quota["used"],
                "limit": quota["limit"],
                "plan": quota["plan"],
            },
        )

    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)
    require_explicit_use_existing_candidate(
        db, current_user.tenant_id, action, candidate_id
    )

    # ─── VALIDATE FILES FIRST (before incrementing usage) ─────────────────────
    # Validate file extension
    if not resume.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail=f"Only {ALLOWED_EXTENSIONS} files are allowed")

    # Read and validate resume file size
    content = await resume.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file too large (max 10MB)")

    # Validate file content matches extension (magic bytes)
    _validate_file_content(content, resume.filename)
    require_explicit_use_existing_candidate(
        db,
        current_user.tenant_id,
        action,
        candidate_id,
        resume_file_hash=hashlib.md5(content).hexdigest(),
    )

    # Read JD file if provided
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        jd_name  = job_file.filename

    # Resolve and validate job description
    job_description = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(job_description)
    _check_jd_size(job_description)
    
    _validate_optional_analyze_payloads(scoring_weights, skill_overrides)
    _assert_custom_weights_allowed_if_provided(db, current_user.tenant_id, scoring_weights)

    weights = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
            log.info("Received custom weights from frontend: %s", weights)
        except json.JSONDecodeError as e:
            log.warning("Non-critical: Invalid scoring_weights JSON, using defaults: %s", e)

    # Parse skill_overrides JSON (accepts strings or proficiency dicts)
    parsed_skill_overrides = None
    if skill_overrides:
        try:
            parsed_skill_overrides = json.loads(skill_overrides)
            # Validate structure: must contain lists of strings or proficiency dicts
            if not isinstance(parsed_skill_overrides, dict):
                raise ValueError("skill_overrides must be a JSON object")
            for key in ("required_skills", "nice_to_have_skills"):
                if key in parsed_skill_overrides:
                    if not isinstance(parsed_skill_overrides[key], list):
                        raise ValueError(f"skill_overrides.{key} must be a list")
                    for item in parsed_skill_overrides[key]:
                        if isinstance(item, str):
                            continue  # Plain string — backward compatible
                        if isinstance(item, dict) and isinstance(item.get("skill"), str):
                            prof = item.get("proficiency")
                            if prof is not None and not isinstance(prof, str):
                                raise ValueError(
                                    f"skill_overrides.{key} proficiency must be a string"
                                )
                            continue
                        raise ValueError(
                            f"skill_overrides.{key} items must be strings or "
                            f'{{"skill": "...", "proficiency": "..."}} dicts'
                        )
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Non-critical: Invalid skill_overrides JSON, ignoring: %s", e)
            parsed_skill_overrides = None
    
    # If no explicit weights provided, load tenant default weights
    if not weights:
        try:
            tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
            if tenant and tenant.scoring_weights:
                from app.backend.services.feature_flag_service import is_feature_enabled
                if is_feature_enabled(db, current_user.tenant_id, "custom_weights"):
                    weights = json.loads(tenant.scoring_weights)
                    log.info("Loaded tenant default weights for tenant %s: %s", current_user.tenant_id, weights)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
            log.warning(
                "Non-critical: Failed to load tenant weights, using defaults: %s", e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
    
    if weights:
        log.info("Final weights to be used for scoring: %s", weights)
    else:
        log.info("No custom weights provided, will use system defaults")

    job_description, parsed_skill_overrides, weights, requisition_id, template_id = _finalize_analyze_context(
        db, current_user.tenant_id, job_description, weights, parsed_skill_overrides,
        requisition_id, template_id, current_user=current_user,
    )

    file_hash = hashlib.md5(content).hexdigest()
    tenant_id = current_user.tenant_id
    t_start   = time.time()

    # Parse resume and JD in thread pool before entering the generator
    try:
        parsed_data, pdf_bytes = await _parse_resume_with_doc_conversion(content, resume.filename)
    except HTTPException as parse_exc:
        error_msg = str(parse_exc.detail)
        async def _error_stream():
            error = {"stage": "error", "result": {"message": error_msg}}
            yield f"data: {json.dumps(error, default=_json_default)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_error_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as parse_exc:
        error_msg = str(parse_exc)
        async def _error_stream():
            error = {"stage": "error", "result": {"message": error_msg}}
            yield f"data: {json.dumps(error, default=_json_default)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_error_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async with _get_tenant_lock(current_user.tenant_id):
        allowed, message = _check_and_increment_usage(db, current_user.tenant_id, current_user.id, 1)
    if not allowed:
        raise HTTPException(status_code=429, detail=message)

    gap_analysis = analyze_gaps(parsed_data.get("work_experience", []))
    jd_analysis  = _get_or_cache_jd(db, job_description)
    _apply_skill_overrides(jd_analysis, parsed_skill_overrides)

    # Build Phase 3 context for scoring integration
    phase3_context = _build_phase3_context(
        db, tenant_id, jd_analysis, team_id=team_id,
    )

    db_result, is_dup = _persist_via_screening_command(
        db=db,
        tenant_id=tenant_id,
        user_id=current_user.id,
        resume_text=parsed_data.get("raw_text", ""),
        jd_text=job_description,
        parsed_data=parsed_data,
        pipeline_result={},
        filename=resume.filename,
        file_hash=file_hash,
        file_content=content,
        gap_analysis=gap_analysis,
        requisition_id=requisition_id,
        role_template_id=template_id,
        scoring_weights=weights,
        skill_overrides=parsed_skill_overrides,
        action=action,
        converted_pdf_content=pdf_bytes,
    )
    candidate_id = db_result.candidate_id
    screening_result_id = db_result.id

    # Cancellation token: set when client disconnects so pipeline can break early
    cancel_event = asyncio.Event()

    async def event_stream():
        final_result: dict = {}
        python_scores_saved = False

        try:
            # Check for client disconnect before starting pipeline
            if await request.is_disconnected():
                log.warning("Client disconnected before streaming analysis started")
                cancel_event.set()
                return

            async for event in astream_hybrid_pipeline(
                resume_text=parsed_data["raw_text"],
                job_description=job_description,
                parsed_data=parsed_data,
                gap_analysis=gap_analysis,
                scoring_weights=weights,
                jd_analysis=jd_analysis,
                screening_result_id=screening_result_id,
                tenant_id=tenant_id,
                phase3_context=phase3_context,
                db_session=db,
            ):
                # Check for client disconnect between stages
                if await request.is_disconnected():
                    log.warning("Client disconnected during streaming analysis")
                    cancel_event.set()
                    # Early DB save: ensure Python results are preserved
                    # Only save on "parsing" stage which has the full Python results
                    if not python_scores_saved and event.get("stage") in ("parsing", "complete"):
                        try:
                            stage_result = event.get("result", {})
                            if stage_result:
                                stage_result["result_id"] = screening_result_id
                                stage_result["candidate_id"] = candidate_id
                                # Use a dedicated session to avoid detached object issues
                                # The route's db session may be closed before the streaming generator runs
                                from app.backend.db.database import SessionLocal
                                disc_db = SessionLocal()
                                try:
                                    sr = disc_db.query(ScreeningResult).filter(ScreeningResult.id == screening_result_id).first()
                                    if sr:
                                        sr.analysis_result = json.dumps(stage_result, default=_json_default)
                                        _populate_denormalized_columns(sr, stage_result)
                                        disc_db.commit()
                                        python_scores_saved = True
                                        log.info("Early DB save completed after client disconnect (fit_score=%s)", stage_result.get("fit_score"))
                                    else:
                                        log.error("ScreeningResult id=%s not found for disconnect save", screening_result_id)
                                finally:
                                    disc_db.close()
                        except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as db_exc:
                            log.warning(
                                "Failed to save early DB results: %s", db_exc,
                                extra={"error_code": "DB_ERROR" if isinstance(db_exc, SQLAlchemyError) else "VALIDATION_ERROR"},
                            )
                    break

                if isinstance(event, str):
                    # SSE heartbeat ping from the generator
                    yield event
                    continue
                yield f"data: {json.dumps(event, default=_json_default)}\n\n"

                # Early DB save after Python parsing phase completes (NOT scoring phase)
                # The "parsing" stage contains the full Python results
                if event.get("stage") == "parsing" and not python_scores_saved:
                    try:
                        parsing_result = event.get("result", {})
                        if parsing_result:
                            # Ensure we have the full Python result with all fields
                            parsing_result["result_id"] = screening_result_id
                            parsing_result["candidate_id"] = candidate_id
                            # Use a dedicated session to avoid detached object issues
                            # The route's db session may be closed before the streaming generator runs
                            from app.backend.db.database import SessionLocal
                            early_db = SessionLocal()
                            try:
                                sr = early_db.query(ScreeningResult).filter(ScreeningResult.id == screening_result_id).first()
                                if sr:
                                    sr.analysis_result = json.dumps(parsing_result, default=_json_default)
                                    _populate_denormalized_columns(sr, parsing_result)
                                    early_db.commit()
                                    python_scores_saved = True
                                    log.info("Early DB save completed after parsing phase (fit_score=%s)", parsing_result.get("fit_score"))
                                else:
                                    log.error("ScreeningResult id=%s not found for early save", screening_result_id)
                            finally:
                                early_db.close()
                    except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as db_exc:
                        log.warning(
                            "Failed to save early DB results after parsing: %s", db_exc,
                            extra={"error_code": "DB_ERROR" if isinstance(db_exc, SQLAlchemyError) else "VALIDATION_ERROR"},
                        )

                if event.get("stage") == "complete":
                    final_result = event.get("result", {})

        except HTTPException as exc:
            log.warning(
                "Streaming analysis recovered after validation error: %s", exc.detail,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            final_result = _fallback_result(gap_analysis)
        except (ValueError, TypeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, SQLAlchemyError) as exc:
            log.warning(
                "Streaming analysis recovered after pipeline error: %s", exc,
                extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
            final_result = _fallback_result(gap_analysis)

        # If the client disconnected, skip the final save/complete cycle
        if cancel_event.is_set():
            log.info("Skipping final save/complete — client already disconnected")
            return

        # Update the ScreeningResult with the final analysis_result
        # CRITICAL: DB save must complete BEFORE yielding the 'complete' event
        # so the frontend can safely poll for results immediately on receipt.
        save_db = None
        try:
            final_result["result_id"]    = screening_result_id
            final_result["candidate_id"] = candidate_id
            _cand_row_s = db.get(Candidate, candidate_id)
            final_result["candidate_name"] = (
                (_cand_row_s.name if _cand_row_s and _cand_row_s.name else None)
                or (parsed_data.get("contact_info", {}).get("name") or "").strip()
                or (final_result.get("candidate_profile", {}).get("name") or "").strip()
                or None
            )
            if is_dup and action not in ("update_profile", "create_new"):
                existing = db.get(Candidate, candidate_id)
                if existing:
                    final_result["duplicate_candidate"] = _build_duplicate_info(db, existing).model_dump(mode='json')

            # Use a dedicated session for the final save to avoid detached object issues
            # The route's db session may be closed before the streaming generator runs
            from app.backend.db.database import SessionLocal
            save_db = SessionLocal()
            try:
                sr = save_db.query(ScreeningResult).filter(ScreeningResult.id == screening_result_id).first()
                if sr:
                    if _should_preserve_analysis_scores(sr, sr.resume_text, sr.jd_text):
                        final_result = _restore_preserved_scores(sr, final_result)
                    sr.analysis_result = json.dumps(final_result, default=_json_default)
                    _populate_denormalized_columns(sr, final_result)
                    # Also update candidate profile
                    cand = save_db.query(Candidate).filter(Candidate.id == candidate_id).first()
                    if cand:
                        _store_candidate_profile(cand, parsed_data, gap_analysis, file_hash, final_result.get("analysis_quality", "medium"), content, resume.filename, db=save_db)
                    save_db.commit()
                    log.info("Final DB save completed for screening_result_id=%s (fit_score=%s)", screening_result_id, final_result.get("fit_score"))

                    # Persist skill overrides to template after successful analysis
                    _persist_skill_overrides_to_template(
                        save_db, template_id, tenant_id, parsed_skill_overrides
                    )
                else:
                    log.error("ScreeningResult id=%s not found for final save", screening_result_id)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as inner_db_exc:
                save_db.rollback()
                raise inner_db_exc
            finally:
                save_db.close()
                save_db = None
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as db_exc:
            log.error(
                "Final DB save failed for screening_result_id=%s: %s", screening_result_id, db_exc,
                extra={"error_code": "DB_ERROR" if isinstance(db_exc, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
            # DB save failed — yield error event instead of complete so the frontend
            # knows the result was not persisted and should not poll for it.
            yield f"data: {json.dumps({'stage': 'error', 'result': {'message': 'Failed to save analysis result'}}, default=_json_default)}\n\n"
            return

        log.info(json.dumps({
            "event":       "analysis_complete",
            "tenant_id":   tenant_id,
            "filename":    resume.filename,
            "fit_score":   final_result.get("fit_score"),
            "llm_pending": final_result.get("narrative_pending", False),
            "quality":     final_result.get("analysis_quality"),
            "total_ms":    int((time.time() - t_start) * 1000),
        }, default=_json_default))

        # Link to screening project if specified
        if project_id:
            try:
                from app.backend.db.database import SessionLocal as _SL
                _link_db = _SL()
                try:
                    _link_to_project(_link_db, project_id, tenant_id, candidate_id, screening_result_id, current_user.id)
                finally:
                    _link_db.close()
            except (ValueError, TypeError, KeyError, OSError, RuntimeError, SQLAlchemyError) as link_exc:
                log.warning(
                    "Non-critical: Failed to link to project in stream: %s", link_exc,
                    extra={"error_code": "DB_ERROR" if isinstance(link_exc, SQLAlchemyError) else "VALIDATION_ERROR"},
                )

        # Webhook dispatch — never let webhook failure affect analysis
        try:
            from app.backend.services.webhook_service import dispatch_event_background
            from app.backend.db.database import SessionLocal
            dispatch_event_background(SessionLocal, tenant_id, "analysis.completed", {"result_id": screening_result_id})
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            log.warning(
                "Webhook dispatch failed: %s", e,
                extra={"error_code": "UPSTREAM_ERROR"},
            )

        # Yield final complete ONLY after successful DB save
        complete_payload = {"stage": "complete", "result": final_result}
        yield f"data: {json.dumps(complete_payload, default=_json_default)}\n\n"

    async def event_stream_with_cleanup():
        """Wrapper to ensure [DONE] event and resource cleanup always happen."""
        try:
            async for chunk in event_stream():
                yield chunk
        except HTTPException as e:
            log.warning(
                "Stream error: %s", e.detail,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            yield f"data: {json.dumps({'error': str(e.detail)})}\n\n"
        except (ValueError, TypeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, SQLAlchemyError) as e:
            log.exception(
                "Stream error: %s", e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Guaranteed [DONE] event
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream_with_cleanup(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



# ─── Batch resume analysis (chunked upload) ─────────────────────────────────

@router.post("/analyze/batch-chunked", response_model=BatchAnalysisResponse, dependencies=[Depends(require_feature("batch_analysis"))])
async def batch_analyze_chunked_endpoint(
    upload_ids: list[str] = Form(...),
    filenames: list[str] = Form(...),
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    scoring_weights: str = Form(None),
    template_id: Optional[int] = Form(None),
    requisition_id: Optional[int] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """
    Batch analysis for chunked uploads.

    Accepts upload_ids from the chunked upload system instead of raw files.
    Reads assembled files from the chunk storage directory and processes
    them through the same analysis pipeline as /analyze/batch.
    """
    # ─── HARD QUOTA CHECK (before any work) ───────────────────────────────────
    quota = check_quota(current_user.tenant_id, db)
    if not quota["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Monthly analysis quota exceeded",
                "used": quota["used"],
                "limit": quota["limit"],
                "plan": quota["plan"],
            },
        )

    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)

    from app.backend.routes.upload import CHUNK_STORAGE_DIR

    if not upload_ids:
        raise HTTPException(status_code=400, detail="At least one upload_id required")

    if len(upload_ids) != len(filenames):
        raise HTTPException(
            status_code=400,
            detail=f"upload_ids ({len(upload_ids)}) and filenames ({len(filenames)}) must have the same length",
        )

    if len(upload_ids) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum batch size is {MAX_BATCH_SIZE} resumes",
        )

    # Read and validate JD file if provided (before usage check)
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        jd_name = job_file.filename

    # Resolve and validate job description (before usage check)
    job_description = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(job_description)
    _check_jd_size(job_description)

    # Locate assembled files for each upload_id
    assembled_dir = CHUNK_STORAGE_DIR / "assembled"
    file_data = []
    failed_items: list[BatchFailedItem] = []

    for upload_id, filename in zip(upload_ids, filenames):
        # Sanitise to prevent directory traversal
        safe_uid = upload_id.replace("..", "").replace("/", "").replace("\\", "")
        safe_fname = filename.replace("..", "").replace("/", "").replace("\\", "")
        try:
            from app.backend.routes.upload import assert_upload_owned
            assert_upload_owned(safe_uid, current_user)
        except HTTPException:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Upload {upload_id} not found or expired. Please re-upload.",
            ))
            continue
        assembled_path = assembled_dir / f"{safe_uid}_{safe_fname}"

        if not assembled_path.exists():
            log.warning("Assembled file not found for upload_id=%s, filename=%s", upload_id, filename)
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Upload {upload_id} not found or expired. Please re-upload.",
            ))
            continue

        try:
            content = assembled_path.read_bytes()
        except OSError as e:
            log.warning(
                "Failed to read assembled file for upload_id=%s: %s", upload_id, e,
                extra={"error_code": "IO_ERROR"},
            )
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Failed to read assembled file: {str(e)}",
            ))
            continue

        # Validate size and extension
        if len(content) > 10 * 1024 * 1024:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error="Resume file too large (max 10MB)",
            ))
            continue

        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Only {ALLOWED_EXTENSIONS} files are allowed",
            ))
            continue

        file_data.append((content, filename, upload_id))

    # Pre-flight file content validation (magic bytes)
    validated_file_data = []
    for content, filename, upload_id in file_data:
        try:
            _validate_file_content(content, filename)
            validated_file_data.append((content, filename, upload_id))
        except HTTPException as e:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"File validation failed: {e.detail}",
            ))
    file_data = validated_file_data

    valid_count = len(file_data)

    if valid_count == 0:
        if not failed_items:
            raise HTTPException(status_code=400, detail="No valid resume files provided")
        # All files failed - return empty results with failures
        return BatchAnalysisResponse(
            results=[],
            failed=failed_items,
            total=len(upload_ids),
            successful=0,
            failed_count=len(failed_items),
        )

    # Get tenant's plan for batch size limit
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    max_batch_size = MAX_BATCH_SIZE
    plan = get_tenant_plan(db, tenant.id) if tenant else None
    if plan:
        limits = _get_plan_limits(plan)
        plan_batch_limit = limits.get("batch_size", MAX_BATCH_SIZE)
        max_batch_size = min(max_batch_size, plan_batch_limit)

    if valid_count > max_batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"Your plan allows maximum {max_batch_size} resumes per batch. Please upgrade to process more.",
        )

    _validate_optional_analyze_payloads(scoring_weights)
    _assert_custom_weights_allowed_if_provided(db, current_user.tenant_id, scoring_weights)

    # CHECK AND INCREMENT USAGE
    async with _get_tenant_lock(current_user.tenant_id):
        allowed, message = _check_and_increment_usage(db, current_user.tenant_id, current_user.id, valid_count)
    if not allowed:
        raise HTTPException(status_code=429, detail=message)

    weights = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
        except json.JSONDecodeError as e:
            log.warning("Non-critical: Invalid scoring_weights JSON, using defaults: %s", e)

    job_description, _, weights, requisition_id, template_id = _finalize_analyze_context(
        db, current_user.tenant_id, job_description, weights, None, requisition_id, template_id,
        current_user=current_user,
    )

    # Pre-parse JD once
    _get_or_cache_jd(db, job_description)

    # Process all resumes with semaphore-wrapped calls (fast Python scoring)
    tasks = [
        _process_with_semaphore(content, filename, job_description, weights, db)
        for content, filename, _ in file_data
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # SEPARATE SUCCESSES FROM FAILURES
    batch_results = []

    for raw, (content, filename, upload_id) in zip(raw_results, file_data):
        if isinstance(raw, Exception):
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=str(raw),
            ))
            continue

        try:
            parsed_data = raw.pop("_parsed_data", {})
            gap_analysis = raw.pop("_gap_analysis", {})
            file_hash = hashlib.md5(content).hexdigest()

            db_result, _ = _persist_via_screening_command(
                db=db,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                resume_text=parsed_data.get("raw_text", ""),
                jd_text=job_description,
                parsed_data=parsed_data,
                pipeline_result=raw,
                filename=filename,
                file_hash=file_hash,
                file_content=content,
                gap_analysis=gap_analysis,
                requisition_id=requisition_id,
                role_template_id=template_id,
                scoring_weights=weights,
            )
            raw["result_id"] = db_result.id

            # Spawn background LLM narrative generation
            _spawn_background_narrative(raw, db_result.id, current_user.tenant_id)

            batch_results.append({"filename": filename, "result": raw})
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as e:
            db.rollback()
            log.error(
                "Failed to save analysis for %s: %s", filename, e,
                extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
            )
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Database error: {str(e)}",
            ))

    # Clean up assembled files after successful processing
    for _content, filename, upload_id in file_data:
        try:
            safe_uid = upload_id.replace("..", "").replace("/", "").replace("\\", "")
            safe_fname = filename.replace("..", "").replace("/", "").replace("\\", "")
            assembled_path = assembled_dir / f"{safe_uid}_{safe_fname}"
            if assembled_path.exists():
                assembled_path.unlink()
                log.info("Cleaned up assembled file: %s", assembled_path)
        except OSError as e:
            log.warning(
                "Non-critical: Failed to clean up assembled file for upload_id=%s: %s", upload_id, e,
                extra={"error_code": "IO_ERROR"},
            )

    # Sort by fit score
    batch_results.sort(key=lambda x: x["result"].get("fit_score") or 0, reverse=True)
    ranked = [
        BatchAnalysisResult(rank=i + 1, filename=r["filename"], result=r["result"])
        for i, r in enumerate(batch_results)
    ]

    return BatchAnalysisResponse(
        results=ranked,
        failed=failed_items,
        total=len(upload_ids),
        successful=len(ranked),
        failed_count=len(failed_items),
    )



# ─── Batch resume analysis (SSE streaming) ───────────────────────────────────

@router.post("/analyze/batch-stream", dependencies=[Depends(require_feature("batch_analysis"))])
async def batch_analyze_stream_endpoint(
    upload_ids: list[str] = Form(...),
    filenames: list[str] = Form(...),
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    scoring_weights: str = Form(None),
    skill_overrides: str = Form(None),
    template_id: Optional[int] = Form(None),
    requisition_id: Optional[int] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """
    SSE streaming batch analysis for chunked uploads.

    Processes resumes concurrently and streams each result as an SSE event
    as soon as it completes, instead of waiting for all resumes to finish.

    Emits:
      data: {"event": "failed",  "index": N, ...}   — per pre-flight or runtime failure
      data: {"event": "result", "index": N, ...}   — per successful resume
      data: {"event": "done",   "total": N, ...}   — final summary
      data: [DONE]
    """
    # ─── HARD QUOTA CHECK (before any work) ───────────────────────────────────
    quota = check_quota(current_user.tenant_id, db)
    if not quota["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Monthly analysis quota exceeded",
                "used": quota["used"],
                "limit": quota["limit"],
                "plan": quota["plan"],
            },
        )

    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)

    from app.backend.routes.upload import CHUNK_STORAGE_DIR

    # ── Input validation ────────────────────────────────────────────────────
    if not upload_ids:
        raise HTTPException(status_code=400, detail="At least one upload_id required")

    if len(upload_ids) != len(filenames):
        raise HTTPException(
            status_code=400,
            detail=f"upload_ids ({len(upload_ids)}) and filenames ({len(filenames)}) must have the same length",
        )

    if len(upload_ids) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum batch size is {MAX_BATCH_SIZE} resumes",
        )

    # Read and validate JD file if provided (before usage check)
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        jd_name = job_file.filename

    # Resolve and validate job description (before usage check)
    job_description = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(job_description)
    _check_jd_size(job_description)

    # Locate assembled files for each upload_id
    assembled_dir = CHUNK_STORAGE_DIR / "assembled"
    file_data = []
    failed_items: list[BatchFailedItem] = []

    for upload_id, filename in zip(upload_ids, filenames):
        # Sanitise to prevent directory traversal
        safe_uid = upload_id.replace("..", "").replace("/", "").replace("\\", "")
        safe_fname = filename.replace("..", "").replace("/", "").replace("\\", "")
        try:
            from app.backend.routes.upload import assert_upload_owned
            assert_upload_owned(safe_uid, current_user)
        except HTTPException:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Upload {upload_id} not found or expired. Please re-upload.",
            ))
            continue
        assembled_path = assembled_dir / f"{safe_uid}_{safe_fname}"

        if not assembled_path.exists():
            log.warning("Assembled file not found for upload_id=%s, filename=%s", upload_id, filename)
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Upload {upload_id} not found or expired. Please re-upload.",
            ))
            continue

        try:
            content = assembled_path.read_bytes()
        except OSError as e:
            log.warning(
                "Failed to read assembled file for upload_id=%s: %s", upload_id, e,
                extra={"error_code": "IO_ERROR"},
            )
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Failed to read assembled file: {str(e)}",
            ))
            continue

        # Validate size and extension
        if len(content) > 10 * 1024 * 1024:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error="Resume file too large (max 10MB)",
            ))
            continue

        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"Only {ALLOWED_EXTENSIONS} files are allowed",
            ))
            continue

        file_data.append((content, filename, upload_id))

    # Pre-flight file content validation (magic bytes)
    validated_file_data = []
    for content, filename, upload_id in file_data:
        try:
            _validate_file_content(content, filename)
            validated_file_data.append((content, filename, upload_id))
        except HTTPException as e:
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=f"File validation failed: {e.detail}",
            ))
    file_data = validated_file_data

    valid_count = len(file_data)

    if valid_count == 0:
        if not failed_items:
            raise HTTPException(status_code=400, detail="No valid resume files provided")
        # All files failed — stream failures then done
        async def _empty_stream():
            total = len(failed_items)
            for i, fail in enumerate(failed_items):
                evt = BatchStreamEvent(
                    event="failed", index=i + 1, total=total,
                    filename=fail.filename, error=fail.error,
                )
                yield f"data: {json.dumps(evt.model_dump(exclude_none=True), default=_json_default)}\n\n"
            done_evt = BatchStreamEvent(
                event="done", index=0, total=total,
                successful=0, failed_count=total,
            )
            yield f"data: {json.dumps(done_evt.model_dump(exclude_none=True), default=_json_default)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(
            _empty_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    # Get tenant's plan for batch size limit
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    max_batch_size = MAX_BATCH_SIZE
    plan = get_tenant_plan(db, tenant.id) if tenant else None
    if plan:
        limits = _get_plan_limits(plan)
        plan_batch_limit = limits.get("batch_size", MAX_BATCH_SIZE)
        max_batch_size = min(max_batch_size, plan_batch_limit)

    if valid_count > max_batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"Your plan allows maximum {max_batch_size} resumes per batch. Please upgrade to process more.",
        )

    _validate_optional_analyze_payloads(scoring_weights, skill_overrides)
    _assert_custom_weights_allowed_if_provided(db, current_user.tenant_id, scoring_weights)

    # CHECK AND INCREMENT USAGE
    async with _get_tenant_lock(current_user.tenant_id):
        allowed, message = _check_and_increment_usage(db, current_user.tenant_id, current_user.id, valid_count)
    if not allowed:
        raise HTTPException(status_code=429, detail=message)

    parsed_weights = None
    if scoring_weights:
        try:
            parsed_weights = json.loads(scoring_weights)
        except json.JSONDecodeError as e:
            log.warning("Non-critical: Invalid scoring_weights JSON, using defaults: %s", e)

    # Parse skill_overrides JSON (accepts strings or proficiency dicts)
    parsed_skill_overrides = None
    if skill_overrides:
        try:
            parsed_skill_overrides = json.loads(skill_overrides)
            # Validate structure: must contain lists of strings or proficiency dicts
            if not isinstance(parsed_skill_overrides, dict):
                raise ValueError("skill_overrides must be a JSON object")
            for key in ("required_skills", "nice_to_have_skills"):
                if key in parsed_skill_overrides:
                    if not isinstance(parsed_skill_overrides[key], list):
                        raise ValueError(f"skill_overrides.{key} must be a list")
                    for item in parsed_skill_overrides[key]:
                        if isinstance(item, str):
                            continue  # Plain string — backward compatible
                        if isinstance(item, dict) and isinstance(item.get("skill"), str):
                            prof = item.get("proficiency")
                            if prof is not None and not isinstance(prof, str):
                                raise ValueError(
                                    f"skill_overrides.{key} proficiency must be a string"
                                )
                            continue
                        raise ValueError(
                            f"skill_overrides.{key} items must be strings or "
                            f'{{"skill": "...", "proficiency": "..."}} dicts'
                        )
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Non-critical: Invalid skill_overrides JSON, ignoring: %s", e)
            parsed_skill_overrides = None

    job_description, parsed_skill_overrides, parsed_weights, requisition_id, template_id = _finalize_analyze_context(
        db, current_user.tenant_id, job_description, parsed_weights, parsed_skill_overrides,
        requisition_id, template_id, current_user=current_user,
    )

    # Pre-parse JD once
    _get_or_cache_jd(db, job_description)

    # Extract tenant_id while session is still active
    tenant_id = current_user.tenant_id
    _template_id = template_id
    _requisition_id = requisition_id

    # ── Tagged wrapper for asyncio.as_completed mapping ──────────────────────
    async def _process_and_tag(
        index: int, content: bytes, filename: str, upload_id: str,
        jd: str, weights: dict | None, db_session: Session,
        skill_overrides: dict | None = None,
    ) -> tuple[dict, bytes, str, str]:
        """Wrapper that returns result alongside file metadata."""
        await asyncio.sleep(0.3 * index)  # Stagger to avoid thundering herd
        result = await _process_with_semaphore(
            content, filename, jd, weights, db_session, skill_overrides,
        )
        return result, content, filename, upload_id

    total = len(file_data) + len(failed_items)

    # ── SSE generator ────────────────────────────────────────────────────────
    async def event_generator():
        completed = 0
        successful_count = 0
        failed_count = len(failed_items)

        # Emit pre-flight failures first
        for i, fail in enumerate(failed_items):
            evt = BatchStreamEvent(
                event="failed",
                index=i + 1,
                total=total,
                filename=fail.filename,
                error=fail.error,
            )
            yield f"data: {json.dumps(evt.model_dump(exclude_none=True), default=_json_default)}\n\n"

        # Emit processing events so the UI knows which files have started
        for idx, (_, filename, _) in enumerate(file_data):
            processing_evt = BatchStreamEvent(
                event="processing",
                index=idx + 1,
                total=total,
                filename=filename,
            )
            yield f"data: {json.dumps(processing_evt.model_dump(exclude_none=True), default=_json_default)}\n\n"

        # Create tagged tasks
        tasks = [
            _process_and_tag(idx, c, f, uid, job_description, parsed_weights, db, parsed_skill_overrides)
            for idx, (c, f, uid) in enumerate(file_data)
        ]

        # Process resumes as they complete
        for coro in asyncio.as_completed(tasks):
            try:
                raw, content, filename, upload_id = await coro
            except (ValueError, TypeError, json.JSONDecodeError, KeyError, OSError, RuntimeError, SQLAlchemyError) as e:
                log.warning(
                    "Batch stream task failed: %s", e,
                    extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
                )
                failed_count += 1
                completed += 1
                evt = BatchStreamEvent(
                    event="failed",
                    index=completed + failed_count,
                    total=total,
                    error=str(e),
                )
                yield f"data: {json.dumps(evt.model_dump(exclude_none=True), default=_json_default)}\n\n"
                continue

            # Per-resume DB save using a fresh session to avoid detached object issues
            save_db = SessionLocal()
            try:
                parsed_data = raw.pop("_parsed_data", {})
                gap_analysis = raw.pop("_gap_analysis", {})
                file_hash = hashlib.md5(content).hexdigest()

                db_result, _ = _persist_via_screening_command(
                    db=save_db,
                    tenant_id=tenant_id,
                    user_id=current_user.id,
                    resume_text=parsed_data.get("raw_text", ""),
                    jd_text=job_description,
                    parsed_data=parsed_data,
                    pipeline_result=raw,
                    filename=filename,
                    file_hash=file_hash,
                    file_content=content,
                    gap_analysis=gap_analysis,
                    requisition_id=_requisition_id,
                    role_template_id=_template_id,
                    scoring_weights=parsed_weights,
                    skill_overrides=parsed_skill_overrides,
                )
                candidate_id = db_result.candidate_id
                screening_result_id = db_result.id

                # Spawn background LLM narrative generation
                _spawn_background_narrative(raw, screening_result_id, tenant_id)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError, RuntimeError, SQLAlchemyError) as e:
                save_db.rollback()
                log.error(
                    "Failed to save analysis for %s: %s", filename, e,
                    extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
                )
                failed_count += 1
                completed += 1
                evt = BatchStreamEvent(
                    event="failed",
                    index=completed + failed_count,
                    total=total,
                    filename=filename,
                    error=f"Database error: {str(e)}",
                )
                yield f"data: {json.dumps(evt.model_dump(exclude_none=True), default=_json_default)}\n\n"
                continue
            finally:
                save_db.close()

            completed += 1
            successful_count += 1

            # Emit result event
            evt = BatchStreamEvent(
                event="result",
                index=completed,
                total=total,
                filename=filename,
                result=raw,
                screening_result_id=screening_result_id,
            )
            yield f"data: {json.dumps(evt.model_dump(exclude_none=True), default=_json_default)}\n\n"

        # Emit done event
        done_evt = BatchStreamEvent(
            event="done",
            index=0,
            total=total,
            successful=successful_count,
            failed_count=failed_count,
        )
        yield f"data: {json.dumps(done_evt.model_dump(exclude_none=True), default=_json_default)}\n\n"
        yield "data: [DONE]\n\n"

        # Clean up assembled files after all processing
        for _content, filename, upload_id in file_data:
            try:
                safe_uid = upload_id.replace("..", "").replace("/", "").replace("\\", "")
                safe_fname = filename.replace("..", "").replace("/", "").replace("\\", "")
                assembled_path = assembled_dir / f"{safe_uid}_{safe_fname}"
                if assembled_path.exists():
                    assembled_path.unlink()
                    log.info("Cleaned up assembled file: %s", assembled_path)
            except OSError as e:
                log.warning(
                    "Non-critical: Failed to clean up assembled file for upload_id=%s: %s", upload_id, e,
                    extra={"error_code": "IO_ERROR"},
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )



@router.post("/analyze/batch", response_model=BatchAnalysisResponse, dependencies=[Depends(require_feature("batch_analysis"))])
async def batch_analyze_endpoint(
    resumes: list[UploadFile] = File(...),
    job_description: str = Form(None),
    job_file: UploadFile = File(None),
    scoring_weights: str = Form(None),
    template_id: Optional[int] = Form(None),
    requisition_id: Optional[int] = Form(None),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    # ─── HARD QUOTA CHECK (before any work) ───────────────────────────────────
    quota = check_quota(current_user.tenant_id, db)
    if not quota["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Monthly analysis quota exceeded",
                "used": quota["used"],
                "limit": quota["limit"],
                "plan": quota["plan"],
            },
        )

    _enforce_screening_mode(db, current_user.tenant_id, requisition_id)

    if not resumes:
        raise HTTPException(status_code=400, detail="At least one resume required")
    
        # ─── VALIDATE BATCH SIZE FIRST (before reading any files) ─────────────────
    if len(resumes) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400, 
            detail=f"Maximum {MAX_BATCH_SIZE} resumes per batch. Received {len(resumes)}."
        )
    
    # Read and validate JD file if provided (before usage check)
    jd_bytes = jd_name = None
    if job_file and job_file.filename:
        jd_bytes = await job_file.read()
        jd_name  = job_file.filename

    # Resolve and validate job description (before usage check)
    job_description = _resolve_jd(job_description, jd_bytes, jd_name)
    _check_jd_length(job_description)
    _check_jd_size(job_description)
    
    # ─── VALIDATE FILES (before incrementing usage) ─────────────────────────────
    # Read and validate all files
    file_data = []
    for f in resumes:
        if not f.filename.lower().endswith(ALLOWED_EXTENSIONS):
            continue
        content = await f.read()
        if len(content) <= 10 * 1024 * 1024:
            file_data.append((content, f.filename))
    
    valid_count = len(file_data)
    
    # Get tenant's plan for batch size limit
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    max_batch_size = MAX_BATCH_SIZE  # Use module constant
    plan = get_tenant_plan(db, tenant.id) if tenant else None
    if plan:
        limits = _get_plan_limits(plan)
        plan_batch_limit = limits.get("batch_size", MAX_BATCH_SIZE)
        max_batch_size = min(max_batch_size, plan_batch_limit)
    
    if valid_count > max_batch_size:
        raise HTTPException(
            status_code=400, 
            detail=f"Your plan allows maximum {max_batch_size} resumes per batch. Please upgrade to process more."
        )
    
    _validate_optional_analyze_payloads(scoring_weights)
    _assert_custom_weights_allowed_if_provided(db, current_user.tenant_id, scoring_weights)

    weights = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
        except json.JSONDecodeError as e:
            log.warning("Non-critical: Invalid scoring_weights JSON, using defaults: %s", e)

    job_description, parsed_skill_overrides, weights, requisition_id, template_id = _finalize_analyze_context(
        db, current_user.tenant_id, job_description, weights, None, requisition_id, template_id,
        current_user=current_user,
    )

    # Pre-parse JD once for all resumes in this batch
    _get_or_cache_jd(db, job_description)

    if not file_data:
        raise HTTPException(status_code=400, detail="No valid resume files provided")

    # Process all resumes with semaphore-wrapped calls for concurrency control (fast path)
    tasks = [
        _process_with_semaphore(content, filename, job_description, weights, db)
        for content, filename in file_data
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # ─── SEPARATE SUCCESSES FROM FAILURES ───────────────────────────────────────
    batch_results = []
    failed_items = []
    
    for raw, (content, filename) in zip(raw_results, file_data):
        if isinstance(raw, Exception) or _is_parse_failure_result(raw):
            err = str(raw) if isinstance(raw, Exception) else "; ".join(raw.get("pipeline_errors") or ["unreadable"])
            failed_items.append(BatchFailedItem(
                filename=filename,
                error=err,
            ))
            continue
        
        # Extract internal data
        parsed_data  = raw.pop("_parsed_data", {})
        gap_analysis = raw.pop("_gap_analysis", {})
        pdf_bytes    = raw.pop("_pdf_bytes", None)
        file_hash    = hashlib.md5(content).hexdigest()

        db_result, _ = _persist_via_screening_command(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            resume_text=parsed_data.get("raw_text", ""),
            jd_text=job_description,
            parsed_data=parsed_data,
            pipeline_result=raw,
            filename=filename,
            file_hash=file_hash,
            file_content=content,
            gap_analysis=gap_analysis,
            requisition_id=requisition_id,
            role_template_id=template_id,
            scoring_weights=weights,
            converted_pdf_content=pdf_bytes,
        )
        raw["result_id"] = db_result.id
        candidate_id = db_result.candidate_id

        # Spawn background LLM narrative generation
        _spawn_background_narrative(raw, db_result.id, current_user.tenant_id)

        batch_results.append({"filename": filename, "result": raw})

    db.commit()

    # Sort by fit score
    batch_results.sort(key=lambda x: x["result"].get("fit_score") or 0, reverse=True)
    ranked = [
        BatchAnalysisResult(rank=i + 1, filename=r["filename"], result=r["result"])
        for i, r in enumerate(batch_results)
    ]

    if ranked:
        async with _get_tenant_lock(current_user.tenant_id):
            allowed, message = _check_and_increment_usage(
                db, current_user.tenant_id, current_user.id, len(ranked),
            )
        if not allowed:
            raise HTTPException(status_code=429, detail=message)
    
    return BatchAnalysisResponse(
        results=ranked,
        failed=failed_items,
        total=len(file_data),
        successful=len(ranked),
        failed_count=len(failed_items),
    )

from app.backend.routes.analyze_results import results_router
router.include_router(results_router)
