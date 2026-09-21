"""Cross-process LLM concurrency caps (AUD-045) plus per-tenant Redis permits."""
from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager

from app.backend.services.shared_cache import cache_release_slot, cache_try_acquire_slot


class LLMConcurrencySaturated(RuntimeError):
    pass


class LlmConcurrencyUnavailable(Exception):
    pass


def _max_slots(provider: str) -> int:
    env = os.getenv(f"{provider.upper()}_MAX_CONCURRENT")
    if env:
        return max(1, int(env))
    return 2 if provider == "gemini" else 4


@contextmanager
def llm_slot(provider: str, tenant_id: int | None = None, ttl_seconds: int = 180):
    from app.backend.services.metrics import LLM_SATURATION, LLM_SATURATION_TOTAL

    key = f"llm:slots:{provider}:{tenant_id or 'global'}"
    if not cache_try_acquire_slot(key, _max_slots(provider), ttl_seconds=ttl_seconds):
        LLM_SATURATION_TOTAL.labels(provider=provider).inc()
        LLM_SATURATION.labels(provider=provider).set(1)
        raise LLMConcurrencySaturated(f"{provider} concurrency saturated")
    LLM_SATURATION.labels(provider=provider).set(0)
    try:
        yield
    finally:
        cache_release_slot(key)


ACQUIRE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 0
end
redis.call('ZADD', key, now + ttl, token)
redis.call('EXPIRE', key, ttl)
return 1
"""

RELEASE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
return redis.call('ZREM', key, token)
"""


def _production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "production"


def acquire_llm_permit(tenant_id: int, limit: int, ttl_seconds: int = 120) -> str | None:
    token = uuid.uuid4().hex
    try:
        from app.backend.services.shared_cache import _client

        client = _client()
        if client is None:
            if _production() or os.getenv("REDIS_REQUIRED", "").lower() in ("1", "true", "yes"):
                raise LlmConcurrencyUnavailable("redis unavailable")
            return token
        ok = client.eval(ACQUIRE_LUA, 1, f"llmconc:{tenant_id}", token, int(limit), int(ttl_seconds), int(time.time()))
        if int(ok) != 1:
            return None
        return token
    except LlmConcurrencyUnavailable:
        raise
    except Exception as exc:
        if _production() or os.getenv("REDIS_REQUIRED", "").lower() in ("1", "true", "yes"):
            raise LlmConcurrencyUnavailable(str(exc)) from exc
        return token


def release_llm_permit(tenant_id: int, token: str | None) -> None:
    if not token:
        return
    try:
        from app.backend.services.shared_cache import _client

        client = _client()
        if client is None:
            return
        client.eval(RELEASE_LUA, 1, f"llmconc:{tenant_id}", token)
    except Exception:
        return
