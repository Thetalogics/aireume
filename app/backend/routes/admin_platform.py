"""Platform admin routes: audit logs, notifications, dunning, bias."""
import json
import logging

from fastapi import APIRouter
from sqlalchemy.exc import SQLAlchemyError

from app.backend.routes.admin import *  # noqa: F401,F403
from app.backend.routes.admin import get_client_ip, _dt_to_iso, _parse_audit_details  # noqa: F401

log = logging.getLogger("aria.admin")

platform_router = APIRouter()

# ─── 8. Audit Logs ──────────────────────────────────────────────────────────────

@platform_router.get("/audit-logs", response_model=AuditLogResponse)
def get_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    actor_email: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Query audit logs with filters and pagination."""
    query = db.query(AuditLog)

    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if actor_email:
        query = query.filter(AuditLog.actor_email.ilike(f"%{actor_email}%"))
    if date_from:
        try:
            from_dt = datetime.fromisoformat(date_from)
            query = query.filter(AuditLog.created_at >= from_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use ISO 8601.")
    if date_to:
        try:
            to_dt = datetime.fromisoformat(date_to)
            query = query.filter(AuditLog.created_at <= to_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use ISO 8601.")

    total = query.count()

    logs = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    pages = math.ceil(total / per_page) if total > 0 else 0

    items = [
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
        for al in logs
    ]

    return AuditLogResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@platform_router.get("/audit-logs/export")
def export_audit_logs(
    format: str = Query("csv", pattern="^(csv|json)$"),
    action: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Export filtered audit logs as CSV or JSON (capped at 10 000 rows)."""
    from fastapi.responses import JSONResponse as _JSONResponse, Response as _Response

    query = db.query(AuditLog).order_by(AuditLog.created_at.desc())

    if action:
        query = query.filter(AuditLog.action == action)
    if start_date:
        try:
            query = query.filter(AuditLog.created_at >= datetime.fromisoformat(start_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use ISO 8601.")
    if end_date:
        try:
            query = query.filter(AuditLog.created_at <= datetime.fromisoformat(end_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use ISO 8601.")

    logs = query.limit(10000).all()

    if format == "json":
        data = [
            {
                "id": l.id,
                "action": l.action,
                "actor_email": l.actor_email,
                "resource_type": l.resource_type,
                "resource_id": l.resource_id,
                "details": l.details,
                "ip_address": l.ip_address,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
        return _JSONResponse(content=data)

    # CSV
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Action", "Actor", "Resource Type", "Resource ID", "Details", "IP", "Timestamp"])
    for l in logs:
        writer.writerow([
            l.id, l.action, l.actor_email, l.resource_type, l.resource_id,
            l.details, l.ip_address,
            l.created_at.isoformat() if l.created_at else "",
        ])

    return _Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


# ─── Admin Notifications ────────────────────────────────────────────────────

@platform_router.get("/notifications")
def get_admin_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=200),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List admin platform notifications, newest first."""
    query = db.query(AdminNotification).order_by(AdminNotification.created_at.desc())
    if unread_only:
        query = query.filter(AdminNotification.is_read == False)  # noqa: E712
    notifications = query.limit(limit).all()
    unread_count = (
        db.query(func.count(AdminNotification.id))
        .filter(AdminNotification.is_read == False)  # noqa: E712
        .scalar()
    )
    return {
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "severity": n.severity,
                "title": n.title,
                "message": n.message,
                "tenant_id": n.tenant_id,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "unread_count": unread_count,
    }


@platform_router.put("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Mark a single admin notification as read."""
    notif = db.query(AdminNotification).filter(AdminNotification.id == notification_id).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"success": True}


@platform_router.put("/notifications/read-all")
def mark_all_notifications_read(
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Mark all unread admin notifications as read."""
    db.query(AdminNotification).filter(
        AdminNotification.is_read == False  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"success": True}


# ─── Feature Flags ─────────────────────────────────────
@platform_router.get("/feature-flags")
def list_feature_flags(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List all feature flags with their global state."""
    flags = db.query(FeatureFlag).order_by(FeatureFlag.key).all()
    return [
        {
            "id": f.id,
            "key": f.key,
            "display_name": f.display_name,
            "description": f.description,
            "enabled_globally": f.enabled_globally,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in flags
    ]

@platform_router.put("/feature-flags/{flag_id}")
def toggle_feature_flag(
    flag_id: int,
    body: dict,  # { "enabled_globally": bool }
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Toggle a feature flag's global state."""
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    
    old_state = flag.enabled_globally
    flag.enabled_globally = body.get("enabled_globally", flag.enabled_globally)
    db.commit()
    
    # Invalidate cache
    from app.backend.services.feature_flag_service import invalidate_cache
    invalidate_cache(feature_key=flag.key)
    
    log_audit(
        db, actor=admin, action="feature_flag.toggle", resource_type="feature_flag",
        resource_id=flag_id, details={"key": flag.key, "old": old_state, "new": flag.enabled_globally},
        ip_address=get_client_ip(request),
    )
    
    return {"message": "Feature flag updated", "key": flag.key, "enabled_globally": flag.enabled_globally}

@platform_router.get("/tenants/{tenant_id}/features")
def get_tenant_feature_overrides(
    tenant_id: int,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get feature overrides for a specific tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    overrides = (
        db.query(TenantFeatureOverride)
        .filter(TenantFeatureOverride.tenant_id == tenant_id)
        .all()
    )
    return [
        {
            "id": o.id,
            "feature_flag_id": o.feature_flag_id,
            "feature_key": o.feature_flag.key if o.feature_flag else None,
            "enabled": o.enabled,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in overrides
    ]

@platform_router.put("/tenants/{tenant_id}/features/{flag_id}")
def set_tenant_feature_override(
    tenant_id: int,
    flag_id: int,
    body: dict,  # { "enabled": bool }
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Set a feature override for a specific tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    
    override = (
        db.query(TenantFeatureOverride)
        .filter(TenantFeatureOverride.tenant_id == tenant_id, TenantFeatureOverride.feature_flag_id == flag_id)
        .first()
    )
    if override:
        override.enabled = body.get("enabled", override.enabled)
    else:
        override = TenantFeatureOverride(tenant_id=tenant_id, feature_flag_id=flag_id, enabled=body.get("enabled", True))
        db.add(override)
    db.commit()
    
    from app.backend.services.feature_flag_service import invalidate_cache
    invalidate_cache(tenant_id=tenant_id, feature_key=flag.key)
    
    log_audit(
        db, actor=admin, action="tenant_feature.override", resource_type="tenant",
        resource_id=tenant_id, details={"flag": flag.key, "enabled": override.enabled},
        ip_address=get_client_ip(request),
    )
    
    return {"message": "Feature override set", "flag": flag.key, "enabled": override.enabled}

@platform_router.delete("/tenants/{tenant_id}/features/{flag_id}")
def delete_tenant_feature_override(
    tenant_id: int,
    flag_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Remove a tenant feature override (revert to global)."""
    override = (
        db.query(TenantFeatureOverride)
        .filter(TenantFeatureOverride.tenant_id == tenant_id, TenantFeatureOverride.feature_flag_id == flag_id)
        .first()
    )
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    db.delete(override)
    db.commit()
    
    from app.backend.services.feature_flag_service import invalidate_cache
    invalidate_cache(tenant_id=tenant_id)
    
    log_audit(
        db, actor=admin, action="tenant_feature.override_removed", resource_type="tenant",
        resource_id=tenant_id, details={"flag": flag.key if flag else str(flag_id)},
        ip_address=get_client_ip(request),
    )
    
    return {"message": "Override removed, reverted to global setting"}


# ─── Platform Metrics ─────────────────────────────────────────────────────────

@platform_router.get("/invoices")
def list_all_invoices(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    tenant_id: Optional[int] = Query(None),
):
    """List invoices across all tenants (platform admin)."""
    from app.backend.services.billing.invoice_service import get_all_invoices
    invoices = get_all_invoices(db, limit=limit, offset=offset, status=status, tenant_id=tenant_id)
    return {"invoices": invoices, "total": len(invoices)}


@platform_router.get("/metrics/overview")
def get_platform_metrics_overview(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get platform-wide metrics overview."""
    from sqlalchemy import func as sa_func, case
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    # Tenant counts by status
    tenant_stats = db.query(
        Tenant.subscription_status,
        sa_func.count(Tenant.id)
    ).group_by(Tenant.subscription_status).all()

    tenant_counts = {"total": 0, "active": 0, "suspended": 0, "trialing": 0, "cancelled": 0, "past_due": 0}
    for status, count in tenant_stats:
        tenant_counts[status] = count
        tenant_counts["total"] += count

    # Also count suspended by suspended_at (since subscription_status might be "suspended")
    suspended_count = db.query(sa_func.count(Tenant.id)).filter(Tenant.suspended_at.isnot(None)).scalar()
    tenant_counts["suspended"] = suspended_count

    # User counts
    total_users = db.query(sa_func.count(User.id)).filter(User.is_active == True).scalar()

    # Analysis counts (from usage_logs)
    analyses_today = db.query(sa_func.coalesce(sa_func.sum(UsageLog.quantity), 0)).filter(
        UsageLog.action.in_(["resume_analysis", "batch_analysis"]),
        UsageLog.created_at >= today_start
    ).scalar()

    analyses_this_week = db.query(sa_func.coalesce(sa_func.sum(UsageLog.quantity), 0)).filter(
        UsageLog.action.in_(["resume_analysis", "batch_analysis"]),
        UsageLog.created_at >= week_start
    ).scalar()

    analyses_this_month = db.query(sa_func.coalesce(sa_func.sum(UsageLog.quantity), 0)).filter(
        UsageLog.action.in_(["resume_analysis", "batch_analysis"]),
        UsageLog.created_at >= month_start
    ).scalar()

    # Storage
    total_storage = db.query(sa_func.coalesce(sa_func.sum(Tenant.storage_used_bytes), 0)).scalar()

    # Plan distribution
    plan_dist = db.query(
        SubscriptionPlan.name,
        sa_func.count(Tenant.id)
    ).outerjoin(Tenant, Tenant.plan_id == SubscriptionPlan.id).group_by(SubscriptionPlan.name).all()
    plan_distribution = {name: count for name, count in plan_dist}

    from app.backend.services.billing.invoice_service import get_revenue_metrics
    revenue_metrics = get_revenue_metrics(db)

    # Onboarding funnel metrics
    total_tenants = db.query(sa_func.count(Tenant.id)).filter(Tenant.deleted_at.is_(None)).scalar() or 0
    onboarding_completed = db.query(sa_func.count(Tenant.id)).filter(
        Tenant.onboarding_completed == True, Tenant.deleted_at.is_(None)
    ).scalar() or 0
    verified_users = db.query(sa_func.count(User.id)).filter(
        User.email_verified == True, User.is_active == True
    ).scalar() or 0
    tenants_with_analysis = db.query(sa_func.count(sa_func.distinct(UsageLog.tenant_id))).filter(
        UsageLog.action.in_(["resume_analysis", "batch_analysis"])
    ).scalar() or 0

    from app.backend.models.db_models import OnboardingFunnelEvent
    funnel_event_counts = {}
    for event_name in (
        "wizard_completed",
        "wizard_skipped",
        "sample_data_loaded",
        "checklist_dismissed",
        "readiness_completed",
        "feature_guide_seen",
    ):
        funnel_event_counts[event_name] = db.query(sa_func.count(OnboardingFunnelEvent.id)).filter(
            OnboardingFunnelEvent.event_name == event_name
        ).scalar() or 0

    return {
        # Flat fields for AdminOverviewPage
        "active_users": total_users,
        "total_analyses": int(analyses_this_month),
        "mrr": revenue_metrics["mrr"],
        "collected_this_month": revenue_metrics["collected_this_month"],
        # Nested structure preserved for other consumers
        "tenants": tenant_counts,
        "users": {"total": total_users, "verified": verified_users},
        "analyses": {
            "today": int(analyses_today),
            "this_week": int(analyses_this_week),
            "this_month": int(analyses_this_month),
        },
        "storage": {"total_gb": round(total_storage / (1024 ** 3), 2)},
        "plans": plan_distribution,
        "revenue": revenue_metrics,
        "onboarding_funnel": {
            "registered_tenants": total_tenants,
            "onboarding_completed": onboarding_completed,
            "verified_users": verified_users,
            "tenants_with_first_analysis": tenants_with_analysis,
            "completion_rate_pct": round((onboarding_completed / total_tenants * 100) if total_tenants else 0, 1),
            "events": funnel_event_counts,
        },
    }


@platform_router.get("/metrics/usage-trends")
def get_usage_trends(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
    days: int = 30,
):
    """Get daily usage trends for the last N days."""
    from sqlalchemy import func as sa_func, cast, Date
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Daily analysis counts
    daily_analyses = db.query(
        sa_func.date(UsageLog.created_at).label("day"),
        sa_func.coalesce(sa_func.sum(UsageLog.quantity), 0).label("count")
    ).filter(
        UsageLog.action.in_(["resume_analysis", "batch_analysis"]),
        UsageLog.created_at >= start_date
    ).group_by(sa_func.date(UsageLog.created_at)).order_by(sa_func.date(UsageLog.created_at)).all()

    # Daily new tenant signups
    daily_signups = db.query(
        sa_func.date(Tenant.created_at).label("day"),
        sa_func.count(Tenant.id).label("count")
    ).filter(
        Tenant.created_at >= start_date
    ).group_by(sa_func.date(Tenant.created_at)).order_by(sa_func.date(Tenant.created_at)).all()

    return {
        "period_days": days,
        "analyses": [{"date": str(day), "count": int(count)} for day, count in daily_analyses],
        "signups": [{"date": str(day), "count": int(count)} for day, count in daily_signups],
    }


# ─── Webhooks ─────────────────────────────────────

@platform_router.get("/tenants/{tenant_id}/webhooks")
def list_tenant_webhooks(
    tenant_id: int,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List all webhooks for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    webhooks = db.query(Webhook).filter(Webhook.tenant_id == tenant_id).all()
    return [
        {
            "id": w.id,
            "url": w.url,
            "events": json.loads(w.events) if w.events else [],
            "is_active": w.is_active,
            "failure_count": w.failure_count,
            "last_triggered_at": w.last_triggered_at.isoformat() if w.last_triggered_at else None,
            "last_failure_at": w.last_failure_at.isoformat() if w.last_failure_at else None,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in webhooks
    ]


@platform_router.post("/tenants/{tenant_id}/webhooks")
def create_tenant_webhook(
    tenant_id: int,
    body: dict,  # { "url": str, "secret": str, "events": list[str] }
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Create a new webhook for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    url = body.get("url", "")
    from app.backend.services.webhook_service import validate_webhook_url
    valid, error = validate_webhook_url(url)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    import secrets as secrets_mod
    webhook = Webhook(
        tenant_id=tenant_id,
        url=url,
        secret=body.get("secret", secrets_mod.token_hex(32)),
        events=json.dumps(body.get("events", ["*"])),
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    log_audit(
        db, actor=admin, action="webhook.create", resource_type="webhook",
        resource_id=webhook.id, details={"tenant_id": tenant_id, "url": webhook.url},
        ip_address=get_client_ip(request),
    )

    return {"id": webhook.id, "url": webhook.url, "secret": webhook.secret, "events": body.get("events", ["*"])}


@platform_router.delete("/tenants/{tenant_id}/webhooks/{webhook_id}")
def delete_tenant_webhook(
    tenant_id: int,
    webhook_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Delete a webhook."""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    db.delete(webhook)
    db.commit()

    log_audit(
        db, actor=admin, action="webhook.delete", resource_type="webhook",
        resource_id=webhook_id, details={"tenant_id": tenant_id},
        ip_address=get_client_ip(request),
    )

    return {"message": "Webhook deleted"}


@platform_router.get("/tenants/{tenant_id}/webhooks/{webhook_id}/deliveries")
def list_webhook_deliveries(
    tenant_id: int,
    webhook_id: int,
    limit: int = 50,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List delivery history for a webhook."""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": d.id,
            "event": d.event,
            "response_status": d.response_status,
            "success": d.success,
            "attempt": d.attempt,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in deliveries
    ]


# ─── Billing Configuration ────────────────────────────────────────────────────

def _mask_sensitive(value: str) -> str:
    """Mask a sensitive value, showing only the last 4 characters."""
    if not value or len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


_SENSITIVE_KEY_PATTERNS = ("api_key", "key_secret", "webhook_secret")


def _is_sensitive_key(key: str) -> bool:
    """Check if a config key holds a sensitive value that should be masked."""
    return any(p in key for p in _SENSITIVE_KEY_PATTERNS)


@platform_router.get("/billing/config")
def get_billing_config(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get current billing provider configuration (sensitive values masked)."""
    configs = db.query(PlatformConfig).filter(
        PlatformConfig.config_key.startswith("billing.")
    ).all()

    active_provider = "manual"
    config_items = []
    for c in configs:
        if c.config_key == "billing.active_provider":
            active_provider = c.config_value
            config_items.append({
                "key": c.config_key,
                "value": c.config_value,
                "description": c.description,
            })
        else:
            value = _mask_sensitive(c.config_value) if _is_sensitive_key(c.config_key) else c.config_value
            config_items.append({
                "key": c.config_key,
                "value": value,
                "description": c.description,
            })

    return {
        "active_provider": active_provider,
        "configs": config_items,
    }


@platform_router.put("/billing/config")
def set_billing_config(
    body: dict,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Set active billing provider and credentials.

    Expected body::

        {
            "active_provider": "stripe" | "razorpay" | "manual",
            "configs": {
                "billing.stripe.api_key": "sk_test_...",
                "billing.stripe.webhook_secret": "whsec_...",
                ...
            }
        }
    """
    active_provider = body.get("active_provider", "manual")
    configs = body.get("configs", {})

    # Validate provider name
    from app.backend.services.billing.factory import PROVIDER_REGISTRY
    if active_provider not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {active_provider}")

    # Upsert active_provider config
    existing = db.query(PlatformConfig).filter(
        PlatformConfig.config_key == "billing.active_provider"
    ).first()
    if existing:
        existing.config_value = active_provider
        existing.updated_by = admin.id
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(PlatformConfig(
            config_key="billing.active_provider",
            config_value=active_provider,
            description="Active payment provider",
            updated_by=admin.id,
            updated_at=datetime.now(timezone.utc),
        ))

    # Upsert provider-specific configs
    for key, value in configs.items():
        if not key.startswith("billing."):
            continue
        existing = db.query(PlatformConfig).filter(
            PlatformConfig.config_key == key
        ).first()
        if existing:
            existing.config_value = value
            existing.updated_by = admin.id
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(PlatformConfig(
                config_key=key,
                config_value=value,
                updated_by=admin.id,
                updated_at=datetime.now(timezone.utc),
            ))

    db.commit()

    log_audit(
        db, actor=admin, action="billing.config_updated", resource_type="platform_config",
        resource_id=None, details={"active_provider": active_provider},
        ip_address=get_client_ip(request),
    )

    return {"message": "Billing configuration updated", "active_provider": active_provider}


@platform_router.get("/billing/providers")
def list_billing_providers(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List available billing providers with their required config fields."""
    from app.backend.services.billing.factory import PROVIDER_REGISTRY

    providers = []
    for name, entry in PROVIDER_REGISTRY.items():
        providers.append({
            "name": name,
            "required_config_keys": entry["config_keys"],
        })

    return providers


# ─── Billing Settings Endpoints (platform_settings based) ─────────────────────

@platform_router.get("/billing/settings")
def get_billing_settings(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get current billing provider configuration from platform_settings."""
    keys = ["billing_provider", "stripe_config", "razorpay_config"]
    rows = db.query(PlatformSetting).filter(PlatformSetting.key.in_(keys)).all()
    settings_map = {r.key: r.value for r in rows}

    provider = settings_map.get("billing_provider", "manual")
    stripe_raw = settings_map.get("stripe_config", "{}")
    razorpay_raw = settings_map.get("razorpay_config", "{}")

    try:
        stripe_cfg = json.loads(stripe_raw) if stripe_raw else {}
    except json.JSONDecodeError:
        stripe_cfg = {}
    try:
        razorpay_cfg = json.loads(razorpay_raw) if razorpay_raw else {}
    except json.JSONDecodeError:
        razorpay_cfg = {}

    # Mask sensitive fields on read
    def _mask(val):
        if not val or len(val) <= 4:
            return "****"
        return "*" * (len(val) - 4) + val[-4:]

    if stripe_cfg.get("secret_key"):
        stripe_cfg["secret_key"] = _mask(stripe_cfg["secret_key"])
    if razorpay_cfg.get("key_secret"):
        razorpay_cfg["key_secret"] = _mask(razorpay_cfg["key_secret"])

    return {
        "active_provider": provider,
        "stripe": stripe_cfg,
        "razorpay": razorpay_cfg,
    }


@platform_router.put("/billing/settings")
def update_billing_settings(
    body: BillingSettingsRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Store billing provider configuration in platform_settings."""
    provider = body.active_provider
    if provider not in ("stripe", "razorpay", "manual"):
        raise HTTPException(status_code=400, detail="active_provider must be stripe, razorpay, or manual")

    def _upsert(key: str, value: str):
        row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(PlatformSetting(key=key, value=value))

    _upsert("billing_provider", provider)
    if body.stripe is not None:
        _upsert("stripe_config", json.dumps(body.stripe))
    if body.razorpay is not None:
        _upsert("razorpay_config", json.dumps(body.razorpay))

    db.commit()

    log_audit(
        db,
        actor=admin,
        action="billing.settings_updated",
        resource_type="platform_setting",
        resource_id=None,
        details={"active_provider": provider},
        ip_address=get_client_ip(request),
    )

    return {"message": "Billing settings updated", "active_provider": provider}


@platform_router.post("/billing/settings/test")
def test_billing_connection(
    body: TestBillingConnectionRequest,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Test connectivity with stored Stripe or Razorpay credentials via basic HTTP."""
    provider = body.provider
    if provider not in ("stripe", "razorpay"):
        raise HTTPException(status_code=400, detail="provider must be stripe or razorpay")

    row = db.query(PlatformSetting).filter(PlatformSetting.key == f"{provider}_config").first()
    if not row or not row.value:
        raise HTTPException(status_code=400, detail=f"No configuration found for {provider}")

    try:
        cfg = json.loads(row.value)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"Invalid JSON stored for {provider}")

    import urllib.request
    import urllib.error
    import base64

    if provider == "stripe":
        secret_key = cfg.get("secret_key")
        if not secret_key:
            raise HTTPException(status_code=400, detail="Stripe secret_key not configured")
        req = urllib.request.Request("https://api.stripe.com/v1/account")
        creds = base64.b64encode(f"{secret_key}:".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
            return {"success": True, "message": "Stripe connection successful"}
        except urllib.error.HTTPError as e:
            return {"success": False, "message": f"Stripe API error: {e.code} {e.reason}"}
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            log.warning(
                "Stripe connection test failed: %s", e,
                extra={"error_code": "IO_ERROR" if isinstance(e, OSError) else "UPSTREAM_ERROR"},
            )
            return {"success": False, "message": f"Connection failed: {str(e)}"}

    else:  # razorpay
        key_id = cfg.get("key_id")
        key_secret = cfg.get("key_secret")
        if not key_id or not key_secret:
            raise HTTPException(status_code=400, detail="Razorpay key_id or key_secret not configured")
        req = urllib.request.Request("https://api.razorpay.com/v1/items")
        creds = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
            return {"success": True, "message": "Razorpay connection successful"}
        except urllib.error.HTTPError as e:
            return {"success": False, "message": f"Razorpay API error: {e.code} {e.reason}"}
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            log.warning(
                "Razorpay connection test failed: %s", e,
                extra={"error_code": "IO_ERROR" if isinstance(e, OSError) else "UPSTREAM_ERROR"},
            )
            return {"success": False, "message": f"Connection failed: {str(e)}"}


@platform_router.post("/billing/generate-checkout-link")
def generate_checkout_link(
    body: GenerateCheckoutLinkRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Generate a checkout session/payment link for a tenant and plan."""
    tenant = db.query(Tenant).filter(Tenant.id == body.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == body.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    provider_row = db.query(PlatformSetting).filter(PlatformSetting.key == "billing_provider").first()
    provider = provider_row.value if provider_row else "manual"

    if provider == "manual":
        raise HTTPException(status_code=400, detail="Checkout links are not available for manual billing")

    expires_at = datetime.now(timezone.utc)
    if provider == "stripe":
        # Generate a simulated Stripe checkout session URL
        import uuid
        session_id = f"cs_test_{uuid.uuid4().hex[:24]}"
        checkout_url = f"https://checkout.stripe.com/c/pay/{session_id}"
        expires_at = expires_at.replace(minute=expires_at.minute + 30)
    else:  # razorpay
        import uuid
        link_id = f"plink_{uuid.uuid4().hex[:14]}"
        checkout_url = f"https://razorpay.com/payment-link/{link_id}"
        expires_at = expires_at.replace(minute=expires_at.minute + 30)

    log_audit(
        db,
        actor=admin,
        action="billing.checkout_link_generated",
        resource_type="tenant",
        resource_id=tenant.id,
        details={
            "provider": provider,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "checkout_url": checkout_url,
        },
        ip_address=get_client_ip(request),
    )

    return {
        "checkout_url": checkout_url,
        "expires_at": expires_at.isoformat(),
    }


# ─── Email Notification Endpoints ─────────────────────────────────────────────

@platform_router.get("/notifications/config")
def get_notification_config(
    admin: User = Depends(require_readonly_platform),
):
    """Return SMTP configuration status (password never exposed)."""
    from app.backend.services.email_service import email_service

    return {
        "configured": email_service.is_configured,
        "smtp_host": email_service.smtp_host,
        "smtp_from": email_service.smtp_from,
    }


class TestEmailRequest(BaseModel):
    email: Optional[str] = None


@platform_router.post("/notifications/test")
def send_test_email(
    body: TestEmailRequest = TestEmailRequest(),
    admin: User = Depends(require_platform_write),
):
    """Send a test email to the requesting admin's address (or a provided one).

    Requires platform admin privileges.
    """
    from app.backend.services.email_service import email_service

    recipient = body.email or admin.email
    html = (
        "<h2>Test Email from ARIA</h2>"
        "<p>This is a test notification sent by the ARIA admin panel.</p>"
        "<p>If you received this, SMTP is correctly configured.</p>"
        "<hr><p style='color:gray;font-size:12px;'>"
        "Automated message from ARIA Resume Intelligence.</p>"
    )
    success = email_service.send_email(
        to=recipient,
        subject="ARIA — Test Notification",
        body_html=html,
    )

    if success:
        return {"message": "Test email sent", "recipient": recipient}
    if not email_service.is_configured:
        return {"message": "SMTP not configured — email not sent", "recipient": recipient}
    return {"message": "Failed to send test email", "recipient": recipient}


# --- Tenant Email Configuration ---------------------------------------------


class EmailConfigRequest(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: str
    smtp_from: str
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    encryption_type: str = "tls"  # tls | ssl | none


def _mask_smtp_password(pw: str) -> str:
    """Mask password showing only first and last char, or dots if too short."""
    if not pw:
        return ""
    if len(pw) <= 4:
        return "****"
    return pw[0] + "*" * (len(pw) - 2) + pw[-1]


@platform_router.post("/email-config")
def upsert_email_config(
    body: EmailConfigRequest,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Create or update the tenant SMTP email configuration.

    Passwords are encrypted with Fernet before storage.
    """
    from app.backend.services.email_service import encrypt_password

    config = db.query(TenantEmailConfig).filter(
        TenantEmailConfig.tenant_id == admin.tenant_id
    ).first()

    encrypted_pw = encrypt_password(body.smtp_password)

    if config:
        config.smtp_host = body.smtp_host
        config.smtp_port = body.smtp_port
        config.smtp_user = body.smtp_user
        config.smtp_password = encrypted_pw
        config.smtp_from = body.smtp_from
        config.from_name = body.from_name
        config.reply_to = body.reply_to
        config.encryption_type = body.encryption_type
        config.is_active = True
        config.configured_by = admin.id
        config.updated_at = datetime.now(timezone.utc)
    else:
        config = TenantEmailConfig(
            tenant_id=admin.tenant_id,
            smtp_host=body.smtp_host,
            smtp_port=body.smtp_port,
            smtp_user=body.smtp_user,
            smtp_password=encrypted_pw,
            smtp_from=body.smtp_from,
            from_name=body.from_name,
            reply_to=body.reply_to,
            encryption_type=body.encryption_type,
            is_active=True,
            configured_by=admin.id,
        )
        db.add(config)

    db.commit()
    db.refresh(config)

    log_audit(
        db, actor=admin, action="email_config.upsert",
        resource_type="tenant_email_config", resource_id=config.id,
        details={"smtp_host": body.smtp_host, "smtp_from": body.smtp_from},
        ip_address=get_client_ip(request),
    )

    return {
        "id": config.id,
        "smtp_host": config.smtp_host,
        "smtp_port": config.smtp_port,
        "smtp_user": config.smtp_user,
        "smtp_password": _mask_smtp_password(body.smtp_password),
        "smtp_from": config.smtp_from,
        "from_name": config.from_name,
        "reply_to": config.reply_to,
        "encryption_type": config.encryption_type,
        "is_active": config.is_active,
        "configured_by": config.configured_by,
        "last_test_at": _dt_to_iso(config.last_test_at),
        "last_test_success": config.last_test_success,
        "created_at": _dt_to_iso(config.created_at),
        "updated_at": _dt_to_iso(config.updated_at),
    }


@platform_router.get("/email-config")
def get_email_config(
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get the email configuration for the current admin's tenant.

    Password is masked in the response.
    """
    config = db.query(TenantEmailConfig).filter(
        TenantEmailConfig.tenant_id == admin.tenant_id
    ).first()

    if not config:
        return {"configured": False}

    return {
        "configured": True,
        "id": config.id,
        "smtp_host": config.smtp_host,
        "smtp_port": config.smtp_port,
        "smtp_user": config.smtp_user,
        "smtp_password": _mask_smtp_password(config.smtp_password),
        "smtp_from": config.smtp_from,
        "from_name": config.from_name,
        "reply_to": config.reply_to,
        "encryption_type": config.encryption_type,
        "is_active": config.is_active,
        "configured_by": config.configured_by,
        "last_test_at": _dt_to_iso(config.last_test_at),
        "last_test_success": config.last_test_success,
        "created_at": _dt_to_iso(config.created_at),
        "updated_at": _dt_to_iso(config.updated_at),
    }


@platform_router.post("/email-config/test")
def test_email_config(
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Send a test email using the tenant's saved SMTP configuration.

    Updates last_test_at and last_test_success on the config record.
    """
    from app.backend.services.email_service import get_tenant_email_service

    config = db.query(TenantEmailConfig).filter(
        TenantEmailConfig.tenant_id == admin.tenant_id
    ).first()

    if not config:
        raise HTTPException(
            status_code=404,
            detail="No email configuration found for this tenant. Create one first.",
        )

    tenant_service = get_tenant_email_service(db, admin.tenant_id)
    if not tenant_service:
        raise HTTPException(
            status_code=500,
            detail="Failed to load tenant email configuration. Check ENCRYPTION_KEY.",
        )

    recipient = admin.email
    html = (
        "<h2>Test Email from ARIA (Tenant SMTP)</h2>"
        "<p>This is a test email sent using your tenant's SMTP configuration.</p>"
        "<p>If you received this, your tenant email settings are working correctly.</p>"
        "<hr><p style='color:gray;font-size:12px'>"
        "Automated message from ARIA Resume Intelligence.</p>"
    )
    success = tenant_service.send_email(
        to=recipient,
        subject="ARIA - Tenant SMTP Test",
        body_html=html,
    )

    config.last_test_at = datetime.now(timezone.utc)
    config.last_test_success = success
    db.commit()

    if success:
        return {"message": "Test email sent successfully", "recipient": recipient}
    return {
        "message": "Failed to send test email - check your SMTP credentials",
        "recipient": recipient,
    }


@platform_router.delete("/email-config")
def delete_email_config(
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Remove the tenant email configuration (revert to system default)."""
    config = db.query(TenantEmailConfig).filter(
        TenantEmailConfig.tenant_id == admin.tenant_id
    ).first()

    if not config:
        raise HTTPException(
            status_code=404,
            detail="No email configuration found for this tenant.",
        )

    log_audit(
        db, actor=admin, action="email_config.delete",
        resource_type="tenant_email_config", resource_id=config.id,
        details={"smtp_host": config.smtp_host, "smtp_from": config.smtp_from},
        ip_address=get_client_ip(request),
    )

    db.delete(config)
    db.commit()

    return {"message": "Email configuration removed - system default will be used"}


# ─── Security Events ───────────────────────────────────────────────────────────

class SecurityEventItem(BaseModel):
    id: int
    event_type: str
    tenant_id: Optional[int] = None
    user_id: Optional[int] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    details: Optional[dict] = None
    created_at: Optional[str] = None


class SecurityEventResponse(BaseModel):
    items: list[SecurityEventItem]
    total: int
    page: int
    per_page: int
    pages: int


@platform_router.get("/security-events", response_model=SecurityEventResponse)
def get_security_events_list(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    tenant_id: Optional[int] = Query(None),
    user_id: Optional[int] = Query(None),
    ip_address: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    admin: User = Depends(require_security_admin),
    db: Session = Depends(get_db),
):
    """Query security events with filters and pagination."""
    from_dt = None
    to_dt = None
    if date_from:
        try:
            from_dt = datetime.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use ISO 8601.")
    if date_to:
        try:
            to_dt = datetime.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use ISO 8601.")

    items, total = get_security_events(
        db,
        event_type=event_type,
        tenant_id=tenant_id,
        user_id=user_id,
        ip_address=ip_address,
        date_from=from_dt,
        date_to=to_dt,
        limit=per_page,
        offset=(page - 1) * per_page,
    )

    pages = math.ceil(total / per_page) if total > 0 else 0

    return SecurityEventResponse(
        items=[
            SecurityEventItem(
                id=e.id,
                event_type=e.event_type,
                tenant_id=e.tenant_id,
                user_id=e.user_id,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
                details=_parse_audit_details(e.details),
                created_at=_dt_to_iso(e.created_at),
            )
            for e in items
        ],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ─── Impersonation ─────────────────────────────────────────────────────────────

class ImpersonationSessionItem(BaseModel):
    id: int
    admin_user_id: int
    target_user_id: int
    target_email: Optional[str] = None
    admin_email: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    ip_address: Optional[str] = None


@platform_router.post("/impersonate/{user_id}")
def impersonate_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Create an impersonation session for a target user. Returns a one-time token."""
    if os.getenv("ENVIRONMENT") == "production":
        ticket = (request.headers.get("X-Support-Ticket") or "").strip()
        if not ticket:
            raise HTTPException(status_code=400, detail="Support ticket ID is required to impersonate")
        if not getattr(admin, "mfa_enabled", False):
            raise HTTPException(status_code=403, detail="MFA must be enabled to impersonate")
        from app.backend.services.mfa_service import verify_code
        if not verify_code(admin.mfa_secret, request.headers.get("X-MFA-Code")):
            raise HTTPException(status_code=401, detail="Valid MFA code is required to impersonate")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="Cannot impersonate inactive user")

    # Rate limit: max 5 impersonation sessions per hour per admin
    from datetime import timedelta
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_sessions = db.query(func.count(ImpersonationSession.id)).filter(
        ImpersonationSession.admin_user_id == admin.id,
        ImpersonationSession.created_at > one_hour_ago,
    ).scalar()
    if recent_sessions >= 5:
        raise HTTPException(status_code=429, detail="Impersonation rate limit exceeded (max 5/hour)")

    ip = request.client.host if request.client else ""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()

    raw_token = create_impersonation_session(
        db,
        admin_user_id=admin.id,
        target_user_id=target.id,
        ip_address=ip,
        ttl_minutes=15,
    )

    log_audit(
        db,
        actor=admin,
        action="impersonation.start",
        resource_type="user",
        resource_id=target.id,
        details={"target_email": target.email, "ip": ip, "ticket": request.headers.get("X-Support-Ticket")},
        ip_address=ip,
    )

    from app.backend.services.security_event_service import record_impersonation
    record_impersonation(db, admin_id=admin.id, target_id=target.id, ip_address=ip, started=True)

    return {
        "message": "Impersonation session created",
        "impersonation_token": raw_token,
        "target_user": {"id": target.id, "email": target.email, "tenant_id": target.tenant_id},
        "expires_in_minutes": 15,
    }


@platform_router.get("/impersonate/sessions")
def list_impersonation_sessions(
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """List all active impersonation sessions."""
    sessions = list_active_sessions(db)
    return [
        ImpersonationSessionItem(
            id=s.id,
            admin_user_id=s.admin_user_id,
            target_user_id=s.target_user_id,
            target_email=s.target_user.email if s.target_user else None,
            admin_email=s.admin_user.email if s.admin_user else None,
            expires_at=_dt_to_iso(s.expires_at),
            created_at=_dt_to_iso(s.created_at),
            ip_address=s.ip_address,
        )
        for s in sessions
    ]


@platform_router.delete("/impersonate/sessions/{session_id}")
def revoke_impersonation(
    session_id: int,
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Revoke an active impersonation session."""
    session = db.query(ImpersonationSession).filter(ImpersonationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    success = revoke_impersonation_session_by_id(db, session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Session already revoked or expired")

    log_audit(
        db,
        actor=admin,
        action="impersonation.revoke",
        resource_type="impersonation_session",
        resource_id=session_id,
        details={"target_user_id": session.target_user_id},
        ip_address=get_client_ip(request),
    )

    return {"message": "Impersonation session revoked", "session_id": session_id}


# ─── GDPR Data Erasure ─────────────────────────────────────────────────────────

class ErasureRequest(BaseModel):
    confirm: bool


class ErasureLogItem(BaseModel):
    id: int
    tenant_id: int
    actor_email: Optional[str] = None
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    records_affected: int
    created_at: Optional[str] = None


@platform_router.post("/tenants/{tenant_id}/anonymize")
def anonymize_tenant(
    tenant_id: int,
    body: ErasureRequest,
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Request GDPR data erasure for a tenant. Requires explicit confirmation."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Auto-suspend if not already
    if tenant.suspended_at is None:
        tenant.suspended_at = datetime.now(timezone.utc)
        tenant.suspended_reason = "GDPR data erasure (preparation)"
        db.commit()

    from app.backend.services.erasure_service import request_erasure, execute_erasure

    log = request_erasure(db, tenant_id=tenant_id, actor_user_id=admin.id)

    # Execute immediately (can be made async in future)
    try:
        records_affected = execute_erasure(db, erasure_log_id=log.id)
    except HTTPException:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        log.warning(
            "Erasure failed: %s", exc,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=400, detail="Invalid request") from exc
    except (OSError, RuntimeError, SQLAlchemyError) as exc:
        log.exception(
            "Erasure failed: %s", exc,
            extra={"error_code": "DB_ERROR" if isinstance(exc, SQLAlchemyError) else "IO_ERROR"},
        )
        raise HTTPException(status_code=500, detail=f"Erasure failed: {str(exc)}") from exc

    log_audit(
        db,
        actor=admin,
        action="tenant.anonymize",
        resource_type="tenant",
        resource_id=tenant_id,
        details={"erasure_log_id": log.id, "records_affected": records_affected},
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Tenant data erasure completed",
        "tenant_id": tenant_id,
        "erasure_log_id": log.id,
        "records_affected": records_affected,
        "status": "completed",
    }


@platform_router.get("/tenants/{tenant_id}/anonymize")
def list_erasure_logs(
    tenant_id: int,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """List erasure logs for a tenant."""
    logs = (
        db.query(ErasureLog)
        .filter(ErasureLog.tenant_id == tenant_id)
        .order_by(ErasureLog.created_at.desc())
        .all()
    )
    return [
        ErasureLogItem(
            id=log.id,
            tenant_id=log.tenant_id,
            actor_email=log.actor.email if log.actor else None,
            status=log.status,
            started_at=_dt_to_iso(log.started_at),
            completed_at=_dt_to_iso(log.completed_at),
            records_affected=log.records_affected,
            created_at=_dt_to_iso(log.created_at),
        )
        for log in logs
    ]


@platform_router.get("/tenants/{tenant_id}/anonymize/{erasure_log_id}")
def get_erasure_detail(
    tenant_id: int,
    erasure_log_id: int,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Get details of a specific erasure request."""
    log = (
        db.query(ErasureLog)
        .filter(ErasureLog.id == erasure_log_id, ErasureLog.tenant_id == tenant_id)
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Erasure log not found")

    return ErasureLogItem(
        id=log.id,
        tenant_id=log.tenant_id,
        actor_email=log.actor.email if log.actor else None,
        status=log.status,
        started_at=_dt_to_iso(log.started_at),
        completed_at=_dt_to_iso(log.completed_at),
        records_affected=log.records_affected,
        created_at=_dt_to_iso(log.created_at),
    )


# ─── Plan-Feature Entitlement Management ───────────────────────────────────────

class PlanFeatureItem(BaseModel):
    id: int
    plan_id: int
    feature_flag_id: int
    feature_key: Optional[str] = None
    feature_name: Optional[str] = None
    enabled: bool
    created_at: Optional[str] = None


@platform_router.get("/plans/{plan_id}/features")
def get_plan_feature_mappings(
    plan_id: int,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """List all feature flag mappings for a subscription plan."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    mappings = (
        db.query(PlanFeature)
        .filter(PlanFeature.plan_id == plan_id)
        .all()
    )
    return [
        PlanFeatureItem(
            id=m.id,
            plan_id=m.plan_id,
            feature_flag_id=m.feature_flag_id,
            feature_key=m.feature_flag.key if m.feature_flag else None,
            feature_name=m.feature_flag.display_name if m.feature_flag else None,
            enabled=m.enabled,
            created_at=_dt_to_iso(m.created_at),
        )
        for m in mappings
    ]


@platform_router.put("/plans/{plan_id}/features/{feature_flag_id}")
def update_plan_feature_mapping(
    plan_id: int,
    feature_flag_id: int,
    body: dict,  # { "enabled": bool }
    request: Request,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """Enable or disable a feature for a specific subscription plan."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    flag = db.query(FeatureFlag).filter(FeatureFlag.id == feature_flag_id).first()
    if not flag:
        raise HTTPException(status_code=404, detail="Feature flag not found")

    mapping = (
        db.query(PlanFeature)
        .filter(PlanFeature.plan_id == plan_id, PlanFeature.feature_flag_id == feature_flag_id)
        .first()
    )

    enabled = body.get("enabled", True)
    if mapping:
        mapping.enabled = enabled
    else:
        mapping = PlanFeature(
            plan_id=plan_id,
            feature_flag_id=feature_flag_id,
            enabled=enabled,
        )
        db.add(mapping)

    db.commit()
    db.refresh(mapping)

    # Invalidate cache for all tenants on this plan
    from app.backend.services.feature_flag_service import invalidate_cache
    tenants = db.query(Tenant).filter(Tenant.plan_id == plan_id).all()
    for t in tenants:
        invalidate_cache(tenant_id=t.id, feature_key=flag.key)

    log_audit(
        db, actor=admin, action="plan_feature.update",
        resource_type="plan_feature", resource_id=mapping.id,
        details={"plan_id": plan_id, "feature_flag_id": feature_flag_id, "enabled": enabled},
        ip_address=get_client_ip(request),
    )

    return PlanFeatureItem(
        id=mapping.id,
        plan_id=mapping.plan_id,
        feature_flag_id=mapping.feature_flag_id,
        feature_key=flag.key,
        feature_name=flag.display_name,
        enabled=mapping.enabled,
        created_at=_dt_to_iso(mapping.created_at),
    )


@platform_router.delete("/plans/{plan_id}/features/{feature_flag_id}")
def delete_plan_feature_mapping(
    plan_id: int,
    feature_flag_id: int,
    request: Request,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """Remove a plan-feature mapping (reverts to global flag default)."""
    mapping = (
        db.query(PlanFeature)
        .filter(PlanFeature.plan_id == plan_id, PlanFeature.feature_flag_id == feature_flag_id)
        .first()
    )
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    flag = db.query(FeatureFlag).filter(FeatureFlag.id == feature_flag_id).first()

    db.delete(mapping)
    db.commit()

    # Invalidate cache for all tenants on this plan
    from app.backend.services.feature_flag_service import invalidate_cache
    tenants = db.query(Tenant).filter(Tenant.plan_id == plan_id).all()
    for t in tenants:
        invalidate_cache(tenant_id=t.id, feature_key=flag.key if flag else None)

    log_audit(
        db, actor=admin, action="plan_feature.delete",
        resource_type="plan_feature", resource_id=None,
        details={"plan_id": plan_id, "feature_flag_id": feature_flag_id},
        ip_address=get_client_ip(request),
    )

    return {"message": "Plan-feature mapping removed", "plan_id": plan_id, "feature_flag_id": feature_flag_id}


# ─── Rate Limit Configuration ────────────────────────────────────────────────

class RateLimitConfigItem(BaseModel):
    id: int
    tenant_id: int
    tenant_name: Optional[str] = None
    tenant_slug: Optional[str] = None
    requests_per_minute: int
    llm_concurrent_max: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RateLimitConfigUpdate(BaseModel):
    requests_per_minute: Optional[int] = None
    llm_concurrent_max: Optional[int] = None


@platform_router.get("/rate-limits")
def list_rate_limit_configs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """List all tenant rate limit configurations with pagination and search."""
    query = db.query(RateLimitConfig, Tenant).join(
        Tenant, RateLimitConfig.tenant_id == Tenant.id
    )

    if search:
        search_filter = f"%{search.lower()}%"
        query = query.filter(
            (Tenant.name.ilike(search_filter)) | (Tenant.slug.ilike(search_filter))
        )

    total = query.count()
    configs = query.order_by(Tenant.name).offset((page - 1) * per_page).limit(per_page).all()

    pages = math.ceil(total / per_page) if total > 0 else 0

    return {
        "items": [
            {
                "id": config.id,
                "tenant_id": config.tenant_id,
                "tenant_name": tenant.name,
                "tenant_slug": tenant.slug,
                "requests_per_minute": config.requests_per_minute,
                "llm_concurrent_max": config.llm_concurrent_max,
                "created_at": _dt_to_iso(config.created_at),
                "updated_at": _dt_to_iso(config.updated_at),
            }
            for config, tenant in configs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@platform_router.get("/tenants/{tenant_id}/rate-limit")
def get_tenant_rate_limit(
    tenant_id: int,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get rate limit configuration for a specific tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    config = db.query(RateLimitConfig).filter(
        RateLimitConfig.tenant_id == tenant_id
    ).first()

    if not config:
        return {
            "configured": False,
            "tenant_id": tenant_id,
            "tenant_name": tenant.name,
            "defaults": {
                "requests_per_minute": 60,
                "llm_concurrent_max": 2,
            }
        }

    return {
        "configured": True,
        "id": config.id,
        "tenant_id": config.tenant_id,
        "tenant_name": tenant.name,
        "tenant_slug": tenant.slug,
        "requests_per_minute": config.requests_per_minute,
        "llm_concurrent_max": config.llm_concurrent_max,
        "created_at": _dt_to_iso(config.created_at),
        "updated_at": _dt_to_iso(config.updated_at),
    }


@platform_router.put("/tenants/{tenant_id}/rate-limit")
def update_tenant_rate_limit(
    tenant_id: int,
    body: RateLimitConfigUpdate,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Create or update rate limit configuration for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Validate inputs
    if body.requests_per_minute is not None and body.requests_per_minute < 1:
        raise HTTPException(status_code=400, detail="requests_per_minute must be >= 1")
    if body.llm_concurrent_max is not None and body.llm_concurrent_max < 1:
        raise HTTPException(status_code=400, detail="llm_concurrent_max must be >= 1")

    config = db.query(RateLimitConfig).filter(
        RateLimitConfig.tenant_id == tenant_id
    ).first()

    if config:
        # Update existing
        if body.requests_per_minute is not None:
            config.requests_per_minute = body.requests_per_minute
        if body.llm_concurrent_max is not None:
            config.llm_concurrent_max = body.llm_concurrent_max
        config.updated_at = datetime.now(timezone.utc)
    else:
        # Create new with defaults
        config = RateLimitConfig(
            tenant_id=tenant_id,
            requests_per_minute=body.requests_per_minute or 60,
            llm_concurrent_max=body.llm_concurrent_max or 2,
        )
        db.add(config)

    db.commit()
    db.refresh(config)

    # Invalidate rate limit cache in middleware
    from app.backend.middleware.rate_limit import RateLimitMiddleware
    if RateLimitMiddleware._instance:
        RateLimitMiddleware._instance.config_cache.pop(tenant_id, None)

    log_audit(
        db, actor=admin, action="rate_limit.update",
        resource_type="rate_limit_config", resource_id=config.id,
        details={
            "tenant_id": tenant_id,
            "requests_per_minute": config.requests_per_minute,
            "llm_concurrent_max": config.llm_concurrent_max,
        },
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Rate limit configuration updated",
        "id": config.id,
        "tenant_id": config.tenant_id,
        "requests_per_minute": config.requests_per_minute,
        "llm_concurrent_max": config.llm_concurrent_max,
        "updated_at": _dt_to_iso(config.updated_at),
    }


@platform_router.delete("/tenants/{tenant_id}/rate-limit")
def delete_tenant_rate_limit(
    tenant_id: int,
    request: Request,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Delete tenant rate limit configuration (revert to defaults)."""
    config = db.query(RateLimitConfig).filter(
        RateLimitConfig.tenant_id == tenant_id
    ).first()

    if not config:
        raise HTTPException(status_code=404, detail="Rate limit configuration not found")

    db.delete(config)
    db.commit()

    # Invalidate cache
    from app.backend.middleware.rate_limit import RateLimitMiddleware
    if RateLimitMiddleware._instance:
        RateLimitMiddleware._instance.config_cache.pop(tenant_id, None)

    log_audit(
        db, actor=admin, action="rate_limit.delete",
        resource_type="rate_limit_config", resource_id=config.id,
        details={"tenant_id": tenant_id},
        ip_address=get_client_ip(request),
    )

    return {"message": "Rate limit configuration deleted, tenant will use defaults"}


# ─── Plan CRUD ──────────────────────────────────────────────────────────────────

class PlanListItem(BaseModel):
    id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    price_monthly: int
    price_yearly: int
    currency: str
    features: list[str]
    limits: dict
    is_active: bool
    sort_order: int
    subscriber_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PlanListResponse(BaseModel):
    plans: list[PlanListItem]
    total: int


class CreatePlanRequest(BaseModel):
    name: str
    display_name: str
    price_monthly: int
    price_yearly: int
    description: Optional[str] = None
    limits: Optional[dict] = None
    features: Optional[list[str]] = None
    is_active: Optional[bool] = True
    sort_order: Optional[int] = None
    currency: Optional[str] = "usd"


class UpdatePlanRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    price_monthly: Optional[int] = None
    price_yearly: Optional[int] = None
    description: Optional[str] = None
    limits: Optional[dict] = None
    features: Optional[list[str]] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    currency: Optional[str] = None


class PlanDetailResponse(BaseModel):
    id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    price_monthly: int
    price_yearly: int
    currency: str
    features: list[str]
    limits: dict
    is_active: bool
    sort_order: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _parse_plan_json(text_value: Optional[str], default):
    """Safely parse a JSON text column."""
    if text_value is None:
        return default
    try:
        return json.loads(text_value)
    except (json.JSONDecodeError, TypeError):
        return default


def _validate_plan_name(db: Session, name: str, exclude_id: Optional[int] = None):
    """Ensure plan name is unique."""
    query = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == name)
    if exclude_id is not None:
        query = query.filter(SubscriptionPlan.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=409, detail=f"Plan name '{name}' already exists")


def _validate_plan_fields(body: CreatePlanRequest | UpdatePlanRequest):
    """Validate plan fields."""
    errors = []

    if hasattr(body, "name") and body.name is not None:
        if not body.name.strip():
            errors.append("name cannot be empty")

    if hasattr(body, "price_monthly") and body.price_monthly is not None:
        if body.price_monthly < 0:
            errors.append("price_monthly must be >= 0")

    if hasattr(body, "price_yearly") and body.price_yearly is not None:
        if body.price_yearly < 0:
            errors.append("price_yearly must be >= 0")

    if hasattr(body, "limits") and body.limits is not None:
        if not isinstance(body.limits, dict):
            errors.append("limits must be a JSON object")
        else:
            for key in ("analyses_per_month", "team_members", "storage_gb", "batch_size"):
                if key in body.limits and not isinstance(body.limits[key], int):
                    errors.append(f"limits.{key} must be an integer")

    if hasattr(body, "features") and body.features is not None:
        if not isinstance(body.features, list):
            errors.append("features must be a JSON array")
        else:
            for idx, item in enumerate(body.features):
                if not isinstance(item, str):
                    errors.append(f"features[{idx}] must be a string")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


def _get_subscriber_count(db: Session, plan_id: int) -> int:
    """Count active subscribers for a plan."""
    return (
        db.query(func.count(Tenant.id))
        .filter(
            Tenant.plan_id == plan_id,
            Tenant.subscription_status.in_(["active", "trialing"]),
        )
        .scalar()
        or 0
    )


def _subscription_plan_to_list_item(plan: SubscriptionPlan, subscriber_count: int) -> PlanListItem:
    return PlanListItem(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        description=plan.description,
        price_monthly=plan.price_monthly,
        price_yearly=plan.price_yearly,
        currency=plan.currency,
        features=_parse_plan_json(plan.features, []),
        limits=_parse_plan_json(plan.limits, {}),
        is_active=plan.is_active,
        sort_order=plan.sort_order,
        subscriber_count=subscriber_count,
        created_at=_dt_to_iso(plan.created_at),
        updated_at=_dt_to_iso(plan.updated_at),
    )


@platform_router.get("/plans", response_model=PlanListResponse)
def list_plans(
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """List all subscription plans (including inactive) with subscriber counts."""
    plans = db.query(SubscriptionPlan).order_by(SubscriptionPlan.sort_order, SubscriptionPlan.id).all()

    items = []
    for plan in plans:
        subscriber_count = _get_subscriber_count(db, plan.id)
        items.append(_subscription_plan_to_list_item(plan, subscriber_count))

    return PlanListResponse(plans=items, total=len(items))


@platform_router.post("/plans", response_model=PlanDetailResponse, status_code=201)
def create_plan(
    body: CreatePlanRequest,
    request: Request,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """Create a new subscription plan."""
    _validate_plan_name(db, body.name)
    _validate_plan_fields(body)

    plan = SubscriptionPlan(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        price_monthly=body.price_monthly,
        price_yearly=body.price_yearly,
        currency=(body.currency or "usd").upper(),
        limits=json.dumps(body.limits) if body.limits is not None else "{}",
        features=json.dumps(body.features) if body.features is not None else "[]",
        is_active=body.is_active if body.is_active is not None else True,
        sort_order=body.sort_order if body.sort_order is not None else 0,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    log_audit(
        db,
        actor=admin,
        action="plan.create",
        resource_type="plan",
        resource_id=plan.id,
        details={
            "name": plan.name,
            "display_name": plan.display_name,
            "price_monthly": plan.price_monthly,
            "price_yearly": plan.price_yearly,
            "currency": plan.currency,
        },
        ip_address=get_client_ip(request),
    )

    return PlanDetailResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        description=plan.description,
        price_monthly=plan.price_monthly,
        price_yearly=plan.price_yearly,
        currency=plan.currency,
        features=_parse_plan_json(plan.features, []),
        limits=_parse_plan_json(plan.limits, {}),
        is_active=plan.is_active,
        sort_order=plan.sort_order,
        created_at=_dt_to_iso(plan.created_at),
        updated_at=_dt_to_iso(plan.updated_at),
    )


@platform_router.put("/plans/{plan_id}", response_model=PlanDetailResponse)
def update_plan(
    plan_id: int,
    body: UpdatePlanRequest,
    request: Request,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """Update an existing subscription plan."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    _validate_plan_fields(body)

    before = {}
    after = {}

    if body.name is not None and body.name != plan.name:
        _validate_plan_name(db, body.name, exclude_id=plan.id)
        before["name"] = plan.name
        after["name"] = body.name
        plan.name = body.name

    if body.display_name is not None:
        before["display_name"] = plan.display_name
        after["display_name"] = body.display_name
        plan.display_name = body.display_name

    if body.description is not None:
        before["description"] = plan.description
        after["description"] = body.description
        plan.description = body.description

    if body.price_monthly is not None:
        before["price_monthly"] = plan.price_monthly
        after["price_monthly"] = body.price_monthly
        plan.price_monthly = body.price_monthly

    if body.price_yearly is not None:
        before["price_yearly"] = plan.price_yearly
        after["price_yearly"] = body.price_yearly
        plan.price_yearly = body.price_yearly

    if body.currency is not None:
        before["currency"] = plan.currency
        after["currency"] = body.currency
        plan.currency = body.currency.upper()

    if body.limits is not None:
        before["limits"] = _parse_plan_json(plan.limits, {})
        after["limits"] = body.limits
        plan.limits = json.dumps(body.limits)

    if body.features is not None:
        before["features"] = _parse_plan_json(plan.features, [])
        after["features"] = body.features
        plan.features = json.dumps(body.features)

    if body.is_active is not None:
        before["is_active"] = plan.is_active
        after["is_active"] = body.is_active
        plan.is_active = body.is_active

    if body.sort_order is not None:
        before["sort_order"] = plan.sort_order
        after["sort_order"] = body.sort_order
        plan.sort_order = body.sort_order

    db.commit()
    db.refresh(plan)

    log_audit(
        db,
        actor=admin,
        action="plan.update",
        resource_type="plan",
        resource_id=plan.id,
        details={"before": before, "after": after},
        ip_address=get_client_ip(request),
    )

    return PlanDetailResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        description=plan.description,
        price_monthly=plan.price_monthly,
        price_yearly=plan.price_yearly,
        currency=plan.currency,
        features=_parse_plan_json(plan.features, []),
        limits=_parse_plan_json(plan.limits, {}),
        is_active=plan.is_active,
        sort_order=plan.sort_order,
        created_at=_dt_to_iso(plan.created_at),
        updated_at=_dt_to_iso(plan.updated_at),
    )


@platform_router.delete("/plans/{plan_id}")
def archive_plan(
    plan_id: int,
    request: Request,
    force: bool = Query(False),
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete (archive) a subscription plan. Requires super_admin."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if not plan.is_active:
        raise HTTPException(status_code=400, detail="Plan is already archived")

    subscriber_count = _get_subscriber_count(db, plan_id)
    if subscriber_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Plan has {subscriber_count} active subscriber(s). Use ?force=true to archive anyway.",
        )

    plan.is_active = False
    db.commit()

    log_audit(
        db,
        actor=admin,
        action="plan.archive",
        resource_type="plan",
        resource_id=plan.id,
        details={
            "name": plan.name,
            "subscriber_count": subscriber_count,
            "forced": force,
        },
        ip_address=get_client_ip(request),
    )

    return {
        "message": "Plan archived successfully",
        "plan_id": plan_id,
        "name": plan.name,
        "subscriber_count": subscriber_count,
    }


# ─── Dunning Management ──────────────────────────────────────────────────────

@platform_router.get("/dunning")
def list_dunning(
    status: Optional[str] = Query(None, description="Filter by dunning status: active, exhausted, resolved"),
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """List tenants currently in dunning (active or exhausted).
    Accessible by billing_admin and above."""
    query = db.query(DunningRecord)

    if status:
        query = query.filter(DunningRecord.status == status)
    else:
        # Default: show active + exhausted (not resolved)
        query = query.filter(DunningRecord.status.in_(["active", "exhausted"]))

    records = query.order_by(DunningRecord.next_retry_at.nulls_last()).all()

    results = []
    for r in records:
        tenant = db.query(Tenant).filter(Tenant.id == r.tenant_id).first()
        results.append({
            "id": r.id,
            "tenant_id": r.tenant_id,
            "tenant_name": tenant.name if tenant else "Unknown",
            "tenant_slug": tenant.slug if tenant else None,
            "subscription_status": tenant.subscription_status if tenant else None,
            "status": r.status,
            "retry_count": r.retry_count,
            "max_retries": r.max_retries,
            "next_retry_at": _dt_to_iso(r.next_retry_at),
            "last_retry_at": _dt_to_iso(r.last_retry_at),
            "failure_reason": r.failure_reason,
            "created_at": _dt_to_iso(r.created_at),
        })

    return {"items": results, "total": len(results)}


@platform_router.post("/dunning/{tenant_id}/resolve")
def manually_resolve_dunning(
    tenant_id: int,
    request: Request,
    admin: User = Depends(require_billing_admin),
    db: Session = Depends(get_db),
):
    """Manually resolve dunning for a tenant (e.g., after offline payment).
    Sets dunning status to resolved and tenant subscription_status to active."""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    record = dunning_service.resolve_dunning(db, tenant_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No active dunning record found for this tenant",
        )

    # Also set tenant back to active
    tenant.subscription_status = "active"
    tenant.subscription_updated_at = datetime.now(timezone.utc)
    tenant.suspended_at = None
    tenant.suspended_reason = None
    db.commit()

    log_audit(
        db,
        actor=admin,
        action="dunning.resolve",
        resource_type="tenant",
        resource_id=tenant_id,
        details={
            "dunning_id": record.id,
            "retry_count": record.retry_count,
            "resolved_by": "admin_manual",
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    return {
        "message": "Dunning resolved successfully",
        "tenant_id": tenant_id,
        "dunning_id": record.id,
        "retry_count": record.retry_count,
    }


@platform_router.get("/dunning/process-retries")
def trigger_dunning_retries(
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Manually trigger dunning retry processing.
    This is normally called by a scheduler/cron, but can be triggered manually.
    Requires super_admin."""
    results = dunning_service.process_due_retries(db)

    log_audit(
        db,
        actor=admin,
        action="dunning.process_retries",
        resource_type="platform",
        details={
            "records_processed": len(results),
            "results": results,
        },
        ip_address=get_client_ip(request),
    )

    return {
        "processed": len(results),
        "results": results,
    }


# ─── Bias Audit ───────────────────────────────────────────────────────────────

@platform_router.post("/bias-audit/run")
def run_bias_audit(
    tenant_id: Optional[int] = None,
    group_field: str = "gender",
    days_back: int = 90,
    admin: User = Depends(require_platform_write),
    db: Session = Depends(get_db),
):
    """Run a bias audit on screening outcomes. Requires platform admin.

    Analyzes screening results for disparate impact across demographic groups
    using the EEOC four-fifths rule and score disparity tests.
    """
    from app.backend.services.bias_audit_service import run_bias_audit as _run_audit
    result = _run_audit(db, tenant_id=tenant_id, group_field=group_field, days_back=days_back)
    from dataclasses import asdict
    return asdict(result)


@platform_router.get("/bias-audit/run")
def get_bias_audit(
    tenant_id: Optional[int] = None,
    group_field: str = "gender",
    days_back: int = 90,
    admin: User = Depends(require_readonly_platform),
    db: Session = Depends(get_db),
):
    """Get bias audit results (read-only). Same as POST but for read access."""
    from app.backend.services.bias_audit_service import run_bias_audit as _run_audit
    result = _run_audit(db, tenant_id=tenant_id, group_field=group_field, days_back=days_back)
    from dataclasses import asdict
    return asdict(result)
