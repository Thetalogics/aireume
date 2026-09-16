"""Tenant admin operations: invite, password reset, export, API key rotation, notify."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import require_platform_write, require_readonly_platform
from app.backend.models.db_models import (
    ATSConnection,
    AuditLog,
    AdminNotification,
    PasswordResetToken,
    Tenant,
    User,
    Webhook,
)
from app.backend.services.audit_service import log_audit
from app.backend.services.integration_secrets import encrypt_secret
from app.backend.services.invite_service import send_team_invite_email


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


class NotifyTenantBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    title: Optional[str] = None


def register_tenant_ops(router: APIRouter) -> None:
    @router.post("/tenants/{tenant_id}/users/{user_id}/reset-password")
    def reset_user_password(
        tenant_id: int,
        user_id: int,
        request: Request,
        admin: User = Depends(require_platform_write),
        db: Session = Depends(get_db),
    ):
        target = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        reset_token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=target.id,
            token=reset_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        db.commit()
        email_sent = False
        try:
            from app.backend.services.email_service import email_service
            frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
            reset_url = f"{frontend_url}/reset-password/{reset_token}"
            html_body = (
                f"<h2>Reset your ARIA password</h2>"
                f"<p>A platform administrator requested a password reset for your account.</p>"
                f'<p><a href="{reset_url}">Set a new password</a> (valid 24 hours).</p>'
            )
            email_sent = bool(email_service.send_email(
                target.email, "Reset Your Password — ARIA Platform", html_body
            ))
        except (OSError, RuntimeError, ValueError):
            email_sent = False
        log_audit(
            db, actor=admin, action="user.reset_password",
            resource_type="user", resource_id=target.id,
            details={"tenant_id": tenant_id, "email_sent": email_sent},
            ip_address=_client_ip(request),
            tenant_id=tenant_id,
        )
        return {"message": "Password reset email queued", "email_sent": email_sent}

    @router.post("/tenants/{tenant_id}/users/{user_id}/invite")
    def invite_user(
        tenant_id: int,
        user_id: int,
        request: Request,
        admin: User = Depends(require_platform_write),
        db: Session = Depends(get_db),
    ):
        target = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not target or not tenant:
            raise HTTPException(status_code=404, detail="User not found")
        email_sent = send_team_invite_email(
            db,
            invitee=target,
            inviter_name=admin.email,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
        )
        db.commit()
        log_audit(
            db, actor=admin, action="user.invite",
            resource_type="user", resource_id=target.id,
            details={"tenant_id": tenant_id, "email_sent": email_sent},
            ip_address=_client_ip(request),
            tenant_id=tenant_id,
        )
        return {"message": "Invite email queued", "email_sent": email_sent}

    @router.get("/tenants/{tenant_id}/users/{user_id}/activity")
    def user_activity(
        tenant_id: int,
        user_id: int,
        admin: User = Depends(require_readonly_platform),
        db: Session = Depends(get_db),
    ):
        target = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        logs = (
            db.query(AuditLog)
            .filter(
                (AuditLog.actor_user_id == user_id) | (AuditLog.resource_id == user_id),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(50)
            .all()
        )
        return {
            "items": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "resource_type": entry.resource_type,
                    "resource_id": entry.resource_id,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    "details": entry.details,
                }
                for entry in logs
            ]
        }

    @router.post("/tenants/{tenant_id}/reset-api-keys")
    def reset_tenant_api_keys(
        tenant_id: int,
        request: Request,
        admin: User = Depends(require_platform_write),
        db: Session = Depends(get_db),
    ):
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        rotated = 0
        for hook in db.query(Webhook).filter(Webhook.tenant_id == tenant_id).all():
            hook.secret = secrets.token_urlsafe(32)
            rotated += 1
        for conn in db.query(ATSConnection).filter(ATSConnection.tenant_id == tenant_id).all():
            conn.api_key = None
            conn.api_secret = None
            conn.webhook_secret = encrypt_secret(secrets.token_urlsafe(32))
            rotated += 1
        db.commit()
        log_audit(
            db, actor=admin, action="tenant.reset_api_keys",
            resource_type="tenant", resource_id=tenant_id,
            details={"rotated": rotated},
            ip_address=_client_ip(request),
            tenant_id=tenant_id,
        )
        return {"message": "API keys rotated", "rotated": rotated}

    @router.get("/tenants/{tenant_id}/export")
    def export_tenant_data(
        tenant_id: int,
        admin: User = Depends(require_readonly_platform),
        db: Session = Depends(get_db),
    ):
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        users = db.query(User).filter(User.tenant_id == tenant_id).all()
        payload = {
            "tenant": {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "contact_email": tenant.contact_email,
                "subscription_status": tenant.subscription_status,
                "analyses_count_this_month": tenant.analyses_count_this_month,
                "storage_used_bytes": tenant.storage_used_bytes,
                "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            },
            "users": [
                {
                    "id": u.id,
                    "email": u.email,
                    "role": u.role,
                    "is_active": u.is_active,
                    "email_verified": u.email_verified,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ],
        }
        return JSONResponse(content=payload)

    @router.post("/tenants/{tenant_id}/notify")
    def notify_tenant(
        tenant_id: int,
        body: NotifyTenantBody,
        request: Request,
        admin: User = Depends(require_platform_write),
        db: Session = Depends(get_db),
    ):
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        title = body.title or f"Message from ARIA support"
        db.add(AdminNotification(
            type="admin_message",
            severity="info",
            title=title,
            message=body.message,
            tenant_id=tenant_id,
        ))
        db.commit()
        email_sent = False
        if tenant.contact_email:
            try:
                from app.backend.services.email_service import email_service
                email_sent = bool(email_service.send_email(
                    tenant.contact_email,
                    title,
                    f"<p>{body.message}</p>",
                ))
            except (OSError, RuntimeError, ValueError):
                email_sent = False
        log_audit(
            db, actor=admin, action="tenant.notify",
            resource_type="tenant", resource_id=tenant_id,
            details={"email_sent": email_sent},
            ip_address=_client_ip(request),
            tenant_id=tenant_id,
        )
        return {"message": "Notification recorded", "email_sent": email_sent}
