"""
Tenant-level RBAC — admin, ta_lead, recruiter, viewer, hiring_manager.
"""
from fastapi import Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.backend.middleware.auth import get_current_user, require_active_subscription
from app.backend.models.db_models import Requisition, User

TENANT_ROLE_ADMIN = "admin"
TENANT_ROLE_TA_LEAD = "ta_lead"
TENANT_ROLE_RECRUITER = "recruiter"
TENANT_ROLE_VIEWER = "viewer"
TENANT_ROLE_HIRING_MANAGER = "hiring_manager"

WRITE_ROLES = {TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER}
ASSIGNMENT_ROLES = {TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD}
ALL_ROLES = {
    TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER,
    TENANT_ROLE_VIEWER, TENANT_ROLE_HIRING_MANAGER,
}


def get_tenant_role(user: User) -> str:
    """Normalized tenant role; unknown values fail closed to viewer (read-only)."""
    raw = getattr(user, "role", None)
    role = (raw or "").strip().lower()
    if role not in ALL_ROLES:
        return TENANT_ROLE_VIEWER
    return role


def can_write_tenant(user: User) -> bool:
    return get_tenant_role(user) in WRITE_ROLES


def is_tenant_admin(user: User) -> bool:
    return get_tenant_role(user) == TENANT_ROLE_ADMIN


def is_tenant_viewer(user: User) -> bool:
    return get_tenant_role(user) == TENANT_ROLE_VIEWER


def is_hiring_manager(user: User) -> bool:
    return get_tenant_role(user) == TENANT_ROLE_HIRING_MANAGER


def require_candidate_read_access(db: Session, user: User, candidate_id: int) -> None:
    """Hiring managers may only read candidates on requisitions they are assigned to."""
    if get_tenant_role(user) != TENANT_ROLE_HIRING_MANAGER:
        return
    from sqlalchemy import select
    from app.backend.models.db_models import RequisitionCandidate, RequisitionHiringManager

    assigned_req_ids = (
        db.query(Requisition.id)
        .outerjoin(RequisitionHiringManager, RequisitionHiringManager.requisition_id == Requisition.id)
        .filter(
            Requisition.tenant_id == user.tenant_id,
            (Requisition.primary_hiring_manager_id == user.id)
            | (RequisitionHiringManager.user_id == user.id),
        )
        .distinct()
        .subquery()
    )
    linked = (
        db.query(RequisitionCandidate.candidate_id)
        .filter(
            RequisitionCandidate.candidate_id == candidate_id,
            RequisitionCandidate.requisition_id.in_(select(assigned_req_ids.c.id)),
        )
        .first()
    )
    if not linked:
        raise HTTPException(status_code=404, detail="Candidate not found")


def apply_hm_candidate_scope(query, db: Session, user: User):
    """Restrict a Candidate query to HM-assigned requisitions when role is HM."""
    if get_tenant_role(user) != TENANT_ROLE_HIRING_MANAGER:
        return query
    from sqlalchemy import select
    from app.backend.models.db_models import Candidate, RequisitionCandidate, RequisitionHiringManager

    assigned_req_ids = (
        db.query(Requisition.id)
        .outerjoin(RequisitionHiringManager, RequisitionHiringManager.requisition_id == Requisition.id)
        .filter(
            Requisition.tenant_id == user.tenant_id,
            (Requisition.primary_hiring_manager_id == user.id)
            | (RequisitionHiringManager.user_id == user.id),
        )
        .distinct()
        .subquery()
    )
    hm_candidate_ids = (
        db.query(RequisitionCandidate.candidate_id)
        .filter(RequisitionCandidate.requisition_id.in_(select(assigned_req_ids.c.id)))
        .distinct()
        .subquery()
    )
    return query.filter(Candidate.id.in_(select(hm_candidate_ids.c.candidate_id)))


def hm_assigned_candidate_ids_subquery(db: Session, user: User):
    from sqlalchemy import select
    from app.backend.models.db_models import RequisitionCandidate, RequisitionHiringManager

    assigned_req_ids = (
        db.query(Requisition.id)
        .outerjoin(RequisitionHiringManager, RequisitionHiringManager.requisition_id == Requisition.id)
        .filter(
            Requisition.tenant_id == user.tenant_id,
            (Requisition.primary_hiring_manager_id == user.id)
            | (RequisitionHiringManager.user_id == user.id),
        )
        .distinct()
        .subquery()
    )
    return (
        db.query(RequisitionCandidate.candidate_id)
        .filter(RequisitionCandidate.requisition_id.in_(select(assigned_req_ids.c.id)))
        .distinct()
        .subquery()
    )


def restrict_to_hm_candidates(stmt, candidate_id_column, db: Session, user: User):
    if get_tenant_role(user) != TENANT_ROLE_HIRING_MANAGER:
        return stmt
    from sqlalchemy import select

    sub = hm_assigned_candidate_ids_subquery(db, user)
    return stmt.where(candidate_id_column.in_(select(sub.c.candidate_id)))


def get_tenant_user_or_404(db: Session, tenant_id: int, user_id: int, allowed_roles: set[str] | None = None) -> User:
    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in this tenant")
    if allowed_roles is not None and get_tenant_role(user) not in allowed_roles:
        raise HTTPException(status_code=400, detail="User does not have the required role")
    return user


def is_ta_lead(user: User) -> bool:
    return get_tenant_role(user) == TENANT_ROLE_TA_LEAD


def can_assign_recruiters(user: User) -> bool:
    return get_tenant_role(user) in ASSIGNMENT_ROLES


def can_read_tenant(user: User) -> bool:
    return get_tenant_role(user) in ALL_ROLES


def _role_forbidden(required_roles: list[str]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": "Your account is read-only. Ask an admin to upgrade your role to recruiter.",
            "error_code": "ROLE_FORBIDDEN",
            "required_roles": required_roles,
        },
    )


def require_recruiter_or_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not can_write_tenant(current_user):
        raise _role_forbidden([TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER])
    return current_user


def require_assignment_role(
    current_user: User = Depends(get_current_user),
) -> User:
    if not can_assign_recruiters(current_user):
        raise _role_forbidden([TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD])
    return current_user


def require_active_recruiter(
    current_user: User = Depends(require_recruiter_or_admin),
    response: Response = None,
) -> User:
    """Write access with active subscription (analyze, imports, etc.)."""
    return require_active_subscription(current_user, response)


def can_manage_role_template(user: User, template) -> bool:
    """Admins manage all reqs; recruiters manage reqs they created (or legacy unowned)."""
    if is_tenant_admin(user):
        return True
    if not can_write_tenant(user):
        return False
    owner_id = getattr(template, "created_by", None)
    return owner_id is None or owner_id == user.id


def require_role_template_manager(user: User, template) -> None:
    if not can_manage_role_template(user, template):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "You can only edit roles you created. Ask an admin for access.",
                "error_code": "ROLE_OWNERSHIP_FORBIDDEN",
            },
        )


def can_manage_requisition(user: User, req: Requisition) -> bool:
    if is_tenant_admin(user) or is_ta_lead(user):
        return True
    if is_hiring_manager(user):
        return False
    if not can_write_tenant(user):
        return False
    owner_id = getattr(req, "created_by", None)
    assigned_id = getattr(req, "assigned_recruiter_id", None)
    if assigned_id and assigned_id == user.id:
        return True
    return owner_id is None or owner_id == user.id


def require_requisition_access(user: User, req: Requisition, db: Session) -> None:
    """Read access — tenant members + assigned HMs."""
    from app.backend.services.requisition_service import hm_assigned_to_requisition

    if req.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Requisition not found")
    role = get_tenant_role(user)
    if role in {TENANT_ROLE_ADMIN, TENANT_ROLE_TA_LEAD, TENANT_ROLE_RECRUITER, TENANT_ROLE_VIEWER}:
        return
    if role == TENANT_ROLE_HIRING_MANAGER and hm_assigned_to_requisition(db, user.id, req.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"message": "You do not have access to this requisition.", "error_code": "REQ_ACCESS_FORBIDDEN"},
    )


def require_requisition_write(user: User, req: Requisition, db: Session) -> None:
    """Write access — recruiters/admins with ownership; HMs can edit intake when assigned."""
    from app.backend.services.requisition_service import hm_assigned_to_requisition

    if req.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if is_tenant_admin(user):
        return
    if can_write_tenant(user) and can_manage_requisition(user, req):
        return
    if is_hiring_manager(user) and hm_assigned_to_requisition(db, user.id, req.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"message": "You cannot edit this requisition.", "error_code": "REQ_WRITE_FORBIDDEN"},
    )
