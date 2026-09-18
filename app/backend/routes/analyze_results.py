"""Analysis history, status, rescore, narrative, and JD templates."""
from datetime import datetime, timezone
from typing import Optional
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user
from app.backend.middleware.rbac import hm_assigned_candidate_ids_subquery, is_hiring_manager, require_active_recruiter
from app.backend.models.db_models import Candidate, ScreeningResult, Tenant, User, RequisitionCandidate
from app.backend.models.schemas import RescoreRequest
from app.backend.services.audit_service import log_field_change, log_tenant_event
from app.backend.services.fit_scorer import compute_fit_score, scalar_breakdown_score
from app.backend.services.interview_kit_generator import refresh_interview_questions_in_analysis
from app.backend.services.weight_mapper import convert_to_new_schema

from app.backend.services.constants import RECOMMENDATION_THRESHOLDS

log = logging.getLogger("aria.analysis")
results_router = APIRouter()

from app.backend.routes.analyze_helpers import (
    _json_default,
    _populate_denormalized_columns,
    _schedule_auto_trigger,
    _sync_pipeline_status_for_result,
)

# ─── History ──────────────────────────────────────────────────────────────────

@results_router.get("/history")
def get_analysis_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.tenant_id == current_user.tenant_id)
    )
    if is_hiring_manager(current_user):
        sub = hm_assigned_candidate_ids_subquery(db, current_user)
        query = query.filter(ScreeningResult.candidate_id.in_(select(sub.c.candidate_id)))
    results = query.order_by(ScreeningResult.timestamp.desc()).limit(100).all()
    def _safe_loads(data):
        try:
            return json.loads(data or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    output = []
    for r in results:
        analysis = _safe_loads(r.analysis_result)
        parsed = _safe_loads(r.parsed_data)

        # Resolve candidate name: Candidate.name (possibly edited) takes priority
        cand = db.get(Candidate, r.candidate_id) if r.candidate_id else None
        candidate_name = (
            (cand.name or "").strip() if cand and cand.name else None
        ) or (
            (analysis.get("candidate_name") or "").strip() or
            (analysis.get("contact_info", {}).get("name") or "").strip() or
            (analysis.get("candidate_profile", {}).get("name") or "").strip() or
            (parsed.get("contact_info", {}).get("name") or "").strip() or
            None
        )

        job_role = (
            analysis.get("job_role") or
            analysis.get("jd_analysis", {}).get("role_title") or
            None
        )

        output.append({
            "id": r.id,
            "timestamp": r.timestamp,
            "status": r.status,
            "candidate_id": r.candidate_id,
            "fit_score": analysis.get("fit_score"),
            "final_recommendation": analysis.get("final_recommendation"),
            "risk_level": analysis.get("risk_level"),
            "candidate_name": candidate_name,
            "job_role": job_role,
        })

    return output


# ─── Result status update ─────────────────────────────────────────────────────

@results_router.put("/results/{result_id}/status")
def update_status(
    result_id: int,
    body: dict,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == result_id,
        ScreeningResult.tenant_id == current_user.tenant_id,
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    allowed_statuses = {"pending", "shortlisted", "rejected", "in-review", "hired"}
    new_status = body.get("status", "")
    if new_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {allowed_statuses}")

    old_status = result.status
    result.status = new_status
    result.status_updated_at = datetime.now(timezone.utc)
    log_field_change(
        db=db,
        tenant_id=current_user.tenant_id,
        entity_type="screening_result",
        entity_id=result.id,
        field_name="status",
        old_value=old_status,
        new_value=new_status,
        user_id=current_user.id,
    )
    log_tenant_event(
        db,
        actor=current_user,
        action="result.status_change",
        resource_type="screening_result",
        resource_id=result.id,
        details={"old_status": old_status, "new_status": new_status},
    )
    _sync_pipeline_status_for_result(db, result.id, new_status)
    db.commit()

    # Fire-and-forget auto-trigger evaluation using a fresh DB session.
    if result.candidate_id:
        background_tasks.add_task(
            _schedule_auto_trigger,
            current_user.tenant_id,
            result.candidate_id,
            result.id,
            new_status,
        )

    return {"id": result_id, "status": new_status}


# ─── Re-score endpoint (post-analysis skill edit) ────────────────────────────

@results_router.post("/analyze/{result_id}/rescore")
def rescore_endpoint(
    result_id: int,
    body: RescoreRequest,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Re-score an existing analysis with overridden skill classification.

    Does NOT re-run the full pipeline or call the LLM — this is a quick
    recalculation using stored data.  Only skill-related scores change
    (skill_match + fit_score).  Changes are persisted to the database.
    """
    # ── 1. Load screening result & verify tenant ownership ───────────────────
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == result_id,
        ScreeningResult.tenant_id == current_user.tenant_id,
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    # Rescore is a deterministic recalculation of stored evidence, not a new
    # AI processing of candidate data. Consent policy is not applied here.

    # ── 2. Parse stored JSON blobs ────────────────────────────────────────────
    try:
        analysis = json.loads(result.analysis_result)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Stored analysis_result is corrupt")

    try:
        parsed_data = json.loads(result.parsed_data)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Stored parsed_data is corrupt")

    # ── 3. Extract candidate skills from parsed_data ──────────────────────────
    # The pipeline stores skills in multiple places; gather them all.
    candidate_skills_raw: list[str] = list(parsed_data.get("skills", []))
    # Also check the candidate_profile / skills_identified path
    cp = analysis.get("candidate_profile", {})
    candidate_skills_raw.extend(cp.get("skills_identified", []))
    # Deduplicate (case-insensitive)
    seen_lower: set[str] = set()
    candidate_skills: list[str] = []
    for s in candidate_skills_raw:
        if isinstance(s, str) and s.lower() not in seen_lower:
            seen_lower.add(s.lower())
            candidate_skills.append(s)

    # ── 4. Apply new skill classification ─────────────────────────────────────
    jd_analysis = analysis.get("jd_analysis", {})
    # Save originals
    jd_analysis.setdefault("original_required_skills", jd_analysis.get("required_skills", []))
    jd_analysis.setdefault("original_nice_to_have_skills", jd_analysis.get("nice_to_have_skills", []))

    # Extract proficiency data and normalise skills to plain strings
    proficiency_map: dict[str, str] = {}
    def _normalise_skill_list(skill_list):
        result = []
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
            else:
                result.append(str(item))
        return result

    required_skills = _normalise_skill_list(body.required_skills)
    nice_to_have_skills = _normalise_skill_list(body.nice_to_have_skills)

    jd_analysis["required_skills"] = required_skills
    jd_analysis["nice_to_have_skills"] = nice_to_have_skills
    jd_analysis["skill_overrides_applied"] = True
    if proficiency_map:
        jd_analysis["skill_proficiency_requirements"] = proficiency_map
    else:
        jd_analysis.pop("skill_proficiency_requirements", None)

    # ── 5. Re-run skill matching (case-insensitive) ──────────────────────────
    req_lower = {s.lower() for s in required_skills if isinstance(s, str)}
    nice_lower = {s.lower() for s in nice_to_have_skills if isinstance(s, str)}
    cand_lower = {s.lower() for s in candidate_skills}

    matched_required = [s for s in required_skills if s.lower() in cand_lower]
    missing_required = [s for s in required_skills if s.lower() not in cand_lower]
    matched_nice_to_have = [s for s in nice_to_have_skills if s.lower() in cand_lower]
    missing_nice_to_have = [s for s in nice_to_have_skills if s.lower() not in cand_lower]

    # Backward-compat unions
    matched_skills = matched_required + matched_nice_to_have
    missing_skills = missing_required + missing_nice_to_have

    required_match_pct = (len(matched_required) / max(len(required_skills), 1)) * 100
    nice_to_have_match_pct = (len(matched_nice_to_have) / max(len(nice_to_have_skills), 1)) * 100

    # ── 5b. Proficiency-aware scoring (if proficiency data provided) ────────
    proficiency_analysis = {}
    prof_factor = None
    if proficiency_map and matched_required:
        from app.backend.services.hybrid_pipeline import (
            _compute_proficiency_score,
            _estimate_candidate_proficiency,
        )
        # Build candidate skills data for proficiency estimation
        candidate_skills_data = {
            "skills_identified": candidate_skills,
            "total_effective_years": analysis.get("candidate_profile", {}).get("total_effective_years", 0),
            "work_experience": parsed_data.get("work_experience", []),
        }
        prof_factor = _compute_proficiency_score(
            matched_required, candidate_skills_data, proficiency_map,
        )
        # Build proficiency_analysis details
        for skill in matched_required:
            req_level = proficiency_map.get(skill.lower())
            if req_level:
                cand_level = _estimate_candidate_proficiency(skill, candidate_skills_data)
                from app.backend.services.hybrid_pipeline import PROFICIENCY_LEVELS
                req_rank = PROFICIENCY_LEVELS.get(req_level, 2)
                cand_rank = PROFICIENCY_LEVELS.get(cand_level, 2)
                if cand_rank >= req_rank:
                    match_factor = 1.0
                elif cand_rank == req_rank - 1:
                    match_factor = 0.6
                else:
                    match_factor = 0.3
                proficiency_analysis[skill] = {
                    "required": req_level,
                    "estimated_candidate": cand_level,
                    "match_factor": match_factor,
                }

    # ── 6. Recalculate skill_score (70/30 weighting) ──────────────────────────
    if nice_to_have_skills:
        req_ratio = required_match_pct
        if prof_factor is not None:
            req_ratio = required_match_pct * prof_factor
        skill_score = round((req_ratio * 0.70) + (nice_to_have_match_pct * 0.30))
    else:
        req_ratio = required_match_pct
        if prof_factor is not None:
            req_ratio = required_match_pct * prof_factor
        skill_score = round(req_ratio)

    # ── 7. Recalculate fit_score using compute_fit_score() ────────────────────
    sb = analysis.get("score_breakdown", {})
    exp_match_raw = sb.get("experience_match", 50)
    candidate_profile = analysis.get("candidate_profile", {})
    if isinstance(exp_match_raw, dict):
        exp_score = scalar_breakdown_score(exp_match_raw, 50)
        actual_years = exp_match_raw.get("actual_years")
        if actual_years is None:
            actual_years = candidate_profile.get("total_effective_years", 0)
        required_years = exp_match_raw.get("required_years")
        if required_years is None:
            required_years = candidate_profile.get("required_years", 0)
    else:
        exp_score = scalar_breakdown_score(exp_match_raw, 50)
        actual_years = candidate_profile.get("total_effective_years", 0)
        required_years = candidate_profile.get("required_years", 0)

    all_scores = {
        "skill_score":     skill_score,
        "exp_score":       exp_score,
        "arch_score":      sb.get("architecture", 50),
        "edu_score":       sb.get("education", 60),
        "timeline_score":  sb.get("stability", sb.get("timeline", 85)),
        "domain_score":    sb.get("domain_fit", 60),
        "actual_years":    actual_years,
        "required_years":  required_years,
        "matched_skills":  matched_skills,
        "missing_skills":  missing_skills,
        "required_count":  len(required_skills),
        "employment_gaps": analysis.get("edu_timeline_analysis", {}).get("employment_gaps", []),
        "short_stints":    analysis.get("edu_timeline_analysis", {}).get("short_stints", []),
    }

    # Load tenant scoring weights (same pattern as analyze_endpoint)
    scoring_weights = None
    try:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant and tenant.scoring_weights:
            from app.backend.services.feature_flag_service import is_feature_enabled
            if is_feature_enabled(db, current_user.tenant_id, "custom_weights"):
                scoring_weights = json.loads(tenant.scoring_weights)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError, SQLAlchemyError) as e:
        log.warning(
            "Non-critical: Failed to load tenant weights, using defaults: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "VALIDATION_ERROR"},
        )

    # Convert weights to internal schema (mirrors _run_python_phase)
    new_weights = convert_to_new_schema(scoring_weights)
    internal_weights = {
        "skills":       new_weights.get("core_competencies", 0.30),
        "experience":   new_weights.get("experience", 0.20),
        "architecture": new_weights.get("role_excellence", 0.15),
        "education":    new_weights.get("education", 0.10),
        "timeline":     new_weights.get("career_trajectory", 0.10),
        "domain":       new_weights.get("domain_fit", 0.10),
        "risk":         new_weights.get("risk", 0.15),
    }

    jd_for_fit = {
        "required_skills": required_skills,
        "nice_to_have_skills": nice_to_have_skills,
    }

    fit_r = compute_fit_score(all_scores, internal_weights, jd_analysis=jd_for_fit)

    # Preserve deterministic score if it exists — rescore only changes skill components
    # The deterministic engine applies caps based on core_skill_match and domain_match;
    # since we are not re-running domain detection, keep the deterministic score logic
    # but adjust it if the new skill match is worse.
    deterministic_score = analysis.get("deterministic_score", fit_r["fit_score"])
    det_features = analysis.get("deterministic_features", {})
    if det_features:
        # Recompute core_skill_match based on new required match
        new_core_ratio = len(matched_required) / max(len(required_skills), 1)
        det_features = dict(det_features)
        det_features["core_skill_match"] = new_core_ratio
        # Re-derive secondary_skill_match from nice-to-have
        new_secondary_ratio = len(matched_nice_to_have) / max(len(nice_to_have_skills), 1) if nice_to_have_skills else 0
        det_features["secondary_skill_match"] = new_secondary_ratio

        # Re-run deterministic score with updated features
        eligibility = None
        jd_domain = {}
        candidate_domain = {}
        try:
            from app.backend.services.eligibility_service import check_eligibility
            from app.backend.services.fit_scorer import compute_deterministic_score

            # Use stored domain data — we are NOT re-running domain detection
            jd_domain = analysis.get("jd_domain", {})
            candidate_domain = analysis.get("candidate_domain", {})
            eligibility = check_eligibility(
                jd_domain=jd_domain,
                candidate_domain=candidate_domain,
                core_skill_match=det_features["core_skill_match"],
                relevant_experience=det_features.get("relevant_experience", 0),
            )
            deterministic_score = compute_deterministic_score(det_features, eligibility, new_weights)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            log.warning(
                "Deterministic re-score failed, using fit_score: %s", e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            deterministic_score = fit_r["fit_score"]

    # Blend deterministic score with fit_score for eligible candidates
    # (same logic as _run_python_phase in hybrid_pipeline.py)
    if det_features:
        if eligibility is not None and eligibility.eligible:
            final_fit_score = int(0.6 * fit_r["fit_score"] + 0.4 * deterministic_score)
        else:
            final_fit_score = deterministic_score
        final_fit_score = max(0, min(100, final_fit_score))
    else:
        final_fit_score = fit_r["fit_score"]
    final_recommendation = fit_r["final_recommendation"]
    # Override recommendation based on deterministic score thresholds
    if det_features:
        if final_fit_score >= RECOMMENDATION_THRESHOLDS["shortlist"]:
            final_recommendation = "Shortlist"
        elif final_fit_score >= RECOMMENDATION_THRESHOLDS["consider"]:
            final_recommendation = "Consider"
        else:
            final_recommendation = "Reject"

    # ── 8. Update analysis_result JSON ────────────────────────────────────────
    # Skill analysis
    skill_analysis = analysis.get("skill_analysis", {})
    skill_analysis.update({
        "matched_skills":        matched_skills,
        "missing_skills":        missing_skills,
        "matched_required":      matched_required,
        "missing_required":      missing_required,
        "matched_nice_to_have":  matched_nice_to_have,
        "missing_nice_to_have":  missing_nice_to_have,
        "required_match_pct":    required_match_pct,
        "nice_to_have_match_pct": nice_to_have_match_pct,
        "skill_score":           skill_score,
        "required_count":        len(required_skills),
    })
    if proficiency_analysis:
        skill_analysis["proficiency_analysis"] = proficiency_analysis

    # Top-level fields
    refresh_interview_questions_in_analysis(
        analysis,
        parsed_data=parsed_data,
        kit_status=getattr(result, "interview_kit_status", None),
    )

    # fit_score is the public final/effective score. deterministic_score is the
    # uncapped component used for blending and must not be overwritten by the blend.
    analysis.update({
        "skill_analysis":        skill_analysis,
        "jd_analysis":          jd_analysis,
        "fit_score":            final_fit_score,
        "final_recommendation": final_recommendation,
        "risk_level":           fit_r["risk_level"],
        "risk_signals":         fit_r["risk_signals"],
        "score_breakdown":      fit_r["score_breakdown"],
        "matched_skills":       matched_skills,
        "missing_skills":       missing_skills,
        "required_skills_count": len(required_skills),
        "deterministic_score":  deterministic_score,
        "component_fit_score":  fit_r["fit_score"],
        "deterministic_features": det_features if det_features else analysis.get("deterministic_features"),
    })

    # ── 9. Persist to database ────────────────────────────────────────────────
    from app.backend.routes.analyze_helpers import _write_ai_decision_log

    result.analysis_result = json.dumps(analysis, default=_json_default)
    _populate_denormalized_columns(result, analysis)
    _write_ai_decision_log(
        db, result, analysis, decision_type="RESCORE", actor_id=current_user.id, required=True,
    )
    db.commit()

    log.info(json.dumps({
        "event":             "rescore_complete",
        "result_id":         result_id,
        "tenant_id":         current_user.tenant_id,
        "new_fit_score":     final_fit_score,
        "new_skill_score":   skill_score,
        "required_matched":  len(matched_required),
        "required_total":    len(required_skills),
        "nice_matched":      len(matched_nice_to_have),
        "nice_total":        len(nice_to_have_skills),
    }))

    return analysis


# ─── Narrative polling endpoint ───────────────────────────────────────────────


from app.backend.services.screening_outcome import outcome_fields_from_result


def _outcome_payload(result) -> dict:
    return outcome_fields_from_result(result)



@results_router.get("/analysis/{analysis_id}/narrative")
def get_narrative(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Poll for LLM narrative after Python results are returned.
    
    Returns:
      - {"status": "ready", "narrative": {...}} if narrative is available
      - {"status": "pending"} if LLM is still processing
      - {"status": "failed", "error": "...", "narrative": {...}} if LLM failed (includes fallback)
      - 404 if analysis not found or not owned by user's tenant
    """
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == analysis_id,
        ScreeningResult.tenant_id == current_user.tenant_id,
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    # Use narrative_status column if available, fall back to checking narrative_json
    status = getattr(result, 'narrative_status', None)
    
    if status == 'fallback':
        # Fallback — return fallback narrative + error message
        narrative = None
        if result.narrative_json:
            try:
                narrative = json.loads(result.narrative_json)
            except json.JSONDecodeError:
                pass
        return {
            "status": "fallback",
            "error": result.narrative_error or "AI analysis encountered an error",
            "narrative": narrative,
            "interview_kit_status": getattr(result, "interview_kit_status", None),
            "interview_kit_error": getattr(result, "interview_kit_error", None),
            "voice_strategy_status": getattr(result, "voice_strategy_status", None),
        }

    if status == 'failed':
        # Failed — return fallback narrative + error message (legacy, should not happen with new fallback logic)
        narrative = None
        if result.narrative_json:
            try:
                narrative = json.loads(result.narrative_json)
            except json.JSONDecodeError:
                pass
        return {
            "status": "failed",
            "error": result.narrative_error or "AI analysis encountered an error",
            "narrative": narrative,
            "interview_kit_status": getattr(result, "interview_kit_status", None),
            "interview_kit_error": getattr(result, "interview_kit_error", None),
            "voice_strategy_status": getattr(result, "voice_strategy_status", None),
        }

    if status == 'ready' or (status is None and result.narrative_json):
        # Ready — return narrative
        if result.narrative_json:
            try:
                narrative = json.loads(result.narrative_json)
                kit_status = getattr(result, "interview_kit_status", None) or "pending"
                voice_strategy_status = getattr(result, "voice_strategy_status", None) or "pending"
                return {
                    "status": "ready",
                    "narrative": narrative,
                    "interview_kit_status": kit_status,
                    "interview_kit_error": getattr(result, "interview_kit_error", None),
                    "voice_strategy_status": voice_strategy_status,
                    **_outcome_payload(result),
                }
            except json.JSONDecodeError:
                return {"status": "pending"}
    
    # Still pending or processing
    kit_status = getattr(result, "interview_kit_status", None)
    kit_error = getattr(result, "interview_kit_error", None)
    voice_strategy_status = getattr(result, "voice_strategy_status", None)
    payload = {"status": status or "pending", **_outcome_payload(result)}
    if kit_status:
        payload["interview_kit_status"] = kit_status
    if kit_error:
        payload["interview_kit_error"] = kit_error
    if voice_strategy_status:
        payload["voice_strategy_status"] = voice_strategy_status
    return payload


# ─── JD Templates Endpoints ───────────────────────────────────────────────────

@results_router.get("/jd-templates")
async def get_jd_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
):
    """Get available JD templates for common roles.

    These templates help recruiters write JDs that the system can parse effectively.
    """
    from app.backend.services.jd_template_service import (
        get_all_templates,
        get_templates_by_category,
        get_categories,
    )

    if category:
        return {
            "templates": get_templates_by_category(category),
            "category": category,
        }

    return {
        "templates": get_all_templates(),
        "categories": get_categories(),
    }


@results_router.get("/jd-templates/{template_id}")
async def get_jd_template(
    template_id: str,
    company_name: Optional[str] = Query(None, description="Company name for customization"),
    location: Optional[str] = Query(None, description="Job location"),
):
    """Get a specific JD template and optionally generate full JD text."""
    from app.backend.services.jd_template_service import (
        get_template,
        generate_jd_from_template,
    )

    template = get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Generate full JD if customization provided
    full_jd = None
    if company_name or location:
        full_jd = generate_jd_from_template(
            template_id,
            {"company_name": company_name, "location": location}
        )

    return {
        "template": template,
        "generated_jd": full_jd,
    }
