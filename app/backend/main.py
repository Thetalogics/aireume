from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
import json
import re
import httpx
import uuid
import contextvars
import time
import threading
import shutil
import asyncio
from datetime import datetime, timezone, timedelta
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] [%(funcName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("aria")

# Redact API keys and bearer tokens from third-party HTTP client logs (httpx, Gemini SDK)
_SECRET_LOG_PATTERNS = (
    (re.compile(r"(key=)[^&\s\"']+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(google_api_key[=:]\s*)\S+", re.IGNORECASE), r"\1***REDACTED***"),
)


class _RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern, replacement in _SECRET_LOG_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


for _secret_logger_name in ("httpx", "httpcore", "google_genai", "google.auth"):
    logging.getLogger(_secret_logger_name).addFilter(_RedactSecretsFilter())

# Production JSON logging
if os.getenv("ENVIRONMENT") == "production":
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_obj = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "function": record.funcName,
            }
            if record.exc_info:
                log_obj["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_obj)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.root.handlers = [handler]

# Request correlation ID context variable
request_id_var = contextvars.ContextVar('request_id', default='-')


# ─── Startup Environment Validation ─────────────────────────────────────────────
def _validate_environment() -> None:
    """Validate critical environment variables at startup. Fail fast in production."""
    env = os.getenv("ENVIRONMENT", "development")
    missing_vars = []
    warnings = []

    # Required in production
    required_production = ["JWT_SECRET_KEY", "DATABASE_URL", "POSTGRES_PASSWORD", "REDIS_URL", "INTERNAL_SERVICE_SECRET"]
    for var in required_production:
        value = os.getenv(var)
        if not value or value.startswith("change-me") or value.startswith("change-this"):
            missing_vars.append(var)

    # Warn for missing but not critical
    if not os.getenv("OLLAMA_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        warnings.append("OLLAMA_API_KEY not set - LLM features will be limited")
    elif os.getenv("GEMINI_API_KEY"):
        logger.info("STARTUP: GEMINI_API_KEY set — resume analysis will use Google Gemini")
    if not os.getenv("CORS_ORIGINS"):
        warnings.append("CORS_ORIGINS not set - using default localhost origins")

    # LiveKit dev credentials must never be used in production.
    if env == "production":
        if os.getenv("LIVEKIT_API_KEY") == "devkey" or os.getenv("LIVEKIT_API_SECRET") == "devsecret":
            missing_vars.append("LIVEKIT_API_KEY/LIVEKIT_API_SECRET (dev defaults not allowed in production)")
        if not os.getenv("S3_ENDPOINT") or not os.getenv("S3_BUCKET"):
            missing_vars.append("S3_ENDPOINT/S3_BUCKET (object storage required in production)")
        if not os.getenv("SENTRY_DSN"):
            warnings.append("SENTRY_DSN not set — error reporting will be local-only")
        # CORS_ORIGINS wildcard is a security risk in production.
        cors = os.getenv("CORS_ORIGINS", "")
        if cors.strip() == "*" or "*" in [o.strip() for o in cors.split(",")]:
            missing_vars.append("CORS_ORIGINS (wildcard '*' not allowed in production)")

    if env == "production" and missing_vars:
        logger.critical(
            f"FATAL: Missing or insecure environment variables in production: {', '.join(missing_vars)}. Refusing to start."
        )
        import sys
        sys.exit(1)

    for warning in warnings:
        logger.warning(f"STARTUP: {warning}")

    if env == "production":
        logger.info("STARTUP: Running in PRODUCTION mode with strict validation")


_validate_environment()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that generates/propagates a correlation ID per request."""
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        request_id_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

from app.backend.db.database import engine, Base, SessionLocal
from app.backend.middleware.csrf import CSRFMiddleware
from app.backend.middleware.rate_limit import RateLimitMiddleware
from app.backend.middleware.auth import require_feature
from app.backend.middleware.idempotency import IdempotencyMiddleware
from app.backend.services.observability import init_observability

init_observability()
from app.backend.routes import analyze
from app.backend.routes import auth
from app.backend.routes import compare
from app.backend.routes import export
from app.backend.routes import templates
from app.backend.routes import candidates
from app.backend.routes import email_gen
from app.backend.routes import jd_url
from app.backend.routes import team
from app.backend.routes import training
from app.backend.routes import video
from app.backend.routes import transcript
from app.backend.routes import subscription
from app.backend.routes import queue_api
from app.backend.routes import admin
from app.backend.routes import upload
from app.backend.routes import billing
from app.backend.routes import interview_kit, interview_kit_regeneration
from app.backend.routes import dashboard
from app.backend.routes import onboarding
from app.backend.routes import users
from app.backend.routes import sso
from app.backend.routes import webhook_docs
from app.backend.routes import voice
from app.backend.routes import recruiter
from app.backend.routes import interviews
from app.backend.routes import projects
from app.backend.routes import requisitions
from app.backend.routes import ats
from app.backend.routes import tenant_audit
from app.backend.routes import share_links
from app.backend.routes import oauth
from app.backend.routes import crm
from app.backend.routes import branding
from app.backend.routes import nps
from app.backend.routes import client_errors
from app.backend.services import llm_service

log = logging.getLogger("aria.startup")

VERSION = "2.0.0"
W = 54  # banner width (inner)


def _banner_line(text: str = "", fill: str = " ") -> str:
    return f"║ {text:{fill}<{W}} ║"


def _check(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


def _print_startup_banner(checks: dict) -> None:
    """Print a one-glance startup status table to stdout (captured by docker logs)."""
    if not checks:
        logger.info("\n[ARIA startup] No dependency checks ran.")
        return

    overall_ok = all(v["ok"] for v in checks.values())
    status_label = "READY" if overall_ok else "DEGRADED — check items marked FAIL"

    lines = [
        f"╔{'═' * (W + 2)}╗",
        _banner_line(f"  ARIA — Resume Intelligence v{VERSION}  ·  ThetaLogics"),
        f"╠{'═' * (W + 2)}╣",
    ]
    for name, info in checks.items():
        icon  = "✓" if info["ok"] else "✗"
        label = info["label"]
        # Keep row within W so box-drawing does not overflow terminals
        note  = (info.get("note", "") or "")[:28]
        row   = f"{icon}  {label:<18} {note}"
        lines.append(_banner_line(row))

    lines += [
        f"╠{'═' * (W + 2)}╣",
        _banner_line(f"  Status : {status_label}"),
        f"╚{'═' * (W + 2)}╝",
    ]
    logger.info("\n" + "\n".join(lines))


async def _startup_checks() -> dict:
    """Run all dependency checks and return structured results."""
    from sqlalchemy import text

    results = {}

    # ── 1. Database ───────────────────────────────────────────────────────────
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_name = os.getenv("DATABASE_URL", "").split("/")[-1] or "connected"
            results["database"] = {"ok": True, "label": "Database",
                                   "note": f"connected  ({db_name})"}
        finally:
            db.close()
    except Exception as e:
        results["database"] = {"ok": False, "label": "Database",
                               "note": str(e)[:30]}

    # ── 2. Skills registry ────────────────────────────────────────────────────
    try:
        from app.backend.services.skill_matcher import skills_registry
        db = SessionLocal()
        try:
            skills_registry.seed_if_empty(db)
            skills_registry.load(db)
        finally:
            db.close()
        count = len(skills_registry._skills)
        results["skills"] = {"ok": count > 0, "label": "Skills registry",
                             "note": f"{count} skills loaded"}
    except Exception as e:
        results["skills"] = {"ok": False, "label": "Skills registry",
                             "note": str(e)[:30]}

    # ── 3–5. Analysis LLM (Gemini or local Ollama) ───────────────────────────
    from app.backend.services.llm_service import use_gemini_for_analysis, get_gemini_model

    if use_gemini_for_analysis():
        gemini_model = get_gemini_model()
        results["analysis_llm"] = {
            "ok": True,
            "label": "Analysis LLM",
            "note": f"Google Gemini ({gemini_model})",
        }
        results["jd_cache"] = {
            "ok": True,
            "label": "JD profile cache",
            "note": "Postgres jd_cache table",
        }
    else:
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        from app.backend.services.llm_service import get_ollama_model

        narrative_model = get_ollama_model()
        pulled_models = []
        hot_models = []
        ollama_ok = False

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{ollama_url}/api/tags")
                r.raise_for_status()
                ollama_ok = True
                pulled_models = [m["name"] for m in r.json().get("models", [])]
                try:
                    ps = await client.get(f"{ollama_url}/api/ps")
                    if ps.status_code == 200:
                        hot_models = [m["name"] for m in ps.json().get("models", [])]
                except Exception:
                    pass
            results["ollama"] = {"ok": True, "label": "Ollama",
                                 "note": f"reachable  ({ollama_url})"}
        except Exception as e:
            results["ollama"] = {"ok": False, "label": "Ollama",
                                 "note": str(e)[:35]}

        model_pulled = ollama_ok and any(narrative_model in m for m in pulled_models)
        results["model_pulled"] = {
            "ok": model_pulled,
            "label": "Model (pulled)",
            "note": f"{narrative_model}" if model_pulled else f"{narrative_model}  NOT FOUND",
        }

        model_hot = ollama_ok and any(narrative_model in m for m in hot_models)
        results["model_hot"] = {
            "ok": model_hot,
            "label": "Model (hot/RAM)",
            "note": "loaded" if model_hot else "cold — will load on first request",
        }

    # ── 6. Environment ────────────────────────────────────────────────────────
    env = os.getenv("ENVIRONMENT", "development")
    results["environment"] = {"ok": True, "label": "Environment", "note": env}

    return results


async def _cleanup_revoked_tokens():
    """Background task to delete expired revoked tokens every 24 hours."""
    while True:
        await asyncio.sleep(86400)  # 24 hours
        try:
            db = SessionLocal()
            from app.backend.models.db_models import RevokedToken
            deleted = db.query(RevokedToken).filter(
                RevokedToken.expires_at < datetime.now(timezone.utc)
            ).delete()
            db.commit()
            db.close()
            if deleted > 0:
                log.info("Cleaned up %d expired revoked tokens", deleted)
        except Exception as e:
            log.exception("Failed to clean up revoked tokens: %s", e)


async def _cleanup_jd_cache():
    """Delete JdCache entries older than 30 days."""
    while True:
        await asyncio.sleep(86400)  # 24 hours
        try:
            db = SessionLocal()
            from app.backend.models.db_models import JdCache
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            deleted = db.query(JdCache).filter(JdCache.created_at < cutoff).delete()
            db.commit()
            db.close()
            if deleted > 0:
                log.info("Cleaned up %d expired JD cache entries", deleted)
        except Exception as e:
            log.exception("Failed to clean up JD cache: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables, run dependency checks, print banner, start sentinel.

    Never raises: if startup checks crash, we still bind the server so nginx
    does not return 502 Bad Gateway (upstream connection refused).
    """
    # Schema is managed exclusively by Alembic (see docker-entrypoint.sh:
    # `alembic upgrade head`). We only auto-create tables in the test/dev-SQLite
    # context where migrations are not run, to avoid production schema drift.
    import os as _os
    if _os.getenv("TESTING", "").lower() in ("1", "true") or _os.getenv("AUTO_CREATE_TABLES", "").lower() in ("1", "true"):
        try:
            Base.metadata.create_all(bind=engine)
        except Exception as e:
            log.exception("create_all failed: %s", e)

    try:
        checks = await _startup_checks()
        _print_startup_banner(checks)
    except Exception as e:
        logger.exception("Startup checks failed — API will still start: %s", e)

    # Start Ollama health sentinel only when local/cloud Ollama is used for analysis
    try:
        if llm_service.should_run_ollama_sentinel():
            llm_service._sentinel = llm_service.OllamaHealthSentinel()
            await llm_service._sentinel.start()
        else:
            log.info("Ollama health sentinel skipped — analysis uses Google Gemini")
    except Exception as e:
        log.exception("Failed to start Ollama health sentinel: %s", e)

    # Start revoked tokens cleanup task
    cleanup_task = asyncio.create_task(_cleanup_revoked_tokens())

    # Start JD cache cleanup task
    jd_cache_cleanup_task = asyncio.create_task(_cleanup_jd_cache())

    # Start background workers only in the dedicated worker process (or single-process dev).
    run_workers = os.getenv("RUN_BACKGROUND_WORKERS", "").strip().lower() in ("1", "true", "yes")
    if not run_workers and os.getenv("ENVIRONMENT", "development") != "production":
        # Default on for local/dev/test so existing workflows keep working.
        run_workers = True
    if os.getenv("TESTING", "").lower() in ("1", "true"):
        run_workers = False

    if run_workers:
        # Start queue worker
        queue_worker_task = None
        try:
            from app.backend.services.queue_manager import start_queue_worker
            await start_queue_worker()
            log.info("Queue worker started successfully")
        except Exception as e:
            log.exception("Failed to start queue worker: %s", e)

        # Start O*NET background sync (daemon thread — non-blocking, graceful on failure)
        try:
            def _onet_bg_sync():
                try:
                    from app.backend.services.onet.onet_sync import sync_if_stale
                    sync_if_stale(max_age_days=30)
                except Exception as exc:
                    log.warning("O*NET startup sync skipped: %s", exc)

            onet_thread = threading.Thread(
                target=_onet_bg_sync, daemon=True, name="onet-sync"
            )
            onet_thread.start()
        except Exception as e:
            log.exception("Failed to start O*NET background sync thread: %s", e)

        # Start background scheduler (dunning retries, etc.)
        try:
            from app.backend.services.scheduler import start_scheduler
            start_scheduler()
        except Exception as e:
            log.exception("Failed to start background scheduler: %s", e)

        # Start voice call scheduler (screening call scheduling + retries)
        try:
            from app.backend.services.voice_call_scheduler import start_voice_scheduler
            start_voice_scheduler()
        except Exception as e:
            log.exception("Failed to start voice call scheduler: %s", e)
    else:
        log.info("Inline background workers disabled (RUN_BACKGROUND_WORKERS not set)")

    yield

    # Shutdown: stop the sentinel gracefully
    try:
        if llm_service._sentinel:
            await llm_service._sentinel.stop()
    except Exception as e:
        log.exception("Error stopping Ollama health sentinel: %s", e)

    # Stop queue worker
    try:
        from app.backend.services.queue_manager import stop_queue_worker
        await stop_queue_worker()
        log.info("Queue worker stopped")
    except Exception as e:
        log.exception("Error stopping queue worker: %s", e)

    # Stop background scheduler
    try:
        from app.backend.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as e:
        log.exception("Error stopping background scheduler: %s", e)

    # Stop voice call scheduler
    try:
        from app.backend.services.voice_call_scheduler import stop_voice_scheduler
        stop_voice_scheduler()
    except Exception as e:
        log.exception("Error stopping voice call scheduler: %s", e)

    # Cancel the cleanup tasks
    cleanup_task.cancel()
    jd_cache_cleanup_task.cancel()


# In production, disable interactive API docs and the OpenAPI schema endpoint
# to reduce the reconnaissance attack surface.
_is_production = os.getenv("ENVIRONMENT", "development") == "production"
app = FastAPI(
    title="ARIA — AI Resume Intelligence by ThetaLogics",
    description="Multi-tenant AI resume screening platform",
    version=VERSION,
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# ─── Prometheus Metrics ───────────────────────────────────────────────────────

instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
)
# include_in_schema=False keeps /metrics out of the OpenAPI spec. Network-level
# access is additionally restricted to internal/monitoring IPs by nginx.
instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ─── CORS ─────────────────────────────────────────────────────────────────────

# Load CORS origins from environment variable (comma-separated).
# In production, CORS_ORIGINS should be set explicitly to only real frontend
# domains (e.g. "https://airesume.thetalogics.com"). The default below is
# convenience for local development only — it includes localhost ports and
# the staging domain so developers can spin up the API without extra config.
cors_origins_str = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000,https://airesume-staging.thetalogics.com"
)
origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]

# Security: fail if any localhost origin is configured in production.
# Production deployments must only allow real frontend domains.
if os.getenv("ENVIRONMENT") == "production":
    localhost_origins = [o for o in origins if "localhost" in o or "127.0.0.1" in o]
    if localhost_origins:
        log.critical(
            "CORS_ORIGINS contains localhost/127.0.0.1 in production: %s — "
            "this is a security risk. Set CORS_ORIGINS to only production domains.",
            localhost_origins,
        )
        import sys
        sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Request-ID",
        "X-Idempotency-Key",
        "X-Impersonation-Token",
        "X-ARIA-Token-Grant",
        "X-Internal-Service-Secret",
        "X-Metrics-Token",
        "X-Signature",
    ],
)

class _OpsAuthMiddleware(BaseHTTPMiddleware):
    """Lock down diagnostics in production. /health stays public."""
    PROTECTED = {"/metrics", "/api/health/deep", "/api/llm-status"}

    async def dispatch(self, request, call_next):
        if request.url.path in self.PROTECTED:
            testing = os.getenv("TESTING", "").lower() in ("1", "true")
            if not testing and os.getenv("ENVIRONMENT") == "production":
                expected = os.getenv("METRICS_TOKEN") or os.getenv("INTERNAL_SERVICE_SECRET") or ""
                provided = (
                    request.headers.get("X-Metrics-Token")
                    or request.headers.get("X-Internal-Service-Secret")
                    or ""
                )
                if not expected or provided != expected:
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)


class _APIV1RewriteMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/api/v1/"):
            request.scope["path"] = "/api/" + path[len("/api/v1/"):]
        elif path == "/api/v1":
            request.scope["path"] = "/api"
        return await call_next(request)


app.add_middleware(_OpsAuthMiddleware)
app.add_middleware(_APIV1RewriteMiddleware)
app.add_middleware(IdempotencyMiddleware)

# ─── Rate Limiting ────────────────────────────────────────────────────────────

app.add_middleware(RateLimitMiddleware)

# ─── Request Correlation ID ───────────────────────────────────────────────────

app.add_middleware(RequestIdMiddleware)

# ─── CSRF Protection ─────────────────────────────────────────────────────────

app.add_middleware(CSRFMiddleware)

# ─── Routers ──────────────────────────────────────────────────────────────────

def _plan_gate(feature_key: str):
    return [Depends(require_feature(feature_key))]

app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(analyze.router)
app.include_router(compare.router, dependencies=_plan_gate("compare"))
app.include_router(export.router, dependencies=_plan_gate("export_excel"))
app.include_router(templates.router)
app.include_router(candidates.router)
app.include_router(candidates.jd_router)
app.include_router(email_gen.router, dependencies=_plan_gate("email_generation"))
app.include_router(jd_url.router)
app.include_router(team.router)
app.include_router(training.router)
app.include_router(video.router, dependencies=_plan_gate("video_analysis"))
app.include_router(transcript.router, dependencies=_plan_gate("transcript_analysis"))
app.include_router(subscription.router)
app.include_router(queue_api.router)
app.include_router(queue_api.router, prefix="/api")
app.include_router(admin.router)
app.include_router(crm.router)
app.include_router(branding.router)
app.include_router(nps.router)
app.include_router(upload.router)
app.include_router(billing.router)
app.include_router(interview_kit.router)
app.include_router(interview_kit_regeneration.router)
app.include_router(dashboard.router)
app.include_router(onboarding.router)
app.include_router(users.router)
app.include_router(sso.sso_router)
app.include_router(webhook_docs.router)
app.include_router(voice.router, dependencies=_plan_gate("ai_interviews"))
app.include_router(voice.internal_router)
app.include_router(recruiter.router, dependencies=_plan_gate("ai_interviews"))
app.include_router(recruiter.internal_router)
app.include_router(interviews.router, dependencies=_plan_gate("ai_interviews"))
app.include_router(interviews.internal_router)
app.include_router(projects.router, dependencies=_plan_gate("pipeline"))
app.include_router(requisitions.router, dependencies=_plan_gate("requisitions"))
app.include_router(ats.router)
app.include_router(tenant_audit.router)
app.include_router(share_links.router)
app.include_router(share_links.public_router)
app.include_router(client_errors.router)


# ─── Request Size Limits ───────────────────────────────────────────────────────

# Global request size limits (can be overridden per-endpoint)
DEFAULT_MAX_REQUEST_SIZE = int(os.getenv("MAX_REQUEST_SIZE_MB", "25")) * 1024 * 1024  # 25MB default


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce maximum request body size limits."""

    def __init__(self, app, max_size: int = DEFAULT_MAX_REQUEST_SIZE):
        super().__init__(app)
        self.max_size = max_size
        # Endpoints that allow larger payloads (file uploads)
        self.large_endpoints = {
            "/api/analyze",
            "/api/analyze/batch",
            "/api/upload/resume",
            "/api/upload/jd",
            "/api/video/upload",
            "/api/transcript/upload",
        }

    async def dispatch(self, request: Request, call_next):
        # Skip size check for GET requests and small payloads
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # Check if endpoint allows larger payloads
        path = request.url.path
        allows_large = any(path.startswith(endpoint) for endpoint in self.large_endpoints)

        # Set appropriate limit
        max_size = self.max_size * 3 if allows_large else self.max_size

        # Check Content-Length header first
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_size:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body too large. Maximum size: {max_size // (1024*1024)}MB"}
                    )
            except ValueError:
                pass

        body = await request.body()
        if len(body) > max_size:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large. Maximum size: {max_size // (1024*1024)}MB"}
            )
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)


# ─── API Versioning ───────────────────────────────────────────────────────────

API_VERSION = "v1"
API_VERSION_MAJOR = 1


class APIVersioningMiddleware(BaseHTTPMiddleware):
    """Middleware to handle API versioning and deprecation warnings."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Add API version header to all /api/* responses
        if request.url.path.startswith("/api/"):
            response.headers["X-API-Version"] = API_VERSION
            response.headers["X-API-Version-Major"] = str(API_VERSION_MAJOR)

            # Check for deprecated version in request
            requested_version = request.headers.get("X-API-Version")
            if requested_version and requested_version != API_VERSION:
                response.headers["X-API-Deprecated"] = "true"
                response.headers["X-API-Migration-Guide"] = f"/docs/migration/{requested_version}-to-{API_VERSION}"

        return response


app.add_middleware(APIVersioningMiddleware)


# ─── Root endpoints ───────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "message": "ARIA API — AI Resume Intelligence",
        "version": VERSION,
        "docs":    "/docs",
    }


@app.get("/health")
async def health_check():
    """Liveness + cheap DB ping for orchestrators."""
    try:
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "disconnected"})
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/version")
async def app_version():
    """Build id for deploy refresh detection (compare with frontend __APP_BUILD_ID__)."""
    return {
        "build_id": os.getenv("BUILD_ID") or os.getenv("GIT_SHA") or "dev",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/health")
async def api_health_check():
    """
    Health check with dependency validation.
    Checks DB connectivity and Ollama/LLM reachability.
    Returns 200 if healthy, 503 if degraded.
    """
    errors = []
    db_status = "connected"
    llm_status = "connected"

    # Check database
    try:
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        db_status = "disconnected"
        errors.append(f"Database: {str(e)}")

    # Check Ollama/LLM
    try:
        from app.backend.services.llm_service import get_ollama_headers
        ollama_host = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        headers = get_ollama_headers(ollama_host)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_host}/api/tags", headers=headers)
            if resp.status_code != 200:
                raise Exception(f"Ollama returned {resp.status_code}")
    except Exception as e:
        llm_status = "disconnected"
        errors.append(f"LLM: {str(e)}")

    status = "healthy" if not errors else "degraded"
    status_code = 200 if not errors else 503

    return JSONResponse(
        status_code=status_code,
        content={"status": status, "database": db_status, "llm": llm_status, "errors": errors},
    )


@app.get("/api/health/deep")
async def deep_health_check():
    """
    Deep health check — comprehensive check of all dependencies.
    Checks DB connectivity, Ollama sentinel state, and disk space.
    Returns 'healthy', 'degraded', or 'unhealthy' status.
    """
    from sqlalchemy import text
    from app.backend.services.llm_service import get_sentinel

    start_time = time.monotonic()
    checks = {}
    overall_status = "healthy"

    # ── 1. Database check ─────────────────────────────────────────────────────
    db_start = time.monotonic()
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = {
                "status": "ok",
                "latency_ms": round((time.monotonic() - db_start) * 1000, 1),
            }
        finally:
            db.close()
    except Exception as e:
        checks["database"] = {
            "status": f"error: {str(e)[:50]}",
            "latency_ms": round((time.monotonic() - db_start) * 1000, 1),
        }
        overall_status = "unhealthy"  # DB failure = unhealthy

    # ── 2. Ollama check via sentinel ────────────────────────────────────────────
    ollama_start = time.monotonic()
    from app.backend.services.llm_service import get_sentinel, get_ollama_model

    sentinel = get_sentinel()
    model_name = get_ollama_model()

    if sentinel is None:
        checks["ollama"] = {
            "status": "unknown",
            "latency_ms": round((time.monotonic() - ollama_start) * 1000, 1),
            "model": model_name,
            "message": "Sentinel not initialized",
        }
        overall_status = "degraded"
    else:
        sentinel_status = sentinel.get_status()
        ollama_state = sentinel_status.get("state", "unknown")
        is_healthy = sentinel_status.get("healthy", False)

        checks["ollama"] = {
            "status": ollama_state,
            "latency_ms": round((time.monotonic() - ollama_start) * 1000, 1),
            "model": sentinel_status.get("model", model_name),
            "last_probe_latency_ms": sentinel_status.get("last_latency_ms"),
        }

        if not is_healthy:
            # If Ollama is not HOT, mark as degraded
            if overall_status == "healthy":
                overall_status = "degraded"

    # ── 3. Disk space check (optional) ─────────────────────────────────────────
    try:
        disk_usage = shutil.disk_usage("/")
        free_gb = round(disk_usage.free / (1024 ** 3), 1)
        total_gb = round(disk_usage.total / (1024 ** 3), 1)
        used_percent = round((disk_usage.used / disk_usage.total) * 100, 1)

        disk_status = "ok"
        if used_percent > 90:
            disk_status = "warning"
            if overall_status == "healthy":
                overall_status = "degraded"

        checks["disk"] = {
            "status": disk_status,
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_percent": used_percent,
        }
    except Exception as e:
        checks["disk"] = {
            "status": f"error: {str(e)[:30]}",
        }

    response_time_ms = round((time.monotonic() - start_time) * 1000, 1)

    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "response_time_ms": response_time_ms,
        "checks": checks,
    }


@app.get("/api/llm-status")
async def llm_status():
    """
    Diagnostic endpoint — shows sentinel state, pulled models, hot models,
    and a plain-English diagnosis of why the LLM narrative may be falling back to Python.

    Usage from the VPS:
      curl http://localhost:8080/api/llm-status
    """
    from app.backend.services.llm_service import get_sentinel, is_ollama_cloud, get_ollama_headers, get_ollama_model

    sentinel = get_sentinel()
    if sentinel is None:
        return {"state": "unknown", "healthy": False, "message": "Sentinel not initialized"}

    ollama_url     = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    target_model   = get_ollama_model()
    is_cloud       = is_ollama_cloud(ollama_url)

    result: dict = {
        "ollama_url":            ollama_url,
        "narrative_model":       target_model,
        "mode":                  "cloud" if is_cloud else "local",
        "ollama_reachable":      False,
        "pulled_models":         [],
        "narrative_model_ready": False,
        "running_models":        [],
        "diagnosis":             "",
    }

    # Add sentinel status
    sentinel_status = sentinel.get_status()
    result["sentinel"] = sentinel_status

    # For Ollama Cloud, return early with cloud-specific status
    if is_cloud:
        result["ollama_reachable"] = True
        result["narrative_model_ready"] = True
        result["diagnosis"] = f"Using Ollama Cloud with model: {target_model}"
        if sentinel_status.get("healthy"):
            result["diagnosis"] += " — Cloud connection is healthy."
        else:
            result["diagnosis"] += " — Cloud connection status unknown."
        return result

    # Local Ollama checks
    headers = get_ollama_headers(ollama_url)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags_resp = await client.get(f"{ollama_url}/api/tags", headers=headers)
            tags_resp.raise_for_status()
            result["ollama_reachable"] = True
            result["pulled_models"] = [m["name"] for m in tags_resp.json().get("models", [])]
            result["narrative_model_ready"] = any(
                target_model in m for m in result["pulled_models"]
            )
            try:
                ps = await client.get(f"{ollama_url}/api/ps", headers=headers)
                if ps.status_code == 200:
                    result["running_models"] = [
                        m["name"] for m in ps.json().get("models", [])
                    ]
            except Exception:
                pass
    except Exception as e:
        result["diagnosis"] = f"Ollama unreachable: {str(e)[:120]}"
        return result

    narrative_hot = any(target_model in m for m in result["running_models"])

    if not result["narrative_model_ready"]:
        result["diagnosis"] = (
            f"'{target_model}' not pulled. "
            f"Run: docker exec resume-screener-ollama ollama pull {target_model}"
        )
    elif not narrative_hot:
        result["diagnosis"] = (
            f"'{target_model}' pulled but NOT in RAM (cold). "
            f"Pre-warm: docker exec resume-screener-ollama ollama run {target_model} 'Hi'"
        )
    else:
        result["diagnosis"] = f"'{target_model}' is HOT in RAM — LLM narrative is active."

    return result
