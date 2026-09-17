"""
Team collaboration — invite members, manage comments, share links.
Team composition — skill profiles and gap analysis.
"""
import hashlib
import json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_admin, require_active_subscription
from app.backend.middleware.rbac import require_candidate_read_access, require_recruiter_or_admin
from app.backend.models.db_models import Comment, JdCache, RoleTemplate, ScreeningResult, TeamMember, TeamSkillProfile, Tenant, User
from app.backend.models.schemas import CommentCreate, CommentOut, InviteRequest
from app.backend.routes.auth import _hash_password
from app.backend.services.hybrid_pipeline import parse_jd_rules
from app.backend.services.skill_matcher import JD_CACHE_VERSION
from app.backend.services.audit_service import log_tenant_event
from app.backend.services.team_service import (
    compute_team_gaps,
    create_team_profile,
    delete_team_profile,
    get_team_profile,
    get_team_profiles,
    update_team_profile,
    _profile_to_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["team"])


# ─── Request schemas for team profile endpoints ────────────────────────────────

class TeamProfileCreate(BaseModel):
    team_name: str
    skills: List[Dict[str, Any]] = []
    job_functions: List[str] = []
    member_count: Optional[int] = None


class TeamProfileUpdate(BaseModel):
    team_name: Optional[str] = None
    skills: Optional[List[Dict[str, Any]]] = None
    job_functions: Optional[List[str]] = None
    member_count: Optional[int] = None


@router.get("/team")
def list_team(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    members = (
        db.query(User)
        .filter(User.tenant_id == current_user.tenant_id, User.is_active == True)
        .all()
    )
    return [
        {"id": m.id, "email": m.email, "role": m.role, "created_at": m.created_at}
        for m in members
    ]


@router.post("/invites", dependencies=[Depends(require_active_subscription)])
def invite_member(
    body: InviteRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if db.query(User).filter(User.email == body.email, User.tenant_id == current_user.tenant_id).first():
        raise HTTPException(status_code=400, detail="Email already registered in this workspace")

    from app.backend.services.plan_entitlement_service import check_team_member_capacity

    allowed, count, limit = check_team_member_capacity(db, current_user.tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "detail": f"Team member limit reached ({count}/{limit}). Upgrade your plan to invite more members.",
                "error_code": "PLAN_LIMIT_REACHED",
                "limit": limit,
                "current": count,
            },
        )

    temp_password = secrets.token_urlsafe(12)
    new_user = User(
        tenant_id=current_user.tenant_id,
        email=body.email,
        hashed_password=_hash_password(temp_password),
        role=body.role,
        email_verified=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    from app.backend.services.invite_service import send_team_invite_email
    email_sent = send_team_invite_email(
        db,
        invitee=new_user,
        inviter_name=current_user.email,
        tenant_name=tenant.name if tenant else "your workspace",
        tenant_slug=tenant.slug if tenant else "",
    )
    db.commit()

    log_tenant_event(
        db,
        actor=current_user,
        action="team.invite",
        resource_type="user",
        resource_id=new_user.id,
        details={"email": new_user.email, "role": new_user.role, "email_sent": email_sent},
    )
    db.commit()

    return {
        "message": (
            "Invitation sent. The user will receive an email to set their password."
            if email_sent else
            "User created, but the invitation email could not be sent. "
            "Ask them to use 'Forgot password' to set their password."
        ),
        "user_id": new_user.id,
        "email": new_user.email,
        "role": new_user.role,
        "invite_email_sent": email_sent,
    }


@router.delete("/team/{user_id}")
def remove_member(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    log_tenant_event(
        db,
        actor=current_user,
        action="team.remove",
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email},
    )
    db.commit()
    return {"removed": user_id}


@router.get("/results/{result_id}/comments", response_model=list[CommentOut])
def get_comments(
    result_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == result_id,
        ScreeningResult.tenant_id == current_user.tenant_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    if result.candidate_id:
        require_candidate_read_access(db, current_user, result.candidate_id)

    comments = db.query(Comment).filter(Comment.result_id == result_id).order_by(Comment.created_at).all()
    return [
        CommentOut(
            id=c.id,
            text=c.text,
            created_at=c.created_at,
            author_email=c.author.email if c.author else None,
        )
        for c in comments
    ]


@router.post("/results/{result_id}/comments", response_model=CommentOut)
def add_comment(
    result_id: int,
    body: CommentCreate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db)
):
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == result_id,
        ScreeningResult.tenant_id == current_user.tenant_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    if result.candidate_id:
        require_candidate_read_access(db, current_user, result.candidate_id)

    comment = Comment(result_id=result_id, user_id=current_user.id, text=body.text)
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return CommentOut(
        id=comment.id,
        text=comment.text,
        created_at=comment.created_at,
        author_email=current_user.email,
    )


# ─── Team Skill Profile endpoints ──────────────────────────────────────────────

def _get_or_cache_jd(db: Session, jd_text: str, tenant_id: int | None = None) -> dict:
    """Parse a JD or return the cached result (mirrors analyze.py pattern)."""
    jd_hash = hashlib.md5(f"{tenant_id or 0}:{jd_text}".encode()).hexdigest()
    cached = db.query(JdCache).filter(JdCache.hash == jd_hash).first()
    if cached:
        try:
            parsed = json.loads(cached.result_json)
            if parsed.get("_cache_version") == JD_CACHE_VERSION:
                return parsed
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as e:
            logger.warning(
                "Invalid JD cache JSON for hash %s: %s", jd_hash, e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
    jd_analysis = parse_jd_rules(jd_text)
    jd_analysis["_cache_version"] = JD_CACHE_VERSION
    try:
        db.merge(JdCache(hash=jd_hash, result_json=json.dumps(jd_analysis)))
        db.commit()
    except SQLAlchemyError as e:
        logger.warning(
            "JD cache write failed: %s", e,
            extra={"error_code": "DB_ERROR"},
        )
        db.rollback()
    return jd_analysis


@router.post("/team/profiles")
def create_profile(
    body: TeamProfileCreate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    """Create a new team skill profile."""
    profile = create_team_profile(
        db=db,
        tenant_id=current_user.tenant_id,
        team_name=body.team_name,
        skills=body.skills,
        job_functions=body.job_functions,
        user_id=current_user.id,
        member_count=body.member_count,
    )
    return _profile_to_dict(profile)


@router.get("/team/profiles")
def list_profiles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all team skill profiles for the current tenant."""
    profiles = get_team_profiles(db, tenant_id=current_user.tenant_id)
    return [_profile_to_dict(p) for p in profiles]


@router.put("/team/profiles/{profile_id}")
def update_profile(
    profile_id: int,
    body: TeamProfileUpdate,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    """Update an existing team skill profile."""
    profile = update_team_profile(
        db=db,
        profile_id=profile_id,
        tenant_id=current_user.tenant_id,
        skills=body.skills,
        team_name=body.team_name,
        job_functions=body.job_functions,
        member_count=body.member_count,
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Team profile not found")
    return _profile_to_dict(profile)


@router.delete("/team/profiles/{profile_id}")
def delete_profile(
    profile_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a team skill profile."""
    deleted = delete_team_profile(db, profile_id=profile_id, tenant_id=current_user.tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Team profile not found")
    return {"deleted": profile_id}


@router.get("/team/profiles/{profile_id}/gap-analysis")
def gap_analysis(
    profile_id: int,
    jd_text: Optional[str] = Query(None, description="Raw JD text to parse"),
    role_template_id: Optional[int] = Query(None, description="Role template ID with cached JD"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare a team profile against a JD to identify skill gaps.

    Provide either ``jd_text`` or ``role_template_id``.
    """
    if not jd_text and not role_template_id:
        raise HTTPException(status_code=400, detail="Provide either jd_text or role_template_id")

    # Resolve JD analysis
    if role_template_id:
        template = db.query(RoleTemplate).filter(
            RoleTemplate.id == role_template_id,
            RoleTemplate.tenant_id == current_user.tenant_id,
        ).first()
        if not template:
            raise HTTPException(status_code=404, detail="Role template not found")
        jd_text = template.jd_text

    jd_analysis = _get_or_cache_jd(db, jd_text, current_user.tenant_id)

    result = compute_team_gaps(
        db=db,
        profile_id=profile_id,
        tenant_id=current_user.tenant_id,
        jd_analysis=jd_analysis,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Team profile not found")
    return result
