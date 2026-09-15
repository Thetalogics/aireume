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
