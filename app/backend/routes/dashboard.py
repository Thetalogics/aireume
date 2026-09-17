"""
Dashboard summary/activity and screening analytics endpoints.
"""
import json
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_admin, require_feature
from app.backend.models.db_models import Candidate, Requisition, RequisitionCandidate, RoleTemplate, ScreeningResult, User
from app.backend.services.skill_trend_service import compute_monthly_snapshot, get_skill_trends
from app.backend.models.schemas import AnalyticsHubOut, ScreeningAnalyticsOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard", "analytics"])


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _safe_parse_json(raw: Optional[str]) -> dict:
    """Safely parse a JSON string; return {} on failure."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _extract_fit_score(analysis: dict) -> Optional[float]:
    """Extract fit_score from analysis_result JSON, handling various types."""
    score = analysis.get("fit_score")
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _period_to_start(period: str) -> datetime:
    """Convert a period string to the corresponding start datetime (UTC)."""
    now = datetime.now(timezone.utc)
    mapping = {
        "last_7_days": timedelta(days=7),
        "last_30_days": timedelta(days=30),
        "last_90_days": timedelta(days=90),
    }
    delta = mapping.get(period, timedelta(days=30))
    return now - delta


# ── GET /api/dashboard/summary ─────────────────────────────────────────────────

@router.get("/api/dashboard/summary")
def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = current_user.tenant_id

    # ── Action items ────────────────────────────────────────────────────────────
    pending_review = (
        db.query(func.count(ScreeningResult.id))
        .filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.status == "pending",
            ScreeningResult.is_active == True,
        )
        .scalar()
    ) or 0

    in_progress_analyses = (
        db.query(func.count(ScreeningResult.id))
        .filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.narrative_status == "processing",
        )
        .scalar()
    ) or 0

    shortlisted_count = (
        db.query(func.count(ScreeningResult.id))
        .filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.status == "shortlisted",
            ScreeningResult.is_active == True,
        )
        .scalar()
    ) or 0

    # ── Pipeline by JD ─────────────────────────────────────────────────────────
    # Fetch all active results for this tenant, grouped by role_template_id
    results = (
        db.query(ScreeningResult)
        .filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.is_active == True,
        )
        .all()
    )

    # Collect unique role_template_ids
    jd_ids = {r.role_template_id for r in results if r.role_template_id is not None}
    jd_map: Dict[int, str] = {}
    if jd_ids:
        templates = db.query(RoleTemplate).filter(RoleTemplate.id.in_(jd_ids)).all()
        jd_map = {t.id: t.name for t in templates}

    # Group results by JD
    jd_groups: Dict[int, List[ScreeningResult]] = {}
    for r in results:
        jid = r.role_template_id
        if jid is None:
            continue
        jd_groups.setdefault(jid, []).append(r)

    pipeline_by_jd = []
    for jid, group in jd_groups.items():
        by_status: Dict[str, int] = Counter()
        fit_scores: List[float] = []
        for r in group:
            by_status[r.status or "pending"] += 1
            analysis = _safe_parse_json(r.analysis_result)
            score = _extract_fit_score(analysis)
            if score is not None:
                fit_scores.append(score)
        pipeline_by_jd.append({
            "jd_id": jid,
            "jd_name": jd_map.get(jid, "Unknown JD"),
            "total_candidates": len(group),
            "by_status": dict(by_status),
            "avg_fit_score": round(sum(fit_scores) / len(fit_scores), 1) if fit_scores else 0,
        })

    # ── Pipeline by Requisition (primary) ────────────────────────────────────
    requisitions = (
        db.query(Requisition)
        .filter(Requisition.tenant_id == tenant_id)
        .order_by(Requisition.updated_at.desc())
        .limit(50)
        .all()
    )
    pipeline_by_requisition = []
    for req in requisitions:
        req_results = [r for r in results if r.requisition_id == req.id]
        if not req_results and req.legacy_role_template_id:
            req_results = [r for r in results if r.role_template_id == req.legacy_role_template_id]
        rc_rows = (
            db.query(RequisitionCandidate)
            .filter(RequisitionCandidate.requisition_id == req.id)
            .all()
        )
        by_status: Dict[str, int] = Counter()
        fit_scores = []
        for r in req_results:
            by_status[r.status or "pending"] += 1
            analysis = _safe_parse_json(r.analysis_result)
            score = _extract_fit_score(analysis)
            if score is not None:
                fit_scores.append(score)
        for rc in rc_rows:
            by_status[rc.pipeline_status or "pending"] += 1
        pipeline_by_requisition.append({
            "requisition_id": req.id,
            "title": req.title,
            "status": req.status,
            "is_calibrated": bool(req.calibrated_criteria_json),
            "total_candidates": max(len(req_results), len(rc_rows)),
            "by_status": dict(by_status),
            "avg_fit_score": round(sum(fit_scores) / len(fit_scores), 1) if fit_scores else 0,
        })

    # ── Weekly metrics ──────────────────────────────────────────────────────────
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    weekly_results = (
        db.query(ScreeningResult)
        .filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.is_active == True,
            ScreeningResult.timestamp >= week_ago,
        )
        .all()
    )

    weekly_fit_scores: List[float] = []
    weekly_shortlisted = 0
    skill_gap_counter: Counter = Counter()
    for r in weekly_results:
        analysis = _safe_parse_json(r.analysis_result)
        score = _extract_fit_score(analysis)
        if score is not None:
            weekly_fit_scores.append(score)
        if r.status == "shortlisted":
            weekly_shortlisted += 1
        # Skill gaps
        missing = analysis.get("missing_skills", [])
        if isinstance(missing, list):
            for skill in missing:
                if isinstance(skill, str) and skill.strip():
                    skill_gap_counter[skill.strip()] += 1

    top_skill_gaps = [skill for skill, _ in skill_gap_counter.most_common(5)]

    return {
        "action_items": {
            "pending_review": pending_review,
            "in_progress_analyses": in_progress_analyses,
            "shortlisted_count": shortlisted_count,
        },
        "pipeline_by_jd": pipeline_by_jd,
        "pipeline_by_requisition": pipeline_by_requisition,
        "weekly_metrics": {
            "analyses_this_week": len(weekly_results),
            "avg_fit_score": round(sum(weekly_fit_scores) / len(weekly_fit_scores), 1) if weekly_fit_scores else 0,
            "shortlist_rate": round(weekly_shortlisted / len(weekly_results), 2) if weekly_results else 0,
            "top_skill_gaps": top_skill_gaps,
        },
    }


# ── GET /api/dashboard/activity ────────────────────────────────────────────────

@router.get("/api/dashboard/activity")
async def get_dashboard_activity(
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = current_user.tenant_id

    results = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.tenant_id == tenant_id)
        .order_by(ScreeningResult.timestamp.desc())
        .limit(limit)
        .all()
    )

    # Pre-load candidate and JD names
    candidate_ids = {r.candidate_id for r in results if r.candidate_id is not None}
    jd_ids = {r.role_template_id for r in results if r.role_template_id is not None}

    candidate_map: Dict[int, str] = {}
    if candidate_ids:
        candidates = db.query(Candidate).filter(Candidate.id.in_(candidate_ids)).all()
        candidate_map = {c.id: (c.name or "Unknown") for c in candidates}

    jd_map: Dict[int, str] = {}
    if jd_ids:
        templates = db.query(RoleTemplate).filter(RoleTemplate.id.in_(jd_ids)).all()
        jd_map = {t.id: t.name for t in templates}

    activities = []
    for r in results:
        analysis = _safe_parse_json(r.analysis_result)
        fit_score = _extract_fit_score(analysis)
        recommendation = analysis.get("final_recommendation", "Pending")

        activities.append({
            "type": "analysis_completed",
            "candidate_name": candidate_map.get(r.candidate_id, "Unknown"),
            "jd_name": jd_map.get(r.role_template_id, "Unknown JD"),
            "fit_score": fit_score,
            "recommendation": recommendation,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "result_id": r.id,
        })

    return {"activities": activities}


# ── GET /api/analytics/screening ───────────────────────────────────────────────

@router.get(
    "/api/analytics/screening",
    dependencies=[Depends(require_feature("analytics"))],
    response_model=ScreeningAnalyticsOut,
)
async def get_screening_analytics(
    period: str = Query("last_30_days", pattern="^(last_7_days|last_30_days|last_90_days)$"),
    start_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD (custom range)"),
    end_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD (custom range)"),
    requisition_id: Optional[int] = Query(None),
    compare: bool = Query(False, description="Include prior-period comparison"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.analytics_hub_service import validate_hub_filters
    from app.backend.services.screening_analytics_service import build_screening_analytics

    validate_hub_filters(db, current_user.tenant_id, requisition_id=requisition_id)
    return build_screening_analytics(
        db,
        current_user.tenant_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        requisition_id=requisition_id,
        compare=compare,
    )


# ── GET /api/analytics/skill-trends ───────────────────────────────────────────

@router.get("/api/analytics/skill-trends", dependencies=[Depends(require_feature("analytics"))])
async def get_skill_trends_endpoint(
    role_category: Optional[str] = Query(None, description="Filter by role category"),
    months: int = Query(6, ge=1, le=24, description="Number of months to look back"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return skill trend time-series data for the tenant."""
    tenant_id = current_user.tenant_id
    return get_skill_trends(db, tenant_id, role_category=role_category, months=months)


# ── POST /api/analytics/skill-trends/compute ──────────────────────────────────

@router.post("/api/analytics/skill-trends/compute", dependencies=[Depends(require_feature("analytics"))])
async def compute_skill_trends_endpoint(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: Compute skill frequency snapshot for the current month."""
    from datetime import date as _date
    tenant_id = current_user.tenant_id
    target = _date.today().replace(day=1)
    count = compute_monthly_snapshot(db, tenant_id, target_date=target)
    return {"snapshots_created": count, "period": target.strftime("%Y-%m")}


# ── Analytics hub (interactive deep-dive) ─────────────────────────────────────

@router.get(
    "/api/analytics/hub",
    dependencies=[Depends(require_feature("analytics"))],
    response_model=AnalyticsHubOut,
)
async def get_analytics_hub(
    period: str = Query("last_30_days", pattern="^(last_7_days|last_30_days|last_90_days)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    requisition_id: Optional[int] = Query(None),
    recruiter_id: Optional[int] = Query(None),
    slices: Optional[str] = Query(None, description="Comma-separated slice ids to load"),
    drill_limit: int = Query(100, ge=1, le=500),
    drill_offset: int = Query(0, ge=0),
    compare: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.analytics_hub_service import (
        VALID_SLICES,
        build_analytics_hub,
        validate_hub_filters,
    )

    validate_hub_filters(
        db,
        current_user.tenant_id,
        requisition_id=requisition_id,
        recruiter_id=recruiter_id,
    )
    slice_list = None
    if slices:
        slice_list = [s.strip() for s in slices.split(",") if s.strip()]
        invalid = [s for s in slice_list if s not in VALID_SLICES]
        if invalid:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Invalid slices: {', '.join(invalid)}")

    include_pii = current_user.role not in ("viewer",)
    return build_analytics_hub(
        db,
        current_user.tenant_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        requisition_id=requisition_id,
        recruiter_id=recruiter_id,
        slices=slice_list,
        drill_limit=drill_limit,
        drill_offset=drill_offset,
        include_pii=include_pii,
        compare=compare,
        user_role=current_user.role,
    )


# ── Report builder & BI export ────────────────────────────────────────────────

@router.get("/api/analytics/reports/templates", dependencies=[Depends(require_feature("analytics"))])
async def list_report_templates_endpoint(current_user: User = Depends(get_current_user)):
    from app.backend.services.report_builder_service import list_report_templates
    return {"templates": list_report_templates()}


@router.post("/api/analytics/reports/run", dependencies=[Depends(require_feature("analytics"))])
async def run_report_endpoint(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.audit_service import log_tenant_event
    from app.backend.services.report_builder_service import run_report
    try:
        result = run_report(
            db,
            current_user.tenant_id,
            template_id=body.get("template_id", ""),
            period=body.get("period", "last_30_days"),
            requisition_id=body.get("requisition_id"),
            recruiter_id=body.get("recruiter_id"),
            format=body.get("format", "json"),
        )
        log_tenant_event(
            db,
            actor=current_user,
            action="analytics.report_export",
            resource_type="report",
            details={
                "template_id": body.get("template_id"),
                "format": body.get("format", "json"),
                "period": body.get("period", "last_30_days"),
                "requisition_id": body.get("requisition_id"),
            },
        )
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.get("/api/analytics/reports/bi-manifest", dependencies=[Depends(require_feature("analytics"))])
async def bi_manifest_endpoint(current_user: User = Depends(get_current_user)):
    from app.backend.services.report_builder_service import bi_export_manifest
    return bi_export_manifest(current_user.tenant_id)


# ── Analytics overview, views, metrics ────────────────────────────────────────

@router.get("/api/analytics/overview", dependencies=[Depends(require_feature("analytics"))])
async def get_analytics_overview(
    period: str = Query("last_30_days", pattern="^(last_7_days|last_30_days|last_90_days)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    requisition_id: Optional[int] = Query(None),
    recruiter_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.analytics_hub_service import validate_hub_filters
    from app.backend.services.analytics_overview_service import build_analytics_overview

    validate_hub_filters(db, current_user.tenant_id, requisition_id=requisition_id, recruiter_id=recruiter_id)
    include_pii = current_user.role not in ("viewer",)
    return build_analytics_overview(
        db,
        current_user.tenant_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
        requisition_id=requisition_id,
        recruiter_id=recruiter_id,
        include_pii=include_pii,
        user_role=current_user.role,
    )


@router.get("/api/analytics/metrics", dependencies=[Depends(require_feature("analytics"))])
async def get_analytics_metrics():
    from app.backend.services.analytics_metrics_service import get_metric_glossary
    return get_metric_glossary()


@router.get("/api/analytics/views", dependencies=[Depends(require_feature("analytics"))])
async def list_analytics_views(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.analytics_views_service import get_default_view, list_views
    return {
        "views": list_views(db, current_user.tenant_id, current_user.id),
        "default_view": get_default_view(db, current_user.tenant_id, current_user.id),
    }


@router.post("/api/analytics/views", dependencies=[Depends(require_feature("analytics"))])
async def create_analytics_view(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.analytics_views_service import create_view
    try:
        row = create_view(
            db,
            current_user.tenant_id,
            current_user.id,
            name=body.get("name", "Saved view"),
            view_type=body.get("view_type", "explore"),
            slice=body.get("slice"),
            filters=body.get("filters"),
            is_default=bool(body.get("is_default")),
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.put("/api/analytics/views/{view_id}", dependencies=[Depends(require_feature("analytics"))])
async def update_analytics_view(
    view_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.analytics_views_service import update_view
    try:
        row = update_view(
            db,
            current_user.tenant_id,
            current_user.id,
            view_id,
            name=body.get("name"),
            slice=body.get("slice"),
            filters=body.get("filters"),
            is_default=body.get("is_default"),
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


@router.delete("/api/analytics/views/{view_id}", dependencies=[Depends(require_feature("analytics"))])
async def delete_analytics_view(
    view_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.analytics_views_service import delete_view
    try:
        delete_view(db, current_user.tenant_id, current_user.id, view_id)
        db.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


# ── Custom report builder ─────────────────────────────────────────────────────

@router.get("/api/analytics/reports/fields", dependencies=[Depends(require_feature("analytics"))])
async def report_fields_endpoint(current_user: User = Depends(get_current_user)):
    from app.backend.services.custom_report_service import get_field_catalog
    return get_field_catalog()


@router.post("/api/analytics/reports/custom/run", dependencies=[Depends(require_feature("analytics"))])
async def run_custom_report_endpoint(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.audit_service import log_tenant_event
    from app.backend.services.custom_report_service import run_custom_report
    try:
        include_pii = current_user.role not in ("viewer",)
        result = run_custom_report(
            db,
            current_user.tenant_id,
            body.get("definition") or body,
            include_pii=include_pii,
            format=body.get("format", "json"),
        )
        log_tenant_event(
            db,
            actor=current_user,
            action="analytics.custom_report_run",
            resource_type="report",
            details={"entity": result.get("entity"), "format": body.get("format", "json")},
        )
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.get("/api/analytics/reports/saved", dependencies=[Depends(require_feature("analytics"))])
async def list_saved_reports_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.custom_report_service import list_saved_reports
    return {"reports": list_saved_reports(db, current_user.tenant_id, current_user.id)}


@router.post("/api/analytics/reports/saved", dependencies=[Depends(require_feature("analytics"))])
async def create_saved_report_endpoint(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import create_saved_report
    try:
        row = create_saved_report(
            db,
            current_user.tenant_id,
            current_user.id,
            name=body.get("name", "Untitled report"),
            definition=body.get("definition") or {},
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.put("/api/analytics/reports/saved/{report_id}", dependencies=[Depends(require_feature("analytics"))])
async def update_saved_report_endpoint(
    report_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import update_saved_report
    try:
        row = update_saved_report(
            db,
            current_user.tenant_id,
            current_user.id,
            report_id,
            name=body.get("name"),
            definition=body.get("definition"),
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


@router.delete("/api/analytics/reports/saved/{report_id}", dependencies=[Depends(require_feature("analytics"))])
async def delete_saved_report_endpoint(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import delete_saved_report
    try:
        delete_saved_report(db, current_user.tenant_id, current_user.id, report_id)
        db.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/api/analytics/reports/saved/{report_id}/share", dependencies=[Depends(require_feature("analytics"))])
async def share_saved_report_endpoint(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import share_saved_report
    try:
        row = share_saved_report(db, current_user.tenant_id, current_user.id, report_id)
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


@router.delete("/api/analytics/reports/saved/{report_id}/share", dependencies=[Depends(require_feature("analytics"))])
async def unshare_saved_report_endpoint(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import unshare_saved_report
    try:
        row = unshare_saved_report(db, current_user.tenant_id, current_user.id, report_id)
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


# ── Scheduled reports ─────────────────────────────────────────────────────────

@router.get("/api/analytics/reports/scheduled", dependencies=[Depends(require_feature("analytics"))])
async def list_scheduled_reports_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.custom_report_service import list_scheduled_reports
    return {"schedules": list_scheduled_reports(db, current_user.tenant_id, current_user.id)}


@router.post("/api/analytics/reports/scheduled", dependencies=[Depends(require_feature("analytics"))])
async def create_scheduled_report_endpoint(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import create_scheduled_report
    try:
        row = create_scheduled_report(
            db,
            current_user.tenant_id,
            current_user.id,
            saved_report_id=body.get("saved_report_id"),
            schedule=body.get("schedule", "weekly"),
            recipients=body.get("recipients") or [],
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid request")


@router.put("/api/analytics/reports/scheduled/{schedule_id}", dependencies=[Depends(require_feature("analytics"))])
async def update_scheduled_report_endpoint(
    schedule_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import update_scheduled_report
    try:
        row = update_scheduled_report(
            db,
            current_user.tenant_id,
            current_user.id,
            schedule_id,
            schedule=body.get("schedule"),
            recipients=body.get("recipients"),
            enabled=body.get("enabled"),
        )
        db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")


@router.delete("/api/analytics/reports/scheduled/{schedule_id}", dependencies=[Depends(require_feature("analytics"))])
async def delete_scheduled_report_endpoint(
    schedule_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    from app.backend.services.custom_report_service import delete_scheduled_report
    try:
        delete_scheduled_report(db, current_user.tenant_id, current_user.id, schedule_id)
        db.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Not found")
