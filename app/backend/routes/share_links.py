"""HM magic links — tokenized public handoff access."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user
from app.backend.middleware.rbac import require_recruiter_or_admin
from app.backend.models.db_models import HandoffShareLink, RoleTemplate, User
from app.backend.services.audit_service import log_audit, log_tenant_event
from app.backend.services.handoff_service import build_handoff_package
from app.backend.services.share_crypto import (
    hash_passcode,
    hash_share_token,
    new_share_token,
    verify_passcode,
)

router = APIRouter(prefix="/api", tags=["share-links"])
public_router = APIRouter(prefix="/api/public", tags=["public-handoff"])


class ShareLinkCreate(BaseModel):
    label: Optional[str] = None
    expires_in_days: int = Field(default=14, ge=1, le=90)
    passcode: Optional[str] = Field(default=None, min_length=4, max_length=128)


def _hash_passcode(passcode: str) -> str:
    return hash_passcode(passcode)


class ShareLinkOut(BaseModel):
    id: int
    token: str
    label: Optional[str] = None
    url: str
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    view_count: int
    created_at: str


def _link_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/handoff/{token}"


def _serialize_link(link: HandoffShareLink, request: Request, token: str | None = None) -> ShareLinkOut:
    shown = token or ""
    return ShareLinkOut(
        id=link.id,
        token=shown,
        label=link.label,
        url=_link_url(request, shown) if shown else "",
        expires_at=link.expires_at.isoformat() if link.expires_at else None,
        revoked_at=link.revoked_at.isoformat() if link.revoked_at else None,
        view_count=link.view_count or 0,
        created_at=link.created_at.isoformat() if link.created_at else "",
    )


def _lookup_share_link(db: Session, token: str) -> HandoffShareLink | None:
    token_hash = hash_share_token(token)
    return (
        db.query(HandoffShareLink)
        .filter(
            (HandoffShareLink.token_hash == token_hash) | (HandoffShareLink.token == token)
        )
        .first()
    )


def _get_valid_link(db: Session, token: str) -> HandoffShareLink:
    link = _lookup_share_link(db, token)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    if link.revoked_at:
        raise HTTPException(status_code=410, detail="This link has been revoked")
    if link.expires_at:
        expires = link.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires:
            raise HTTPException(status_code=410, detail="This link has expired")
    return link


@router.post("/jd/{jd_id}/share-links", response_model=ShareLinkOut)
def create_share_link(
    jd_id: int,
    body: ShareLinkCreate,
    request: Request,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    jd = db.query(RoleTemplate).filter(
        RoleTemplate.id == jd_id,
        RoleTemplate.tenant_id == current_user.tenant_id,
    ).first()
    if not jd:
        raise HTTPException(status_code=404, detail="Job description not found")

    token = new_share_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    link = HandoffShareLink(
        token=None,
        token_hash=hash_share_token(token),
        tenant_id=current_user.tenant_id,
        role_template_id=jd_id,
        created_by=current_user.id,
        label=body.label or f"HM Handoff — {jd.name}",
        expires_at=expires_at,
        passcode_hash=_hash_passcode(body.passcode) if body.passcode else None,
    )
    db.add(link)
    log_tenant_event(
        db,
        actor=current_user,
        action="share_link.create",
        resource_type="role_template",
        resource_id=jd_id,
        details={"label": link.label, "expires_at": expires_at.isoformat()},
    )
    db.commit()
    db.refresh(link)
    return _serialize_link(link, request, token=token)


@router.get("/jd/{jd_id}/share-links", response_model=List[ShareLinkOut])
def list_share_links(
    jd_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jd = db.query(RoleTemplate).filter(
        RoleTemplate.id == jd_id,
        RoleTemplate.tenant_id == current_user.tenant_id,
    ).first()
    if not jd:
        raise HTTPException(status_code=404, detail="Job description not found")

    links = (
        db.query(HandoffShareLink)
        .filter(
            HandoffShareLink.role_template_id == jd_id,
            HandoffShareLink.tenant_id == current_user.tenant_id,
        )
        .order_by(HandoffShareLink.created_at.desc())
        .all()
    )
    return [_serialize_link(link, request) for link in links]


@router.delete("/jd/{jd_id}/share-links/{link_id}")
def revoke_share_link(
    jd_id: int,
    link_id: int,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    link = db.query(HandoffShareLink).filter(
        HandoffShareLink.id == link_id,
        HandoffShareLink.role_template_id == jd_id,
        HandoffShareLink.tenant_id == current_user.tenant_id,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found")
    if not link.revoked_at:
        link.revoked_at = datetime.now(timezone.utc)
        log_tenant_event(
            db,
            actor=current_user,
            action="share_link.revoke",
            resource_type="role_template",
            resource_id=jd_id,
            details={"link_id": link_id},
        )
        db.commit()
    return {"revoked": link_id}


@public_router.get("/handoff/{token}")
def get_public_handoff(token: str, request: Request, db: Session = Depends(get_db)):
    """Public read-only HM handoff via magic link (no login required)."""
    from app.backend.services.shared_cache import cache_incr
    ip = request.client.host if request.client else "unknown"
    if cache_incr(f"handoff:{ip}", ttl_seconds=3600) > 60:
        raise HTTPException(status_code=429, detail="Too many requests")

    link = _get_valid_link(db, token)
    passcode = request.headers.get("X-Handoff-Passcode")
    if link.passcode_hash:
        if not verify_passcode(passcode or "", link.passcode_hash):
            raise HTTPException(status_code=401, detail="Passcode required")
    package = build_handoff_package(
        db,
        tenant_id=link.tenant_id,
        jd_id=link.role_template_id,
        requisition_id=link.requisition_id,
        public_view=True,
        generated_by_email="ARIA Recruiting",
    )
    if not package:
        raise HTTPException(status_code=404, detail="Handoff package not found")

    link.view_count = (link.view_count or 0) + 1
    db.commit()

    class _PublicActor:
        id = None
        email = "public-handoff"
        _impersonated_by = None

    log_audit(
        db,
        actor=_PublicActor(),
        action="handoff.view",
        resource_type="handoff_share_link",
        resource_id=link.id,
        details={"token_suffix": token[-6:] if token else "", "ip": ip},
        ip_address=ip,
        tenant_id=link.tenant_id,
    )
    db.commit()

    package["share_link"] = {
        "label": link.label,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
    }
    return package
