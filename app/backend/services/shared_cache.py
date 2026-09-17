"""Process-shared cache. Uses Redis when REDIS_URL is set, else process memory."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_memory: dict[str, tuple[Any, float]] = {}
_lock = threading.Lock()
_redis = None
_redis_failed = False


class RedisUnavailable(RuntimeError):
    """Raised when a caller required Redis and it is not healthy."""


def redis_is_healthy() -> bool:
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return False
    try:
        import redis
        client = redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        client.ping()
        return True
    except Exception:
        return False


def _client(*, retry: bool = False):
    global _redis, _redis_failed
    if retry:
        _redis_failed = False
        _redis = None
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
        _redis = redis.Redis.from_url(url, decode_responses=True)
        _redis.ping()
        return _redis
    except Exception as exc:
        logger.warning("Redis unavailable (%s); falling back to in-memory cache", exc)
        _redis_failed = True
        return None


def _redis_required():
    client = _client(retry=True)
    if client is None:
        raise RedisUnavailable("Redis unavailable")
    return client


def cache_get(key: str, require_redis: bool = False) -> Any:
    r = _redis_required() if require_redis else _client()
    if r is not None:
        raw = r.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    now = time.time()
    with _lock:
        entry = _memory.get(key)
        if not entry:
            return None
        val, exp = entry
        if exp and exp < now:
            _memory.pop(key, None)
            return None
        return val


def cache_set(key: str, value: Any, ttl_seconds: int = 60, require_redis: bool = False) -> None:
    r = _redis_required() if require_redis else _client()
    payload = json.dumps(value)
    if r is not None:
        r.setex(key, ttl_seconds, payload)
        return
    with _lock:
        _memory[key] = (value, time.time() + ttl_seconds)


def cache_incr(key: str, ttl_seconds: int = 60) -> int:
    r = _client()
    if r is not None:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        count, _ = pipe.execute()
        return int(count)
    with _lock:
        val, exp = _memory.get(key, (0, time.time() + ttl_seconds))
        if exp < time.time():
            val = 0
        val = int(val) + 1
        _memory[key] = (val, time.time() + ttl_seconds)
        return val


def cache_delete(key: str, require_redis: bool = False) -> None:
    r = _redis_required() if require_redis else _client()
    if r is not None:
        r.delete(key)
        return
    with _lock:
        _memory.pop(key, None)


_GETDEL_LUA = """
local v = redis.call('GET', KEYS[1])
if not v then
  return false
end
redis.call('DEL', KEYS[1])
return v
"""


def cache_getdel(key: str, require_redis: bool = False) -> Any:
    """Atomically read and delete a cache key (GETDEL / Lua fallback)."""
    r = _redis_required() if require_redis else _client()
    if r is not None:
        raw = None
        try:
            raw = r.execute_command("GETDEL", key)
        except Exception:
            raw = r.eval(_GETDEL_LUA, 1, key)
            if raw is False:
                raw = None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
    now = time.time()
    with _lock:
        entry = _memory.pop(key, None)
        if not entry:
            return None
        val, exp = entry
        if exp and exp < now:
            return None
        return val


_CONSUME_IF_TENANT_LUA = """
local v = redis.call('GET', KEYS[1])
if not v then
  return nil
end
if v == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return v
end
return 'TENANT_MISMATCH'
"""


def cache_consume_if_tenant(key: str, tenant_id: int, require_redis: bool = False) -> Any:
    """Atomically consume a JSON value when tenant_id matches. Mismatch leaves the key."""
    expected = json.dumps({"tenant_id": int(tenant_id)})
    r = _redis_required() if require_redis else _client()
    if r is not None:
        raw = r.eval(_CONSUME_IF_TENANT_LUA, 1, key, expected)
        if raw is None:
            return None
        if raw == "TENANT_MISMATCH":
            return False
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
    now = time.time()
    with _lock:
        entry = _memory.get(key)
        if not entry:
            return None
        val, exp = entry
        if exp and exp < now:
            _memory.pop(key, None)
            return None
        if not isinstance(val, dict) or int(val.get("tenant_id") or 0) != int(tenant_id):
            return False
        _memory.pop(key, None)
        return val


_ACQUIRE_SLOT_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local max_slots = tonumber(ARGV[1])
if current >= max_slots then
  return 0
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


def cache_try_acquire_slot(key: str, max_slots: int, ttl_seconds: int = 120) -> bool:
    """Distributed concurrency slot. Shared across processes when Redis is configured."""
    max_slots = max(1, int(max_slots))
    r = _client()
    if r is not None:
        ok = r.eval(_ACQUIRE_SLOT_LUA, 1, key, max_slots, int(ttl_seconds))
        return int(ok or 0) == 1
    with _lock:
        val, exp = _memory.get(key, (0, time.time() + ttl_seconds))
        if exp < time.time():
            val = 0
        if int(val) >= max_slots:
            return False
        _memory[key] = (int(val) + 1, time.time() + ttl_seconds)
        return True


def cache_release_slot(key: str) -> None:
    r = _client()
    if r is not None:
        r.eval(
            "local v = tonumber(redis.call('GET', KEYS[1]) or '0'); if v > 0 then redis.call('DECR', KEYS[1]) end",
            1,
            key,
        )
        return
    with _lock:
        val, exp = _memory.get(key, (0, 0))
        nxt = max(0, int(val) - 1)
        if nxt == 0:
            _memory.pop(key, None)
        else:
            _memory[key] = (nxt, exp)
