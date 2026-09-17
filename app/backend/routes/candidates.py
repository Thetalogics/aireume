"""
Candidate management routes.

New endpoints vs original:
  POST /{candidate_id}/analyze-jd   — re-analyze existing candidate against a new JD
                                       (no file upload — uses stored profile)

Enriched responses:
  GET  ""                — now returns current_role, total_years_exp
  GET  "/{id}"           — now returns full profile fields + skills_snapshot
"""
import json
import logging
from datetime import datetime, date, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional

from app.backend.db.database import get_db, get_read_db
from app.backend.middleware.auth import get_current_user, require_admin, require_feature
from app.backend.middleware.rbac import require_recruiter_or_admin, require_active_recruiter
from app.backend.models.db_models import Candidate, ScreeningResult, CandidateNote, User, RoleTemplate, HiringOutcome, FieldAuditLog, RequisitionCandidate
from app.backend.models.schemas import CandidateNameUpdate, AnalyzeJdRequest, CandidateSkillCompareRequest
from app.backend.services.audit_service import log_field_change
from app.backend.services.interview_kit_generator import refresh_interview_questions_in_analysis
from app.backend.routes.candidates_jd import jd_router, _VALID_STATUSES
from app.backend.services.outcome_service import (
    record_outcome, record_feedback, get_outcome_for_result, get_outcomes_for_jd, compute_skill_patterns
)
from app.backend.services.screening_outcome import outcome_fields_from_result

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


# ─── Bulk Import ───────────────────────────────────────────────────────────────

@router.post("/import/csv")
def import_candidates_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Bulk import candidates from CSV (name,email required; phone/notes optional)."""
    import csv
    import io

    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")
    raw = file.file.read()
    if len(raw) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV too large (max 1MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV is missing a header row")
    created = []
    errors = []
    for index, row in enumerate(reader, start=2):
        if index > 501:
            errors.append({"row": index, "error": "Row limit of 500 exceeded"})
            break
        name = (row.get("name") or "").strip()[:200]
        email = (row.get("email") or "").strip().lower()[:255]
        phone = (row.get("phone") or "").strip()[:50] or None
        if not name or not email or "@" not in email:
            errors.append({"row": index, "error": "name and valid email are required"})
            continue
        existing = (
            db.query(Candidate)
            .filter(Candidate.tenant_id == current_user.tenant_id, Candidate.email == email)
            .first()
        )
        if existing:
            errors.append({"row": index, "error": "duplicate email in this workspace", "candidate_id": existing.id})
            continue
        cand = Candidate(
            tenant_id=current_user.tenant_id,
            name=name,
            email=email,
            phone=phone,
        )
        db.add(cand)
        db.flush()
        note_text = (row.get("notes") or "").strip()[:2000]
        if note_text:
            db.add(CandidateNote(
                candidate_id=cand.id,
                user_id=current_user.id,
                tenant_id=current_user.tenant_id,
                text=note_text,
            ))
        db.add(cand)
        db.flush()
        created.append({"id": cand.id, "email": email})
    db.commit()
    return {"created": created, "errors": errors, "created_count": len(created)}


class CandidateMergeRequest(BaseModel):
    source_candidate_id: int
    target_candidate_id: int
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/merge")
def merge_candidate_records(
    body: CandidateMergeRequest,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    from app.backend.services.candidate_merge_service import MergeError, merge_candidates

    try:
        result = merge_candidates(
            db,
            current_user.tenant_id,
            body.source_candidate_id,
            body.target_candidate_id,
            current_user.id,
            reason=body.reason,
        )
        db.commit()
        return result
    except MergeError as exc:
        db.rollback()
        status = 403 if exc.code == "cross_tenant" else 400
        if exc.code == "not_found":
            status = 404
        raise HTTPException(status_code=status, detail=exc.message) from exc


def _json_default(obj):
    """Handle non-serializable types for json.dumps (datetime, date, Decimal)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


@router.get("")
def list_candidates(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    narrative_status: Optional[str] = Query(None),
    skill: Optional[str] = Query(None),
    requisition_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    # Validate status filter early
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )

    query = db.query(Candidate).filter(
        Candidate.tenant_id == current_user.tenant_id,
        Candidate.merged_into_id.is_(None),
    )
    from app.backend.middleware.rbac import apply_hm_candidate_scope
    query = apply_hm_candidate_scope(query, db, current_user)

    if requisition_id is not None:
        req_candidate_ids = (
            db.query(RequisitionCandidate.candidate_id)
            .filter(RequisitionCandidate.requisition_id == requisition_id)
            .distinct()
            .subquery()
        )
        query = query.filter(Candidate.id.in_(req_candidate_ids))

    # When status filter is active, we must join through ScreeningResult to find
    # candidates that have at least one result with that status.
    if status:
        candidate_ids_with_status = (
            db.query(ScreeningResult.candidate_id)
            .filter(
                ScreeningResult.tenant_id == current_user.tenant_id,
                ScreeningResult.status == status,
            )
            .distinct()
            .subquery()
        )
        query = query.filter(Candidate.id.in_(candidate_ids_with_status))

    # Filter by narrative_status (e.g., processing) — separate from status
    if narrative_status:
        candidate_ids_with_narrative = (
            db.query(ScreeningResult.candidate_id)
            .filter(
                ScreeningResult.tenant_id == current_user.tenant_id,
                ScreeningResult.narrative_status == narrative_status,
            )
            .distinct()
            .subquery()
        )
        query = query.filter(Candidate.id.in_(candidate_ids_with_narrative))

    if search:
        q = f"%{search}%"
        query = query.filter(
            (Candidate.name.ilike(q))
            | (Candidate.email.ilike(q))
            | (Candidate.current_role.ilike(q))
            | (Candidate.current_company.ilike(q))
            | (Candidate.phone.ilike(q))
        )

    # Skill filter: find candidates whose screening results contain that skill
    if skill:
        skill_token = (skill or "").strip().replace('"', "")
        skill_like = f'%"{skill_token}"%'
        candidate_ids_with_skill = (
            db.query(ScreeningResult.candidate_id)
            .filter(
                ScreeningResult.tenant_id == current_user.tenant_id,
                ScreeningResult.analysis_result.ilike(skill_like),
            )
            .distinct()
            .subquery()
        )
        query = query.filter(Candidate.id.in_(candidate_ids_with_skill))

    total      = query.count()
    candidates = (
        query.order_by(Candidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Fetch all screening results for these candidates in a single query
    candidate_ids = [c.id for c in candidates]
    results_map = {}
    if candidate_ids:
        all_results = (
            db.query(ScreeningResult)
            .filter(ScreeningResult.candidate_id.in_(candidate_ids))
            .order_by(ScreeningResult.timestamp.desc())
            .all()
        )
        for r in all_results:
            if r.candidate_id not in results_map:
                results_map[r.candidate_id] = []
            results_map[r.candidate_id].append(r)

    result = []
    for c in candidates:
        candidate_results = results_map.get(c.id, [])
        result_count = len(candidate_results)
        
        # Find the result with the highest fit_score and the latest status
        best_score = None
        latest_status = "pending"
        latest_result_id = None
        if candidate_results:
            try:
                scores = []
                for r in candidate_results:
                    analysis = json.loads(r.analysis_result)
                    fit_score = analysis.get("fit_score")
                    if fit_score is not None:
                        scores.append(fit_score)
                
                if scores:
                    best_score = max(scores)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                logger.warning(
                    "Non-critical: Failed to parse analysis results for best_score: %s", e,
                    extra={"error_code": "VALIDATION_ERROR"},
                )

            # Latest result (first in desc-ordered list) provides the status
            latest = candidate_results[0]
            latest_status = latest.status or "pending"
            latest_result_id = latest.id
            latest_call_fit_score = latest.call_fit_score
            latest_call_source = latest.call_source
            latest_consolidated = latest.consolidated_recommendation
        else:
            latest_call_fit_score = None
            latest_call_source = None
            latest_consolidated = None

        # Prefer deterministic score for display when available
        if candidate_results:
            det_scores = [
                r.deterministic_score for r in candidate_results
                if r.deterministic_score is not None
            ]
            if det_scores:
                best_score = max(det_scores)
            elif latest_call_fit_score is not None and best_score is not None:
                pass  # keep analysis best_score as analysis leg

        # Extract top 5 matched skills from latest screening result
        matched_skills = []
        if candidate_results:
            try:
                latest_analysis = json.loads(candidate_results[0].analysis_result)
                matched_skills = latest_analysis.get("matched_skills", [])[:5]
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
                logger.warning(
                    "Non-critical: Failed to parse matched_skills: %s", e,
                    extra={"error_code": "VALIDATION_ERROR"},
                )

        result.append({
            "id":              c.id,
            "name":            c.name,
            "email":           c.email,
            "phone":           c.phone,
            "created_at":      c.created_at,
            "result_count":    result_count,
            "best_score":      best_score,
            "latest_status":   latest_status,
            "latest_result_id": latest_result_id,
            "call_fit_score":  latest_call_fit_score,
            "call_source":     latest_call_source,
            "consolidated_recommendation": latest_consolidated,
            "matched_skills":  matched_skills,
            # Enriched profile fields
            "current_role":    c.current_role,
            "current_company": c.current_company,
            "total_years_exp": c.total_years_exp,
            "profile_quality": c.profile_quality,
        })

    return {"candidates": result, "total": total, "page": page, "page_size": page_size}


@router.get("/search")
def search_candidates(
    q: str = Query(..., min_length=2, description="Search query"),
    fields: Optional[str] = Query(None, description="Comma-separated fields to search: name,email,skills,company,title,education,raw_text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Advanced candidate search across multiple fields.

    Searches across name, email, skills, current company, current role,
    education, work experience, and raw resume text by default. Use the
    `fields` parameter to restrict which fields are searched.
    """
    all_fields = {
        "name": Candidate.name,
        "email": Candidate.email,
        "skills": Candidate.parsed_skills,
        "company": Candidate.current_company,
        "title": Candidate.current_role,
        "education": Candidate.parsed_education,
        "experience": Candidate.parsed_work_exp,
        "raw_text": Candidate.raw_resume_text,
    }

    if fields:
        search_fields = {k: v for k, v in all_fields.items() if k in fields.split(",")}
        if not search_fields:
            raise HTTPException(status_code=400, detail=f"No valid fields specified. Available: {list(all_fields.keys())}")
    else:
        search_fields = all_fields

    query = db.query(Candidate).filter(Candidate.tenant_id == current_user.tenant_id)
    from app.backend.middleware.rbac import apply_hm_candidate_scope
    query = apply_hm_candidate_scope(query, db, current_user)

    like_term = f"%{q}%"
    from sqlalchemy import or_
    conditions = []
    for field_col in search_fields.values():
        conditions.append(field_col.ilike(like_term))
    query = query.filter(or_(*conditions))

    total = query.count()
    candidates = (
        query.order_by(Candidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    result = []
    for c in candidates:
        result.append({
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "current_role": c.current_role,
            "current_company": c.current_company,
            "total_years_exp": c.total_years_exp,
            "profile_quality": c.profile_quality,
            "created_at": c.created_at,
        })

    return {"candidates": result, "total": total, "page": page, "page_size": page_size, "query": q}


@router.get("/results/{result_id}")
def get_screening_result(
    result_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch full screening result by ID for report display.

    Used by the frontend ReportPage when navigating via URL with ?id= param
    and no result data is available in location.state or sessionStorage.
    Returns the same data shape that the report page expects.
    """
    result = db.query(ScreeningResult).options(
        joinedload(ScreeningResult.candidate),
        joinedload(ScreeningResult.role_template),
    ).filter(
        ScreeningResult.id == result_id,
        ScreeningResult.tenant_id == current_user.tenant_id,
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    if result.candidate_id:
        from app.backend.middleware.rbac import require_candidate_read_access
        require_candidate_read_access(db, current_user, result.candidate_id)

    candidate = result.candidate
    role_template = result.role_template

    # ── Parse JSON columns ──────────────────────────────────────────────────
    analysis = {}
    if result.analysis_result:
        try:
            analysis = json.loads(result.analysis_result) if isinstance(result.analysis_result, str) else result.analysis_result
        except (json.JSONDecodeError, TypeError, ValueError):
            analysis = {}

    parsed = {}
    if result.parsed_data:
        try:
            parsed = json.loads(result.parsed_data) if isinstance(result.parsed_data, str) else result.parsed_data
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}

    narrative_data = {}
    if result.narrative_json:
        try:
            narrative_data = json.loads(result.narrative_json) if isinstance(result.narrative_json, str) else result.narrative_json
        except (json.JSONDecodeError, TypeError, ValueError):
            narrative_data = {}

    # ── Merge analysis + narrative (same logic as get_candidate) ────────────
    merged_data = dict(analysis)
    if narrative_data:
        narrative_fields = {
            "ai_enhanced", "fit_summary", "strengths", "concerns", "weaknesses",
            "recommendation_rationale", "explainability", "interview_questions",
            "candidate_profile_summary", "dealbreakers", "differentiators", "hiring_decision",
        }
        for field in narrative_fields:
            if field in narrative_data and narrative_data[field] not in (None, '', [], {}):
                merged_data[field] = narrative_data[field]

    # ── Ensure key fields with fallback to parsed_data ──────────────────────
    contact_info = merged_data.get("contact_info") or parsed.get("contact_info") or {}
    if candidate:
        if not contact_info.get("email") and candidate.email:
            contact_info["email"] = candidate.email
        if not contact_info.get("phone") and candidate.phone:
            contact_info["phone"] = candidate.phone
        if not contact_info.get("name") and candidate.name:
            contact_info["name"] = candidate.name
    merged_data["contact_info"] = contact_info

    if not merged_data.get("candidate_profile") and candidate:
        work_exp = parsed.get("work_experience", [])
        merged_data["candidate_profile"] = {
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "skills_identified": parsed.get("skills", []),
            "education": parsed.get("education", []),
            "work_experience": work_exp,
            "career_summary": "",
            "total_effective_years": candidate.total_years_exp or 0,
            "current_role": work_exp[0].get("title", "") if work_exp else (candidate.current_role or ""),
            "current_company": work_exp[0].get("company", "") if work_exp else (candidate.current_company or ""),
        }

    if not merged_data.get("work_experience"):
        merged_data["work_experience"] = parsed.get("work_experience", [])

    merged_data["requisition_id"] = result.requisition_id
    merged_data["tenant_id"] = result.tenant_id

    refresh_interview_questions_in_analysis(
        merged_data,
        parsed_data=parsed,
        kit_status=getattr(result, "interview_kit_status", None),
    )

    # Set defaults for all fields expected by ReportPage / ResultCard
    merged_data.setdefault("fit_score", None)
    merged_data.setdefault("job_role", None)
    merged_data.setdefault("final_recommendation", "Pending")
    merged_data.setdefault("risk_level", None)
    merged_data.setdefault("score_breakdown", {})
    merged_data.setdefault("strengths", [])
    merged_data.setdefault("weaknesses", [])
    merged_data.setdefault("concerns", [])
    merged_data.setdefault("risk_signals", [])
    merged_data.setdefault("employment_gaps", [])
    merged_data.setdefault("skill_analysis", {})
    merged_data.setdefault("jd_analysis", {})
    merged_data.setdefault("edu_timeline_analysis", {})
    merged_data.setdefault("education_analysis", None)
    merged_data.setdefault("matched_skills", [])
    merged_data.setdefault("missing_skills", [])
    merged_data.setdefault("adjacent_skills", [])
    merged_data.setdefault("required_skills_count", 0)
    merged_data.setdefault("analysis_quality", "low")
    merged_data.setdefault("pipeline_errors", [])
    merged_data.setdefault("score_rationales", {})
    merged_data.setdefault("risk_summary", {})
    merged_data.setdefault("skill_depth", {})
    merged_data.setdefault("status", result.status or "pending")
    # narrative_pending / ai_enhanced are computed from narrative_status, not stored
    merged_data.pop("narrative_pending", None)
    merged_data.pop("ai_enhanced", None)

    # ── Resolve candidate name (candidate.name > parsed > contact_info) ─────
    candidate_name = None
    if candidate and candidate.name:
        candidate_name = candidate.name.strip()
    if not candidate_name:
        candidate_name = (
            (merged_data.get("candidate_name") or "").strip() or
            (contact_info.get("name") or "").strip() or
            (merged_data.get("candidate_profile", {}).get("name") or "").strip() or
            None
        )

    # ── Resolve JD name ─────────────────────────────────────────────────────
    jd_name = role_template.name if role_template else None

    # ── Build response (same structure as get_candidate history items) ──────
    response_data = {
        "id":                   result.id,
        "result_id":            result.id,
        "analysis_id":          result.id,
        "timestamp":            result.timestamp,
        "candidate_id":         result.candidate_id,
        "candidate_name":       candidate_name,
        "role_template_id":     result.role_template_id,
        "jd_name":              jd_name,
        "deterministic_score":  result.deterministic_score,
        "status_updated_at":    result.status_updated_at,
        "narrative_status":     result.narrative_status or "pending",
        "narrative_error":      result.narrative_error,
        "interview_kit_status": getattr(result, "interview_kit_status", None) or "pending",
        "interview_kit_error": getattr(result, "interview_kit_error", None),
        "voice_strategy_status": getattr(result, "voice_strategy_status", None) or "pending",
        **outcome_fields_from_result(result),
        "ai_enhanced":          result.narrative_status == "ready" and result.narrative_json is not None,
        "narrative_pending":    result.narrative_status in ("pending", "processing"),
    }
    response_data.update(merged_data)

    # Re-apply resolved name (merged_data.update may overwrite with stale value)
    response_data["candidate_name"] = candidate_name
    response_data["jd_name"] = jd_name
    response_data["deterministic_score"] = result.deterministic_score
    response_data["status_updated_at"] = result.status_updated_at
    response_data["role_template_id"] = result.role_template_id
    response_data["interview_kit_status"] = getattr(result, "interview_kit_status", None) or "pending"
    response_data["interview_kit_error"] = getattr(result, "interview_kit_error", None)
    response_data["voice_strategy_status"] = getattr(result, "voice_strategy_status", None) or "pending"
    response_data.update(outcome_fields_from_result(result))

    return response_data


@router.get("/pipeline", dependencies=[Depends(require_feature("pipeline"))])
def get_candidate_pipeline(
    jd_id: Optional[int] = Query(None, description="Filter by JD (role_template_id)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return candidates grouped by their latest screening status for Kanban view.

    Each candidate appears once, in the column matching the status of their
    most recent ScreeningResult. Cards are ordered by status_updated_at DESC
    (most recently moved first), falling back to result timestamp.

    When jd_id is provided, only screening results where role_template_id = jd_id
    are considered. Candidates with no matching results are excluded.
    """
    columns = {status: [] for status in _VALID_STATUSES}
    counts = {status: 0 for status in _VALID_STATUSES}

    candidates = (
        db.query(Candidate)
        .filter(Candidate.tenant_id == current_user.tenant_id)
        .all()
    )

    if not candidates:
        return {"columns": columns, "counts": counts}

    candidate_ids = [c.id for c in candidates]

    # Fetch all active screening results for these candidates in a single query
    results_query = db.query(ScreeningResult).filter(
        ScreeningResult.candidate_id.in_(candidate_ids),
        ScreeningResult.tenant_id == current_user.tenant_id,
        ScreeningResult.is_active == True,
    )
    if jd_id is not None:
        results_query = results_query.filter(ScreeningResult.role_template_id == jd_id)
    all_results = results_query.all()

    # Group results by candidate and collect template IDs
    results_by_candidate: dict = {}
    template_ids: set = set()
    for r in all_results:
        results_by_candidate.setdefault(r.candidate_id, []).append(r)
        if r.role_template_id:
            template_ids.add(r.role_template_id)

    # Fetch JD names in one query
    templates = {}
    if template_ids:
        for rt in db.query(RoleTemplate).filter(RoleTemplate.id.in_(template_ids)).all():
            templates[rt.id] = rt.name

    # Build candidate cards grouped by latest status
    cards_by_status = {status: [] for status in _VALID_STATUSES}

    for c in candidates:
        cand_results = results_by_candidate.get(c.id, [])

        # When filtering by JD, skip candidates with no matching results entirely
        # (they were fetched by tenant but don't belong to this JD pipeline)
        if jd_id is not None and not cand_results:
            continue

        # Best fit_score across ALL results for this candidate
        # Prefer deterministic_score (consistent with list endpoint)
        best_score = None
        for r in cand_results:
            score = r.deterministic_score
            if score is None:
                try:
                    analysis = json.loads(r.analysis_result)
                    score = analysis.get("fit_score")
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            if score is not None:
                if best_score is None or score > best_score:
                    best_score = score

        latest_status = "pending"
        latest_result_id = None
        latest_matched_skills = []
        latest_jd_name = None
        latest_outcome = outcome_fields_from_result(None)
        sort_key = datetime.min.replace(tzinfo=timezone.utc)

        if cand_results:
            # Latest result = highest timestamp (ties broken by higher id)
            sorted_results = sorted(
                cand_results,
                key=lambda r: (r.timestamp or datetime.min.replace(tzinfo=timezone.utc), r.id or 0),
                reverse=True,
            )
            latest = sorted_results[0]
            latest_status = latest.status or "pending"
            latest_result_id = latest.id
            latest_outcome = outcome_fields_from_result(latest)
            sort_key = latest.status_updated_at or latest.timestamp or datetime.min.replace(tzinfo=timezone.utc)

            try:
                analysis = json.loads(latest.analysis_result)
                latest_matched_skills = analysis.get("matched_skills", [])[:3]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

            if latest.role_template_id and latest.role_template_id in templates:
                latest_jd_name = templates[latest.role_template_id]

        card = {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "best_score": best_score,
            "current_role": c.current_role,
            "latest_result_id": latest_result_id,
            "matched_skills": latest_matched_skills,
            "jd_name": latest_jd_name,
            **latest_outcome,
        }

        cards_by_status[latest_status].append((sort_key, card))
        counts[latest_status] += 1

    # Sort each column by status_updated_at DESC, fallback to timestamp DESC
    for status in _VALID_STATUSES:
        cards_by_status[status].sort(key=lambda x: x[0], reverse=True)
        columns[status] = [card for _, card in cards_by_status[status]]

    return {"columns": columns, "counts": counts}


@router.put("/{candidate_id}/name")
def update_candidate_name(
    candidate_id: int,
    body: CandidateNameUpdate,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    old_name = candidate.name
    log_field_change(
        db=db,
        tenant_id=current_user.tenant_id,
        entity_type="candidate",
        entity_id=candidate.id,
        field_name="name",
        old_value=old_name,
        new_value=body.name,
        user_id=current_user.id,
    )
    candidate.name = body.name
    candidate.profile_updated_at = datetime.now(timezone.utc)

    # Propagate name change into screening_result analysis_result & narrative_json
    if old_name and old_name != body.name:
        results = db.query(ScreeningResult).filter(
            ScreeningResult.candidate_id == candidate_id
        ).all()
        for sr in results:
            # --- analysis_result ---
            if sr.analysis_result:
                try:
                    analysis = json.loads(sr.analysis_result) if isinstance(sr.analysis_result, str) else sr.analysis_result
                except (json.JSONDecodeError, TypeError):
                    analysis = sr.analysis_result if isinstance(sr.analysis_result, dict) else {}

                if isinstance(analysis, dict):
                    # Detect old name from the analysis itself
                    detected_old = (
                        analysis.get("candidate_name")
                        or (analysis.get("contact_info") or {}).get("name")
                        or old_name
                    )
                    # Replace in fit_summary
                    fit_summary = analysis.get("fit_summary", "")
                    if fit_summary and detected_old in fit_summary:
                        analysis["fit_summary"] = fit_summary.replace(detected_old, body.name)
                    # Replace in candidate_profile_summary
                    profile_summary = analysis.get("candidate_profile_summary", "")
                    if profile_summary and detected_old in profile_summary:
                        analysis["candidate_profile_summary"] = profile_summary.replace(detected_old, body.name)
                    # Update name fields
                    analysis["candidate_name"] = body.name
                    contact = analysis.get("contact_info")
                    if isinstance(contact, dict):
                        contact["name"] = body.name

                    sr.analysis_result = json.dumps(analysis) if isinstance(sr.analysis_result, str) else analysis

            # --- narrative_json ---
            if sr.narrative_json:
                try:
                    narrative = json.loads(sr.narrative_json) if isinstance(sr.narrative_json, str) else sr.narrative_json
                except (json.JSONDecodeError, TypeError):
                    narrative = sr.narrative_json if isinstance(sr.narrative_json, dict) else {}

                if isinstance(narrative, dict):
                    detected_old_narr = (
                        narrative.get("candidate_name")
                        or (narrative.get("contact_info") or {}).get("name")
                        or old_name
                    )
                    narr_summary = narrative.get("fit_summary", "")
                    if narr_summary and detected_old_narr in narr_summary:
                        narrative["fit_summary"] = narr_summary.replace(detected_old_narr, body.name)
                    narr_profile = narrative.get("candidate_profile_summary", "")
                    if narr_profile and detected_old_narr in narr_profile:
                        narrative["candidate_profile_summary"] = narr_profile.replace(detected_old_narr, body.name)
                    if "candidate_name" in narrative:
                        narrative["candidate_name"] = body.name
                    narr_contact = narrative.get("contact_info")
                    if isinstance(narr_contact, dict):
                        narr_contact["name"] = body.name

                    sr.narrative_json = json.dumps(narrative) if isinstance(sr.narrative_json, str) else narrative

    db.commit()
    db.refresh(candidate)
    return {"id": candidate.id, "name": candidate.name}


@router.patch("/{candidate_id}")
def update_candidate(
    candidate_id: int,
    body: CandidateNameUpdate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    log_field_change(
        db=db,
        tenant_id=current_user.tenant_id,
        entity_type="candidate",
        entity_id=candidate.id,
        field_name="name",
        old_value=candidate.name,
        new_value=body.name,
        user_id=current_user.id,
    )
    candidate.name = body.name
    db.commit()
    db.refresh(candidate)
    return {"id": candidate.id, "name": candidate.name}


@router.put("/{candidate_id}/parsed-data")
def correct_parsed_data(
    candidate_id: int,
    body: dict,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    """Correct mis-parsed resume data (skills, work experience, education, contact info).

    Accepts partial updates — only fields present in the request body are updated.
    All changes are audit-logged.

    Request body (all fields optional):
    {
        "skills": ["Python", "FastAPI", ...],
        "work_experience": [{...}, ...],
        "education": [{...}, ...],
        "contact_info": {"email": "...", "phone": "..."},
        "current_role": "Senior Engineer",
        "current_company": "Acme Corp",
        "total_years_exp": 8.5
    }
    """
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    updated_fields = []

    if "skills" in body:
        old_val = candidate.parsed_skills
        new_val = json.dumps(body["skills"], default=_json_default)
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "parsed_skills", old_val, new_val, current_user.id)
        candidate.parsed_skills = new_val
        updated_fields.append("skills")

    if "work_experience" in body:
        old_val = candidate.parsed_work_exp
        new_val = json.dumps(body["work_experience"], default=_json_default)
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "parsed_work_exp", old_val, new_val, current_user.id)
        candidate.parsed_work_exp = new_val
        updated_fields.append("work_experience")

    if "education" in body:
        old_val = candidate.parsed_education
        new_val = json.dumps(body["education"], default=_json_default)
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "parsed_education", old_val, new_val, current_user.id)
        candidate.parsed_education = new_val
        updated_fields.append("education")

    if "contact_info" in body:
        ci = body["contact_info"]
        if "email" in ci:
            log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "email", candidate.email, ci["email"], current_user.id)
            candidate.email = ci["email"]
        if "phone" in ci:
            log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "phone", candidate.phone, ci["phone"], current_user.id)
            candidate.phone = ci["phone"]
        if "name" in ci:
            log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "name", candidate.name, ci["name"], current_user.id)
            candidate.name = ci["name"]
        updated_fields.append("contact_info")

    if "current_role" in body:
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "current_role", candidate.current_role, body["current_role"], current_user.id)
        candidate.current_role = body["current_role"]
        updated_fields.append("current_role")

    if "current_company" in body:
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "current_company", candidate.current_company, body["current_company"], current_user.id)
        candidate.current_company = body["current_company"]
        updated_fields.append("current_company")

    if "total_years_exp" in body:
        log_field_change(db, current_user.tenant_id, "candidate", candidate.id, "total_years_exp", candidate.total_years_exp, body["total_years_exp"], current_user.id)
        candidate.total_years_exp = body["total_years_exp"]
        updated_fields.append("total_years_exp")

    if not updated_fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    candidate.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(candidate)

    return {
        "id": candidate.id,
        "updated_fields": updated_fields,
        "message": f"Updated {len(updated_fields)} field(s)",
    }


@router.get("/{candidate_id}/audit-log")
def get_candidate_audit_log(
    candidate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return field-level edit history for a candidate (most recent 50 entries)."""
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    from app.backend.middleware.rbac import require_candidate_read_access
    require_candidate_read_access(db, current_user, candidate_id)

    logs = (
        db.query(FieldAuditLog)
        .filter(
            FieldAuditLog.entity_type == "candidate",
            FieldAuditLog.entity_id == candidate_id,
            FieldAuditLog.tenant_id == str(current_user.tenant_id),
        )
        .order_by(FieldAuditLog.changed_at.desc())
        .limit(50)
        .all()
    )

    # Resolve changed_by user emails for display
    user_ids = {log.changed_by for log in logs if log.changed_by}
    user_map = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            user_map[u.id] = u.email

    return [
        {
            "id": log.id,
            "field_name": log.field_name,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "changed_by": log.changed_by,
            "changed_by_email": user_map.get(log.changed_by),
            "changed_at": log.changed_at.isoformat() if log.changed_at else None,
            "change_reason": log.change_reason,
        }
        for log in logs
    ]


@router.get("/{candidate_id}")
def get_candidate(
    candidate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    from app.backend.middleware.rbac import require_candidate_read_access
    require_candidate_read_access(db, current_user, candidate_id)

    results = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.candidate_id == candidate_id)
        .order_by(ScreeningResult.timestamp.desc())
        .all()
    )

    # ── Fetch RoleTemplate names for JD lookup ──────────────────────────────
    template_ids = {r.role_template_id for r in results if r.role_template_id}
    templates = {}
    if template_ids:
        for rt in db.query(RoleTemplate).filter(RoleTemplate.id.in_(template_ids)).all():
            templates[rt.id] = rt.name

    history = []
    for r in results:
        try:
            analysis = json.loads(r.analysis_result)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(
                "Non-critical: Failed to parse analysis_result for result %s: %s", r.id, e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            analysis = {}

        # Log when analysis_result is empty or missing critical fields
        if not analysis or analysis.get("fit_score") is None:
            logger.warning(
                "analysis_result for screening_result_id=%s is empty or missing fit_score "
                "(has %d keys, fit_score=%s). Will reconstruct from parsed_data.",
                r.id, len(analysis), analysis.get("fit_score"),
            )

        # Parse and merge narrative_json if available
        narrative_data = {}
        if r.narrative_json:
            try:
                narrative_data = json.loads(r.narrative_json)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Non-critical: Failed to parse narrative_json for result %s: %s", r.id, e,
                    extra={"error_code": "VALIDATION_ERROR"},
                )

        # Parse parsed_data as fallback source for missing analysis_result fields
        # parsed_data is always saved correctly (not a placeholder like analysis_result can be)
        parsed = {}
        if r.parsed_data:
            try:
                parsed = json.loads(r.parsed_data)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Non-critical: Failed to parse parsed_data for result %s: %s", r.id, e,
                    extra={"error_code": "VALIDATION_ERROR"},
                )

        # Merge narrative data with analysis - narrative fields take precedence
        # but only for narrative-specific fields (strengths, weaknesses, etc.)
        # Preserve analysis fields like fit_score, final_recommendation
        merged_data = dict(analysis)
        if narrative_data:
            # Only merge narrative-specific fields, not core analysis fields
            narrative_fields = {
                "ai_enhanced", "fit_summary", "strengths", "concerns", "weaknesses",
                "recommendation_rationale", "explainability", "interview_questions",
                "candidate_profile_summary", "dealbreakers", "differentiators", "hiring_decision",
            }
            for field in narrative_fields:
                if field in narrative_data and narrative_data[field] not in (None, '', [], {}):
                    merged_data[field] = narrative_data[field]

        # ── Ensure key fields exist with fallback to parsed_data ──────────────
        # When analysis_result is empty/incomplete (e.g., DB save failed during SSE
        # streaming), reconstruct essential fields from parsed_data and other DB
        # columns so the Candidates page report matches the post-analysis report.

        if not merged_data.get("contact_info"):
            merged_data["contact_info"] = parsed.get("contact_info") or {
                "name": candidate.name,
                "email": candidate.email,
                "phone": candidate.phone,
            }

        if not merged_data.get("candidate_profile"):
            # Reconstruct candidate_profile from parsed_data
            work_exp = parsed.get("work_experience", [])
            contact = parsed.get("contact_info", {})
            merged_data["candidate_profile"] = {
                "name": contact.get("name", ""),
                "email": contact.get("email", ""),
                "phone": contact.get("phone", ""),
                "skills_identified": parsed.get("skills", []),
                "education": parsed.get("education", []),
                "work_experience": work_exp,
                "career_summary": "",
                "total_effective_years": candidate.total_years_exp or 0,
                "current_role": work_exp[0].get("title", "") if work_exp else (candidate.current_role or ""),
                "current_company": work_exp[0].get("company", "") if work_exp else (candidate.current_company or ""),
            }

        if not merged_data.get("work_experience"):
            merged_data["work_experience"] = parsed.get("work_experience", [])

        refresh_interview_questions_in_analysis(
            merged_data,
            parsed_data=parsed,
            kit_status=getattr(r, "interview_kit_status", None),
        )

        # Ensure all fields expected by ReportPage / ResultCard have defaults
        merged_data.setdefault("fit_score", None)
        merged_data.setdefault("job_role", None)
        merged_data.setdefault("final_recommendation", "Pending")
        merged_data.setdefault("risk_level", None)
        merged_data.setdefault("score_breakdown", {})
        merged_data.setdefault("strengths", [])
        merged_data.setdefault("weaknesses", [])
        merged_data.setdefault("concerns", [])
        merged_data.setdefault("risk_signals", [])
        merged_data.setdefault("employment_gaps", [])
        merged_data.setdefault("skill_analysis", {})
        merged_data.setdefault("jd_analysis", {})
        merged_data.setdefault("edu_timeline_analysis", {})
        merged_data.setdefault("education_analysis", None)
        merged_data.setdefault("matched_skills", [])
        merged_data.setdefault("missing_skills", [])
        merged_data.setdefault("adjacent_skills", [])
        merged_data.setdefault("required_skills_count", 0)
        merged_data.setdefault("analysis_quality", "low")
        merged_data.setdefault("pipeline_errors", [])
        merged_data.setdefault("score_rationales", {})
        merged_data.setdefault("risk_summary", {})
        merged_data.setdefault("skill_depth", {})
        # Include the ScreeningResult status (pending/shortlisted/rejected/in-review/hired)
        merged_data.setdefault("status", r.status or "pending")
        # narrative_pending and ai_enhanced are computed from narrative_status below,
        # NOT from analysis_result (which may contain stale values)
        merged_data.pop("narrative_pending", None)
        merged_data.pop("ai_enhanced", None)

        # Resolve candidate name: candidate.name takes priority (may have been edited by recruiter)
        candidate_name = (
            (candidate.name or "").strip() or
            (merged_data.get("candidate_name") or "").strip() or
            (merged_data.get("contact_info", {}).get("name") or "").strip() or
            (merged_data.get("candidate_profile", {}).get("name") or "").strip() or
            None
        )

        # Resolve JD name from RoleTemplate
        jd_name = None
        if r.role_template_id and r.role_template_id in templates:
            jd_name = templates[r.role_template_id]

        # Build result with EXACT same structure as analysis result
        # This ensures ReportPage receives consistent data regardless of source
        result_item = {
            # IDs and metadata (minimal additions for UI)
            "id":                   r.id,
            "result_id":            r.id,
            "analysis_id":          r.id,  # For narrative polling
            "timestamp":            r.timestamp,
            "candidate_id":         r.candidate_id,
            "candidate_name":       candidate_name,
            # JD and role metadata
            "role_template_id":     r.role_template_id,
            "jd_name":              jd_name,
            "deterministic_score":  r.deterministic_score,
            "status_updated_at":    r.status_updated_at,

            # Narrative status fields (for UI polling)
            "narrative_status":     r.narrative_status or "pending",
            "narrative_error":      r.narrative_error,
            "interview_kit_status": getattr(r, "interview_kit_status", None) or "pending",
            "voice_strategy_status": getattr(r, "voice_strategy_status", None) or "pending",
            "ai_enhanced":          r.narrative_status == "ready" and r.narrative_json is not None,
            "narrative_pending":    r.narrative_status in ("pending", "processing"),
        }

        # Spread all analysis data - this ensures EXACT same structure as direct analysis
        # merged_data contains: fit_score, final_recommendation, candidate_profile,
        # contact_info, strengths, weaknesses, etc.
        result_item.update(merged_data)

        # Re-apply resolved candidate_name: merged_data.update() above may overwrite
        # candidate_name with the stale parsed value; the recruiter-edited candidate.name
        # must always win.
        result_item["candidate_name"] = candidate_name

        # Ensure jd_name and deterministic_score from DB columns win over
        # any stale values that may have been in merged_data
        result_item["jd_name"] = jd_name
        result_item["deterministic_score"] = r.deterministic_score
        result_item["status_updated_at"] = r.status_updated_at
        result_item["role_template_id"] = r.role_template_id
        result_item["interview_kit_status"] = getattr(r, "interview_kit_status", None) or "pending"
        result_item["voice_strategy_status"] = getattr(r, "voice_strategy_status", None) or "pending"
        result_item.update(outcome_fields_from_result(r))

        history.append(result_item)

    # ── Parse full profile fields from candidate JSON columns ────────────────
    def _safe_json_parse(raw, default=None):
        """Safely parse a JSON column, returning default on failure."""
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return default

    full_parsed_skills = _safe_json_parse(candidate.parsed_skills, [])
    parsed_education  = _safe_json_parse(candidate.parsed_education, [])
    parsed_work_exp   = _safe_json_parse(candidate.parsed_work_exp, [])

    # Extract professional_summary, certifications, languages from
    # parser_snapshot_json (preferred) or fall back to latest analysis_result
    professional_summary = None
    certifications = []
    languages = []

    snap = _safe_json_parse(candidate.parser_snapshot_json, {})
    if snap:
        professional_summary = snap.get("professional_summary") or snap.get("career_summary")
        certifications = snap.get("certifications", [])
        languages = snap.get("languages", [])

    # Fallback: try the most recent screening result's analysis for these fields
    if not professional_summary and history:
        professional_summary = history[0].get("professional_summary") or history[0].get("candidate_profile", {}).get("career_summary")
    if not certifications and history:
        certifications = history[0].get("certifications", [])
    if not languages and history:
        languages = history[0].get("languages", [])

    # Skills snapshot from stored profile (top 15 for list views)
    skills_snapshot = full_parsed_skills[:15] if full_parsed_skills else []

    contact_info: dict = {}
    if getattr(candidate, "parser_snapshot_json", None):
        try:
            snap_ci = json.loads(candidate.parser_snapshot_json)
            contact_info = dict(snap_ci.get("contact_info") or {})
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
            logger.warning(
                "Non-critical: Failed to parse parser_snapshot_json: %s", e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            contact_info = {}
    if not contact_info:
        contact_info = {
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
        }
    else:
        # Recruiter-edited columns win over stale snapshot
        if candidate.name:
            contact_info["name"] = candidate.name
        if candidate.email is not None:
            contact_info["email"] = candidate.email
        if candidate.phone is not None:
            contact_info["phone"] = candidate.phone

    # Build structured screening_results for the profile page
    screening_results = []
    for item in history:
        screening_results.append({
            "id":                item.get("id"),
            "role_template_id":  item.get("role_template_id"),
            "jd_name":           item.get("jd_name"),
            "fit_score":         item.get("fit_score"),
            "deterministic_score": item.get("deterministic_score"),
            "recommendation":    item.get("final_recommendation", "Pending"),
            "status":            item.get("status", "pending"),
            "status_updated_at": item.get("status_updated_at"),
            "matched_skills":    item.get("matched_skills", []),
            "missing_skills":    item.get("missing_skills", []),
            "strengths":         item.get("strengths", []),
            "weaknesses":        item.get("weaknesses", []),
            "interview_questions": item.get("interview_questions"),
            "candidate_profile_summary": item.get("candidate_profile_summary"),
            "narrative":         item.get("fit_summary") or item.get("recommendation_rationale"),
            "created_at":        item.get("timestamp"),
            "call_fit_score":    item.get("call_fit_score"),
            "call_source":       item.get("call_source"),
            "consolidated_recommendation": item.get("consolidated_recommendation"),
            "consolidated_reasoning": item.get("consolidated_reasoning"),
            "call_completed_at": item.get("call_completed_at"),
        })

    return {
        "id":                candidate.id,
        "name":              candidate.name,
        "email":             candidate.email,
        "phone":             candidate.phone,
        "contact_info":      contact_info,
        "created_at":        candidate.created_at,
        "profile_updated_at": candidate.profile_updated_at,
        # Enriched profile
        "current_role":      candidate.current_role,
        "current_company":   candidate.current_company,
        "total_years_exp":   candidate.total_years_exp,
        "profile_quality":   candidate.profile_quality,
        # Full parsed profile (for Candidate Profile Page)
        "parsed_skills":     full_parsed_skills,
        "parsed_education":  parsed_education,
        "parsed_work_exp":   parsed_work_exp,
        "professional_summary": professional_summary,
        "ai_professional_summary": candidate.ai_professional_summary,
        "certifications":    certifications,
        "languages":         languages,
        # Resume file info
        "resume_filename":   candidate.resume_filename,
        "has_resume":        candidate.resume_file_data is not None,
        # Snapshot & profile flags (backward compat)
        "skills_snapshot":   skills_snapshot,
        "has_stored_profile": bool(candidate.raw_resume_text),
        "has_full_parser_snapshot": bool(getattr(candidate, "parser_snapshot_json", None)),
        # Screening results — history (full detail, backward compat) + structured
        "history":           history,
        "screening_results": screening_results,
    }


@router.get("/{candidate_id}/timeline")
def get_candidate_timeline(
    candidate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return status change history for a candidate, ordered by timestamp DESC.

    Constructs timeline events from screening results. Each result generates:
      - An "Analyzed" event (when screening was created, with score detail)
      - A "Status: <status>" event (from status_updated_at, if available)

    If an initial status other than "pending" exists, a status event is also
    emitted at the analysis timestamp (covering cases where status_updated_at
    was not backfilled for legacy records).
    """
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    from app.backend.middleware.rbac import require_candidate_read_access
    require_candidate_read_access(db, current_user, candidate_id)

    results = (
        db.query(ScreeningResult)
        .filter(
            ScreeningResult.candidate_id == candidate_id,
            ScreeningResult.tenant_id == current_user.tenant_id,
        )
        .order_by(ScreeningResult.timestamp.desc())
        .all()
    )

    # Fetch RoleTemplate names in one query
    template_ids = {r.role_template_id for r in results if r.role_template_id}
    templates = {}
    if template_ids:
        for rt in db.query(RoleTemplate).filter(RoleTemplate.id.in_(template_ids)).all():
            templates[rt.id] = rt.name

    events = []

    for r in results:
        jd_name = templates.get(r.role_template_id) if r.role_template_id else None

        # Extract fit_score for the "Analyzed" event detail
        score_detail = None
        try:
            analysis = json.loads(r.analysis_result) if r.analysis_result else {}
            fit_score = r.deterministic_score or analysis.get("fit_score")
            if fit_score is not None:
                score_detail = f"Score: {fit_score}"
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Event 1: Analysis was created
        events.append({
            "event":     "Analyzed",
            "jd_name":   jd_name,
            "timestamp": r.timestamp,
            "details":   score_detail,
        })

        # Event 2: Status change (if status_updated_at exists and differs from timestamp)
        status = r.status or "pending"
        if r.status_updated_at:
            events.append({
                "event":     f"Status: {status}",
                "jd_name":   jd_name,
                "timestamp": r.status_updated_at,
                "details":   None,
            })
        elif status != "pending":
            # Legacy record: status was changed but status_updated_at wasn't set.
            # Emit a status event at the analysis timestamp as best-effort.
            events.append({
                "event":     f"Status: {status}",
                "jd_name":   jd_name,
                "timestamp": r.timestamp,
                "details":   "Estimated (no status_updated_at recorded)",
            })

    # Sort all events by timestamp DESC (most recent first)
    events.sort(
        key=lambda e: e["timestamp"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return {"timeline": events}


# ─── Candidate Notes ────────────────────────────────────────────────────────────


@router.get("/{candidate_id}/notes")
def get_candidate_notes(
    candidate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all notes for a candidate, newest first."""
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    from app.backend.middleware.rbac import require_candidate_read_access
    require_candidate_read_access(db, current_user, candidate_id)

    notes = (
        db.query(CandidateNote)
        .filter(
            CandidateNote.candidate_id == candidate_id,
            CandidateNote.tenant_id == current_user.tenant_id,
        )
        .order_by(CandidateNote.created_at.desc())
        .all()
    )

    result = []
    for note in notes:
        author = db.query(User).filter(User.id == note.user_id).first()
        result.append({
            "id": note.id,
            "text": note.text,
            "user_email": author.email if author else None,
            "user_name": author.email.split("@")[0] if author and author.email else None,
            "created_at": note.created_at,
            "is_own": note.user_id == current_user.id,
        })

    return result


@router.post("/{candidate_id}/notes")
def add_candidate_note(
    candidate_id: int,
    body: dict,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Add a note to a candidate."""
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Note text cannot be empty")
    if len(text) > 2000:
        raise HTTPException(status_code=422, detail="Note text cannot exceed 2000 characters")

    note = CandidateNote(
        candidate_id=candidate_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        text=text,
    )
    db.add(note)
    db.commit()
    db.refresh(note)

    return {
        "id": note.id,
        "text": note.text,
        "user_email": current_user.email,
        "user_name": current_user.email.split("@")[0] if current_user.email else None,
        "created_at": note.created_at,
        "is_own": True,
    }


@router.delete("/{candidate_id}/notes/{note_id}")
def delete_candidate_note(
    candidate_id: int,
    note_id: int,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Delete a note. Only the note author can delete their own notes."""
    note = db.query(CandidateNote).filter(
        CandidateNote.id == note_id,
        CandidateNote.candidate_id == candidate_id,
        CandidateNote.tenant_id == current_user.tenant_id,
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own notes")

    db.delete(note)
    db.commit()
    return {"ok": True}


@router.post("/{candidate_id}/analyze-jd")
async def analyze_existing_candidate(
    candidate_id: int,
    body: AnalyzeJdRequest,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """
    Re-analyze an existing candidate against a new Job Description.

    No file upload required — the candidate's parsed profile (skills, education,
    work experience, gap analysis) is loaded from the database. Only the hybrid
    scoring phase runs, making this ~3× faster than a full re-upload analysis.
    """
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not candidate.raw_resume_text:
        raise HTTPException(
            status_code=422,
            detail=(
                "This candidate does not have a stored profile yet. "
                "Please re-upload their resume to generate a profile before using this endpoint."
            ),
        )

    from app.backend.routes.analyze import (
        _finalize_analyze_context,
        _get_or_cache_jd,
        _link_to_requisition,
        _populate_denormalized_columns,
    )
    from app.backend.services.requisition_service import build_skill_evidence, compute_parse_confidence

    job_description, parsed_skill_overrides, weights, requisition_id, template_id = _finalize_analyze_context(
        db,
        current_user.tenant_id,
        body.job_description or "",
        body.scoring_weights,
        None,
        body.requisition_id,
        None,
    )
    if len(job_description.split()) < 80:
        raise HTTPException(
            status_code=400,
            detail="Job description is too brief (under 80 words). Please provide more detail.",
        )

    # Prefer full parser snapshot (all fields); else reconstruct from denormalized columns
    if getattr(candidate, "parser_snapshot_json", None):
        try:
            parsed_data = json.loads(candidate.parser_snapshot_json)
            if not isinstance(parsed_data, dict):
                raise ValueError("snapshot not an object")
            ci = parsed_data.setdefault("contact_info", {})
            if candidate.name:
                ci["name"] = candidate.name
            if candidate.email is not None:
                ci["email"] = candidate.email
            if candidate.phone is not None:
                ci["phone"] = candidate.phone
            if not parsed_data.get("raw_text") and candidate.raw_resume_text:
                parsed_data["raw_text"] = candidate.raw_resume_text
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
            logger.warning(
                "Non-critical: Failed to parse parser_snapshot_json for candidate %s: %s", candidate.id, e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
            parsed_data = {
                "raw_text":       candidate.raw_resume_text,
                "skills":         json.loads(candidate.parsed_skills   or "[]"),
                "education":      json.loads(candidate.parsed_education or "[]"),
                "work_experience": json.loads(candidate.parsed_work_exp or "[]"),
                "contact_info":   {
                    "name":  candidate.name,
                    "email": candidate.email,
                    "phone": candidate.phone,
                },
            }
    else:
        parsed_data = {
            "raw_text":       candidate.raw_resume_text,
            "skills":         json.loads(candidate.parsed_skills   or "[]"),
            "education":      json.loads(candidate.parsed_education or "[]"),
            "work_experience": json.loads(candidate.parsed_work_exp or "[]"),
            "contact_info":   {
                "name":  candidate.name,
                "email": candidate.email,
                "phone": candidate.phone,
            },
        }
    gap_analysis = json.loads(candidate.gap_analysis_json or "{}")

    jd_analysis = _get_or_cache_jd(db, job_description, current_user.tenant_id)

    from app.backend.services.hybrid_pipeline import run_hybrid_pipeline
    try:
        result = await run_hybrid_pipeline(
            resume_text=candidate.raw_resume_text,
            job_description=job_description,
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=weights,
            jd_analysis=jd_analysis,
            db_session=db,
        )
    except HTTPException:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Analysis failed: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except (OSError, RuntimeError, SQLAlchemyError) as e:
        logger.exception(
            "Analysis failed: %s", e,
            extra={"error_code": "DB_ERROR" if isinstance(e, SQLAlchemyError) else "UPSTREAM_ERROR"},
        )
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}") from e

    # ── Version Management ────────────────────────────────────────────────────
    # When re-analyzing with potentially different weights, create a new version
    # and archive the old one (Option C: Hybrid versioning approach)
    
    # Find current active version for this candidate (if any)
    current_version = db.query(ScreeningResult).filter(
        ScreeningResult.candidate_id == candidate_id,
        ScreeningResult.is_active == True,
    ).first()
    
    # Determine next version number
    max_version = db.query(ScreeningResult).filter(
        ScreeningResult.candidate_id == candidate_id
    ).count()
    next_version = max_version + 1
    
    # Archive current active version
    if current_version:
        current_version.is_active = False
        logger.info(f"Archived version {current_version.version_number} for candidate {candidate_id}")
    
    # Extract weight metadata from JD analysis
    weight_suggestion = jd_analysis.get("weight_suggestion")
    role_category = None
    weight_reasoning = None
    suggested_weights_json = None
    
    if weight_suggestion:
        role_category = weight_suggestion.get("role_category")
        weight_reasoning = weight_suggestion.get("reasoning")
        suggested_weights_json = json.dumps(weight_suggestion.get("suggested_weights", {}), default=_json_default)
    
    # Create new version as active
    db_result = ScreeningResult(
        tenant_id=current_user.tenant_id,
        candidate_id=candidate_id,
        role_template_id=template_id,
        requisition_id=requisition_id,
        resume_text=candidate.raw_resume_text,
        jd_text=job_description,
        parsed_data=json.dumps(parsed_data, default=_json_default),
        analysis_result=json.dumps(result, default=_json_default),
        is_active=True,
        version_number=next_version,
        role_category=role_category,
        weight_reasoning=weight_reasoning,
        suggested_weights_json=suggested_weights_json,
    )
    _populate_denormalized_columns(db_result, result)
    db.add(db_result)
    db.commit()
    db.refresh(db_result)

    if requisition_id:
        _link_to_requisition(
            db, requisition_id, current_user.tenant_id,
            candidate_id, db_result.id, current_user.id,
        )
    
    logger.info(f"Created version {next_version} for candidate {candidate_id} (now active)")

    result["result_id"]      = db_result.id
    result["candidate_id"]   = candidate_id
    result["candidate_name"] = candidate.name
    if requisition_id:
        result["requisition_id"] = requisition_id
    result["parse_confidence"] = compute_parse_confidence(parsed_data)
    result["skill_evidence"] = build_skill_evidence(parsed_data, result.get("matched_skills"))
    return result


@router.get("/{candidate_id}/resume")
def download_candidate_resume(
    candidate_id: int,
    inline: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Download or view the original uploaded resume file.
    PDFs are served inline for browser preview; DOCX/DOC/ODT force download.
    Pass ?inline=true to always receive a browser-renderable response (PDF or plain text).
    """
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    from app.backend.middleware.rbac import require_candidate_read_access
    require_candidate_read_access(db, current_user, candidate_id)

    # Fetch resume data from object storage (preferred) or legacy BYTEA
    from app.backend.services.object_storage import ObjectStorageService
    resume_data = None
    pdf_data = None

    if candidate.resume_file_key and ObjectStorageService.is_available():
        resume_data = ObjectStorageService.download(candidate.resume_file_key)
    if resume_data is None:
        resume_data = candidate.resume_file_data

    if candidate.resume_pdf_key and ObjectStorageService.is_available():
        pdf_data = ObjectStorageService.download(candidate.resume_pdf_key)
    if pdf_data is None:
        pdf_data = candidate.resume_converted_pdf_data

    if not resume_data:
        raise HTTPException(
            status_code=404,
            detail="Resume file not stored for this candidate. Re-upload to enable download.",
        )

    filename = candidate.resume_filename or f"resume_{candidate_id}"
    lower_name = filename.lower()

    # ── Inline mode: always return something the browser can render ─────────
    if inline:
        # Already a PDF → serve inline
        if lower_name.endswith(".pdf"):
            return Response(
                content=resume_data,
                media_type="application/pdf",
                headers={"Content-Disposition": "inline"},
            )
        # .doc or .docx with a converted PDF → serve the PDF inline
        if lower_name.endswith((".doc", ".docx")) and pdf_data:
            return Response(
                content=pdf_data,
                media_type="application/pdf",
                headers={"Content-Disposition": "inline"},
            )
        # Fallback: return raw resume text as plain text
        raw_text = getattr(candidate, "raw_resume_text", None) or getattr(candidate, "resume_text", None) or ""
        return Response(
            content=raw_text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "inline"},
        )

    # ── Standard download/view mode ─────────────────────────────────────────
    # For .doc files with a converted PDF, serve the PDF inline for browser viewing
    if lower_name.endswith(".doc") and pdf_data:
        return Response(
            content=pdf_data,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"},
        )

    # Determine MIME type
    if lower_name.endswith(".pdf"):
        media_type = "application/pdf"
        disposition = "inline"  # Open in browser
    elif lower_name.endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        disposition = f'attachment; filename="{filename}"'
    elif lower_name.endswith(".doc"):
        media_type = "application/msword"
        disposition = f'attachment; filename="{filename}"'
    elif lower_name.endswith(".odt"):
        media_type = "application/vnd.oasis.opendocument.text"
        disposition = f'attachment; filename="{filename}"'
    elif lower_name.endswith(".txt"):
        media_type = "text/plain"
        disposition = f'attachment; filename="{filename}"'
    elif lower_name.endswith(".rtf"):
        media_type = "application/rtf"
        disposition = f'attachment; filename="{filename}"'
    else:
        media_type = "application/octet-stream"
        disposition = f'attachment; filename="{filename}"'

    return Response(
        content=resume_data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


@router.post("/compare")
async def compare_candidates_skill_matrix(
    data: CandidateSkillCompareRequest,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    """Compare multiple candidates against a JD with skill-level detail."""
    from app.backend.services.skill_matcher import match_skills

    # Get candidates
    candidates = db.query(Candidate).filter(
        Candidate.id.in_(data.candidate_ids),
        Candidate.tenant_id == current_user.tenant_id,
    ).all()

    if not candidates:
        raise HTTPException(status_code=404, detail="No candidates found")

    # Get JD analysis (from request or from screening result)
    jd_analysis = data.jd_analysis
    if not jd_analysis and data.screening_result_id:
        result = db.query(ScreeningResult).filter(
            ScreeningResult.id == data.screening_result_id,
            ScreeningResult.tenant_id == current_user.tenant_id,
        ).first()
        if result and result.analysis_result:
            try:
                analysis = json.loads(result.analysis_result) if isinstance(result.analysis_result, str) else result.analysis_result
            except (json.JSONDecodeError, TypeError, ValueError):
                analysis = {}
            jd_analysis = analysis.get("jd_analysis", {}) if isinstance(analysis, dict) else {}

    if not jd_analysis:
        raise HTTPException(status_code=400, detail="JD analysis required (provide jd_analysis or screening_result_id)")

    required_skills = jd_analysis.get("required_skills", []) if isinstance(jd_analysis, dict) else []
    nice_skills = jd_analysis.get("nice_to_have_skills", []) if isinstance(jd_analysis, dict) else []
    if not isinstance(required_skills, list):
        required_skills = []
    if not isinstance(nice_skills, list):
        nice_skills = []
    all_skills = required_skills + nice_skills
    team_gaps = data.team_gaps or []
    team_gaps_lower = [g.lower() for g in team_gaps]

    # Build skills matrix
    skills_matrix = []

    for skill in all_skills:
        skill_entry = {
            "skill": skill,
            "is_required": skill in required_skills,
            "is_team_gap": skill.lower() in team_gaps_lower,
            "candidates": {},
        }
        skills_matrix.append(skill_entry)

    candidate_summaries = {}

    for candidate in candidates:
        # Get candidate skills from parsed data
        parsed_skills = []
        if candidate.parsed_skills:
            try:
                parsed_skills = json.loads(candidate.parsed_skills) if isinstance(candidate.parsed_skills, str) else candidate.parsed_skills
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed_skills = []
        if not isinstance(parsed_skills, list):
            parsed_skills = []

        # Run skill matching
        skill_result = match_skills(
            candidate_skills=parsed_skills,
            jd_skills=all_skills,
        )

        matched = [s.lower() for s in skill_result.get("matched_skills", [])]
        matched_detailed = skill_result.get("matched_skills_detailed", [])
        if not isinstance(matched_detailed, list):
            matched_detailed = []

        # Build per-skill status for this candidate
        total_match = 0
        required_match = 0
        gaps_filled = 0

        for skill_entry in skills_matrix:
            skill_name = skill_entry["skill"]
            skill_lower = skill_name.lower()

            # Check if matched
            is_matched = skill_lower in matched
            confidence = 1.0
            match_type = "exact"

            # Get confidence from detailed if available
            if matched_detailed:
                detail = next((m for m in matched_detailed if m.get("skill", "").lower() == skill_lower), None)
                if detail:
                    is_matched = True
                    confidence = detail.get("confidence", 1.0)
                    match_type = detail.get("match_type", "exact")

            skill_entry["candidates"][str(candidate.id)] = {
                "matched": is_matched,
                "confidence": confidence,
                "match_type": match_type,
            }

            if is_matched:
                total_match += 1
                if skill_entry["is_required"]:
                    required_match += 1
                if skill_entry["is_team_gap"]:
                    gaps_filled += 1

        # Get screening result score if available
        screening = db.query(ScreeningResult).filter(
            ScreeningResult.candidate_id == candidate.id,
            ScreeningResult.tenant_id == current_user.tenant_id,
        ).order_by(ScreeningResult.timestamp.desc()).first()

        fit_score = None
        if screening and screening.analysis_result:
            try:
                sr_data = json.loads(screening.analysis_result) if isinstance(screening.analysis_result, str) else screening.analysis_result
            except (json.JSONDecodeError, TypeError, ValueError):
                sr_data = {}
            fit_score = sr_data.get("fit_score") if isinstance(sr_data, dict) else None

        candidate_summaries[str(candidate.id)] = {
            "name": candidate.name or "Unknown",
            "fit_score": fit_score,
            "required_matched": required_match,
            "required_total": len(required_skills),
            "nice_matched": total_match - required_match,
            "nice_total": len(nice_skills),
            "gaps_filled": gaps_filled,
            "total_gaps": len(team_gaps),
            "match_percentage": round((total_match / max(len(all_skills), 1)) * 100),
        }

    return {
        "skills_matrix": skills_matrix,
        "summary": candidate_summaries,
        "metadata": {
            "total_required": len(required_skills),
            "total_nice": len(nice_skills),
            "total_team_gaps": len(team_gaps),
        }
    }


# ─── Outcome tracking endpoints ──────────────────────────────────────────────

_VALID_OUTCOME_DECISIONS = {"hired", "rejected", "withdrawn", "no_decision"}


@router.post("/{candidate_id}/outcome")
def record_candidate_outcome(
    candidate_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_active_recruiter),
):
    """Record a hiring outcome for a candidate's screening result."""
    # Validate candidate exists and belongs to tenant
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.tenant_id == current_user.tenant_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    screening_result_id = body.get("screening_result_id")
    decision = body.get("decision")
    stage = body.get("stage")
    notes = body.get("notes")

    if not screening_result_id:
        raise HTTPException(status_code=422, detail="screening_result_id is required")
    if not decision:
        raise HTTPException(status_code=422, detail="decision is required")
    if decision not in _VALID_OUTCOME_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision '{decision}'. Must be one of: {', '.join(sorted(_VALID_OUTCOME_DECISIONS))}"
        )

    # Validate screening result belongs to candidate and tenant
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == screening_result_id,
        ScreeningResult.candidate_id == candidate_id,
        ScreeningResult.tenant_id == current_user.tenant_id,
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Screening result not found for this candidate")

    try:
        outcome = record_outcome(
            db=db,
            tenant_id=current_user.tenant_id,
            screening_result_id=screening_result_id,
            candidate_id=candidate_id,
            decision=decision,
            stage=stage,
            user_id=current_user.id,
            notes=notes,
            role_template_id=result.role_template_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return json.loads(json.dumps({
        "id": outcome.id,
        "screening_result_id": outcome.screening_result_id,
        "candidate_id": outcome.candidate_id,
        "role_template_id": outcome.role_template_id,
        "decision": outcome.decision,
        "decision_stage": outcome.decision_stage,
        "decision_date": outcome.decision_date.isoformat() if outcome.decision_date else None,
        "feedback_notes": outcome.feedback_notes,
        "source": outcome.source,
        "created_at": outcome.created_at.isoformat() if outcome.created_at else None,
    }, default=_json_default))


@router.post("/outcomes/{outcome_id}/feedback")
def record_outcome_feedback(
    outcome_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_active_recruiter),
):
    """Record post-hire quality feedback for an outcome."""
    rating = body.get("rating")
    notes = body.get("notes")

    if rating is None:
        raise HTTPException(status_code=422, detail="rating is required")
    try:
        rating = int(rating)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="rating must be an integer")
    if not (1 <= rating <= 5):
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")

    try:
        outcome = record_feedback(
            db=db,
            outcome_id=outcome_id,
            tenant_id=current_user.tenant_id,
            rating=rating,
            notes=notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not outcome:
        raise HTTPException(status_code=404, detail="Outcome not found")

    return json.loads(json.dumps({
        "id": outcome.id,
        "screening_result_id": outcome.screening_result_id,
        "candidate_id": outcome.candidate_id,
        "decision": outcome.decision,
        "feedback_rating": outcome.feedback_rating,
        "feedback_notes": outcome.feedback_notes,
        "updated_at": outcome.updated_at.isoformat() if outcome.updated_at else None,
    }, default=_json_default))


@router.get("/analytics/outcome-patterns")
def get_outcome_patterns(
    role_template_id: Optional[int] = Query(None),
    role_category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compute and return skill-to-outcome correlation patterns."""
    return compute_skill_patterns(
        db=db,
        tenant_id=current_user.tenant_id,
        role_template_id=role_template_id,
        role_category=role_category,
    )


# ─── GDPR endpoints (candidates_privacy.py) ───────────────────────────────────

from app.backend.routes.candidates_privacy import register_privacy_routes
register_privacy_routes(router)
