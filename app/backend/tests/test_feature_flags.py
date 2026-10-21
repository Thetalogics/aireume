"""
Tests for feature flag service and admin endpoints.
"""
import pytest
import importlib.util
import re
import time
from fastapi import HTTPException
from app.backend.models.db_models import FeatureFlag, TenantFeatureOverride, Tenant, User
from app.backend.services.feature_flag_service import (
    is_feature_enabled,
    invalidate_cache,
    get_all_flags,
    get_tenant_overrides,
)
from app.backend.services import feature_flag_service, shared_cache


class CursorRedis:
    """Redis double for generated glob patterns, stable cursor pages and deletes."""

    def __init__(self):
        self.values = {}
        self.scan_calls = []
        self.delete_calls = []
        self.failure = None
        self.page_size = 2

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value

    def scan(self, cursor=0, match=None, count=None):
        self.scan_calls.append((cursor, match, count))
        if self.failure == "scan":
            raise RuntimeError("Redis scan failed")
        if cursor == 0:
            self.scan_keys = list(self.values)
        end = min(cursor + self.page_size, len(self.scan_keys))
        # Production emits wildcards and backslash-escaped literals, never classes.
        regex = []
        tokens = iter(match or "*")
        for token in tokens:
            if token == "\\":
                regex.append(re.escape(next(tokens, "\\")))
            elif token == "*":
                regex.append(".*")
            elif token == "?":
                regex.append(".")
            elif token == "[":
                pytest.fail("CursorRedis requires literal brackets to be escaped")
            else:
                regex.append(re.escape(token))
        pattern = re.compile("".join(regex), re.DOTALL)
        page = [key for key in self.scan_keys[cursor:end] if pattern.fullmatch(key)]
        return (end if end < len(self.scan_keys) else 0), page

    def delete(self, *keys):
        self.delete_calls.append(keys)
        if self.failure == "delete":
            raise RuntimeError("Redis delete failed")
        deleted = 0
        for key in keys:
            deleted += key in self.values
            self.values.pop(key, None)
        return deleted

    def keys(self, *args):
        pytest.fail("Blocking KEYS must never be used")

    def flushdb(self):
        pytest.fail("FLUSHDB must never be used")

    def flushall(self):
        pytest.fail("FLUSHALL must never be used")


@pytest.fixture
def cursor_redis(monkeypatch):
    redis = CursorRedis()
    monkeypatch.setattr(shared_cache, "_client", lambda **kwargs: redis)
    monkeypatch.setattr(shared_cache, "_memory", {})
    monkeypatch.setattr(feature_flag_service, "_cache", {})
    return redis


@pytest.mark.parametrize(
    "kwargs, remaining",
    [
        ({"tenant_id": 1, "feature_key": "report"}, {"11:report", "1:report_builder", "2:other"}),
        ({"tenant_id": 1}, {"11:report", "2:other"}),
        ({"feature_key": "report"}, {"1:report_builder", "2:other"}),
        ({}, set()),
    ],
    ids=["exact", "tenant", "feature", "global"],
)
def test_invalidation_clears_exact_components_in_all_tiers(cursor_redis, kwargs, remaining):
    for key in ("1:report", "11:report", "1:report_builder", "2:other"):
        feature_flag_service._set_cached(key, True)
        feature_flag_service._cache[key] = {"val": True, "ts": time.time()}
        shared_cache._memory[f"ff:{key}"] = (True, 0)
    shared_cache.cache_set("session:1", "keep")
    shared_cache._memory["session:1"] = ("keep", 0)

    invalidate_cache(**kwargs)

    assert set(feature_flag_service._cache) == remaining
    assert set(cursor_redis.values) == {f"ff:{key}" for key in remaining} | {"session:1"}
    assert set(shared_cache._memory) == {f"ff:{key}" for key in remaining} | {"session:1"}
    if "feature_key" in kwargs and "tenant_id" not in kwargs:
        assert {call[1] for call in cursor_redis.scan_calls} == {"ff:*:report"}


@pytest.mark.parametrize("use_redis", [True, False])
def test_shared_delete_returns_presence_and_clears_fallback(cursor_redis, monkeypatch, use_redis):
    if not use_redis:
        monkeypatch.setattr(shared_cache, "_client", lambda **kwargs: None)
    shared_cache.cache_set("ff:1:report", False)
    shared_cache._memory["ff:1:report"] = (False, 0)

    assert shared_cache.cache_delete("ff:1:report") is True
    assert shared_cache.cache_get("ff:1:report") is None
    assert "ff:1:report" not in shared_cache._memory
    assert shared_cache.cache_delete("ff:1:report") is False


def test_prefix_delete_iterates_cursor_and_bounds_delete_batches(cursor_redis):
    cursor_redis.page_size = 250  # SCAN's COUNT is a hint, not a response size limit.
    for i in range(501):
        shared_cache.cache_set(f"ff:1:feature_{i}", True)
    shared_cache.cache_set("ff:11:report", True)
    shared_cache.cache_set("session:1", True)

    assert shared_cache.cache_delete_prefix("ff:1:") == 501
    assert set(cursor_redis.values) == {"ff:11:report", "session:1"}
    assert len(cursor_redis.scan_calls) > 1
    assert cursor_redis.scan_calls[0][0] == 0
    assert any(cursor for cursor, _, _ in cursor_redis.scan_calls)
    assert all(0 < len(keys) <= 100 for keys in cursor_redis.delete_calls)
    assert shared_cache.cache_delete_prefix("missing:") == 0


def test_prefix_delete_without_redis_preserves_literal_prefix(monkeypatch):
    monkeypatch.setattr(shared_cache, "_client", lambda **kwargs: None)
    monkeypatch.setattr(shared_cache, "_memory", {})
    shared_cache.cache_set("ff:1:report", True)
    shared_cache.cache_set("ff:11:report", True)
    shared_cache.cache_set("literal*:key", True)
    shared_cache.cache_set("literal-other:key", True)

    assert shared_cache.cache_delete_prefix("ff:1:") == 1
    assert shared_cache.cache_delete_prefix("literal*:") == 1
    assert set(shared_cache._memory) == {"ff:11:report", "literal-other:key"}


@pytest.mark.parametrize("prefix,neighbor", [
    ("literal*:", "literal-other:"),
    ("literal?:", "literalx:"),
    ("literal[:", "literal:"),
    ("literal]:", "literal:"),
    ("literal\\", "literal"),
    ("literal[ab]:", "literala:"),
])
def test_redis_prefix_delete_preserves_literal_glob_characters(cursor_redis, prefix, neighbor):
    for key in (f"{prefix}one", f"{prefix}two", f"{neighbor}one", "session:1"):
        shared_cache.cache_set(key, True)

    assert shared_cache.cache_delete_prefix(prefix) == 2

    assert set(cursor_redis.values) == {f"{neighbor}one", "session:1"}


@pytest.mark.parametrize("feature,neighbor", [
    ("report*", "report_builder"),
    ("report?", "reports"),
    ("report[", "report"),
    ("report]", "report"),
    ("report\\", "report"),
    ("report[ab]", "reporta"),
])
def test_redis_feature_invalidation_preserves_literal_glob_characters(cursor_redis, feature, neighbor):
    for key in (f"ff:1:{feature}", f"ff:11:{feature}", f"ff:1:{neighbor}", f"ff:1:{feature}_extra", f"session:{feature}"):
        shared_cache.cache_set(key, True)

    invalidate_cache(feature_key=feature)

    assert set(cursor_redis.values) == {f"ff:1:{neighbor}", f"ff:1:{feature}_extra", f"session:{feature}"}


@pytest.mark.parametrize("failure", [None, "delete", "scan"])
@pytest.mark.parametrize("kwargs", [{}, {"tenant_id": 1}, {"feature_key": "report"}, {"tenant_id": 1, "feature_key": "report"}])
def test_redis_failure_never_prevents_local_invalidation(cursor_redis, monkeypatch, failure, kwargs):
    feature_flag_service._cache["1:report"] = {"val": True, "ts": time.time()}
    feature_flag_service._cache["11:report_builder"] = {"val": True, "ts": time.time()}
    shared_cache._memory["ff:1:report"] = (True, 0)
    shared_cache.cache_set("ff:1:report", True)
    cursor_redis.failure = failure
    if failure is None:
        monkeypatch.setattr(shared_cache, "_client", lambda **kwargs: None)

    invalidate_cache(**kwargs)

    assert "1:report" not in feature_flag_service._cache
    assert "ff:1:report" not in shared_cache._memory
    assert ("11:report_builder" in feature_flag_service._cache) == bool(kwargs)


@pytest.mark.parametrize("operation", ["exact", "prefix"])
def test_optional_delete_error_logs_and_clears_memory(cursor_redis, caplog, operation):
    shared_cache._memory["ff:1:report"] = (True, 0)
    cursor_redis.failure = "delete" if operation == "exact" else "scan"
    if operation == "exact":
        assert shared_cache.cache_delete("ff:1:report") is True
    else:
        assert shared_cache.cache_delete_prefix("ff:") == 1
    assert not shared_cache._memory
    assert "Redis" in caplog.text


@pytest.mark.parametrize("operation", ["exact", "prefix"])
@pytest.mark.parametrize("available", [True, False])
def test_required_delete_raises_if_redis_unavailable(cursor_redis, monkeypatch, operation, available):
    cursor_redis.failure = "delete" if operation == "exact" else "scan"
    if not available:
        monkeypatch.setattr(shared_cache, "_client", lambda **kwargs: None)
    with pytest.raises(shared_cache.RedisUnavailable):
        if operation == "exact":
            shared_cache.cache_delete("ff:1:report", require_redis=True)
        else:
            shared_cache.cache_delete_prefix("ff:", require_redis=True)


def test_two_service_instances_observe_redis_invalidation(cursor_redis, db):
    spec = importlib.util.spec_from_file_location("feature_flag_instance_two", feature_flag_service.__file__)
    instance_two = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance_two)
    flag = FeatureFlag(key="report", display_name="Report", enabled_globally=True)
    db.add(flag)
    db.commit()
    assert is_feature_enabled(db, 1, "report") is True
    assert instance_two.is_feature_enabled(db, 1, "report") is True
    flag.enabled_globally = False
    db.commit()
    assert instance_two.is_feature_enabled(db, 1, "report") is True

    invalidate_cache(tenant_id=1, feature_key="report")

    assert instance_two.is_feature_enabled(db, 1, "report") is False
    assert is_feature_enabled(db, 1, "report") is False


@pytest.fixture(autouse=True)
def clear_feature_flag_cache():
    """Clear the feature flag cache before and after each test."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def seed_feature_flags(db):
    """Seed default feature flags for testing (matches migration 013)."""
    flags_data = [
        ("video_analysis", "Video Analysis"),
        ("batch_analysis", "Batch Analysis"),
        ("custom_weights", "Custom Scoring Weights"),
        ("api_access", "API Access"),
        ("export_excel", "Excel Export"),
        ("transcript_analysis", "Transcript Analysis"),
        ("email_generation", "Email Generation"),
    ]
    for key, display in flags_data:
        flag = FeatureFlag(key=key, display_name=display, enabled_globally=True)
        db.add(flag)
    db.commit()
    return flags_data


class TestIsFeatureEnabled:
    def test_is_feature_enabled_default_true(self, db):
        """Unknown flag returns True (safe default)."""
        assert is_feature_enabled(db, tenant_id=1, feature_key="nonexistent_flag") is True

    def test_is_feature_enabled_global_true(self, db, seed_feature_flags):
        """Existing flag enabled globally returns True."""
        assert is_feature_enabled(db, tenant_id=1, feature_key="video_analysis") is True

    def test_is_feature_enabled_global_false(self, db, seed_feature_flags):
        """Disabled global flag returns False."""
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        flag.enabled_globally = False
        db.commit()
        assert is_feature_enabled(db, tenant_id=1, feature_key="video_analysis") is False

    def test_tenant_override_true(self, db, seed_feature_flags):
        """Override enables feature even if globally disabled."""
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        flag.enabled_globally = False
        db.commit()

        tenant = Tenant(name="OverrideTest", slug="override-test")
        db.add(tenant)
        db.commit()

        override = TenantFeatureOverride(tenant_id=tenant.id, feature_flag_id=flag.id, enabled=True)
        db.add(override)
        db.commit()

        assert is_feature_enabled(db, tenant_id=tenant.id, feature_key="video_analysis") is True

    def test_tenant_override_false(self, db, seed_feature_flags):
        """Override disables feature even if globally enabled."""
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        # Globally enabled by default

        tenant = Tenant(name="OverrideTest2", slug="override-test-2")
        db.add(tenant)
        db.commit()

        override = TenantFeatureOverride(tenant_id=tenant.id, feature_flag_id=flag.id, enabled=False)
        db.add(override)
        db.commit()

        assert is_feature_enabled(db, tenant_id=tenant.id, feature_key="video_analysis") is False

    def test_cache_invalidation(self, db, seed_feature_flags):
        """Changing flag invalidates cache."""
        tenant_id = 1
        # First call caches the result
        result1 = is_feature_enabled(db, tenant_id=tenant_id, feature_key="video_analysis")
        assert result1 is True

        # Change in DB without invalidating cache
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        flag.enabled_globally = False
        db.commit()

        # Should still get cached True
        result2 = is_feature_enabled(db, tenant_id=tenant_id, feature_key="video_analysis")
        assert result2 is True

        # Invalidate cache
        invalidate_cache()

        # Now should get False
        result3 = is_feature_enabled(db, tenant_id=tenant_id, feature_key="video_analysis")
        assert result3 is False


class TestAdminEndpoints:
    def test_list_feature_flags_endpoint(self, platform_admin_client, seed_feature_flags):
        """Admin can list all flags."""
        resp = platform_admin_client.get("/api/admin/feature-flags")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 7
        keys = {f["key"] for f in data}
        assert "video_analysis" in keys
        assert "batch_analysis" in keys

    def test_toggle_feature_flag_endpoint(self, platform_admin_client, seed_feature_flags, db):
        """Admin can toggle flag."""
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        assert flag.enabled_globally is True

        resp = platform_admin_client.put(f"/api/admin/feature-flags/{flag.id}", json={"enabled_globally": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled_globally"] is False
        assert data["key"] == "video_analysis"

        db.refresh(flag)
        assert flag.enabled_globally is False

    def test_set_tenant_override_endpoint(self, platform_admin_client, seed_feature_flags, db):
        """Admin can set override for a tenant."""
        # Find the platform admin's tenant
        user = db.query(User).filter(User.email == "platformadmin@test.com").first()
        tenant_id = user.tenant_id

        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        # Globally enabled, override to disable
        resp = platform_admin_client.put(
            f"/api/admin/tenants/{tenant_id}/features/{flag.id}",
            json={"enabled": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["flag"] == "video_analysis"

        override = db.query(TenantFeatureOverride).filter(
            TenantFeatureOverride.tenant_id == tenant_id,
            TenantFeatureOverride.feature_flag_id == flag.id
        ).first()
        assert override is not None
        assert override.enabled is False

    def test_delete_tenant_override_endpoint(self, platform_admin_client, seed_feature_flags, db):
        """Admin can delete override."""
        user = db.query(User).filter(User.email == "platformadmin@test.com").first()
        tenant_id = user.tenant_id

        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()

        # First create an override
        override = TenantFeatureOverride(tenant_id=tenant_id, feature_flag_id=flag.id, enabled=False)
        db.add(override)
        db.commit()

        resp = platform_admin_client.delete(f"/api/admin/tenants/{tenant_id}/features/{flag.id}")
        assert resp.status_code == 200

        override = db.query(TenantFeatureOverride).filter(
            TenantFeatureOverride.tenant_id == tenant_id,
            TenantFeatureOverride.feature_flag_id == flag.id
        ).first()
        assert override is None


class TestPermissionChecks:
    def test_regular_user_cannot_access_flag_endpoints(self, auth_client, seed_feature_flags):
        """Regular user gets 403 on feature flag admin endpoints."""
        endpoints = [
            ("get", "/api/admin/feature-flags"),
            ("put", "/api/admin/feature-flags/1"),
            ("get", "/api/admin/tenants/1/features"),
            ("put", "/api/admin/tenants/1/features/1"),
            ("delete", "/api/admin/tenants/1/features/1"),
        ]
        for method, url in endpoints:
            if method == "get":
                resp = auth_client.get(url)
            elif method == "put":
                resp = auth_client.put(url, json={"enabled_globally": True})
            elif method == "delete":
                resp = auth_client.delete(url)
            assert resp.status_code == 403, f"Expected 403 for {method.upper()} {url}, got {resp.status_code}"


class TestRequireFeatureDependency:
    def test_require_feature_dependency(self, db, seed_feature_flags):
        """Test the require_feature() middleware works."""
        from app.backend.middleware.auth import require_feature

        tenant = Tenant(name="DepTest", slug="dep-test")
        db.add(tenant)
        db.commit()

        user = User(email="dep@test.com", hashed_password="x", tenant_id=tenant.id)
        db.add(user)
        db.commit()

        # Feature enabled globally
        dep = require_feature("video_analysis")
        result = dep(current_user=user, db=db)
        assert result == user

        # Disable globally
        flag = db.query(FeatureFlag).filter(FeatureFlag.key == "video_analysis").first()
        flag.enabled_globally = False
        db.commit()
        invalidate_cache()

        dep = require_feature("video_analysis")
        with pytest.raises(HTTPException) as exc_info:
            dep(current_user=user, db=db)
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        message = detail.get("detail", detail) if isinstance(detail, dict) else detail
        assert "not available" in message.lower() or "upgrade" in message.lower()
