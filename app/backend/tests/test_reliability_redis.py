"""Redis reliability for optional cache and required correctness paths.

Queue durability is PostgreSQL, not Redis. Token revocation is PostgreSQL.
"""
import asyncio

import pytest

from app.backend.tests.reliability_env import require_redis


@pytest.fixture
def redis_url(monkeypatch):
    url = require_redis()
    monkeypatch.setenv("REDIS_URL", url)
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = False
    yield url
    shared_cache._redis = None
    shared_cache._redis_failed = False


def test_redis_ping(redis_url):
    import redis

    client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_optional_cache_degrades_when_redis_is_down(redis_url, monkeypatch):
    from app.backend.services import shared_cache

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    shared_cache._redis = None
    shared_cache._redis_failed = False
    shared_cache.cache_set("rel:optional", {"ok": True}, ttl_seconds=30)
    assert shared_cache.cache_get("rel:optional") == {"ok": True}


def test_required_redis_operation_fails_closed(redis_url, monkeypatch):
    from app.backend.services import shared_cache

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    shared_cache._redis = None
    shared_cache._redis_failed = False
    with pytest.raises(shared_cache.RedisUnavailable):
        shared_cache.cache_set("rel:required", 1, require_redis=True)


def test_required_readiness_fails_when_redis_is_down(redis_url, monkeypatch):
    from app.backend.main import readiness_check

    monkeypatch.setenv("REDIS_REQUIRED", "1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    response = asyncio.run(readiness_check())
    assert response.status_code == 503


def test_optional_readiness_ignores_redis_outage(redis_url, monkeypatch):
    from app.backend.main import readiness_check

    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    response = asyncio.run(readiness_check())
    assert response["status"] == "ready"


def test_client_recovers_after_redis_returns(redis_url, monkeypatch):
    from app.backend.services import shared_cache

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    shared_cache._redis = None
    shared_cache._redis_failed = False
    assert shared_cache._client(retry=True) is None

    monkeypatch.setenv("REDIS_URL", redis_url)
    recovered = shared_cache._client(retry=True)
    assert recovered is not None
    assert recovered.ping() is True
