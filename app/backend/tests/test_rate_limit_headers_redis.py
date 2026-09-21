from app.backend.middleware.rate_limit import RateLimitMiddleware
from app.backend.tests.reliability_env import require_redis


def test_t19_consume_returns_authoritative_remaining(monkeypatch):
    require_redis()
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    mw = RateLimitMiddleware(app=None, register_instance=False)
    allowed, retry = mw._consume_token(88001, rpm=10, cost=1.0)
    assert allowed is True
    assert retry == 0.0
    snap = mw._last_consume[88001]
    assert snap["remaining"] == 9
    assert snap["limit"] == 10
