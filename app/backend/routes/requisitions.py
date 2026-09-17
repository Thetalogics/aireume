"""Requisition routes — intake, calibration, pipeline, HM portal."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_admin, require_feature
from app.backend.middleware.rbac import (
    can_assign_recruiters,
    can_manage_requisition,
    get_tenant_role,
    is_hiring_manager,
    is_tenant_admin,
    require_assignment_role,
    require_requisition_access,
    require_recruiter_or_admin,
    require_requisition_write,
    TENANT_ROLE_HIRING_MANAGER,
)
from app.backend.models.db_models import (
    Candidate,
    Requisition,
    RequisitionCandidate,
    RequisitionCriteriaVersion,
    RequisitionHiringManager,
    RequisitionOpenRequest,
    ScreeningResult,
    TenantRequisitionSettings,
    User,
)
from app.backend.models.schemas import (
    RequisitionAssignRecruiter,
    RequisitionCalibrateRequest,
    RequisitionCriteriaUpdate,
    RequisitionCandidateAdd,
    RequisitionCandidateOut,
    RequisitionCandidateStatusUpdate,
    RequisitionCreate,
    RequisitionFeedbackApply,
    RequisitionHmApproval,
    RequisitionHmRequestCreate,
    RequisitionHmRequestDecision,
    RequisitionIntakeUpdate,
    RequisitionOpenRequestAssign,
    RequisitionOpenRequestCreate,
    RequisitionOpenRequestOut,
    RequisitionOutcomeUpdate,
    RequisitionOut,
    RequisitionSubmissionCreate,
    RequisitionUpdate,
    TenantRequisitionSettingsOut,
    TenantRequisitionSettingsUpdate,
)
from app.backend.services.audit_service import log_tenant_event
from app.backend.services.hm_feedback_service import (
    OUTCOME_REASON_CODES,
    apply_feedback_suggestions,
    build_feedback_suggestions,
    persist_pending_feedback,
)
from app.backend.services.requisition_service import (
    approve_hm_request,
    assign_hiring_managers,
    backfill_pipeline_from_screenings,
    build_submission_packet,
    calibrate_requisition,
    create_requisition,
    get_or_create_tenant_settings,
    get_tenant_settings_readonly,
    hm_assigned_to_requisition,
    intake_gate_blocks,
    intake_gate_message,
    intake_has_minimum_content,
    intake_screening_ready,
    is_requisition_calibrated,
    list_pending_hm_requests,
    load_requisition_list_extras,
    migrate_legacy_data,
    maybe_advance_to_interviewing,
    maybe_advance_to_sourcing,
    notify_hm_candidate_submitted,
    reject_hm_request,
    request_hm_for_requisition,
    requisition_has_hiring_manager,
    requisition_to_dict,
    req_candidate_to_dict,
    suggest_intake_from_jd,
    suggest_screening_action,
    sync_working_criteria_v0,
    update_criteria_manual,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/requisitions", tags=["requisitions"])


def _assert_hm_workflow(db: Session, tenant_id: int) -> None:
    from app.backend.services.feature_flag_service import is_feature_enabled
    from app.backend.services.plan_entitlement_service import plan_feature_detail

    if not is_feature_enabled(db, tenant_id, "hm_workflow"):
        detail = plan_feature_detail(db, tenant_id, "hm_workflow")
        raise HTTPException(
            status_code=403,
            detail={
                "detail": detail.get("upgrade_hint") or "Hiring Manager workflows are not available on your plan",
                "error_code": "PLAN_FEATURE_LOCKED",
                "feature": "hm_workflow",
                "plan": detail.get("plan"),
            },
        )

_ALLOWED_PIPELINE = {"pending", "shortlisted", "rejected", "in-review", "hired"}
_HM_TO_LEARNING = {"hire": "hired", "reject": "rejected"}


def _record_hm_learning_outcome(db: Session, user: User, req: Requisition, rc: RequisitionCandidate, body) -> None:
    decision = _HM_TO_LEARNING.get(body.hm_outcome)
    if not decision or not rc.screening_result_id:
        return
    from app.backend.services.outcome_service import record_outcome, compute_skill_patterns

    record_outcome(
        db,
        tenant_id=user.tenant_id,
        screening_result_id=rc.screening_result_id,
        candidate_id=rc.candidate_id,
        decision=decision,
        stage="hm_review",
        user_id=user.id,
        notes=body.outcome_notes,
        role_template_id=req.legacy_role_template_id,
    )
    try:
        compute_skill_patterns(db, user.tenant_id, role_template_id=req.legacy_role_template_id)
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("Skill pattern compute skipped after HM outcome: %s", exc)


def _ensure_migrated(db: Session, tenant_id: int) -> None:
    migrate_legacy_data(db, tenant_id)
    db.commit()


def _load_req(db: Session, req_id: int, tenant_id: int) -> Requisition:
    req = db.get(Requisition, req_id)
    if not req or req.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return req


def _count_candidates(db: Session, req_id: int) -> int:
    return (
        db.query(func.count(RequisitionCandidate.id))
        .filter(RequisitionCandidate.requisition_id == req_id)
        .scalar()
        or 0
    )


def _to_out(
    db: Session,
    req: Requisition,
    tenant_id: int,
    current_user: User | None = None,
    *,
    persist_settings: bool = False,
    candidate_count: int | None = None,
    hiring_manager_ids: list[int] | None = None,
    email_map: dict[int, str] | None = None,
    include_gate: bool = True,
    settings: TenantRequisitionSettings | None = None,
) -> RequisitionOut:
    if include_gate:
        if settings is None:
            settings = (
                get_or_create_tenant_settings(db, tenant_id)
                if persist_settings
                else get_tenant_settings_readonly(db, tenant_id)
            )
        gate_warning = intake_gate_message(settings, req, db, user=current_user)
    else:
        gate_warning = None
    data = requisition_to_dict(
        db,
        req,
        candidate_count=_count_candidates(db, req.id) if candidate_count is None else candidate_count,
        gate_warning=gate_warning,
        hiring_manager_ids=hiring_manager_ids,
        email_map=email_map,
    )
    return RequisitionOut(**data)


@router.post("/admin/migrate-legacy")
def migrate_legacy_requisitions(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    migrate_legacy_data(db, current_user.tenant_id)
    db.commit()
    return {"ok": True}


@router.get("/settings", response_model=TenantRequisitionSettingsOut)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = get_tenant_settings_readonly(db, current_user.tenant_id)
    from app.backend.models.db_models import Tenant
    import json
    tenant = db.get(Tenant, current_user.tenant_id)
    hw = {}
    if tenant and tenant.metadata_json:
        try:
            meta = json.loads(tenant.metadata_json)
            hw = meta.get("hiring_signal_weights") or {}
        except (json.JSONDecodeError, TypeError):
            hw = {}
    return TenantRequisitionSettingsOut(
        tenant_id=current_user.tenant_id,
        intake_gate_mode=row.intake_gate_mode,
        screening_mode=getattr(row, "screening_mode", None) or "requisition_required",
        hm_pipeline_permission=row.hm_pipeline_permission,
        hiring_signal_weights=hw,
        updated_at=row.updated_at,
    )


@router.put("/settings", response_model=TenantRequisitionSettingsOut)
def update_settings(
    body: TenantRequisitionSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = get_or_create_tenant_settings(db, current_user.tenant_id)
    if body.intake_gate_mode is not None:
        row.intake_gate_mode = body.intake_gate_mode
    if body.screening_mode is not None:
        row.screening_mode = body.screening_mode
    if body.hm_pipeline_permission is not None:
        _assert_hm_workflow(db, current_user.tenant_id)
        row.hm_pipeline_permission = body.hm_pipeline_permission
    if body.hiring_signal_weights is not None:
        from app.backend.models.db_models import Tenant
        import json
        tenant = db.get(Tenant, current_user.tenant_id)
        if tenant:
            try:
                meta = json.loads(tenant.metadata_json or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            meta["hiring_signal_weights"] = body.hiring_signal_weights
            tenant.metadata_json = json.dumps(meta)
    db.commit()
    db.refresh(row)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.settings_update",
        resource_type="tenant",
        resource_id=current_user.tenant_id,
        details=body.model_dump(exclude_none=True),
    )
    db.commit()
    from app.backend.models.db_models import Tenant
    import json
    tenant = db.get(Tenant, current_user.tenant_id)
    hw = {}
    if tenant and tenant.metadata_json:
        try:
            meta = json.loads(tenant.metadata_json)
            hw = meta.get("hiring_signal_weights") or {}
        except (json.JSONDecodeError, TypeError):
            hw = {}
    return TenantRequisitionSettingsOut(
        tenant_id=row.tenant_id,
        intake_gate_mode=row.intake_gate_mode,
        screening_mode=getattr(row, "screening_mode", None) or "requisition_required",
        hm_pipeline_permission=row.hm_pipeline_permission,
        hiring_signal_weights=hw,
        updated_at=row.updated_at,
    )


@router.post("", response_model=RequisitionOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("requisitions"))])
def create_req(
    body: RequisitionCreate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    _ensure_migrated(db, current_user.tenant_id)
    if body.primary_hiring_manager_id or body.hiring_manager_ids:
        _assert_hm_workflow(db, current_user.tenant_id)
    req = create_requisition(
        db,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        title=body.title.strip(),
        jd_text=body.jd_text,
        description=body.description,
        client_name=body.client_name,
        headcount=body.headcount,
        location=body.location,
        scoring_weights=body.scoring_weights,
        tags=body.tags,
        required_skills_override=body.required_skills_override,
        nice_to_have_skills_override=body.nice_to_have_skills_override,
        primary_hiring_manager_id=body.primary_hiring_manager_id,
        hiring_manager_ids=body.hiring_manager_ids,
        assigned_recruiter_id=body.assigned_recruiter_id or current_user.id,
        opened_on_behalf_of_hm_id=body.opened_on_behalf_of_hm_id,
        routing_policy_json=body.routing_policy_json,
        status=body.status,
    )
    if body.primary_hiring_manager_id:
        req.intake_status = "pending_hm"
        req.status = "intake_in_progress"
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.create",
        resource_type="requisition",
        resource_id=req.id,
        details={"title": req.title},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/from-file", response_model=RequisitionOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("requisitions"))])
async def create_req_from_file(
    title: str = Form(...),
    jd_file: UploadFile = File(...),
    tags: str | None = Form(None),
    scoring_weights: str | None = Form(None),
    status: str = Form("draft"),
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    """Create a requisition from an uploaded JD file (PDF/DOCX/TXT)."""
    from app.backend.services.parser_service import extract_jd_text

    jd_bytes = await jd_file.read()
    if not jd_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        jd_text = extract_jd_text(jd_bytes, jd_file.filename)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to extract text from file: {exc}") from exc
    if not jd_text or not jd_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from the uploaded file")

    weights = None
    if scoring_weights:
        try:
            weights = json.loads(scoring_weights)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid scoring_weights JSON")

    parsed_tags = tags
    if tags:
        try:
            loaded = json.loads(tags)
            if isinstance(loaded, list):
                parsed_tags = ",".join(str(t) for t in loaded)
            elif isinstance(loaded, str):
                parsed_tags = loaded
        except (json.JSONDecodeError, TypeError):
            parsed_tags = tags

    req = create_requisition(
        db,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        title=title.strip(),
        jd_text=jd_text,
        scoring_weights=weights,
        tags=parsed_tags,
        assigned_recruiter_id=current_user.id,
        status=status,
    )
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.create",
        resource_type="requisition",
        resource_id=req.id,
        details={"title": req.title, "source": "file"},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.get("", response_model=list[RequisitionOut], dependencies=[Depends(require_feature("requisitions"))])
def list_reqs(
    status_filter: Optional[str] = Query(None, alias="status"),
    mine_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Requisition).filter(Requisition.tenant_id == current_user.tenant_id)
    role = get_tenant_role(current_user)
    if role == TENANT_ROLE_HIRING_MANAGER or (mine_only and is_hiring_manager(current_user)):
        assigned_ids = (
            db.query(RequisitionHiringManager.requisition_id)
            .filter(RequisitionHiringManager.user_id == current_user.id)
            .subquery()
        )
        q = q.filter(
            (Requisition.primary_hiring_manager_id == current_user.id)
            | Requisition.id.in_(select(assigned_ids))
        )
    elif mine_only and not is_tenant_admin(current_user):
        q = q.filter(
            (Requisition.created_by == current_user.id)
            | (Requisition.assigned_recruiter_id == current_user.id)
        )
    if status_filter:
        q = q.filter(Requisition.status == status_filter)
    q = q.order_by(Requisition.updated_at.desc())
    rows = q.all()
    extras = load_requisition_list_extras(db, rows)
    return [
        _to_out(
            db,
            r,
            current_user.tenant_id,
            current_user=current_user,
            persist_settings=False,
            candidate_count=extras["counts"].get(r.id, 0),
            hiring_manager_ids=extras["hm_map"].get(r.id, []),
            email_map=extras["emails"],
            include_gate=False,
        )
        for r in rows
    ]


@router.get("/hm-requests", response_model=list[RequisitionOut], dependencies=[Depends(require_feature("hm_workflow"))])
def list_hm_requests(
    current_user: User = Depends(require_assignment_role),
    db: Session = Depends(get_db),
):
    """Tenant admins — pending hiring manager access requests across requisitions."""
    rows = list_pending_hm_requests(db, current_user.tenant_id)
    extras = load_requisition_list_extras(db, rows)
    return [
        _to_out(
            db,
            r,
            current_user.tenant_id,
            current_user=current_user,
            persist_settings=False,
            candidate_count=extras["counts"].get(r.id, 0),
            hiring_manager_ids=extras["hm_map"].get(r.id, []),
            email_map=extras["emails"],
            include_gate=False,
        )
        for r in rows
    ]


@router.get("/{req_id}", response_model=RequisitionOut, dependencies=[Depends(require_feature("requisitions"))])
def get_req(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.put("/{req_id}", response_model=RequisitionOut, dependencies=[Depends(require_feature("requisitions"))])
def update_req(
    req_id: int,
    body: RequisitionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if is_hiring_manager(current_user):
        raise HTTPException(
            status_code=403,
            detail="Hiring managers must use intake endpoints to update requisitions",
        )
    require_requisition_write(current_user, req, db)
    if body.title is not None:
        req.title = body.title.strip()
    if body.jd_text is not None:
        req.jd_text = body.jd_text
    if body.description is not None:
        req.description = body.description
    if body.client_name is not None:
        req.client_name = body.client_name
    if body.headcount is not None:
        req.headcount = body.headcount
    if body.location is not None:
        req.location = body.location
    if body.status is not None:
        req.status = body.status
        if body.status in ("filled", "cancelled"):
            req.closed_at = datetime.now(timezone.utc)
    if body.scoring_weights is not None:
        req.scoring_weights = json.dumps(body.scoring_weights)
    if body.tags is not None:
        req.tags = body.tags
    if body.required_skills_override is not None:
        req.required_skills_override = json.dumps(body.required_skills_override)
    if body.nice_to_have_skills_override is not None:
        req.nice_to_have_skills_override = json.dumps(body.nice_to_have_skills_override)
    if body.search_brief_json is not None:
        req.search_brief_json = json.dumps(body.search_brief_json)
    if body.must_ask_questions_json is not None:
        req.must_ask_questions_json = json.dumps(body.must_ask_questions_json)
    if body.primary_hiring_manager_id is not None:
        _assert_hm_workflow(db, current_user.tenant_id)
        assign_hiring_managers(db, req, body.primary_hiring_manager_id, None)
        if req.intake_status == "draft":
            req.intake_status = "pending_hm"
        if req.status == "draft":
            req.status = "intake_in_progress"
        # Completing the HM half of "intake screening ready" must advance status
        # even when intake was saved earlier without an HM.
        maybe_advance_to_sourcing(db, req)
    if body.assigned_recruiter_id is not None:
        if not can_assign_recruiters(current_user) and not can_manage_requisition(current_user, req):
            raise HTTPException(status_code=403, detail="Not allowed to assign recruiter")
        from app.backend.middleware.rbac import (
            TENANT_ROLE_ADMIN,
            TENANT_ROLE_RECRUITER,
            TENANT_ROLE_TA_LEAD,
            get_tenant_user_or_404,
        )
        get_tenant_user_or_404(
            db,
            current_user.tenant_id,
            body.assigned_recruiter_id,
            {TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER},
        )
        req.assigned_recruiter_id = body.assigned_recruiter_id
    if body.opened_on_behalf_of_hm_id is not None:
        from app.backend.middleware.rbac import (
            TENANT_ROLE_ADMIN,
            TENANT_ROLE_HIRING_MANAGER,
            TENANT_ROLE_TA_LEAD,
            get_tenant_user_or_404,
        )
        get_tenant_user_or_404(
            db,
            current_user.tenant_id,
            body.opened_on_behalf_of_hm_id,
            {TENANT_ROLE_HIRING_MANAGER, TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD},
        )
        req.opened_on_behalf_of_hm_id = body.opened_on_behalf_of_hm_id
    if body.routing_policy_json is not None:
        req.routing_policy_json = json.dumps(body.routing_policy_json)
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.delete("/{req_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("requisitions"))])
def delete_req(
    req_id: int,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed to delete this requisition")
    db.delete(req)
    db.commit()


@router.put("/{req_id}/intake", response_model=RequisitionOut)
def update_intake(
    req_id: int,
    body: RequisitionIntakeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_write(current_user, req, db)
    req.intake_json = json.dumps(body.intake_json)
    from app.backend.services.interview_kit_context import sync_must_ask_from_intake
    must_ask = sync_must_ask_from_intake(body.intake_json)
    if must_ask is not None:
        req.must_ask_questions_json = must_ask
    if body.intake_status:
        req.intake_status = body.intake_status
    if req.status == "draft":
        req.status = "intake_in_progress"
    from app.backend.services.requisition_service import sync_working_criteria_v0
    sync_working_criteria_v0(db, req)
    maybe_advance_to_sourcing(db, req)
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/intake/suggest")
def suggest_intake(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pre-fill intake from JD parse — helps recruiters start intake faster."""
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_write(current_user, req, db)
    return {"intake_json": suggest_intake_from_jd(req)}


@router.post("/{req_id}/calibrate", response_model=RequisitionOut)
def calibrate(
    req_id: int,
    body: RequisitionCalibrateRequest,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed to calibrate this requisition")
    calibrate_requisition(
        db,
        req,
        user_id=current_user.id,
        criteria_override=body.criteria_json,
        merge_jd=body.merge_jd_parse,
    )
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.calibrate",
        resource_type="requisition",
        resource_id=req.id,
        details={"version": req.current_criteria_version},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.put("/{req_id}/criteria", response_model=RequisitionOut)
def update_criteria(
    req_id: int,
    body: RequisitionCriteriaUpdate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed to edit criteria for this requisition")
    if not req.calibrated_criteria_json:
        raise HTTPException(status_code=400, detail="Calibrate criteria before editing")
    update_criteria_manual(
        db,
        req,
        user_id=current_user.id,
        criteria=body.model_dump(exclude_unset=True),
    )
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.criteria_edit",
        resource_type="requisition",
        resource_id=req.id,
        details={"version": req.current_criteria_version},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/hm-approval", response_model=RequisitionOut, dependencies=[Depends(require_feature("hm_workflow"))])
def hm_approval(
    req_id: int,
    body: RequisitionHmApproval,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not hm_assigned_to_requisition(db, current_user.id, req.id) and not is_tenant_admin(current_user):
        raise HTTPException(status_code=403, detail="Only assigned hiring managers can approve intake")
    if body.approved:
        if body.intake_json:
            req.intake_json = json.dumps(body.intake_json)
            from app.backend.services.interview_kit_context import sync_must_ask_from_intake
            must_ask = sync_must_ask_from_intake(body.intake_json)
            if must_ask is not None:
                req.must_ask_questions_json = must_ask
            sync_working_criteria_v0(db, req)
        req.intake_status = "approved"
        req.hm_approved_at = datetime.now(timezone.utc)
        req.hm_approved_by = current_user.id
        calibrate_requisition(db, req, user_id=current_user.id)
        maybe_advance_to_sourcing(db, req)
    else:
        req.intake_status = "changes_requested"
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/hm-request", response_model=RequisitionOut, dependencies=[Depends(require_feature("hm_workflow"))])
def submit_hm_request(
    req_id: int,
    body: RequisitionHmRequestCreate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    """Recruiter requests HM access — tenant admin must approve before account is created."""
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    if is_tenant_admin(current_user):
        raise HTTPException(
            status_code=400,
            detail="Admins can assign hiring managers directly — use the dropdown or Invite HM.",
        )
    try:
        request_hm_for_requisition(
            db,
            req,
            email=body.email,
            requested_by=current_user.id,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.hm_request",
        resource_type="requisition",
        resource_id=req.id,
        details={"email": req.hm_request_email, "notes": body.notes},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/hm-request/approve", response_model=RequisitionOut, dependencies=[Depends(require_feature("hm_workflow"))])
def approve_hm_request_route(
    req_id: int,
    current_user: User = Depends(require_assignment_role),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    try:
        req, hm_user = approve_hm_request(db, req, approved_by=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.hm_request_approved",
        resource_type="requisition",
        resource_id=req.id,
        details={"email": hm_user.email, "user_id": hm_user.id},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/hm-request/reject", response_model=RequisitionOut, dependencies=[Depends(require_feature("hm_workflow"))])
def reject_hm_request_route(
    req_id: int,
    body: RequisitionHmRequestDecision,
    current_user: User = Depends(require_assignment_role),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    try:
        reject_hm_request(db, req, rejected_by=current_user.id, notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(req)
    log_tenant_event(
        db,
        actor=current_user,
        action="requisition.hm_request_rejected",
        resource_type="requisition",
        resource_id=req.id,
        details={"email": req.hm_request_email, "notes": body.notes},
    )
    db.commit()
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.get("/{req_id}/criteria-versions")
def list_criteria_versions(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    versions = (
        db.query(RequisitionCriteriaVersion)
        .filter(RequisitionCriteriaVersion.requisition_id == req_id)
        .order_by(RequisitionCriteriaVersion.version.desc())
        .all()
    )
    return [
        {
            "id": v.id,
            "requisition_id": v.requisition_id,
            "version": v.version,
            "criteria_json": json.loads(v.criteria_json),
            "source": v.source,
            "created_by": v.created_by,
            "created_at": v.created_at,
        }
        for v in versions
    ]


@router.post("/{req_id}/candidates", response_model=list[RequisitionCandidateOut])
def add_candidates(
    req_id: int,
    body: RequisitionCandidateAdd,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed")
    result_map = body.screening_result_ids or {}
    out = []
    for cid in body.candidate_ids:
        cand = db.get(Candidate, cid)
        if not cand or cand.tenant_id != current_user.tenant_id:
            continue
        existing = (
            db.query(RequisitionCandidate)
            .filter(
                RequisitionCandidate.requisition_id == req_id,
                RequisitionCandidate.candidate_id == cid,
            )
            .first()
        )
        if existing:
            out.append(RequisitionCandidateOut(**req_candidate_to_dict(existing, cand)))
            continue
        sr_id = result_map.get(cid)
        if sr_id:
            sr = db.query(ScreeningResult).filter(
                ScreeningResult.id == sr_id,
                ScreeningResult.candidate_id == cid,
                ScreeningResult.tenant_id == current_user.tenant_id,
            ).first()
            if not sr:
                sr_id = None
        rc = RequisitionCandidate(
            requisition_id=req_id,
            candidate_id=cid,
            screening_result_id=sr_id,
            added_by=current_user.id,
        )
        db.add(rc)
        db.flush()
        out.append(RequisitionCandidateOut(**req_candidate_to_dict(rc, cand)))
    db.commit()
    return out


@router.get("/{req_id}/pipeline")
def get_pipeline(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    sync = backfill_pipeline_from_screenings(db, req)
    db.commit()
    rows = (
        db.query(RequisitionCandidate)
        .options(
            selectinload(RequisitionCandidate.candidate),
            selectinload(RequisitionCandidate.screening_result),
        )
        .filter(RequisitionCandidate.requisition_id == req_id)
        .all()
    )
    pipeline: dict[str, list] = {s: [] for s in _ALLOWED_PIPELINE}
    for rc in rows:
        st = rc.pipeline_status if rc.pipeline_status in pipeline else "pending"
        item = req_candidate_to_dict(rc, rc.candidate)
        item["suggested_action"] = suggest_screening_action(
            req,
            item.get("fit_score"),
            has_call=bool(item.get("call_fit_score")),
        )
        pipeline[st].append(item)
    return {
        "requisition_id": req_id,
        "pipeline": pipeline,
        "sync": sync,
    }


@router.put("/{req_id}/candidates/{candidate_id}", response_model=RequisitionCandidateOut)
def update_candidate_status(
    req_id: int,
    candidate_id: int,
    body: RequisitionCandidateStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    settings = get_or_create_tenant_settings(db, current_user.tenant_id)
    role = get_tenant_role(current_user)
    if role == TENANT_ROLE_HIRING_MANAGER:
        if not hm_assigned_to_requisition(db, current_user.id, req_id):
            raise HTTPException(status_code=403, detail="Not assigned to this requisition")
        perm = settings.hm_pipeline_permission or "view_only"
        if perm == "view_only":
            raise HTTPException(status_code=403, detail="Hiring managers have view-only pipeline access")
        if perm == "shortlist_reject" and body.pipeline_status not in ("shortlisted", "rejected", "pending"):
            raise HTTPException(status_code=403, detail="Hiring managers can only shortlist or reject")
    else:
        require_requisition_write(current_user, req, db)

    rc = (
        db.query(RequisitionCandidate)
        .options(
            selectinload(RequisitionCandidate.candidate),
            selectinload(RequisitionCandidate.screening_result),
        )
        .filter(
            RequisitionCandidate.requisition_id == req_id,
            RequisitionCandidate.candidate_id == candidate_id,
        )
        .first()
    )
    if not rc:
        raise HTTPException(status_code=404, detail="Candidate not on this requisition")
    rc.pipeline_status = body.pipeline_status
    db.commit()
    db.refresh(rc)
    return RequisitionCandidateOut(**req_candidate_to_dict(rc, rc.candidate))


@router.post("/{req_id}/candidates/{candidate_id}/submit")
def submit_to_hm(
    req_id: int,
    candidate_id: int,
    body: RequisitionSubmissionCreate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed to submit candidates on this requisition")
    rc = (
        db.query(RequisitionCandidate)
        .options(selectinload(RequisitionCandidate.screening_result), selectinload(RequisitionCandidate.candidate))
        .filter(
            RequisitionCandidate.requisition_id == req_id,
            RequisitionCandidate.candidate_id == candidate_id,
        )
        .first()
    )
    if not rc:
        raise HTTPException(status_code=404, detail="Candidate not on this requisition")
    packet = build_submission_packet(db, rc, req)
    packet.update(body.submission_json or {})
    rc.submission_json = json.dumps(packet)
    rc.submission_status = "submitted"
    rc.submitted_at = datetime.now(timezone.utc)
    cand_name = rc.candidate.name if rc.candidate else None
    notify_hm_candidate_submitted(db, req, cand_name or "")
    log_tenant_event(
        db,
        actor=current_user,
        action="hm.candidate_submitted",
        resource_type="requisition",
        resource_id=req.id,
        details={"candidate_id": candidate_id, "title": req.title},
    )
    db.commit()
    return packet


@router.put("/{req_id}/candidates/{candidate_id}/outcome", dependencies=[Depends(require_feature("hm_workflow"))])
def record_outcome(
    req_id: int,
    candidate_id: int,
    body: RequisitionOutcomeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not hm_assigned_to_requisition(db, current_user.id, req_id) and not is_tenant_admin(current_user):
        raise HTTPException(status_code=403, detail="Only hiring managers can record outcomes")
    rc = (
        db.query(RequisitionCandidate)
        .filter(
            RequisitionCandidate.requisition_id == req_id,
            RequisitionCandidate.candidate_id == candidate_id,
        )
        .first()
    )
    if not rc:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if body.hm_outcome == "reject" and not body.outcome_reason_code:
        raise HTTPException(
            status_code=400,
            detail="outcome_reason_code is required when rejecting a candidate",
        )
    rc.hm_outcome = body.hm_outcome
    rc.outcome_reason_code = body.outcome_reason_code
    rc.outcome_notes = body.outcome_notes
    rc.outcome_at = datetime.now(timezone.utc)
    rc.submission_status = "reviewed"
    feedback_suggestions = None
    if body.hm_outcome == "advance":
        rc.pipeline_status = "shortlisted"
        maybe_advance_to_interviewing(db, req)
    elif body.hm_outcome == "reject":
        rc.pipeline_status = "rejected"
        feedback_suggestions = build_feedback_suggestions(
            req,
            outcome_reason_code=body.outcome_reason_code,
            outcome_notes=body.outcome_notes,
        )
        persist_pending_feedback(req, feedback_suggestions)
    elif body.hm_outcome == "hire":
        rc.pipeline_status = "hired"
        maybe_advance_to_interviewing(db, req)
    elif body.hm_outcome == "hold":
        rc.pipeline_status = "in-review"
    _record_hm_learning_outcome(db, current_user, req, rc, body)
    db.commit()
    return {
        "status": "ok",
        "hm_outcome": body.hm_outcome,
        "feedback_suggestions": feedback_suggestions,
    }


@router.get("/{req_id}/analytics", dependencies=[Depends(require_feature("analytics"))])
def req_analytics(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    rows = (
        db.query(RequisitionCandidate)
        .filter(RequisitionCandidate.requisition_id == req_id)
        .all()
    )
    funnel = {s: 0 for s in _ALLOWED_PIPELINE}
    outcomes: dict[str, int] = {}
    submitted = 0
    for rc in rows:
        st = rc.pipeline_status if rc.pipeline_status in funnel else "pending"
        funnel[st] += 1
        if rc.submission_status == "submitted":
            submitted += 1
        if rc.hm_outcome:
            outcomes[rc.hm_outcome] = outcomes.get(rc.hm_outcome, 0) + 1
    return {
        "requisition_id": req_id,
        "title": req.title,
        "status": req.status,
        "funnel": funnel,
        "submitted_to_hm": submitted,
        "hm_outcomes": outcomes,
        "criteria_version": req.current_criteria_version,
    }


@router.get("/{req_id}/intake-gate")
def check_intake_gate(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    settings = get_or_create_tenant_settings(db, current_user.tenant_id)
    return {
        "blocks": intake_gate_blocks(settings, req, db, user=current_user),
        "warning": intake_gate_message(settings, req, db, user=current_user),
        "admin_override": is_tenant_admin(current_user),
        "is_calibrated": is_requisition_calibrated(req),
        "intake_has_minimum_content": intake_has_minimum_content(req),
        "hm_assigned": requisition_has_hiring_manager(req, db),
        "intake_approved": req.intake_status == "approved",
        "intake_screening_ready": intake_screening_ready(req, db),
        "requires_hm_approval": (settings.intake_gate_mode or "warn") == "block",
        "intake_gate_mode": settings.intake_gate_mode,
    }


@router.get("/playbook-registry")
def playbook_registry(current_user: User = Depends(get_current_user)):
    """Domain playbook families available for interview kits."""
    from app.backend.services.interview_playbook_templates import get_playbook_registry
    return get_playbook_registry()


@router.get("/{req_id}/handoff-package")
def get_handoff_package(
    req_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.handoff_service import build_handoff_package

    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    package = build_handoff_package(
        db,
        tenant_id=current_user.tenant_id,
        requisition_id=req_id,
        viewer_user_id=current_user.id,
        generated_by_email=current_user.email,
    )
    if not package:
        raise HTTPException(status_code=404, detail="Handoff package not found")
    return package


@router.post("/{req_id}/share-links")
def create_req_share_link(
    req_id: int,
    body: dict,
    request: Request,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    import secrets
    from datetime import timedelta
    from app.backend.models.db_models import HandoffShareLink
    from app.backend.services.share_crypto import hash_passcode, hash_share_token, new_share_token

    req = _load_req(db, req_id, current_user.tenant_id)
    expires_in = int(body.get("expires_in_days") or 14)
    token = new_share_token()
    passcode = body.get("passcode")
    passcode_hash = None
    if isinstance(passcode, str) and passcode.strip():
        passcode_hash = hash_passcode(passcode)
    link = HandoffShareLink(
        token=None,
        token_hash=hash_share_token(token),
        tenant_id=current_user.tenant_id,
        requisition_id=req_id,
        role_template_id=req.legacy_role_template_id,
        created_by=current_user.id,
        label=body.get("label") or f"HM Handoff — {req.title}",
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in),
        passcode_hash=passcode_hash,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    base = str(request.base_url).rstrip("/")
    return {
        "id": link.id,
        "token": token,
        "url": f"{base}/handoff/{token}",
        "label": link.label,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
    }


@router.get("/{req_id}/share-links")
def list_req_share_links(
    req_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.models.db_models import HandoffShareLink

    req = _load_req(db, req_id, current_user.tenant_id)
    require_requisition_access(current_user, req, db)
    links = (
        db.query(HandoffShareLink)
        .filter(
            HandoffShareLink.requisition_id == req_id,
            HandoffShareLink.tenant_id == current_user.tenant_id,
        )
        .order_by(HandoffShareLink.created_at.desc())
        .all()
    )
    base = str(request.base_url).rstrip("/")
    return [
        {
            "id": l.id,
            "token": None,
            "url": None,
            "label": l.label,
            "view_count": l.view_count or 0,
        }
        for l in links
    ]


@router.get("/outcome-reasons")
def list_outcome_reasons():
    return {"reasons": [{"code": k, "label": v} for k, v in OUTCOME_REASON_CODES.items()]}


@router.post("/open-requests", response_model=RequisitionOpenRequestOut, dependencies=[Depends(require_feature("hm_workflow"))])
def create_open_request(
    body: RequisitionOpenRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_hiring_manager(current_user):
        raise HTTPException(status_code=403, detail="Only hiring managers can request new openings")
    row = RequisitionOpenRequest(
        tenant_id=current_user.tenant_id,
        requested_by=current_user.id,
        title=body.title.strip(),
        jd_text=body.jd_text,
        notes=body.notes,
        location=body.location,
        headcount=body.headcount,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return RequisitionOpenRequestOut.model_validate(row)


@router.get("/open-requests", response_model=list[RequisitionOpenRequestOut])
def list_open_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(RequisitionOpenRequest).filter(
        RequisitionOpenRequest.tenant_id == current_user.tenant_id,
    )
    if is_hiring_manager(current_user):
        q = q.filter(RequisitionOpenRequest.requested_by == current_user.id)
    elif not can_assign_recruiters(current_user):
        raise HTTPException(status_code=403, detail="Not allowed to view opening requests")
    rows = q.order_by(RequisitionOpenRequest.created_at.desc()).all()
    out = []
    for row in rows:
        data = RequisitionOpenRequestOut.model_validate(row).model_dump()
        if row.requested_by:
            u = db.get(User, row.requested_by)
            data["requester_email"] = u.email if u else None
        if row.assigned_recruiter_id:
            u = db.get(User, row.assigned_recruiter_id)
            data["assigned_recruiter_email"] = u.email if u else None
        out.append(data)
    return out


@router.post("/open-requests/{request_id}/assign", response_model=RequisitionOut)
def assign_open_request(
    request_id: int,
    body: RequisitionOpenRequestAssign,
    current_user: User = Depends(require_assignment_role),
    db: Session = Depends(get_db),
):
    row = db.query(RequisitionOpenRequest).filter(
        RequisitionOpenRequest.id == request_id,
        RequisitionOpenRequest.tenant_id == current_user.tenant_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Opening request not found")
    if row.status != "pending":
        raise HTTPException(status_code=400, detail="Request already assigned")
    hm_id = body.primary_hiring_manager_id or row.requested_by
    req = create_requisition(
        db,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        title=row.title,
        jd_text=row.jd_text,
        location=row.location,
        headcount=row.headcount,
        primary_hiring_manager_id=hm_id,
        assigned_recruiter_id=body.assigned_recruiter_id,
        opened_on_behalf_of_hm_id=row.requested_by,
        status="intake_in_progress",
    )
    req.intake_status = "pending_hm"
    row.status = "assigned"
    row.assigned_recruiter_id = body.assigned_recruiter_id
    row.assigned_by = current_user.id
    row.requisition_id = req.id
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.put("/{req_id}/assign-recruiter", response_model=RequisitionOut)
def assign_recruiter(
    req_id: int,
    body: RequisitionAssignRecruiter,
    current_user: User = Depends(require_assignment_role),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    req.assigned_recruiter_id = body.assigned_recruiter_id
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)


@router.post("/{req_id}/apply-feedback", response_model=RequisitionOut)
def apply_feedback(
    req_id: int,
    body: RequisitionFeedbackApply,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    req = _load_req(db, req_id, current_user.tenant_id)
    if not can_manage_requisition(current_user, req):
        raise HTTPException(status_code=403, detail="Not allowed to update this requisition")
    apply_feedback_suggestions(
        db, req, body.suggestions, user_id=current_user.id, recalibrate=body.recalibrate,
    )
    db.commit()
    db.refresh(req)
    return _to_out(db, req, current_user.tenant_id, current_user=current_user)
