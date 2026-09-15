"""Plan limits and feature entitlements — single source of truth per tenant."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.backend.models.db_models import SubscriptionPlan, Tenant, User

# Feature keys that can be gated via plan limits JSON and/or plan_features table.
GATED_FEATURE_KEYS = frozenset({
    "video_analysis",
    "batch_analysis",
    "custom_weights",
    "api_access",
    "export_excel",
    "transcript_analysis",
    "email_generation",
    "requisitions",
    "pipeline",
    "compare",
    "analytics",
    "ai_interviews",
    "white_label",
    "hm_workflow",
    "sso",
    "priority_support",
    "dedicated_support",
    "custom_integrations",
})

# Maps limit JSON keys that differ from feature flag keys.
_LIMIT_FEATURE_ALIASES = {
    "batch_analysis": "batch_size",
}


def parse_plan_limits(plan: Optional[SubscriptionPlan]) -> Dict[str, Any]:
    if not plan or not plan.limits:
        return {}
    try:
        data = json.loads(plan.limits)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


DEFAULT_PLAN_NAMES = ("starter", "free")
PAID_DUNNING_STATUSES = frozenset({"active", "past_due"})


def is_paid_plan(plan: Optional[SubscriptionPlan]) -> bool:
    if plan is None:
        return False
    try:
        return int(plan.price_monthly or 0) > 0
    except (TypeError, ValueError):
        return False


def tenant_has_paid_entitlement(tenant: Optional[Tenant]) -> bool:
    """Paid features require verified trial or provider-backed subscription state."""
    if tenant is None:
        return False
    status = (tenant.subscription_status or "").lower()
    if status == "trialing":
        from app.backend.services.trial_service import is_trial_active
        return is_trial_active(tenant)
    return status in PAID_DUNNING_STATUSES


def stripe_price_id_for_plan(plan: SubscriptionPlan) -> str:
    """Map an internal plan to a server-controlled Stripe price_... id."""
    import os

    env_key = f"STRIPE_PRICE_{str(plan.name or '').upper().replace('-', '_')}"
    mapped = os.getenv(env_key, "").strip()
    if mapped.startswith("price_"):
        return mapped
    limits = parse_plan_limits(plan)
    from_limits = str(limits.get("stripe_price_id") or "").strip()
    if from_limits.startswith("price_"):
        return from_limits
    raise ValueError(f"No Stripe price mapped for plan {plan.name}")


def start_paid_plan_checkout(db: Session, tenant: Tenant, plan: SubscriptionPlan) -> Dict[str, Any]:
    import os
    from app.backend.services.billing.factory import get_payment_provider

    frontend = os.getenv("FRONTEND_URL", "http://localhost:5173")
    provider = get_payment_provider(db)
    tenant.desired_plan_id = plan.id
    extra_metadata = {"tenant_id": str(tenant.id), "plan_id": str(plan.id)}
    kwargs: Dict[str, Any] = {}
    if provider.provider_name == "stripe":
        kwargs["price_id"] = stripe_price_id_for_plan(plan)
        kwargs["extra_metadata"] = extra_metadata
    return provider.create_checkout_session(
        tenant_id=tenant.id,
        plan=plan.name,
        success_url=f"{frontend}/billing/success",
        cancel_url=f"{frontend}/billing/cancel",
        stripe_customer_id=tenant.stripe_customer_id or "",
        **kwargs,
    )


def apply_verified_paid_plan(db: Session, tenant: Tenant, purchased_plan_id: int) -> None:
    """Activate the exact plan purchased by a verified checkout event."""
    from app.backend.services.feature_flag_service import invalidate_cache

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == int(purchased_plan_id)).first()
    if plan is None:
        raise ValueError("Purchased plan not found")
    tenant.plan_id = plan.id
    tenant.desired_plan_id = plan.id
    tenant.subscription_status = "active"
    tenant.trial_ends_at = None
    invalidate_cache(tenant_id=tenant.id)


def mark_subscription_active(db: Session, tenant: Tenant) -> None:
    """Renewal/invoice paid: keep the current plan, restore active status."""
    from app.backend.services.feature_flag_service import invalidate_cache

    tenant.subscription_status = "active"
    tenant.trial_ends_at = None
    invalidate_cache(tenant_id=tenant.id)


def get_tenant_plan(db: Session, tenant_id: int) -> Optional[SubscriptionPlan]:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    default = get_default_plan(db)
    if not tenant:
        return default
    plan = None
    if tenant.plan_id:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == tenant.plan_id).first()
    if plan and is_paid_plan(plan) and not tenant_has_paid_entitlement(tenant):
        return default
    if plan:
        return plan
    return default


def get_default_plan(db: Session) -> Optional[SubscriptionPlan]:
    """Resolve the default (starter/free) subscription plan."""
    return db.query(SubscriptionPlan).filter(
        SubscriptionPlan.name.in_(DEFAULT_PLAN_NAMES),
        SubscriptionPlan.is_active == True,
    ).order_by(SubscriptionPlan.sort_order).first()


def resolve_plan_by_name(db: Session, *names: str) -> Optional[SubscriptionPlan]:
    """Look up a plan by one of several names (supports legacy aliases)."""
    return db.query(SubscriptionPlan).filter(
        SubscriptionPlan.name.in_(names),
        SubscriptionPlan.is_active == True,
    ).first()


def get_tenant_limits(db: Session, tenant_id: int) -> Dict[str, Any]:
    return parse_plan_limits(get_tenant_plan(db, tenant_id))


def _limit_allows_feature(limits: Dict[str, Any], feature_key: str) -> Optional[bool]:
    """Return True/False if limits explicitly define this feature, else None."""
    if feature_key in limits and isinstance(limits[feature_key], bool):
        return limits[feature_key]

    if feature_key == "batch_analysis":
        batch_size = limits.get("batch_size", 1)
        try:
            return int(batch_size) > 1
        except (TypeError, ValueError):
            return False

    alias = _LIMIT_FEATURE_ALIASES.get(feature_key)
    if alias and alias in limits:
        val = limits[alias]
        if isinstance(val, bool):
            return val
        if alias == "batch_size":
            try:
                return int(val) > 1
            except (TypeError, ValueError):
                return False
    return None


def tenant_has_feature(db: Session, tenant_id: int, feature_key: str) -> bool:
    """Check plan limits JSON for a feature (used by feature_flag_service)."""
    limits = get_tenant_limits(db, tenant_id)
    allowed = _limit_allows_feature(limits, feature_key)
    if allowed is not None:
        return allowed
    # Unknown feature in limits — defer to plan_features / global flags.
    return True


def check_team_member_capacity(db: Session, tenant_id: int) -> tuple[bool, int, int]:
    """Return (allowed, current_count, limit)."""
    from app.backend.models.db_models import User as UserModel

    limits = get_tenant_limits(db, tenant_id)
    limit = int(limits.get("team_members", 1))
    count = (
        db.query(UserModel)
        .filter(UserModel.tenant_id == tenant_id, UserModel.is_active == True)
        .count()
    )
    if limit < 0:
        return True, count, limit
    return count < limit, count, limit


def get_batch_size_limit(db: Session, tenant_id: int) -> int:
    limits = get_tenant_limits(db, tenant_id)
    try:
        return int(limits.get("batch_size", 1))
    except (TypeError, ValueError):
        return 1


def plan_feature_detail(db: Session, tenant_id: int, feature_key: str) -> Dict[str, Any]:
    """Human-readable entitlement check for API errors."""
    from app.backend.services.feature_flag_service import is_feature_enabled

    enabled = is_feature_enabled(db, tenant_id, feature_key)
    limits = get_tenant_limits(db, tenant_id)
    plan = get_tenant_plan(db, tenant_id)
    return {
        "feature": feature_key,
        "enabled": enabled,
        "plan": plan.name if plan else None,
        "plan_display_name": (plan.display_name or plan.name) if plan else None,
        "upgrade_hint": _upgrade_hint(feature_key),
        "limits": limits,
    }


def _upgrade_hint(feature_key: str) -> str:
    hints = {
        "requisitions": "Upgrade to Growth or higher to use Requisitions.",
        "pipeline": "Upgrade to Growth or higher to use Pipeline.",
        "compare": "Upgrade to Growth or higher to compare candidates.",
        "analytics": "Upgrade to Agency or higher for Analytics.",
        "ai_interviews": "Upgrade to Business or higher for AI Interviews.",
        "video_analysis": "Upgrade to Business or higher for Video Analysis.",
        "api_access": "Upgrade to Agency or higher for API access.",
        "custom_weights": "Upgrade to Business or higher for custom scoring weights.",
        "white_label": "Upgrade to Business or higher for white-label branding.",
        "hm_workflow": "Upgrade to Growth or higher for Hiring Manager workflows.",
        "export_excel": "Upgrade to Growth or higher to export data.",
        "batch_analysis": "Upgrade to Growth or higher for batch screening.",
    }
    return hints.get(feature_key, "Upgrade your plan to access this feature.")
