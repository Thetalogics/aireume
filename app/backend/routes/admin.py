"""
Platform admin routes — tenant management, audit logs, usage oversight.
All endpoints require platform admin privileges.
"""
import json
import math
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.backend.db.database import get_db
from app.backend.middleware.auth import (
    require_platform_admin,
    require_platform_write,
    require_super_admin,
    require_support,
    require_security_admin,
    require_billing_admin,
    require_readonly_platform,
    ALL_PLATFORM_ROLES,
    PLATFORM_ROLE_PRODUCT_OWNER,
    PLATFORM_ROLE_SUPER_ADMIN,
)
from app.backend.models.db_models import (
    AuditLog, Tenant, User, SubscriptionPlan, UsageLog,
    FeatureFlag, TenantFeatureOverride,
    Webhook, WebhookDelivery,
    PlatformConfig, PlatformSetting, TenantEmailConfig,
    SecurityEvent, ImpersonationSession, ErasureLog,
    PlanFeature, RateLimitConfig, DunningRecord,
    SSOConfig, SSOGroupRoleMapping, RevokedToken, AdminNotification,
)
from app.backend.services.audit_service import log_audit
from app.backend.services.weight_mapper import validate_and_normalize_weights
from app.backend.services.billing.factory import get_payment_provider
from app.backend.services.billing.dunning_service import dunning_service
from app.backend.services.proration_service import calculate_proration, get_plan_price_for_period
from app.backend.services.impersonation_service import (
    create_impersonation_session,
    list_active_sessions,
    revoke_impersonation_session_by_id,
)
from app.backend.services.security_event_service import get_security_events

from app.backend.routes.admin_tenant_ops import register_tenant_ops

router = APIRouter(prefix="/api/admin", tags=["admin"])
register_tenant_ops(router)


# ─── Pydantic Models ────────────────────────────────────────────────────────────

class SuspendRequest(BaseModel):
    reason: str


class ChangePlanRequest(BaseModel):
    plan_id: int
    reason: str = Field(..., min_length=3)


class AdjustUsageRequest(BaseModel):
    analyses_count: Optional[int] = None
    storage_used_bytes: Optional[int] = None


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    contact_email: Optional[str] = None
    plan_id: Optional[int] = None


class UpdateTenantRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    contact_email: Optional[str] = None
    subscription_status: Optional[str] = None
    scoring_weights: Optional[dict] = None  # Tenant-level default scoring weights


class AddUserToTenantRequest(BaseModel):
    email: str
    role: str = "user"
    is_platform_admin: bool = False
    platform_role: Optional[str] = None


class SSOGroupMappingItem(BaseModel):
    idp_group: str
    role: str


class SSOConfigRequest(BaseModel):
    idp_entity_id: str
    idp_sso_url: str
    idp_slo_url: Optional[str] = None
    idp_certificate: str
    enforce_sso: bool = False
    auto_provision: bool = True
    default_role: str = "viewer"
    groups_attribute: Optional[str] = "groups"
    group_mappings: Optional[List[SSOGroupMappingItem]] = None
    is_active: bool = True


class BillingSettingsRequest(BaseModel):
    active_provider: str
    stripe: Optional[dict] = None
    razorpay: Optional[dict] = None


class TestBillingConnectionRequest(BaseModel):
    provider: str


class GenerateCheckoutLinkRequest(BaseModel):
    tenant_id: int
    plan_id: int


class TenantListItem(BaseModel):
    id: int
    name: str
    slug: str
    plan_name: Optional[str] = None
    plan_display_name: Optional[str] = None
    subscription_status: str
    analyses_count_this_month: int
    storage_used_bytes: int
    user_count: int
    created_at: Optional[str] = None
    suspended_at: Optional[str] = None


class TenantListResponse(BaseModel):
    items: list[TenantListItem]
    total: int
    page: int
    per_page: int
    pages: int


class TenantUserItem(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    created_at: Optional[str] = None


class UsageLogItem(BaseModel):
    id: int
    action: str
    quantity: int
    details: Optional[dict] = None
    created_at: Optional[str] = None
    user_email: Optional[str] = None


class AuditLogItem(BaseModel):
    id: int
    actor_email: str
    action: str
    resource_type: str
    resource_id: Optional[int] = None
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: Optional[str] = None


class AuditLogResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    per_page: int
    pages: int


class TenantDetailResponse(BaseModel):
    id: int
    name: str
    slug: str
    subscription_status: str
    plan_id: Optional[int] = None
    plan_name: Optional[str] = None
    plan_display_name: Optional[str] = None
    analyses_count_this_month: int
    storage_used_bytes: int
    suspended_at: Optional[str] = None
    suspended_reason: Optional[str] = None
    created_at: Optional[str] = None
    users: list[TenantUserItem]
    recent_usage_logs: list[UsageLogItem]
    recent_audit_logs: list[AuditLogItem]


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _dt_to_iso(dt_value):
    """Safely convert a datetime to ISO string."""
    if dt_value is None:
        return None
    return dt_value.isoformat()


def _parse_audit_details(details_str):
    """Parse JSON details string from AuditLog."""
    if details_str is None:
        return None
    try:
        return json.loads(details_str)
    except (json.JSONDecodeError, TypeError):
        return None


def get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For header or request client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── 1. List Tenants ────────────────────────────────────────────────────────────

@router.get("/tenants", response_model=TenantListResponse)
def list_tenants(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    plan_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List all tenants with pagination, search, and filters."""
    query = db.query(Tenant).options(joinedload(Tenant.plan))

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Tenant.name.ilike(search_term)) | (Tenant.slug.ilike(search_term))
        )

    # Plan filter
    if plan_id is not None:
        query = query.filter(Tenant.plan_id == plan_id)

    # Status filter
    if status:
        query = query.filter(Tenant.subscription_status == status)

    # Total count before pagination
    total = query.count()

    # Sorting
    SORTABLE_TENANT_COLUMNS = {"name", "created_at", "subscription_status", "slug", "updated_at"}
    if sort_by not in SORTABLE_TENANT_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort column: {sort_by}. Allowed: {', '.join(sorted(SORTABLE_TENANT_COLUMNS))}",
        )
    sort_column = getattr(Tenant, sort_by)
    if sort_order.lower() == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Pagination
    offset = (page - 1) * per_page
    tenants = query.offset(offset).limit(per_page).all()

    pages = math.ceil(total / per_page) if total > 0 else 0

    items = []
    for t in tenants:
        user_count = db.query(func.count(User.id)).filter(User.tenant_id == t.id).scalar() or 0
        items.append(TenantListItem(
            id=t.id,
            name=t.name,
            slug=t.slug,
            plan_name=t.plan.name if t.plan else None,
            plan_display_name=t.plan.display_name if t.plan else None,
            subscription_status=t.subscription_status,
            analyses_count_this_month=t.analyses_count_this_month,
            storage_used_bytes=t.storage_used_bytes,
            user_count=user_count,
            created_at=_dt_to_iso(t.created_at),
            suspended_at=_dt_to_iso(t.suspended_at),
        ))

    return TenantListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ─── 2. Tenant Detail ──────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
def get_tenant_detail(
    tenant_id: int,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get full tenant detail with users, usage logs, and audit logs."""
    tenant = db.query(Tenant).options(joinedload(Tenant.plan)).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Users
    users = db.query(User).filter(User.tenant_id == tenant_id).all()
    user_items = [
        TenantUserItem(
            id=u.id,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            created_at=_dt_to_iso(u.created_at),
        )
        for u in users
    ]

    # Recent usage logs (last 20)
    usage_logs = (
        db.query(UsageLog)
        .filter(UsageLog.tenant_id == tenant_id)
        .order_by(UsageLog.created_at.desc())
        .limit(20)
        .all()
    )
    usage_items = [
        UsageLogItem(
            id=ul.id,
            action=ul.action,
            quantity=ul.quantity,
            details=_parse_audit_details(ul.details),
            created_at=_dt_to_iso(ul.created_at),
            user_email=ul.user.email if ul.user else None,
        )
        for ul in usage_logs
    ]

    # Recent audit logs for this tenant (last 20)
    audit_logs = (
        db.query(AuditLog)
        .filter(AuditLog.resource_type == "tenant", AuditLog.resource_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    audit_items = [
        AuditLogItem(
            id=al.id,
            actor_email=al.actor_email,
            action=al.action,
            resource_type=al.resource_type,
            resource_id=al.resource_id,
            details=_parse_audit_details(al.details),
            ip_address=al.ip_address,
            created_at=_dt_to_iso(al.created_at),
        )
        for al in audit_logs
    ]

    return TenantDetailResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        subscription_status=tenant.subscription_status,
        plan_id=tenant.plan_id,
        plan_name=tenant.plan.name if tenant.plan else None,
        plan_display_name=tenant.plan.display_name if tenant.plan else None,
        analyses_count_this_month=tenant.analyses_count_this_month,
        storage_used_bytes=tenant.storage_used_bytes,
        suspended_at=_dt_to_iso(tenant.suspended_at),
        suspended_reason=tenant.suspended_reason,
        created_at=_dt_to_iso(tenant.created_at),
        users=user_items,
        recent_usage_logs=usage_items,
        recent_audit_logs=audit_items,
    )


# ─── 3. Suspend Tenant ─────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/suspend")
def suspend_tenant(
    tenant_id: int,
    body: SuspendRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Suspend a tenant account."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.suspended_at is not None:
        raise HTTPException(status_code=400, detail="Tenant is already suspended")

    tenant.suspended_at = datetime.now(timezone.utc)
    tenant.suspended_reason = body.reason
    tenant.subscription_status = "suspended"
    db.query(User).filter(
        User.tenant_id == tenant.id, User.is_platform_admin == False
    ).update({"is_active": False})
    db.commit()

    log_audit(
        db,
        actor=admin,
        action="tenant.suspend",
        resource_type="tenant",
        resource_id=tenant_id,
        details={"reason": body.reason},
        ip_address=get_client_ip(request),
    )

    return {"message": "Tenant suspended", "tenant_id": tenant_id}


# ─── 4. Reactivate Tenant ──────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/reactivate")
def reactivate_tenant(
    tenant_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Reactivate a suspended tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.suspended_at is None:
        raise HTTPException(status_code=400, detail="Tenant is not suspended")

    tenant.suspended_at = None
    tenant.suspended_reason = None
    tenant.subscription_status = "active"
    db.commit()

    log_audit(
        db,
        actor=admin,
        action="tenant.reactivate",
        resource_type="tenant",
        resource_id=tenant_id,
        ip_address=get_client_ip(request),
    )

    return {"message": "Tenant reactivated", "tenant_id": tenant_id}


# ─── 5. Change Plan ─────────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/change-plan")
def change_tenant_plan(
    tenant_id: int,
    body: ChangePlanRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Change a tenant's subscription plan with proration calculation."""
    tenant = db.query(Tenant).options(joinedload(Tenant.plan)).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    new_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == body.plan_id).first()
    if not new_plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    old_plan = tenant.plan
    old_plan_name = old_plan.name if old_plan else "none"
    old_plan_display = old_plan.display_name if old_plan else "None"
    old_plan_id = tenant.plan_id

    # ── Proration calculation ──────────────────────────────────────────────
    proration = None
    provider_result = None

    if tenant.current_period_start and tenant.current_period_end and old_plan:
        old_price = get_plan_price_for_period(
            old_plan, tenant.current_period_start, tenant.current_period_end
        )
        new_price = get_plan_price_for_period(
            new_plan, tenant.current_period_start, tenant.current_period_end
        )

        proration = calculate_proration(
            old_plan_price=old_price,
            new_plan_price=new_price,
            period_start=tenant.current_period_start,
            period_end=tenant.current_period_end,
        )

        # Apply proration through the active payment provider
        if not proration.get("skipped", False):
            try:
                provider = get_payment_provider(db)
                provider_result = provider.prorate_plan_change(
                    tenant_id=tenant.id,
                    subscription_id=tenant.stripe_subscription_id or "",
                    old_plan_price=old_price,
                    new_plan_price=new_price,
                    proration_data=proration,
                )
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                provider_result = {
                    "status": "error",
                    "error": str(exc),
                    "provider": "unknown",
                    "error_code": "PRORATION_PROVIDER_ERROR",
                }

    # ── Apply the plan change ──────────────────────────────────────────────
    tenant.plan_id = body.plan_id
    tenant.subscription_updated_at = datetime.now(timezone.utc)
    db.commit()

    # ── Audit log ──────────────────────────────────────────────────────────
    audit_details = {
        "old_plan_id": old_plan_id,
        "old_plan_name": old_plan_name,
        "old_plan_display_name": old_plan_display,
        "new_plan_id": new_plan.id,
        "new_plan_name": new_plan.name,
        "new_plan_display_name": new_plan.display_name,
        "reason": body.reason,
    }
    if proration:
        audit_details["proration"] = proration
    if provider_result:
        audit_details["provider_result"] = provider_result

    log_audit(
        db,
        actor=admin,
        action="tenant.change_plan",
        resource_type="tenant",
        resource_id=tenant_id,
        details=audit_details,
        ip_address=get_client_ip(request),
    )

    response = {
        "message": "Plan changed successfully",
        "tenant_id": tenant_id,
        "old_plan": old_plan_name,
        "new_plan": new_plan.name,
    }
    if proration:
        response["proration"] = proration
    if provider_result:
        response["provider_result"] = provider_result

    return response


# ─── 6. Adjust Usage ────────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/adjust-usage")
def adjust_tenant_usage(
    tenant_id: int,
    body: AdjustUsageRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Override usage counters for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    details = {}
    if body.analyses_count is not None:
        details["old_analyses_count"] = tenant.analyses_count_this_month
        details["new_analyses_count"] = body.analyses_count
        tenant.analyses_count_this_month = body.analyses_count

    if body.storage_used_bytes is not None:
        details["old_storage_used_bytes"] = tenant.storage_used_bytes
        details["new_storage_used_bytes"] = body.storage_used_bytes
        tenant.storage_used_bytes = body.storage_used_bytes

    db.commit()

    log_audit(
        db,
        actor=admin,
        action="tenant.adjust_usage",
        resource_type="tenant",
        resource_id=tenant_id,
        details=details,
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Usage adjusted",
        "tenant_id": tenant_id,
        "analyses_count_this_month": tenant.analyses_count_this_month,
        "storage_used_bytes": tenant.storage_used_bytes,
    }


# ─── 7. Tenant Usage History ────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/usage-history")
def get_tenant_usage_history(
    tenant_id: int,
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get usage logs for a specific tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    logs = (
        db.query(UsageLog)
        .filter(UsageLog.tenant_id == tenant_id)
        .order_by(UsageLog.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": ul.id,
            "action": ul.action,
            "quantity": ul.quantity,
            "details": _parse_audit_details(ul.details),
            "created_at": _dt_to_iso(ul.created_at),
            "user_email": ul.user.email if ul.user else None,
        }
        for ul in logs
    ]


# ─── Tenant CRUD Operations ─────────────────────────────────────────────

@router.post("/tenants")
def create_tenant(
    body: CreateTenantRequest,
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Create a new tenant organization."""
    # Validate slug uniqueness
    existing = db.query(Tenant).filter(Tenant.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Tenant slug '{body.slug}' already exists")

    # Validate plan if provided
    if body.plan_id:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == body.plan_id).first()
        if not plan:
            raise HTTPException(status_code=400, detail=f"Plan {body.plan_id} not found")

    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        plan_id=body.plan_id,
        subscription_status="active",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    # Create default rate limit config
    default_rate_limit = RateLimitConfig(
        tenant_id=tenant.id,
        requests_per_minute=60,
        llm_concurrent_max=2,
    )
    db.add(default_rate_limit)
    db.commit()

    log_audit(
        db, actor=admin, action="tenant.create",
        resource_type="tenant", resource_id=tenant.id,
        details={"name": tenant.name, "slug": tenant.slug},
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Tenant created successfully",
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
    }


@router.put("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: int,
    body: UpdateTenantRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Update tenant details."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Validate slug uniqueness if changing
    if body.slug and body.slug != tenant.slug:
        existing = db.query(Tenant).filter(Tenant.slug == body.slug).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Tenant slug '{body.slug}' already exists")

    # Update fields
    updates = {}
    if body.name is not None:
        tenant.name = body.name
        updates["name"] = body.name
    if body.slug is not None:
        tenant.slug = body.slug
        updates["slug"] = body.slug
    if body.contact_email is not None:
        tenant.contact_email = body.contact_email
        updates["contact_email"] = body.contact_email
    if body.subscription_status is not None:
        tenant.subscription_status = body.subscription_status
        updates["subscription_status"] = body.subscription_status
    if body.scoring_weights is not None:
        normalized = validate_and_normalize_weights(body.scoring_weights)
        tenant.scoring_weights = json.dumps(normalized)
        updates["scoring_weights"] = normalized

    db.commit()
    db.refresh(tenant)

    log_audit(
        db, actor=admin, action="tenant.update",
        resource_type="tenant", resource_id=tenant_id,
        details=updates,
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Tenant updated successfully",
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
    }


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: int,
    request: Request,
    confirm: bool = Query(False),
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete a tenant and deactivate all users. Requires explicit confirmation."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant_name = tenant.name
    tenant_slug = tenant.slug

    # Soft delete tenant and deactivate all associated users
    tenant.deleted_at = datetime.now(timezone.utc)
    tenant.subscription_status = "cancelled"
    db.query(User).filter(User.tenant_id == tenant.id).update({"is_active": False})
    db.commit()

    log_audit(
        db, actor=admin, action="tenant.delete",
        resource_type="tenant", resource_id=tenant_id,
        details={"name": tenant_name, "slug": tenant_slug},
        ip_address=get_client_ip(request),
        tenant_id=tenant_id,
    )

    return {"message": f"Tenant '{tenant_name}' soft-deleted successfully"}


# ─── Tenant SSO Configuration ─────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/sso")
def get_tenant_sso(
    tenant_id: int,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get SSO configuration for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant_id).first()
    if not sso_config:
        return {"enabled": False}

    return {
        "enabled": sso_config.is_active,
        "provider_type": sso_config.provider_type,
        "idp_entity_id": sso_config.idp_entity_id,
        "idp_sso_url": sso_config.idp_sso_url,
        "idp_slo_url": sso_config.idp_slo_url,
        "sp_entity_id": sso_config.sp_entity_id,
        "sp_acs_url": sso_config.sp_acs_url,
        "enforce_sso": sso_config.enforce_sso,
        "auto_provision": sso_config.auto_provision,
        "default_role": sso_config.default_role,
        "groups_attribute": getattr(sso_config, "groups_attribute", None) or "groups",
        "group_mappings": [
            {"idp_group": m.idp_group, "role": m.role}
            for m in db.query(SSOGroupRoleMapping).filter(SSOGroupRoleMapping.tenant_id == tenant_id).all()
        ],
        "is_active": sso_config.is_active,
        "created_at": _dt_to_iso(sso_config.created_at),
        "updated_at": _dt_to_iso(sso_config.updated_at),
    }


@router.put("/tenants/{tenant_id}/sso")
def update_tenant_sso(
    tenant_id: int,
    body: SSOConfigRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Create or update SSO configuration for a tenant."""
    # Validate default_role
    ALLOWED_SSO_ROLES = {"viewer", "recruiter", "admin"}
    if body.default_role and body.default_role not in ALLOWED_SSO_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid default_role. Allowed: {', '.join(sorted(ALLOWED_SSO_ROLES))}",
        )

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    base_url = os.getenv("BASE_URL", "http://localhost:8080")
    sp_entity_id = f"{base_url}/api/sso/metadata/{tenant.slug}"
    sp_acs_url = f"{base_url}/api/sso/callback/{tenant.slug}"

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant_id).first()
    if sso_config:
        sso_config.idp_entity_id = body.idp_entity_id
        sso_config.idp_sso_url = body.idp_sso_url
        sso_config.idp_slo_url = body.idp_slo_url
        sso_config.idp_certificate = body.idp_certificate
        sso_config.sp_entity_id = sp_entity_id
        sso_config.sp_acs_url = sp_acs_url
        sso_config.enforce_sso = body.enforce_sso
        sso_config.auto_provision = body.auto_provision
        sso_config.default_role = body.default_role
        sso_config.groups_attribute = body.groups_attribute or "groups"
        sso_config.is_active = body.is_active
        action = "tenant.sso_update"
    else:
        sso_config = SSOConfig(
            tenant_id=tenant_id,
            provider_type="saml2",
            idp_entity_id=body.idp_entity_id,
            idp_sso_url=body.idp_sso_url,
            idp_slo_url=body.idp_slo_url,
            idp_certificate=body.idp_certificate,
            sp_entity_id=sp_entity_id,
            sp_acs_url=sp_acs_url,
            enforce_sso=body.enforce_sso,
            auto_provision=body.auto_provision,
            default_role=body.default_role,
            groups_attribute=body.groups_attribute or "groups",
            is_active=body.is_active,
        )
        db.add(sso_config)
        action = "tenant.sso_create"

    if body.group_mappings is not None:
        db.query(SSOGroupRoleMapping).filter(SSOGroupRoleMapping.tenant_id == tenant_id).delete()
        for item in body.group_mappings:
            if item.role not in ALLOWED_SSO_ROLES:
                raise HTTPException(status_code=400, detail=f"Invalid mapped role: {item.role}")
            db.add(SSOGroupRoleMapping(
                tenant_id=tenant_id,
                idp_group=item.idp_group.strip(),
                role=item.role,
            ))

    db.commit()
    db.refresh(sso_config)

    log_audit(
        db,
        actor=admin,
        action=action,
        resource_type="tenant",
        resource_id=tenant_id,
        details={
            "idp_entity_id": body.idp_entity_id,
            "enforce_sso": body.enforce_sso,
            "auto_provision": body.auto_provision,
        },
        ip_address=get_client_ip(request),
    )

    return {
        "message": "SSO configuration saved",
        "tenant_id": tenant_id,
        "sp_entity_id": sp_entity_id,
        "sp_acs_url": sp_acs_url,
    }


@router.delete("/tenants/{tenant_id}/sso")
def delete_tenant_sso(
    tenant_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Remove SSO configuration for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant_id).first()
    if not sso_config:
        raise HTTPException(status_code=404, detail="SSO configuration not found")

    db.delete(sso_config)
    db.commit()

    log_audit(
        db,
        actor=admin,
        action="tenant.sso_delete",
        resource_type="tenant",
        resource_id=tenant_id,
        ip_address=get_client_ip(request),
    )

    return {"message": "SSO configuration deleted", "tenant_id": tenant_id}


@router.post("/tenants/{tenant_id}/sso/test")
def test_tenant_sso(
    tenant_id: int,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Test SSO configuration (validates certificate format, checks IdP URL)."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant_id).first()
    if not sso_config:
        raise HTTPException(status_code=404, detail="SSO configuration not found")

    errors = []

    # Validate certificate
    try:
        from app.backend.services.sso_service import _parse_x509_cert
        _parse_x509_cert(sso_config.idp_certificate)
    except (ValueError, TypeError) as exc:
        errors.append(f"Invalid X.509 certificate: {exc}")

    # Basic URL validation
    if not sso_config.idp_sso_url.startswith(("http://", "https://")):
        errors.append("IdP SSO URL must start with http:// or https://")

    if sso_config.idp_slo_url and not sso_config.idp_slo_url.startswith(("http://", "https://")):
        errors.append("IdP SLO URL must start with http:// or https://")

    if errors:
        return {"valid": False, "errors": errors}

    return {"valid": True, "message": "SSO configuration appears valid"}


# ─── Tenant User Management ─────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/users")
def add_user_to_tenant(
    tenant_id: int,
    body: AddUserToTenantRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Add an existing user to a tenant or create a new user account."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if body.is_platform_admin:
        role = body.platform_role or "support"
        if role not in ALL_PLATFORM_ROLES:
            raise HTTPException(status_code=400, detail=f"Invalid platform_role: {role}")
        if role == PLATFORM_ROLE_PRODUCT_OWNER and admin.platform_role not in (
            PLATFORM_ROLE_SUPER_ADMIN, PLATFORM_ROLE_PRODUCT_OWNER
        ):
            raise HTTPException(
                status_code=403,
                detail="Only super_admin or product_owner can assign the product_owner role",
            )

    # Check if user already exists
    existing_user = db.query(User).filter(User.email == body.email).first()
    if existing_user:
        # Cross-tenant move requires super_admin
        if existing_user.tenant_id != tenant_id:
            if not (admin.platform_role in ("super_admin", "product_owner")):
                raise HTTPException(
                    status_code=403,
                    detail="Cross-tenant user reassignment requires super_admin privileges",
                )
            # Log the cross-tenant move for audit
            log_audit(
                db,
                actor=admin,
                action="user.cross_tenant_move",
                resource_type="user",
                resource_id=str(existing_user.id),
                details={"from_tenant": existing_user.tenant_id, "to_tenant": tenant_id},
                ip_address=get_client_ip(request),
            )

        # Update tenant assignment
        old_tenant_id = existing_user.tenant_id
        existing_user.tenant_id = tenant_id
        existing_user.role = body.role
        if body.is_platform_admin:
            existing_user.is_platform_admin = True
            existing_user.platform_role = body.platform_role or "support"
        db.commit()

        log_audit(
            db, actor=admin, action="user.update_tenant",
            resource_type="user", resource_id=existing_user.id,
            details={
                "email": body.email,
                "old_tenant_id": old_tenant_id,
                "new_tenant_id": tenant_id,
                "role": body.role,
            },
            ip_address=get_client_ip(request),
        )

        return {
            "message": "User tenant assignment updated",
            "user_id": existing_user.id,
            "email": existing_user.email,
        }

    # Create the user with an unusable random password. The user sets their own
    # password via a password-reset link emailed to them — the plaintext is never
    # returned in the API response or written to logs.
    import secrets
    from datetime import timedelta
    from app.backend.services.auth_service import get_password_hash
    from app.backend.models.db_models import PasswordResetToken

    hashed_pw = get_password_hash(secrets.token_urlsafe(32))

    new_user = User(
        email=body.email,
        password_hash=hashed_pw,
        tenant_id=tenant_id,
        role=body.role,
        is_active=True,
        is_platform_admin=body.is_platform_admin,
        platform_role=body.platform_role if body.is_platform_admin else None,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    log_audit(
        db, actor=admin, action="user.create",
        resource_type="user", resource_id=new_user.id,
        details={
            "email": body.email,
            "tenant_id": tenant_id,
            "role": body.role,
        },
        ip_address=get_client_ip(request),
    )

    # Issue a password-reset token and email a set-password link to the user.
    email_sent = False
    try:
        reset_token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=new_user.id,
            token=reset_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        db.commit()

        from app.backend.services.email_service import email_service
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        reset_url = f"{frontend_url}/reset-password/{reset_token}"
        html_body = (
            f"<h2>Welcome to ARIA</h2>"
            f"<p>An account has been created for you. Set your password using the "
            f"link below (valid for 24 hours):</p>"
            f'<p><a href="{reset_url}">Set your password</a></p>'
        )
        email_sent = email_service.send_email(
            new_user.email, "Set Your Password — ARIA Platform", html_body
        )
    except (OSError, ConnectionError, TimeoutError, ValueError) as e:  # pragma: no cover - email failures are non-fatal
        import logging
        logging.getLogger(__name__).error(
            "Failed to send set-password email: %s", e, extra={"error_code": "EMAIL_SEND_FAILED"}
        )

    return {
        "message": "User created and added to tenant",
        "user_id": new_user.id,
        "email": new_user.email,
        "invite_email_sent": email_sent,
        "note": (
            "A set-password link was emailed to the user."
            if email_sent else
            "User created, but the set-password email could not be sent. "
            "Ask the user to use 'Forgot password' to set their password."
        ),
    }


@router.delete("/tenants/{tenant_id}/users/{user_id}")
def remove_user_from_tenant(
    tenant_id: int,
    user_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Remove a user from a tenant (soft delete by deactivating)."""
    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in this tenant")

    # Deactivate user instead of deleting
    user.is_active = False

    # Revoke any outstanding access tokens for the deactivated user
    from datetime import timedelta
    revoked = RevokedToken(
        jti=f"user_deactivated_{user.id}_{int(datetime.now(timezone.utc).timestamp())}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(revoked)
    db.commit()

    log_audit(
        db, actor=admin, action="user.deactivate",
        resource_type="user", resource_id=user_id,
        details={"email": user.email, "tenant_id": tenant_id},
        ip_address=get_client_ip(request),
    )

    return {"message": f"User '{user.email}' deactivated"}


class UserStatusRequest(BaseModel):
    is_active: bool


@router.patch("/tenants/{tenant_id}/users/{user_id}/status")
def set_tenant_user_status(
    tenant_id: int,
    user_id: int,
    body: UserStatusRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Activate or deactivate a tenant user. Deactivation bumps refresh_epoch."""
    from datetime import timedelta

    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in this tenant")

    user.is_active = body.is_active
    if not body.is_active:
        user.refresh_epoch = int(user.refresh_epoch or 0) + 1
        db.add(RevokedToken(
            jti=f"user_deactivated_{user.id}_{int(datetime.now(timezone.utc).timestamp())}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))

    db.commit()
    log_audit(
        db, actor=admin,
        action="user.activate" if body.is_active else "user.deactivate",
        resource_type="user", resource_id=user_id,
        details={"email": user.email, "tenant_id": tenant_id, "is_active": body.is_active},
        ip_address=get_client_ip(request),
    )
    return {
        "message": f"User '{user.email}' {'activated' if body.is_active else 'deactivated'}",
        "is_active": body.is_active,
    }


from app.backend.routes.admin_platform import platform_router
router.include_router(platform_router)
