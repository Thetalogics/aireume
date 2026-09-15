"""Requisition lifecycle — intake, calibration, migration, HM scoping."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.backend.models.db_models import (
    Candidate,
    Requisition,
    RequisitionCandidate,
    RequisitionCriteriaVersion,
    RequisitionHiringManager,
    RoleTemplate,
    ScreeningProject,
    ScreeningProjectCandidate,
    ScreeningResult,
    TenantRequisitionSettings,
    User,
)
from app.backend.services.hybrid_pipeline import parse_jd_rules

logger = logging.getLogger(__name__)

REQ_ACTIVE_STATUSES = {"sourcing", "interviewing", "offer", "calibrated"}
CALIBRATED_STATUSES = {"calibrated", "sourcing", "interviewing", "offer", "filled"}


def _json_loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value)


def get_or_create_tenant_settings(db: Session, tenant_id: int) -> TenantRequisitionSettings:
    row = db.get(TenantRequisitionSettings, tenant_id)
    if row:
        return row
    row = TenantRequisitionSettings(tenant_id=tenant_id)
    db.add(row)
    db.flush()
    return row


DEFAULT_ROUTING_POLICY = {
    "submit_to_hm_min_score": 80,
    "ai_interview_min_score": 65,
    "ai_interview_max_score": 79,
    "auto_suggest": True,
}


def get_routing_policy(req: Requisition) -> dict[str, Any]:
    stored = _json_loads(req.routing_policy_json, {})
    return {**DEFAULT_ROUTING_POLICY, **(stored or {})}


def suggest_screening_action(req: Requisition, fit_score: int | None, *, has_call: bool = False) -> str:
    """Return suggested next action: submit_hm | ai_interview | pass | review."""
    policy = get_routing_policy(req)
    if fit_score is None:
        return "review"
    if fit_score >= policy["submit_to_hm_min_score"]:
        return "submit_hm"
    if has_call:
        return "review"
    lo, hi = policy["ai_interview_min_score"], policy["ai_interview_max_score"]
    if lo <= fit_score <= hi:
        return "ai_interview"
    if fit_score < lo:
        return "pass"
    return "review"


def maybe_advance_to_sourcing(db: Session, req: Requisition) -> None:
    if req.status not in ("draft", "intake_in_progress", "calibrated"):
        return
    if intake_screening_ready(req, db):
        req.status = "sourcing"


def maybe_advance_to_interviewing(db: Session, req: Requisition) -> None:
    if req.status in ("sourcing", "calibrated", "intake_in_progress"):
        req.status = "interviewing"


def notify_hm_candidate_submitted(db: Session, req: Requisition, candidate_name: str) -> None:
    """Email primary HM when recruiter submits a candidate for review."""
    if not req.primary_hiring_manager_id:
        return
    hm = db.get(User, req.primary_hiring_manager_id)
    if not hm or not hm.email:
        return
    try:
        from app.backend.services.email_service import email_service, get_tenant_email_service
        svc = get_tenant_email_service(db, req.tenant_id) or email_service
        subject = f"Candidate submitted for review: {req.title}"
        body = (
            f"<p>A candidate has been submitted for your review on <strong>{req.title}</strong>.</p>"
            f"<p>Candidate: <strong>{candidate_name or 'Unknown'}</strong></p>"
            f"<p>Log in to your requisitions to review and record your decision.</p>"
        )
        svc.send_email(hm.email, subject, body)
    except Exception:
        logger.exception("Failed to notify HM about candidate submission req=%s", req.id)


def is_requisition_calibrated(req: Requisition) -> bool:
    return (
        req.status in CALIBRATED_STATUSES
        or req.intake_status == "approved"
        or bool(req.calibrated_criteria_json)
    )


def intake_has_minimum_content(req: Requisition) -> bool:
    """True when HM/recruiter saved at least one intake topic or must-have."""
    if not req.intake_json:
        return False
    try:
        intake = json.loads(req.intake_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(intake, dict):
        return False
    for key in ("screen_focus_topics", "must_haves", "deal_breakers"):
        val = intake.get(key)
        if isinstance(val, list) and any(str(x).strip() for x in val):
            return True
        if isinstance(val, str) and val.strip():
            return True
    must_ask = req.must_ask_questions_json
    if must_ask:
        try:
            items = json.loads(must_ask) if isinstance(must_ask, str) else must_ask
            if isinstance(items, list) and len(items) > 0:
                return True
        except json.JSONDecodeError:
            pass
    return False


def requisition_has_hiring_manager(req: Requisition, db: Session | None = None) -> bool:
    """True when a primary or additional hiring manager is assigned."""
    if req.primary_hiring_manager_id:
        return True
    if db is None:
        return False
    return (
        db.query(RequisitionHiringManager)
        .filter(RequisitionHiringManager.requisition_id == req.id)
        .first()
        is not None
    )


def intake_screening_ready(req: Requisition, db: Session | None = None) -> bool:
    """Screening allowed when intake is saved and an HM is assigned (calibration not required)."""
    return intake_has_minimum_content(req) and requisition_has_hiring_manager(req, db)


def sync_working_criteria_v0(db: Session, req: Requisition) -> None:
    """Draft scoring bar from JD + intake — updates in place until HM locks v1+ on approval."""
    if req.intake_status == "approved" and (req.current_criteria_version or 0) >= 1:
        return
    if not intake_has_minimum_content(req):
        return
    intake = _json_loads(req.intake_json, {})
    criteria = merge_calibration_from_jd_and_intake(req.jd_text, intake)
    req.calibrated_criteria_json = _json_dumps(criteria)
    db.flush()


def hm_assigned_to_requisition(db: Session, user_id: int, requisition_id: int) -> bool:
    req = db.get(Requisition, requisition_id)
    if not req:
        return False
    if req.primary_hiring_manager_id == user_id:
        return True
    return (
        db.query(RequisitionHiringManager)
        .filter(
            RequisitionHiringManager.requisition_id == requisition_id,
            RequisitionHiringManager.user_id == user_id,
        )
        .first()
        is not None
    )


def ensure_legacy_role_template(db: Session, req: Requisition) -> int:
    """Ensure shadow RoleTemplate exists for interview/transcript FK compatibility."""
    if req.legacy_role_template_id:
        tpl = db.get(RoleTemplate, req.legacy_role_template_id)
        if tpl and tpl.tenant_id == req.tenant_id:
            tpl.name = req.title
            tpl.jd_text = req.jd_text
            if req.scoring_weights is not None:
                tpl.scoring_weights = req.scoring_weights
            if req.required_skills_override is not None:
                tpl.required_skills_override = req.required_skills_override
            if req.nice_to_have_skills_override is not None:
                tpl.nice_to_have_skills_override = req.nice_to_have_skills_override
            db.flush()
            return tpl.id

    tpl = RoleTemplate(
        tenant_id=req.tenant_id,
        name=req.title,
        jd_text=req.jd_text,
        scoring_weights=req.scoring_weights,
        tags=req.tags,
        required_skills_override=req.required_skills_override,
        nice_to_have_skills_override=req.nice_to_have_skills_override,
        created_by=req.created_by,
    )
    db.add(tpl)
    db.flush()
    req.legacy_role_template_id = tpl.id
    db.flush()
    return tpl.id


def resolve_role_picker_id(
    db: Session,
    tenant_id: int,
    picker_id: int | None,
) -> tuple[str | None, str | None, int | None, int | None]:
    """
    Resolve picker id — may be requisition id (preferred) or legacy role template id.
    Returns (jd_text, display_name, role_template_id, requisition_id).
    """
    if not picker_id:
        return None, None, None, None

    req = (
        db.query(Requisition)
        .filter(Requisition.id == picker_id, Requisition.tenant_id == tenant_id)
        .first()
    )
    if req:
        tpl_id = ensure_legacy_role_template(db, req)
        return req.jd_text, req.title, tpl_id, req.id

    tpl = (
        db.query(RoleTemplate)
        .filter(RoleTemplate.id == picker_id, RoleTemplate.tenant_id == tenant_id)
        .first()
    )
    if tpl:
        return tpl.jd_text, tpl.name, tpl.id, None

    return None, None, None, None


def build_skill_evidence(parsed_data: dict | None, matched_skills: list | None) -> list[dict[str, Any]]:
    """Link matched skills to resume snippets for evidence display (P2)."""
    work = (parsed_data or {}).get("work_experience") or []
    evidence: list[dict[str, Any]] = []
    for skill in matched_skills or []:
        name = skill.get("skill") if isinstance(skill, dict) else str(skill)
        if not name:
            continue
        needle = name.lower()
        snippet = None
        for entry in work:
            if not isinstance(entry, dict):
                continue
            for b in entry.get("responsibilities") or entry.get("bullets") or []:
                if needle in str(b).lower():
                    snippet = str(b)[:240]
                    break
            if not snippet and needle in str(entry.get("title") or "").lower():
                snippet = str(entry.get("title"))[:240]
            if snippet:
                break
        evidence.append({
            "skill": name,
            "snippet": snippet or "Matched in candidate profile",
        })
    return evidence


def build_default_intake(title: str, jd_text: str) -> dict[str, Any]:
    return {
        "role_title": title,
        "must_haves": [],
        "good_to_haves": [],
        "deal_breakers": [],
        "environment": "",
        "seniority_bar": "",
        "team_context": "",
        "success_criteria_90d": "",
        "hm_notes": "",
        "jd_excerpt": (jd_text or "")[:2000],
    }


def merge_calibration_from_jd_and_intake(
    jd_text: str,
    intake: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = parse_jd_rules(jd_text or "")
    must_haves = list(intake.get("must_haves") or [])
    good_to_haves = list(intake.get("good_to_haves") or [])
    deal_breakers = list(intake.get("deal_breakers") or [])

    for skill in parsed.get("required_skills") or []:
        name = skill if isinstance(skill, str) else skill.get("skill") or skill.get("name")
        if name and name not in must_haves:
            must_haves.append(name)
    for skill in parsed.get("nice_to_have_skills") or []:
        name = skill if isinstance(skill, str) else skill.get("skill") or skill.get("name")
        if name and name not in good_to_haves:
            good_to_haves.append(name)

    criteria = {
        "must_haves": must_haves,
        "good_to_haves": good_to_haves,
        "deal_breakers": deal_breakers,
        "environment": intake.get("environment") or "",
        "seniority_bar": intake.get("seniority_bar") or "",
        "team_context": intake.get("team_context") or "",
        "success_criteria_90d": intake.get("success_criteria_90d") or "",
        "role_title": intake.get("role_title") or "",
        "jd_skills": {
            "required": parsed.get("required_skills") or [],
            "nice_to_have": parsed.get("nice_to_have_skills") or [],
        },
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
    }
    if overrides:
        criteria.update(overrides)
    return criteria


def suggest_intake_from_jd(req: Requisition) -> dict[str, Any]:
    """Pre-fill intake fields from JD rules parse (no LLM)."""
    from app.backend.services.hybrid_pipeline import parse_jd_rules

    intake = _json_loads(req.intake_json, build_default_intake(req.title, req.jd_text))
    parsed = parse_jd_rules(req.jd_text or "")

    required = []
    for skill in parsed.get("required_skills") or []:
        name = skill if isinstance(skill, str) else skill.get("skill") or skill.get("name")
        if name and name not in required:
            required.append(str(name))

    nice = []
    for skill in parsed.get("nice_to_have_skills") or []:
        name = skill if isinstance(skill, str) else skill.get("skill") or skill.get("name")
        if name and name not in nice:
            nice.append(str(name))

    if not intake.get("must_haves"):
        intake["must_haves"] = required[:12]
    if not intake.get("good_to_haves"):
        intake["good_to_haves"] = nice[:8]
    if not intake.get("screen_focus_topics"):
        topics = []
        for resp in (parsed.get("key_responsibilities") or [])[:5]:
            text = str(resp).strip()
            if text:
                topics.append(text[:160])
        intake["screen_focus_topics"] = topics
    if not intake.get("seniority_bar") and parsed.get("seniority"):
        intake["seniority_bar"] = str(parsed.get("seniority"))
    if not intake.get("environment") and parsed.get("domain"):
        intake["environment"] = f"{parsed.get('domain')} role"

    intake["role_title"] = intake.get("role_title") or req.title
    return intake


def _admin_intake_gate_override(user: Any | None) -> bool:
    """Tenant admins may screen once intake is saved, even without HM or HM approval."""
    if user is None:
        return False
    from app.backend.middleware.rbac import is_tenant_admin
    return is_tenant_admin(user)


def intake_gate_message(
    settings: TenantRequisitionSettings,
    req: Requisition,
    db: Session | None = None,
    user: Any | None = None,
) -> str | None:
    mode = settings.intake_gate_mode or "warn"
    if mode == "optional":
        return None
    admin_override = _admin_intake_gate_override(user)
    if intake_screening_ready(req, db):
        if mode == "block" and req.intake_status != "approved":
            if admin_override:
                return (
                    "HM approval pending (tenant policy) — admin override allows screening. "
                    "Assign an HM when ready."
                )
            return (
                "HM must approve intake before screening (tenant policy). "
                "Save intake and request HM approval."
            )
        return None
    if not intake_has_minimum_content(req):
        return (
            "Save HM intake first — add screen-focus topics or must-haves, then screen candidates. "
            "Calibrate later when HM feedback changes the bar."
        )
    if not requisition_has_hiring_manager(req, db):
        if getattr(req, "hm_request_status", None) == "pending" and getattr(req, "hm_request_email", None):
            return (
                f"Hiring manager access requested for {req.hm_request_email} — "
                "waiting for tenant admin approval."
            )
        if admin_override:
            return (
                "No hiring manager assigned — assign one in Overview. "
                "Admin override allows screening once intake is saved."
            )
        return "Assign a hiring manager on this requisition before screening candidates."
    return None


def intake_gate_blocks(
    settings: TenantRequisitionSettings,
    req: Requisition,
    db: Session | None = None,
    user: Any | None = None,
) -> bool:
    mode = settings.intake_gate_mode or "warn"
    if mode == "optional":
        return False
    if not intake_has_minimum_content(req):
        return True
    if _admin_intake_gate_override(user):
        return False
    if not requisition_has_hiring_manager(req, db):
        return True
    if mode == "block" and req.intake_status != "approved":
        return True
    return False


def assign_hiring_managers(
    db: Session,
    req: Requisition,
    primary_id: int | None,
    extra_ids: list[int] | None,
) -> None:
    from app.backend.middleware.rbac import (
        TENANT_ROLE_HIRING_MANAGER,
        TENANT_ROLE_RECRUITER,
        TENANT_ROLE_TA_LEAD,
        TENANT_ROLE_ADMIN,
        get_tenant_user_or_404,
    )

    db.query(RequisitionHiringManager).filter(
        RequisitionHiringManager.requisition_id == req.id
    ).delete()
    seen: set[int] = set()
    hm_roles = {TENANT_ROLE_HIRING_MANAGER, TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD}
    if primary_id:
        get_tenant_user_or_404(db, req.tenant_id, primary_id, hm_roles)
        req.primary_hiring_manager_id = primary_id
        seen.add(primary_id)
        db.add(RequisitionHiringManager(
            requisition_id=req.id, user_id=primary_id, is_primary=True,
        ))
    all_ids = list(extra_ids or [])
    for uid in all_ids:
        if uid in seen:
            continue
        get_tenant_user_or_404(db, req.tenant_id, uid, hm_roles)
        seen.add(uid)
        db.add(RequisitionHiringManager(
            requisition_id=req.id, user_id=uid, is_primary=False,
        ))


HM_REQUEST_STATUSES = {"pending", "approved", "rejected"}


def request_hm_for_requisition(
    db: Session,
    req: Requisition,
    *,
    email: str,
    requested_by: int,
    notes: str | None = None,
) -> Requisition:
    """Recruiter submits HM email for tenant admin to approve and provision."""
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("A valid hiring manager email is required")

    if requisition_has_hiring_manager(req, db):
        raise ValueError("A hiring manager is already assigned on this requisition")

    existing = db.query(User).filter(User.email == normalized).first()
    if existing and existing.tenant_id != req.tenant_id:
        raise ValueError("This email belongs to another organization")

    req.hm_request_email = normalized
    req.hm_request_status = "pending"
    req.hm_requested_by = requested_by
    req.hm_requested_at = datetime.now(timezone.utc)
    req.hm_request_notes = (notes or "").strip() or None
    if req.status == "draft":
        req.status = "intake_in_progress"
    db.flush()
    return req


def approve_hm_request(
    db: Session,
    req: Requisition,
    *,
    approved_by: int,
) -> tuple[Requisition, User]:
    """Admin approves pending HM request — create or link user and assign to requisition."""
    import secrets
    from app.backend.routes.auth import _hash_password
    from app.backend.middleware.rbac import TENANT_ROLE_HIRING_MANAGER

    if req.hm_request_status != "pending" or not req.hm_request_email:
        raise ValueError("No pending hiring manager request on this requisition")

    email = req.hm_request_email
    user = (
        db.query(User)
        .filter(User.email == email, User.tenant_id == req.tenant_id)
        .first()
    )
    if user:
        if not user.is_active:
            user.is_active = True
    else:
        global_existing = db.query(User).filter(User.email == email).first()
        if global_existing:
            raise ValueError("This email is already registered in another organization")
        user = User(
            tenant_id=req.tenant_id,
            email=email,
            hashed_password=_hash_password(secrets.token_urlsafe(12)),
            role=TENANT_ROLE_HIRING_MANAGER,
            is_active=True,
        )
        db.add(user)
        db.flush()

    assign_hiring_managers(db, req, user.id, None)
    if req.intake_status == "draft":
        req.intake_status = "pending_hm"
    req.hm_request_status = "approved"
    maybe_advance_to_sourcing(db, req)
    db.flush()
    return req, user


def reject_hm_request(
    db: Session,
    req: Requisition,
    *,
    rejected_by: int,
    notes: str | None = None,
) -> Requisition:
    if req.hm_request_status != "pending":
        raise ValueError("No pending hiring manager request on this requisition")
    req.hm_request_status = "rejected"
    if notes:
        req.hm_request_notes = notes.strip()
    db.flush()
    return req


def list_pending_hm_requests(db: Session, tenant_id: int) -> list[Requisition]:
    return (
        db.query(Requisition)
        .filter(
            Requisition.tenant_id == tenant_id,
            Requisition.hm_request_status == "pending",
        )
        .order_by(Requisition.hm_requested_at.desc())
        .all()
    )


def requisition_to_dict(
    db: Session,
    req: Requisition,
    *,
    candidate_count: int = 0,
    gate_warning: str | None = None,
) -> dict[str, Any]:
    hm_ids = [
        row.user_id
        for row in db.query(RequisitionHiringManager)
        .filter(RequisitionHiringManager.requisition_id == req.id)
        .all()
    ]
    primary_email = None
    if req.primary_hiring_manager_id:
        u = db.get(User, req.primary_hiring_manager_id)
        primary_email = u.email if u else None

    requester_email = None
    if req.hm_requested_by:
        requester = db.get(User, req.hm_requested_by)
        requester_email = requester.email if requester else None

    assigned_email = None
    if req.assigned_recruiter_id:
        ar = db.get(User, req.assigned_recruiter_id)
        assigned_email = ar.email if ar else None

    on_behalf_email = None
    if req.opened_on_behalf_of_hm_id:
        ob = db.get(User, req.opened_on_behalf_of_hm_id)
        on_behalf_email = ob.email if ob else None

    return {
        "id": req.id,
        "tenant_id": req.tenant_id,
        "title": req.title,
        "jd_text": req.jd_text,
        "description": req.description,
        "client_name": req.client_name,
        "headcount": req.headcount,
        "location": req.location,
        "status": req.status,
        "intake_status": req.intake_status,
        "intake_json": _json_loads(req.intake_json, {}),
        "search_brief_json": _json_loads(req.search_brief_json),
        "calibrated_criteria_json": _json_loads(req.calibrated_criteria_json),
        "current_criteria_version": req.current_criteria_version or 0,
        "scoring_weights": _json_loads(req.scoring_weights),
        "tags": req.tags,
        "required_skills_override": _json_loads(req.required_skills_override, []),
        "nice_to_have_skills_override": _json_loads(req.nice_to_have_skills_override, []),
        "must_ask_questions_json": _json_loads(req.must_ask_questions_json, []),
        "primary_hiring_manager_id": req.primary_hiring_manager_id,
        "primary_hiring_manager_email": primary_email,
        "hiring_manager_ids": hm_ids,
        "hm_request_email": req.hm_request_email,
        "hm_request_status": req.hm_request_status,
        "hm_requested_by": req.hm_requested_by,
        "hm_requested_by_email": requester_email,
        "hm_requested_at": req.hm_requested_at,
        "hm_request_notes": req.hm_request_notes,
        "assigned_recruiter_id": req.assigned_recruiter_id,
        "assigned_recruiter_email": assigned_email,
        "opened_on_behalf_of_hm_id": req.opened_on_behalf_of_hm_id,
        "opened_on_behalf_of_hm_email": on_behalf_email,
        "routing_policy_json": get_routing_policy(req),
        "created_by": req.created_by,
        "legacy_role_template_id": req.legacy_role_template_id,
        "external_ats_id": req.external_ats_id,
        "ats_provider": req.ats_provider,
        "hm_approved_at": req.hm_approved_at,
        "calibrated_at": req.calibrated_at,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "closed_at": req.closed_at,
        "candidate_count": candidate_count,
        "intake_gate_warning": gate_warning,
        "is_calibrated": is_requisition_calibrated(req),
    }


def create_requisition(
    db: Session,
    *,
    tenant_id: int,
    created_by: int | None,
    title: str,
    jd_text: str,
    **fields: Any,
) -> Requisition:
    intake = build_default_intake(title, jd_text)
    recruiter_id = fields.get("assigned_recruiter_id")
    on_behalf_id = fields.get("opened_on_behalf_of_hm_id")
    if recruiter_id:
        from app.backend.middleware.rbac import (
            TENANT_ROLE_ADMIN,
            TENANT_ROLE_RECRUITER,
            TENANT_ROLE_TA_LEAD,
            get_tenant_user_or_404,
        )
        get_tenant_user_or_404(
            db,
            tenant_id,
            recruiter_id,
            {TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER},
        )
    if on_behalf_id:
        from app.backend.middleware.rbac import (
            TENANT_ROLE_ADMIN,
            TENANT_ROLE_HIRING_MANAGER,
            TENANT_ROLE_TA_LEAD,
            get_tenant_user_or_404,
        )
        get_tenant_user_or_404(
            db,
            tenant_id,
            on_behalf_id,
            {TENANT_ROLE_HIRING_MANAGER, TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD},
        )
    req = Requisition(
        tenant_id=tenant_id,
        title=title,
        jd_text=jd_text,
        description=fields.get("description"),
        client_name=fields.get("client_name"),
        headcount=fields.get("headcount"),
        location=fields.get("location"),
        status=fields.get("status") or "draft",
        intake_status="draft",
        intake_json=_json_dumps(intake),
        scoring_weights=_json_dumps(fields.get("scoring_weights")),
        tags=fields.get("tags"),
        required_skills_override=_json_dumps(fields.get("required_skills_override")),
        nice_to_have_skills_override=_json_dumps(fields.get("nice_to_have_skills_override")),
        created_by=created_by,
        assigned_recruiter_id=recruiter_id,
        opened_on_behalf_of_hm_id=on_behalf_id,
        routing_policy_json=_json_dumps(fields.get("routing_policy_json") or DEFAULT_ROUTING_POLICY),
    )
    db.add(req)
    db.flush()
    ensure_legacy_role_template(db, req)
    assign_hiring_managers(
        db,
        req,
        fields.get("primary_hiring_manager_id"),
        fields.get("hiring_manager_ids"),
    )
    backfill_pipeline_from_screenings(db, req)
    return req


def calibrate_requisition(
    db: Session,
    req: Requisition,
    *,
    user_id: int | None,
    criteria_override: dict[str, Any] | None = None,
    merge_jd: bool = True,
) -> RequisitionCriteriaVersion:
    intake = _json_loads(req.intake_json, {})
    if merge_jd:
        criteria = merge_calibration_from_jd_and_intake(req.jd_text, intake, criteria_override)
    else:
        criteria = criteria_override or merge_calibration_from_jd_and_intake(req.jd_text, intake)

    version_num = (req.current_criteria_version or 0) + 1
    version = RequisitionCriteriaVersion(
        requisition_id=req.id,
        version=version_num,
        criteria_json=_json_dumps(criteria),
        source="calibration",
        created_by=user_id,
    )
    db.add(version)
    req.current_criteria_version = version_num
    req.calibrated_criteria_json = _json_dumps(criteria)
    req.calibrated_at = datetime.now(timezone.utc)
    req.calibrated_by = user_id
    if req.status in ("draft", "intake_in_progress"):
        req.status = "calibrated"
    maybe_advance_to_sourcing(db, req)
    db.flush()
    return version


def migrate_legacy_data(db: Session, tenant_id: int) -> int:
    """One-time migration: role templates + projects → requisitions.

    Guarded by tenant_requisition_settings.legacy_jd_migrated so deleting all
    requisitions does not re-create them from leftover RoleTemplate shadows
    (create_requisition / sample JD always leave templates behind).
    """
    settings = get_or_create_tenant_settings(db, tenant_id)
    if settings.legacy_jd_migrated:
        return 0

    existing = (
        db.query(Requisition)
        .filter(Requisition.tenant_id == tenant_id)
        .count()
    )
    if existing:
        settings.legacy_jd_migrated = True
        db.flush()
        return 0

    created = 0
    templates = (
        db.query(RoleTemplate)
        .filter(RoleTemplate.tenant_id == tenant_id)
        .all()
    )
    template_to_req: dict[int, int] = {}

    for tpl in templates:
        intake = build_default_intake(tpl.name, tpl.jd_text)
        req = Requisition(
            tenant_id=tenant_id,
            title=tpl.name,
            jd_text=tpl.jd_text,
            status="draft",
            intake_status="draft",
            intake_json=_json_dumps(intake),
            scoring_weights=tpl.scoring_weights,
            tags=tpl.tags,
            required_skills_override=tpl.required_skills_override,
            nice_to_have_skills_override=tpl.nice_to_have_skills_override,
            created_by=tpl.created_by,
            legacy_role_template_id=tpl.id,
        )
        db.add(req)
        db.flush()
        template_to_req[tpl.id] = req.id
        created += 1

        db.query(ScreeningResult).filter(
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.role_template_id == tpl.id,
            ScreeningResult.requisition_id.is_(None),
        ).update({"requisition_id": req.id}, synchronize_session=False)

    projects = (
        db.query(ScreeningProject)
        .filter(ScreeningProject.tenant_id == tenant_id)
        .all()
    )
    for proj in projects:
        req_id = template_to_req.get(proj.role_template_id)
        if not req_id:
            tpl = db.get(RoleTemplate, proj.role_template_id)
            if tpl:
                intake = build_default_intake(tpl.name, tpl.jd_text)
                req = Requisition(
                    tenant_id=tenant_id,
                    title=proj.name or tpl.name,
                    jd_text=tpl.jd_text,
                    status=_map_project_status(proj.status),
                    intake_status="draft",
                    intake_json=_json_dumps(intake),
                    must_ask_questions_json=proj.must_ask_questions_json,
                    created_by=proj.created_by,
                    legacy_role_template_id=proj.role_template_id,
                    legacy_project_id=proj.id,
                )
                db.add(req)
                db.flush()
                req_id = req.id
                created += 1
            else:
                continue
        else:
            req = db.get(Requisition, req_id)
            if req:
                if proj.name:
                    req.title = proj.name
                req.must_ask_questions_json = proj.must_ask_questions_json or req.must_ask_questions_json
                req.legacy_project_id = proj.id
                if proj.status == "active" and req.status == "draft":
                    req.status = "sourcing"
                elif proj.status == "closed":
                    req.status = "filled"
                    req.closed_at = proj.closed_at

        pcs = (
            db.query(ScreeningProjectCandidate)
            .filter(ScreeningProjectCandidate.project_id == proj.id)
            .all()
        )
        for pc in pcs:
            exists = (
                db.query(RequisitionCandidate)
                .filter(
                    RequisitionCandidate.requisition_id == req_id,
                    RequisitionCandidate.candidate_id == pc.candidate_id,
                )
                .first()
            )
            if exists:
                continue
            db.add(RequisitionCandidate(
                requisition_id=req_id,
                candidate_id=pc.candidate_id,
                screening_result_id=pc.screening_result_id,
                pipeline_status=pc.status,
                added_by=pc.added_by,
                added_at=pc.added_at,
            ))

    db.flush()
    for req_id in template_to_req.values():
        req_row = db.get(Requisition, req_id)
        if req_row:
            backfill_pipeline_from_screenings(db, req_row)

    settings.legacy_jd_migrated = True
    db.flush()
    logger.info("Migrated %s requisitions for tenant %s", created, tenant_id)
    return created


def _map_project_status(status: str) -> str:
    return {
        "draft": "draft",
        "active": "sourcing",
        "paused": "sourcing",
        "closed": "filled",
    }.get(status or "draft", "draft")


def req_candidate_to_dict(rc: RequisitionCandidate, candidate: Candidate | None = None) -> dict[str, Any]:
    fit_score = None
    call_fit_score = None
    call_source = None
    if rc.screening_result:
        fit_score = rc.screening_result.deterministic_score
        call_fit_score = rc.screening_result.call_fit_score
        call_source = rc.screening_result.call_source
    return {
        "id": rc.id,
        "requisition_id": rc.requisition_id,
        "candidate_id": rc.candidate_id,
        "screening_result_id": rc.screening_result_id,
        "pipeline_status": rc.pipeline_status,
        "submission_status": rc.submission_status,
        "hm_outcome": rc.hm_outcome,
        "outcome_reason_code": rc.outcome_reason_code,
        "outcome_notes": rc.outcome_notes,
        "submission_json": _json_loads(rc.submission_json),
        "parse_confidence_json": _json_loads(rc.parse_confidence_json),
        "added_at": rc.added_at,
        "updated_at": rc.updated_at,
        "submitted_at": rc.submitted_at,
        "outcome_at": rc.outcome_at,
        "candidate_name": getattr(candidate, "name", None) if candidate else None,
        "candidate_email": getattr(candidate, "email", None) if candidate else None,
        "fit_score": fit_score,
        "call_fit_score": call_fit_score,
        "call_source": call_source,
    }


def get_calibrated_skills_for_matching(req: Requisition) -> dict[str, list]:
    """Skills for analyze/matching from calibrated criteria + overrides."""
    criteria = _json_loads(req.calibrated_criteria_json, {})
    required = list(criteria.get("must_haves") or [])
    nice = list(criteria.get("good_to_haves") or [])

    override_req = _json_loads(req.required_skills_override, [])
    override_nice = _json_loads(req.nice_to_have_skills_override, [])
    for item in override_req or []:
        name = item if isinstance(item, str) else item.get("skill")
        if name and name not in required:
            required.append(name)
    for item in override_nice or []:
        name = item if isinstance(item, str) else item.get("skill")
        if name and name not in nice:
            nice.append(name)

    if not required and not nice:
        parsed = parse_jd_rules(req.jd_text or "")
        for skill in parsed.get("required_skills") or []:
            name = skill if isinstance(skill, str) else skill.get("skill")
            if name:
                required.append(name)
        for skill in parsed.get("nice_to_have_skills") or []:
            name = skill if isinstance(skill, str) else skill.get("skill")
            if name:
                nice.append(name)

    return {"required_skills": required, "nice_to_have_skills": nice}


def compute_parse_confidence(parsed_data: dict[str, Any]) -> dict[str, Any]:
    """Phase 2 — trust signals for resume/JD parsing (evidence-linked UI)."""
    skills = parsed_data.get("skills") or []
    work = parsed_data.get("work_experience") or []
    contact = parsed_data.get("contact_info") or {}
    raw_len = len((parsed_data.get("raw_text") or "").strip())

    scores = {
        "contact": 0.9 if contact.get("email") else (0.5 if contact.get("phone") else 0.2),
        "skills": min(1.0, len(skills) / 8) if skills else 0.1,
        "experience": min(1.0, len(work) / 3) if work else 0.15,
        "dates": 0.7 if work and all(w.get("start_date") or w.get("dates") for w in work[:2]) else 0.35,
        "text_quality": min(1.0, raw_len / 1500) if raw_len else 0.0,
    }
    overall = round(sum(scores.values()) / len(scores), 2)
    level = "high" if overall >= 0.7 else ("medium" if overall >= 0.45 else "low")
    return {
        "overall": overall,
        "level": level,
        "dimensions": scores,
        "evidence_hint": "Verify contact and tenure before advancing — parser confidence is triage-only.",
    }


def build_submission_packet(
    db: Session,
    rc: RequisitionCandidate,
    req: Requisition,
) -> dict[str, Any]:
    """HM submission packet — screening summary + playbook hints."""
    result = rc.screening_result
    analysis = _json_loads(result.analysis_result, {}) if result else {}
    narrative = _json_loads(result.narrative_json, {}) if result and result.narrative_json else {}
    criteria = _json_loads(req.calibrated_criteria_json, {})
    consolidated = {}
    if result:
        consolidated = {
            "call_fit_score": getattr(result, "call_fit_score", None),
            "consolidated_recommendation": getattr(result, "consolidated_recommendation", None),
            "consolidated_reasoning": getattr(result, "consolidated_reasoning", None),
            "call_source": getattr(result, "call_source", None),
        }
    fit = result.deterministic_score if result else None
    return {
        "candidate_id": rc.candidate_id,
        "requisition_id": req.id,
        "requisition_title": req.title,
        "fit_score": fit,
        **consolidated,
        "suggested_action": suggest_screening_action(req, fit, has_call=bool(consolidated.get("call_fit_score"))),
        "recommendation": analysis.get("recommendation"),
        "skill_gaps": analysis.get("skill_gaps") or analysis.get("gaps"),
        "evidence": analysis.get("evidence") or analysis.get("skill_evidence"),
        "parse_confidence": _json_loads(rc.parse_confidence_json),
        "narrative_summary": narrative.get("summary") or narrative.get("executive_summary"),
        "calibrated_must_haves": criteria.get("must_haves") or [],
        "hm_debrief_prompts": [
            "Did the candidate demonstrate ownership on must-have skills?",
            "Any deal-breakers observed?",
            "Would you advance to next round?",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Pipeline backfill (legacy JD → requisition_candidates) ─────────────────

_PIPELINE_STATUSES = {"pending", "in-review", "shortlisted", "rejected", "hired"}


def _map_screening_to_pipeline(sr: ScreeningResult) -> str:
    """Map screening result status/recommendation to pipeline column."""
    raw_status = (sr.status or "pending").lower().replace("_", "-")
    if raw_status in _PIPELINE_STATUSES:
        return raw_status

    analysis = _json_loads(sr.analysis_result, {})
    rec = str(analysis.get("recommendation") or analysis.get("fit_label") or "").lower()
    if any(k in rec for k in ("shortlist", "strong match", "strong fit", "hire")):
        return "shortlisted"
    if any(k in rec for k in ("reject", "not recommended", "no hire", "weak")):
        return "rejected"
    if any(k in rec for k in ("consider", "moderate", "review")):
        return "in-review"
    return "pending"


def backfill_pipeline_from_screenings(
    db: Session,
    req: Requisition,
    *,
    commit: bool = False,
) -> dict[str, int]:
    """
    Idempotently attach matching screening_results to requisition_candidates.

    Matches screenings where:
      - requisition_id == req.id, OR
      - role_template_id == req.legacy_role_template_id (legacy JD path)

    Returns counts: {added, linked, skipped}.
    """
    if not req.legacy_role_template_id:
        ensure_legacy_role_template(db, req)

    legacy_tpl = req.legacy_role_template_id
    added = 0
    linked = 0
    skipped = 0

    q = db.query(ScreeningResult).filter(
        ScreeningResult.tenant_id == req.tenant_id,
        ScreeningResult.candidate_id.isnot(None),
        ScreeningResult.is_active == True,  # noqa: E712
    )
    if legacy_tpl:
        q = q.filter(
            (ScreeningResult.requisition_id == req.id)
            | (
                (ScreeningResult.role_template_id == legacy_tpl)
                & (
                    (ScreeningResult.requisition_id.is_(None))
                    | (ScreeningResult.requisition_id == req.id)
                )
            )
        )
    else:
        q = q.filter(ScreeningResult.requisition_id == req.id)

    screenings = q.order_by(ScreeningResult.timestamp.desc()).all()
    seen_candidates: set[int] = set()

    for sr in screenings:
        cid = sr.candidate_id
        if not cid or cid in seen_candidates:
            skipped += 1
            continue
        seen_candidates.add(cid)

        if sr.requisition_id != req.id:
            sr.requisition_id = req.id
            linked += 1

        existing = (
            db.query(RequisitionCandidate)
            .filter(
                RequisitionCandidate.requisition_id == req.id,
                RequisitionCandidate.candidate_id == cid,
            )
            .first()
        )
        if existing:
            if not existing.screening_result_id and sr.id:
                existing.screening_result_id = sr.id
            skipped += 1
            continue

        db.add(RequisitionCandidate(
            requisition_id=req.id,
            candidate_id=cid,
            screening_result_id=sr.id,
            pipeline_status=_map_screening_to_pipeline(sr),
            added_by=None,
        ))
        added += 1

    db.flush()
    if commit:
        db.commit()
    return {"added": added, "linked": linked, "skipped": skipped}


def update_criteria_manual(
    db: Session,
    req: Requisition,
    *,
    user_id: int | None,
    criteria: dict[str, Any],
) -> RequisitionCriteriaVersion:
    """Save recruiter/admin criteria edit as a new version (audit trail)."""
    current = _json_loads(req.calibrated_criteria_json, {})
    if not current:
        current = merge_calibration_from_jd_and_intake(
            req.jd_text,
            _json_loads(req.intake_json, {}),
        )

    updated = {**current}
    for key in ("must_haves", "good_to_haves", "deal_breakers"):
        if key in criteria and criteria[key] is not None:
            updated[key] = criteria[key]
    for key in ("environment", "seniority_bar", "team_context", "success_criteria_90d"):
        if key in criteria and criteria[key] is not None:
            updated[key] = criteria[key]

    version_num = (req.current_criteria_version or 0) + 1
    version = RequisitionCriteriaVersion(
        requisition_id=req.id,
        version=version_num,
        criteria_json=_json_dumps(updated),
        source="manual_edit",
        created_by=user_id,
    )
    db.add(version)
    req.current_criteria_version = version_num
    req.calibrated_criteria_json = _json_dumps(updated)
    req.updated_at = datetime.now(timezone.utc)
    db.flush()
    return version


def get_hiring_signal_weights(
    db: Session,
    req: Requisition | None,
    tenant_id: int,
) -> tuple[float, float]:
    """Return (resume_weight, interview_weight) — per-requisition override or tenant default."""
    default_resume, default_interview = 0.4, 0.6

    from app.backend.models.db_models import Tenant
    tenant_row = db.get(Tenant, tenant_id)
    if tenant_row and tenant_row.metadata_json:
        meta = _json_loads(tenant_row.metadata_json, {})
        hw = meta.get("hiring_signal_weights") or {}
        if hw.get("resume") is not None:
            default_resume = float(hw["resume"])
        if hw.get("interview") is not None:
            default_interview = float(hw["interview"])

    if req and req.scoring_weights:
        sw = (
            _json_loads(req.scoring_weights, {})
            if isinstance(req.scoring_weights, str)
            else (req.scoring_weights or {})
        )
        if sw.get("resume_weight") is not None:
            default_resume = float(sw["resume_weight"])
        if sw.get("interview_weight") is not None:
            default_interview = float(sw["interview_weight"])

    total = default_resume + default_interview
    if total <= 0:
        return 0.4, 0.6
    return default_resume / total, default_interview / total

