"""
Per-tenant rate limiting middleware using an in-memory token bucket.
"""
import os
import time
import threading
import logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from jose import JWTError, jwt

from app.backend.middleware.auth import SECRET_KEY, ALGORITHM
from app.backend.db import database
from app.backend.models.db_models import RateLimitConfig

logger = logging.getLogger(__name__)

_tenant_cache = {}  # user_id -> (tenant_id, cached_at)
_TENANT_CACHE_TTL = 300  # 5 minutes

_concurrent_llm = defaultdict(int)  # tenant_id -> current count
_concurrent_lock = threading.Lock()


class RateLimitMiddleware(BaseHTTPMiddleware):
    WHITELIST_PREFIXES = [
        "/health",
        "/ready",
        "/api/health",
        "/api/health/deep",
        "/metrics",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/logout",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
        "/docs",
        "/openapi.json",
    ]
    DEFAULT_RPM = 60
    CONFIG_TTL = 60  # seconds
    _instance = None  # set on init for test access

    def __init__(self, app, register_instance: bool = True):
        super().__init__(app)
        if register_instance and RateLimitMiddleware._instance is None:
            RateLimitMiddleware._instance = self
        self.buckets = {}  # {tenant_id: {"tokens": float, "last_refill": float}}
        self.lock = threading.Lock()
        self.config_cache = {}  # {tenant_id: {"rpm": int, "cached_at": float}}

    def _is_whitelisted(self, path: str) -> bool:
        if path == "/":
            return True
        for prefix in self.WHITELIST_PREFIXES:
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                return True
        return False

    def _extract_tenant_id(self, request: Request) -> tuple[int | None, dict]:
        """Extract tenant_id and JWT payload from request."""
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.cookies.get("access_token")

        if not token:
            return None, {}

        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if user_id is None:
                return None, payload
        except JWTError:
            return None, {}

        # Try JWT payload first
        tenant_id = payload.get("tenant_id")
        if tenant_id:
            return int(tenant_id), payload

        # Try cache
        now = time.time()
        user_id_int = int(user_id)
        if user_id_int in _tenant_cache:
            cached_tenant_id, cached_at = _tenant_cache[user_id_int]
            if now - cached_at < _TENANT_CACHE_TTL:
                return cached_tenant_id, payload

        # Fall back to DB
        db = database.SessionLocal()
        try:
            from app.backend.models.db_models import User
            user = db.query(User).filter(User.id == user_id_int).first()
            if user:
                _tenant_cache[user_id_int] = (user.tenant_id, now)
                return user.tenant_id, payload
        except Exception as e:
            logger.warning(f"Rate limit tenant extraction failed: {e}")
        finally:
            db.close()
        return None, payload

    def _get_rate_limit_config(self, tenant_id: int) -> dict:
        """Fetch or cache the full RateLimitConfig for a tenant."""
        now = time.time()
        cached = self.config_cache.get(tenant_id)
        if cached and (now - cached["cached_at"]) < self.CONFIG_TTL:
            return cached

        config_dict = {
            "rpm": self.DEFAULT_RPM,
            "llm_concurrent_max": 2,
        }
        db = database.SessionLocal()
        try:
            config = db.query(RateLimitConfig).filter(
                RateLimitConfig.tenant_id == tenant_id
            ).first()
            if config:
                config_dict["rpm"] = config.requests_per_minute
                config_dict["llm_concurrent_max"] = config.llm_concurrent_max
        except Exception as e:
            logger.warning(f"Rate limit config lookup failed: {e}")
        finally:
            db.close()

        self.config_cache[tenant_id] = {**config_dict, "cached_at": now}
        return config_dict

    def _get_rate_limit(self, tenant_id: int) -> int:
        return self._get_rate_limit_config(tenant_id)["rpm"]

    def _consume_token(self, tenant_id: int, rpm: int, cost: float = 1.0) -> tuple[bool, float]:
        production = os.getenv("ENVIRONMENT", "").lower() == "production"
        try:
            from app.backend.services.shared_cache import cache_incr, _client
            client = _client()
            if client is not None:
                window = int(time.time() // 60)
                count = cache_incr(f"rpm:{tenant_id}:{window}", ttl_seconds=120)
                if count > rpm:
                    return False, 60.0
                return True, 0.0
            if production:
                return False, -1
        except Exception:
            if production:
                return False, -1
        now = time.time()
        with self.lock:
            bucket = self.buckets.get(tenant_id)
            if bucket is None:
                bucket = {"tokens": float(rpm), "last_refill": now}
                self.buckets[tenant_id] = bucket

            # Refill tokens based on elapsed time
            elapsed = now - bucket["last_refill"]
            refill_rate = rpm / 60.0  # tokens per second
            bucket["tokens"] = min(rpm, bucket["tokens"] + elapsed * refill_rate)
            bucket["last_refill"] = now

            if bucket["tokens"] >= cost:
                bucket["tokens"] -= cost
                return True, 0.0

            # Calculate seconds until enough tokens are available
            deficit = cost - bucket["tokens"]
            retry_after = deficit / refill_rate if refill_rate > 0 else 60.0
            return False, retry_after

    def _is_narrative_poll_path(self, path: str, method: str) -> bool:
        """Read-only narrative status polling — lighter RPM cost."""
        return (
            method == "GET"
            and path.startswith("/api/analysis/")
            and path.endswith("/narrative")
        )

    LLM_PATHS = {
        "/api/analyze",
        "/api/analyze/stream",
        "/api/analyze/batch",
        "/api/analyze/batch-chunked",
        "/api/analyze/batch-stream",
        "/api/analyze/suggest-weights",
        "/api/transcript/analyze",
    }

    def _is_llm_path(self, path: str) -> bool:
        """Check if the path is an LLM-invoking endpoint."""
        for prefix in self.LLM_PATHS:
            if path.startswith(prefix):
                return True
        # Dynamic path: /api/analyze/{id}/rescore
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "analyze" and parts[-1] == "rescore":
            return True
        return False

    def _check_llm_concurrency(self, tenant_id: int, config: dict) -> bool:
        """Check if tenant has exceeded LLM concurrent limit."""
        max_concurrent = config.get("llm_concurrent_max", 2)
        with _concurrent_lock:
            if _concurrent_llm[tenant_id] >= max_concurrent:
                return False
            _concurrent_llm[tenant_id] += 1
            return True

    def _release_llm_concurrency(self, tenant_id: int):
        """Release one LLM concurrent slot."""
        with _concurrent_lock:
            _concurrent_llm[tenant_id] = max(0, _concurrent_llm[tenant_id] - 1)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")
        enforce = os.getenv("RATE_LIMIT_ENFORCE", "").lower() in ("1", "true", "yes")
        if testing and not enforce:
            return await call_next(request)

        if self._is_whitelisted(path):
            return await call_next(request)

        tenant_id, token_payload = self._extract_tenant_id(request)
        if tenant_id is None:
            return await call_next(request)

        config = self._get_rate_limit_config(tenant_id)
        rpm = config["rpm"]
        token_cost = 0.25 if self._is_narrative_poll_path(path, request.method) else 1.0
        allowed, retry_after = self._consume_token(tenant_id, rpm, cost=token_cost)

        if retry_after == -1:
            return JSONResponse(
                status_code=503,
                content={"detail": "Service temporarily unavailable. Try again later."},
            )

        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={
                    "Retry-After": str(int(retry_after) + 1),
                    "X-RateLimit-Limit": str(rpm),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + 60),
                },
            )
            return response

        # Check LLM concurrency for LLM endpoints
        is_llm = self._is_llm_path(path)
        acquired_llm = False
        if is_llm:
            if not self._check_llm_concurrency(tenant_id, config):
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Concurrent LLM limit exceeded. Try again later."},
                    headers={
                        "X-RateLimit-Limit": str(rpm),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + 60),
                    },
                )
                return response
            acquired_llm = True

        try:
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rpm)
            response.headers["X-RateLimit-Remaining"] = str(max(0, int(self.buckets.get(tenant_id, {}).get("tokens", 0))))
            response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)
            return response
        finally:
            if acquired_llm:
                self._release_llm_concurrency(tenant_id)
