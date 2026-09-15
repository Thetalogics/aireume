"""
Subscription routes: current plan, usage tracking, available plans, subscription management.
"""
import json
import logging
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_admin
from app.backend.models.db_models import (
    Tenant, User, SubscriptionPlan, UsageLog, Candidate, UsageAlert
)
from app.backend.services.metadata_utils import safe_parse_metadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/subscription", tags=["subscription"])


def _json_default(obj):
    """Handle non-serializable types for json.dumps (datetime, date, Decimal)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ─── Pydantic Models ────────────────────────────────────────────────────────────

class PlanResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str
    price_monthly: int
    price_yearly: int
    currency: str
    features: list[str]
    limits: dict


class CurrentPlanResponse(BaseModel):
    plan: PlanResponse
    status: str
    billing_cycle: str
    current_period_start: Optional[str]
    current_period_end: Optional[str]
    price: int


class UsageResponse(BaseModel):
    analyses_used: int
    analyses_limit: int
    storage_used_mb: float
    storage_limit_gb: int
    team_members_count: int
    team_members_limit: int
    percent_used: float


class FullSubscriptionResponse(BaseModel):
    current_plan: CurrentPlanResponse
    usage: UsageResponse
    available_plans: list[PlanResponse]
    days_until_reset: int
    enabled_features: list[str] = []


class UsageCheckResponse(BaseModel):
    allowed: bool
    current_usage: int
    limit: int
    message: Optional[str] = None


class AlertPreferencesRequest(BaseModel):
    email_alerts: Optional[bool] = None
    webhook_alerts: Optional[bool] = None
    alert_thresholds: Optional[list[int]] = None


def _get_tenant_alert_thresholds(metadata_json) -> list[int]:
    """Extract alert thresholds from tenant metadata."""
    prefs = safe_parse_metadata(metadata_json)
    thresholds = prefs.get("alert_thresholds")
    if thresholds and isinstance(thresholds, list):
        try:
            return sorted([int(t) for t in thresholds if 0 < int(t) <= 100])
        except (ValueError, TypeError):
            pass
    return [80, 100]  # Default


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_monthly_reset(tenant: Tenant) -> None:
    """Reset monthly usage counters if it's a new month."""
    now = datetime.now(timezone.utc)
    
    if tenant.usage_reset_at is None:
        tenant.usage_reset_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return
    
    # Check if we're in a different month than the last reset
    if now.year != tenant.usage_reset_at.year or now.month != tenant.usage_reset_at.month:
        tenant.analyses_count_this_month = 0
        tenant.usage_reset_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _get_plan_limits(plan: SubscriptionPlan) -> dict:
    """Parse JSON limits from subscription plan."""
    try:
        return json.loads(plan.limits) if plan.limits else {}
    except json.JSONDecodeError:
        return {}


def _get_plan_features(plan: SubscriptionPlan) -> list[str]:
    """Parse JSON features from subscription plan."""
    try:
        return json.loads(plan.features) if plan.features else []
    except json.JSONDecodeError:
        return []


def _plan_to_response(plan: SubscriptionPlan) -> PlanResponse:
    """Convert a SubscriptionPlan model to API response."""
    return PlanResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name or plan.name.title(),
        description=plan.description or "",
        price_monthly=plan.price_monthly,
        price_yearly=plan.price_yearly,
        currency=plan.currency,
        features=_get_plan_features(plan),
        limits=_get_plan_limits(plan),
    )


def _get_storage_used(tenant: Tenant) -> int:
    """Return materialized storage value instead of recalculating.

    The storage_used_bytes field is kept in sync incrementally by the
    resume upload / update paths in analyze.py.  A full-scan fallback
    is available via _recalculate_storage_usage() for admin reconciliation.
    """
    return tenant.storage_used_bytes or 0


def _recalculate_storage_usage(db: Session, tenant_id: int) -> int:
    """Full-scan recalculation of storage for admin reconciliation."""
    total_bytes = db.query(func.sum(func.length(Candidate.raw_resume_text))).filter(
        Candidate.tenant_id == tenant_id
    ).scalar() or 0
    snapshot_bytes = db.query(func.sum(func.length(Candidate.parser_snapshot_json))).filter(
        Candidate.tenant_id == tenant_id
    ).scalar() or 0
    return int(total_bytes + snapshot_bytes)


def _calculate_days_until_reset(tenant: Tenant) -> int:
    """Calculate days until monthly usage reset."""
    now = datetime.now(timezone.utc)
    
    # Get next month
    if now.month == 12:
        next_reset = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_reset = now.replace(month=now.month + 1, day=1)
    
    next_reset = next_reset.replace(hour=0, minute=0, second=0, microsecond=0)
    
    return (next_reset - now).days


def _determine_billing_cycle(tenant: Tenant) -> str:
    """Determine if tenant is on monthly or yearly billing."""
    if not tenant.current_period_start or not tenant.current_period_end:
        return "monthly"
    
    # Calculate period duration
    duration_days = (tenant.current_period_end - tenant.current_period_start).days
    
    if duration_days > 32:  # More than a month suggests yearly
        return "yearly"
    return "monthly"


# ─── Public Routes ──────────────────────────────────────────────────────────────

@router.get("/plans", response_model=list[PlanResponse])
def get_available_plans(db: Session = Depends(get_db)):
    """Get all available subscription plans for display."""
    plans = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.is_active == True
    ).order_by(SubscriptionPlan.sort_order).all()
    
    return [_plan_to_response(plan) for plan in plans]


@router.get("", response_model=FullSubscriptionResponse)
def get_my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get full subscription details for the current tenant."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Ensure monthly usage is reset if needed
    _ensure_monthly_reset(tenant)
    db.commit()
    
    # Get current plan
    plan = tenant.plan
    if not plan:
        # Fallback to free plan if no plan assigned
        from app.backend.services.plan_entitlement_service import get_default_plan
        plan = get_default_plan(db)
        if not plan:
            raise HTTPException(status_code=500, detail="No subscription plan found")
    
    limits = _get_plan_limits(plan)
    
    # Use materialized storage value (incrementally maintained)
    actual_storage = _get_storage_used(tenant)
    
    # Build current plan response
    billing_cycle = _determine_billing_cycle(tenant)
    price = plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly
    
    current_plan = CurrentPlanResponse(
        plan=_plan_to_response(plan),
        status=tenant.subscription_status,
        billing_cycle=billing_cycle,
        current_period_start=tenant.current_period_start.isoformat() if tenant.current_period_start else None,
        current_period_end=tenant.current_period_end.isoformat() if tenant.current_period_end else None,
        price=price,
    )
    
    # Build usage response
    analyses_limit = limits.get("analyses_per_month", 20)
    storage_limit = limits.get("storage_gb", 1)
    team_limit = limits.get("team_members", 1)
    
    # Count active team members in this tenant
    team_count = db.query(func.count(User.id)).filter(
        User.tenant_id == tenant.id,
        User.is_active == True,
    ).scalar() or 1
    
    usage = UsageResponse(
        analyses_used=tenant.analyses_count_this_month,
        analyses_limit=analyses_limit if analyses_limit > 0 else -1,  # -1 represents unlimited
        storage_used_mb=tenant.storage_used_bytes / (1024 * 1024),
        storage_limit_gb=storage_limit,
        team_members_count=team_count,
        team_members_limit=team_limit,
        percent_used=min(
            (tenant.analyses_count_this_month / analyses_limit * 100) if analyses_limit > 0 else 0,
            100
        ),
    )
    
    # Get all available plans
    all_plans = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.is_active == True
    ).order_by(SubscriptionPlan.sort_order).all()
    
    # Resolve enabled features for the tenant
    from app.backend.services.feature_flag_service import get_enabled_features_for_tenant
    enabled_features = get_enabled_features_for_tenant(db, tenant.id)

    return FullSubscriptionResponse(
        current_plan=current_plan,
        usage=usage,
        available_plans=[_plan_to_response(p) for p in all_plans],
        days_until_reset=_calculate_days_until_reset(tenant),
        enabled_features=enabled_features,
    )


@router.get("/check/{action}", response_model=UsageCheckResponse)
def check_usage(
    action: str,
    quantity: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Check if a specific action would exceed usage limits.
    
    Actions:
    - resume_analysis: Single resume analysis
    - batch_analysis: Batch resume analysis (quantity = batch size)
    - storage_upload: File upload (not counted against monthly limit)
    """
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Ensure monthly usage is reset if needed
    _ensure_monthly_reset(tenant)
    db.commit()
    
    plan = tenant.plan
    if not plan:
        from app.backend.services.plan_entitlement_service import get_default_plan
        plan = get_default_plan(db)
    
    if not plan:
        return UsageCheckResponse(
            allowed=False,
            current_usage=0,
            limit=0,
            message="No subscription plan found"
        )
    
    limits = _get_plan_limits(plan)
    
    # Check based on action type
    if action in ["resume_analysis", "batch_analysis"]:
        analyses_limit = limits.get("analyses_per_month", 20)
        
        if analyses_limit < 0:  # Unlimited
            return UsageCheckResponse(
                allowed=True,
                current_usage=tenant.analyses_count_this_month,
                limit=-1,
            )
        
        projected_usage = tenant.analyses_count_this_month + quantity
        
        if projected_usage > analyses_limit:
            remaining = analyses_limit - tenant.analyses_count_this_month
            return UsageCheckResponse(
                allowed=False,
                current_usage=tenant.analyses_count_this_month,
                limit=analyses_limit,
                message=f"Usage limit exceeded. Remaining: {remaining}, Requested: {quantity}"
            )
        
        return UsageCheckResponse(
            allowed=True,
            current_usage=tenant.analyses_count_this_month,
            limit=analyses_limit,
        )
    
    elif action == "storage_upload":
        storage_limit_gb = limits.get("storage_gb", 1)
        storage_limit_bytes = storage_limit_gb * 1024 * 1024 * 1024
        
        if tenant.storage_used_bytes >= storage_limit_bytes:
            return UsageCheckResponse(
                allowed=False,
                current_usage=int(tenant.storage_used_bytes / (1024 * 1024)),
                limit=storage_limit_gb * 1024,
                message=f"Storage limit exceeded. Limit: {storage_limit_gb}GB"
            )
        
        return UsageCheckResponse(
            allowed=True,
            current_usage=int(tenant.storage_used_bytes / (1024 * 1024)),
            limit=storage_limit_gb * 1024,
        )
    
    # Unknown action - allow by default
    return UsageCheckResponse(
        allowed=True,
        current_usage=0,
        limit=-1,
    )


@router.get("/usage-history")
def get_usage_history(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get recent usage history for the tenant."""
    logs = db.query(UsageLog).filter(
        UsageLog.tenant_id == user.tenant_id
    ).order_by(UsageLog.created_at.desc()).limit(limit).all()
    
    return [
        {
            "id": log.id,
            "action": log.action,
            "quantity": log.quantity,
            "details": json.loads(log.details) if log.details else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "user_email": log.user.email if log.user else None,
        }
        for log in logs
    ]


# ─── Admin Routes ─────────────────────────────────────────────────────────────

@router.post("/admin/reset-usage")
def admin_reset_usage(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Admin: Reset usage counters for the tenant (useful for testing)."""
    tenant = db.query(Tenant).filter(Tenant.id == admin.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    old_count = tenant.analyses_count_this_month
    tenant.analyses_count_this_month = 0
    tenant.usage_reset_at = datetime.now(timezone.utc)
    db.commit()
    
    return {
        "message": "Usage counters reset",
        "previous_count": old_count,
        "new_count": 0,
    }


@router.post("/admin/recalculate-storage")
def admin_recalculate_storage(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Admin: Recalculate storage from full DB scan and update materialized value."""
    tenant = db.query(Tenant).filter(Tenant.id == admin.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    old_value = tenant.storage_used_bytes or 0
    actual = _recalculate_storage_usage(db, tenant.id)
    tenant.storage_used_bytes = actual
    db.commit()
    
    return {
        "message": "Storage recalculated",
        "previous_bytes": old_value,
        "actual_bytes": actual,
        "previous_mb": round(old_value / (1024 * 1024), 2),
        "actual_mb": round(actual / (1024 * 1024), 2),
    }


@router.post("/admin/change-plan/{plan_id}")
def admin_change_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Tenant admin: set desired plan. Paid plans require checkout, not a direct grant."""
    from app.backend.services.feature_flag_service import invalidate_cache
    from app.backend.services.plan_entitlement_service import (
        get_default_plan,
        is_paid_plan,
        start_paid_plan_checkout,
    )

    tenant = db.query(Tenant).filter(Tenant.id == admin.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    new_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not new_plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    if new_plan.name == "enterprise":
        raise HTTPException(
            status_code=400,
            detail="Enterprise plans are sales-led. Contact sales@thetalogics.com to upgrade.",
        )

    old_plan_name = tenant.plan.name if tenant.plan else "none"

    if is_paid_plan(new_plan):
        tenant.desired_plan_id = new_plan.id
        if tenant.plan_id is None:
            default = get_default_plan(db)
            if default is not None:
                tenant.plan_id = default.id
        checkout = start_paid_plan_checkout(db, tenant, new_plan)
        db.commit()
        invalidate_cache(tenant_id=tenant.id)
        return {
            "message": "Checkout required to activate paid plan",
            "previous_plan": old_plan_name,
            "desired_plan": new_plan.name,
            "new_plan_display": new_plan.display_name,
            "checkout": checkout,
            "reference_id": checkout.get("reference_id"),
            "checkout_url": checkout.get("checkout_url") or checkout.get("url"),
        }

    tenant.plan_id = plan_id
    tenant.desired_plan_id = plan_id
    tenant.subscription_status = "active"
    tenant.current_period_start = datetime.now(timezone.utc)
    tenant.current_period_end = datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1)
    tenant.subscription_updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache(tenant_id=tenant.id)

    try:
        from app.backend.services.webhook_service import dispatch_event_background
        from app.backend.db.database import SessionLocal
        dispatch_event_background(SessionLocal, tenant.id, "subscription.changed", {"old_plan": old_plan_name, "new_plan": new_plan.name})
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(
            "Webhook dispatch failed for subscription.changed: %s", e,
            extra={"error_code": "WEBHOOK_ERROR"},
        )

    return {
        "message": "Plan changed successfully",
        "previous_plan": old_plan_name,
        "new_plan": new_plan.name,
        "new_plan_display": new_plan.display_name,
    }


# ─── Internal Helpers (used by other routes) ───────────────────────────────────

def record_usage(
    db: Session,
    tenant_id: int,
    user_id: int,
    action: str,
    quantity: int = 1,
    details: Optional[dict] = None
) -> bool:
    """Record usage and increment tenant counter. Returns True if successful.
    
    This function should be called from analyze routes after successful analysis.
    """
    try:
        # Get tenant and check limits
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return False
        
        # Ensure monthly reset
        _ensure_monthly_reset(tenant)
        
        # Check plan limits
        plan = tenant.plan
        if plan:
            limits = _get_plan_limits(plan)
            analyses_limit = limits.get("analyses_per_month", 20)
            
            if analyses_limit >= 0:  # Not unlimited
                if tenant.analyses_count_this_month + quantity > analyses_limit:
                    return False  # Would exceed limit
        
        # Increment counter
        tenant.analyses_count_this_month += quantity
        
        # Log the usage
        usage_log = UsageLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            quantity=quantity,
            details=json.dumps(details, default=_json_default) if details else None,
        )
        db.add(usage_log)
        
        db.commit()
        
        # ── Check usage thresholds (non-blocking) ──────────────────────────────
        try:
            if plan:
                limits = _get_plan_limits(plan)
                analyses_limit = limits.get("analyses_per_month", 20)
                if analyses_limit > 0:
                    from app.backend.services.usage_alert_service import usage_alert_service
                    usage_alert_service.check_and_alert(
                        db, tenant_id, "analyses_per_month",
                        tenant.analyses_count_this_month, analyses_limit,
                    )
        except Exception as e:
            logger.warning(
                "Usage alert check failed for tenant %d: %s", tenant_id, e,
                extra={"error_code": "ALERT_ERROR"},
            )
        
        return True
    
    except SQLAlchemyError as e:
        logger.exception(
            "Failed to record usage: %s", e,
            extra={"error_code": "DB_ERROR"},
        )
        db.rollback()
        return False
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Failed to record usage: %s", e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        db.rollback()
        return False
    except (OSError, RuntimeError) as e:
        logger.exception(
            "Failed to record usage: %s", e,
            extra={"error_code": "IO_ERROR"},
        )
        db.rollback()
        return False


def check_usage_alerts(db: Session, tenant_id: int, metric_name: str, current_value: int, limit_value: int) -> None:
    """Convenience wrapper to check usage thresholds for any metric (non-blocking).

    Call from routes that track storage, team members, etc.
    """
    try:
        if limit_value > 0:
            from app.backend.services.usage_alert_service import usage_alert_service
            usage_alert_service.check_and_alert(db, tenant_id, metric_name, current_value, limit_value)
    except Exception as e:
        logger.warning(
            "Usage alert check failed for tenant %d metric %s: %s", tenant_id, metric_name, e,
            extra={"error_code": "ALERT_ERROR"},
        )


# ─── Usage Alert Routes ──────────────────────────────────────────────────────

@router.get("/alerts")
def get_usage_alerts(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get recent usage alerts for the current tenant."""
    from app.backend.services.usage_alert_service import usage_alert_service
    alerts = usage_alert_service.get_tenant_alerts(db, user.tenant_id, limit=limit)
    return {"alerts": alerts, "total": len(alerts)}


@router.get("/alerts/preferences")
def get_alert_preferences(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get notification preferences for usage alerts."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    thresholds = _get_tenant_alert_thresholds(tenant.metadata_json) if tenant else [80, 100]
    return {
        "email_alerts": True,
        "webhook_alerts": True,
        "thresholds": thresholds,
    }


@router.put("/alerts/preferences")
def update_alert_preferences(
    body: AlertPreferencesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Update notification preferences for usage alerts (admin only)."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Store preferences in tenant metadata_json
    prefs = safe_parse_metadata(tenant.metadata_json)

    if body.email_alerts is not None:
        prefs["email_alerts"] = body.email_alerts
    if body.webhook_alerts is not None:
        prefs["webhook_alerts"] = body.webhook_alerts
    if body.alert_thresholds is not None:
        # Validate thresholds
        validated = []
        for t in body.alert_thresholds:
            if not isinstance(t, int) or t <= 0 or t > 100:
                raise HTTPException(status_code=400, detail=f"Invalid threshold {t}. Must be integer between 1 and 100.")
            validated.append(t)
        prefs["alert_thresholds"] = sorted(validated)

    tenant.metadata_json = json.dumps(prefs)
    db.commit()

    return {
        "email_alerts": prefs.get("email_alerts", True),
        "webhook_alerts": prefs.get("webhook_alerts", True),
        "thresholds": _get_tenant_alert_thresholds(tenant.metadata_json),
    }
