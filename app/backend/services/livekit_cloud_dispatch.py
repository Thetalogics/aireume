"""
Dispatch voice screening calls via LiveKit Cloud (no self-hosted voice-agent).

Enabled when LIVEKIT_CLOUD_VOICE=1 on the backend.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from app.backend.services.circuit_breaker import get_circuit_breaker, CircuitBreakerOpenError
from app.backend.services.reliability.timeouts import LIVEKIT_READ
from app.voice_agent.livekit_dispatch import dispatch_cloud_screening_call
from app.voice_agent.voice_flow_log import log_step

logger = logging.getLogger(__name__)

ARIA_BACKEND_URL = os.getenv("ARIA_BACKEND_URL", "http://backend:8000")
# When running inside the backend container, use the internal URL for self-callbacks.
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", ARIA_BACKEND_URL)

def _internal_service_secret() -> str:
    secret = os.getenv("INTERNAL_SERVICE_SECRET")
    if secret:
        return secret
    if os.getenv("TESTING", "").lower() in ("1", "true"):
        return "test-internal-service-secret"
    env = os.getenv("ENVIRONMENT", "development")
    if env in ("development", "dev"):
        return ""
    raise RuntimeError("INTERNAL_SERVICE_SECRET must be set (no hardcoded fallback)")


INTERNAL_SERVICE_SECRET = _internal_service_secret()
INTERNAL_HEADERS = {
    "X-Internal-Service-Secret": INTERNAL_SERVICE_SECRET,
    "X-Internal-Secret": INTERNAL_SERVICE_SECRET,
}


def is_cloud_voice_enabled() -> bool:
    return os.getenv("LIVEKIT_CLOUD_VOICE", "").strip().lower() in ("1", "true", "yes")


VOICE_UNAVAILABLE_USER_MESSAGE = (
    "AI phone screening is temporarily unavailable. Please try again later."
)


def get_voice_unavailability_reason() -> str | None:
    """Return a user-facing reason when cloud voice cannot run, else None."""
    if not is_cloud_voice_enabled():
        return None

    missing = [
        name
        for name, value in (
            ("LIVEKIT_URL", os.getenv("LIVEKIT_URL", "").strip()),
            ("LIVEKIT_API_KEY", os.getenv("LIVEKIT_API_KEY", "").strip()),
            ("LIVEKIT_API_SECRET", os.getenv("LIVEKIT_API_SECRET", "").strip()),
        )
        if not value
    ]
    if missing:
        logger.warning("Cloud voice misconfigured — missing: %s", ", ".join(missing))
        return VOICE_UNAVAILABLE_USER_MESSAGE
    return None


def normalize_voice_dispatch_error(message: str | None) -> str:
    """Map internal dispatch errors to a recruiter-safe message."""
    text = (message or "").strip()
    if not text:
        return VOICE_UNAVAILABLE_USER_MESSAGE
    lowered = text.lower()
    if any(
        token in lowered
        for token in (
            "livekit",
            "dispatch failed",
            "connecterror",
            "connection refused",
            "name resolution",
            "unreachable",
            "sip",
            "agent",
            "503",
            "502",
            "504",
        )
    ):
        return VOICE_UNAVAILABLE_USER_MESSAGE
    return text


async def _http_get(url: str) -> httpx.Response:
    breaker = get_circuit_breaker("outbound_http")

    async def _do_get():
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url, headers=INTERNAL_HEADERS)

    try:
        return await breaker.call(_do_get)
    except CircuitBreakerOpenError:
        logger.warning("Outbound HTTP circuit open for %s", url)
        raise


async def _fetch_tenant_config(tenant_id: int, *, session_id: int | str | None = None) -> dict:
    url = f"{BACKEND_INTERNAL_URL}/api/voice/internal/config/{tenant_id}"
    started = time.perf_counter()
    try:
        resp = await _http_get(url)
    except CircuitBreakerOpenError:
        return {}
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code == 200:
        data = resp.json()
        log_step(
            logger,
            session_id,
            "internal_config_ok",
            tenant_id=tenant_id,
            status=resp.status_code,
            elapsed_ms=elapsed_ms,
            company_name=data.get("company_name"),
        )
        return data
    log_step(
        logger,
        session_id,
        "internal_config_failed",
        level=logging.WARNING,
        tenant_id=tenant_id,
        status=resp.status_code,
        elapsed_ms=elapsed_ms,
        url=url,
    )
    return {}


async def _fetch_candidate_context(
    tenant_id: int,
    candidate_id: int,
    *,
    session_id: int | str | None = None,
) -> dict:
    url = f"{BACKEND_INTERNAL_URL}/api/voice/internal/candidate/{tenant_id}/{candidate_id}"
    started = time.perf_counter()
    try:
        resp = await _http_get(url)
    except CircuitBreakerOpenError:
        return {}
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code == 200:
        data = resp.json()
        log_step(
            logger,
            session_id,
            "internal_candidate_ok",
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            status=resp.status_code,
            elapsed_ms=elapsed_ms,
            candidate_name=data.get("name"),
        )
        return data
    log_step(
        logger,
        session_id,
        "internal_candidate_failed",
        level=logging.WARNING,
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        status=resp.status_code,
        elapsed_ms=elapsed_ms,
        url=url,
    )
    return {}


async def dispatch_screening_call_async(payload: dict[str, Any]) -> dict[str, Any]:
    """Enrich payload with tenant/candidate context and dispatch to LiveKit Cloud."""
    session_id = payload.get("session_id")
    log_step(
        logger,
        session_id,
        "backend_dispatch_start",
        phone=payload.get("phone_number"),
        tenant_id=payload.get("tenant_id"),
        candidate_id=payload.get("candidate_id"),
        backend_internal_url=BACKEND_INTERNAL_URL,
    )
    tenant_id = payload.get("tenant_id")
    candidate_id = payload.get("candidate_id")
    if tenant_id is not None:
        payload["tenant_config"] = await _fetch_tenant_config(
            int(tenant_id),
            session_id=session_id,
        )
    if tenant_id is not None and candidate_id is not None:
        payload["candidate_context"] = await _fetch_candidate_context(
            int(tenant_id),
            int(candidate_id),
            session_id=session_id,
        )
    result = await asyncio.wait_for(
        dispatch_cloud_screening_call(payload),
        timeout=LIVEKIT_READ,
    )
    log_step(
        logger,
        session_id,
        "backend_dispatch_result",
        success=result.get("success"),
        room=result.get("room_name"),
        dispatch_id=result.get("dispatch_id"),
        message=result.get("message"),
    )
    return result


def dispatch_screening_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Sync wrapper for APScheduler and other sync callers."""
    return asyncio.run(dispatch_screening_call_async(payload))
