"""Feature flag service with in-memory caching."""
import time
import threading
from sqlalchemy.orm import Session
from app.backend.models.db_models import FeatureFlag, TenantFeatureOverride, PlanFeature, Tenant
from app.backend.services.plan_entitlement_service import (
    GATED_FEATURE_KEYS,
    _limit_allows_feature,
    parse_plan_limits,
)

# Simple TTL cache
_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 60  # seconds


def _cache_key(tenant_id: int, feature_key: str) -> str:
    return f"{tenant_id}:{feature_key}"


def _get_cached(key: str):
    try:
        from app.backend.services.shared_cache import cache_get, _client
        if _client() is not None:
            return cache_get(f"ff:{key}")
    except Exception:
        pass
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < _CACHE_TTL:
            return entry["val"]
    return None


def _set_cached(key: str, val: bool):
    try:
        from app.backend.services.shared_cache import cache_set, _client
        if _client() is not None:
            cache_set(f"ff:{key}", val, ttl_seconds=_CACHE_TTL)
            return
    except Exception:
        pass
    with _cache_lock:
        _cache[key] = {"val": val, "ts": time.time()}


def invalidate_cache(tenant_id: int = None, feature_key: str = None):
    """Clear cache entries. If no args, clear all."""
    with _cache_lock:
        if tenant_id is None and feature_key is None:
            _cache.clear()
        else:
            keys_to_remove = []
            for k in _cache:
                if tenant_id and str(tenant_id) in k:
                    keys_to_remove.append(k)
                elif feature_key and feature_key in k:
                    keys_to_remove.append(k)
            for k in keys_to_remove:
                del _cache[k]


def is_feature_enabled(db: Session, tenant_id: int, feature_key: str) -> bool:
    """Check if a feature is enabled for a specific tenant.

    Resolution order:
    1. Tenant-specific override (highest priority)
    2. Plan-feature mapping (admin plan_features table)
    3. Plan limits JSON boolean / derived limits (batch_size, etc.)
    4. Global flag state
    5. Gated features default to False; unknown features default to True
    """
    ck = _cache_key(tenant_id, feature_key)
    cached = _get_cached(ck)
    if cached is not None:
        return cached

    # 1. Tenant-specific override
    override = (
        db.query(TenantFeatureOverride)
        .join(FeatureFlag)
        .filter(TenantFeatureOverride.tenant_id == tenant_id)
        .filter(FeatureFlag.key == feature_key)
        .first()
    )
    if override is not None:
        _set_cached(ck, override.enabled)
        return override.enabled

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    plan = None
    if tenant:
        from app.backend.services.plan_entitlement_service import get_tenant_plan as _effective_plan
        plan = _effective_plan(db, tenant_id)

    # 2. Plan-feature mapping
    if plan is not None:
        plan_feature = (
            db.query(PlanFeature)
            .join(FeatureFlag)
            .filter(PlanFeature.plan_id == plan.id)
            .filter(FeatureFlag.key == feature_key)
            .first()
        )
        if plan_feature is not None:
            _set_cached(ck, plan_feature.enabled)
            return plan_feature.enabled

    # 3. Plan limits JSON
    if plan is not None:
        limits = parse_plan_limits(plan)
        limit_allowed = _limit_allows_feature(limits, feature_key)
        if limit_allowed is not None:
            _set_cached(ck, limit_allowed)
            return limit_allowed

    # 4. Global flag
    flag = db.query(FeatureFlag).filter(FeatureFlag.key == feature_key).first()
    if flag is not None:
        _set_cached(ck, flag.enabled_globally)
        return flag.enabled_globally

    # 5. Safe default
    default = feature_key not in GATED_FEATURE_KEYS
    _set_cached(ck, default)
    return default


def get_all_flags(db: Session) -> list:
    """Get all feature flags."""
    return db.query(FeatureFlag).order_by(FeatureFlag.key).all()


def get_tenant_overrides(db: Session, tenant_id: int) -> list:
    """Get all feature overrides for a tenant."""
    return (
        db.query(TenantFeatureOverride)
        .filter(TenantFeatureOverride.tenant_id == tenant_id)
        .all()
    )


def get_plan_features(db: Session, plan_id: int) -> list:
    """Get all plan-feature mappings for a subscription plan."""
    return (
        db.query(PlanFeature)
        .filter(PlanFeature.plan_id == plan_id)
        .all()
    )


def get_enabled_features_for_tenant(db: Session, tenant_id: int) -> list:
    """Return a list of feature keys enabled for the tenant's current plan.

    This is useful for the frontend to know which features to show/hide.
    """
    from app.backend.services.plan_entitlement_service import GATED_FEATURE_KEYS

    enabled = []
    for feature_key in sorted(GATED_FEATURE_KEYS):
        if is_feature_enabled(db, tenant_id, feature_key):
            enabled.append(feature_key)
    return enabled
