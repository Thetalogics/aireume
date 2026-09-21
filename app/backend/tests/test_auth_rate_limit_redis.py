from app.backend.routes.auth import AuthRateLimitUnavailable, InMemoryRateLimiter
from app.backend.tests.reliability_env import require_redis


def test_a1_independent_instances_share_limit(monkeypatch):
    require_redis()
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = False
    a = InMemoryRateLimiter()
    b = InMemoryRateLimiter()
    key = "login:test-a1"
    limited_a, _ = a.is_rate_limited(key, 2, 60)
    limited_b, _ = b.is_rate_limited(key, 2, 60)
    limited_c, _ = a.is_rate_limited(key, 2, 60)
    assert limited_a is False
    assert limited_b is False
    assert limited_c is True


def test_a2_production_redis_outage_fail_closed(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = False
    limiter = InMemoryRateLimiter()
    try:
        limiter.is_rate_limited("login:x", 5, 60)
        assert False, "expected fail-closed"
    except AuthRateLimitUnavailable:
        pass


def test_a3_development_can_fallback(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = True
    limiter = InMemoryRateLimiter()
    limited, _ = limiter.is_rate_limited("login:dev", 5, 60)
    assert limited is False
