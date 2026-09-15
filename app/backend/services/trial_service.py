"""Self-serve trial lifecycle management."""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.backend.models.db_models import Tenant, SubscriptionPlan

logger = logging.getLogger(__name__)

DEFAULT_TRIAL_DAYS = int(os.getenv("DEFAULT_TRIAL_DAYS", "14"))


def start_trial(
    db: Session,
    tenant: Tenant,
    *,
    plan_name: str = "growth",
    trial_days: Optional[int] = None,
) -> Tenant:
    """Start a time-boxed trial on a paid plan."""
    days = trial_days if trial_days is not None else DEFAULT_TRIAL_DAYS
    plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.name == plan_name,
        SubscriptionPlan.is_active == True,
    ).first()
    if not plan:
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.name.in_(("growth", "pro")),
            SubscriptionPlan.is_active == True,
        ).first()
    if not plan:
        raise ValueError(f"No active plan found for trial (requested: {plan_name})")

    now = datetime.now(timezone.utc)
    tenant.plan_id = plan.id
    tenant.subscription_status = "trialing"
    tenant.trial_ends_at = now + timedelta(days=days)
    tenant.current_period_start = now
    tenant.current_period_end = tenant.trial_ends_at
    db.flush()
    logger.info("Started %d-day trial for tenant_id=%s on plan=%s", days, tenant.id, plan.name)
    return tenant


def expire_trials(db: Session) -> int:
    """End unconverted trials: free plan fallback, not payment dunning."""
    from app.backend.services.plan_entitlement_service import get_default_plan

    now = datetime.now(timezone.utc)
    expired = (
        db.query(Tenant)
        .filter(
            Tenant.subscription_status == "trialing",
            Tenant.trial_ends_at.isnot(None),
            Tenant.trial_ends_at < now,
            Tenant.deleted_at.is_(None),
        )
        .all()
    )
    default_plan = get_default_plan(db)
    for tenant in expired:
        tenant.subscription_status = "expired"
        tenant.suspended_reason = "Trial expired — upgrade to continue"
        if default_plan is not None:
            tenant.plan_id = default_plan.id
    if expired:
        db.commit()
    return len(expired)


def is_trial_active(tenant: Tenant) -> bool:
    if tenant.subscription_status != "trialing" or not tenant.trial_ends_at:
        return False
    ends = tenant.trial_ends_at
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < ends


def trial_days_remaining(tenant: Tenant) -> Optional[int]:
    if not is_trial_active(tenant):
        return None
    ends = tenant.trial_ends_at
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    delta = ends - datetime.now(timezone.utc)
    return max(0, delta.days)
