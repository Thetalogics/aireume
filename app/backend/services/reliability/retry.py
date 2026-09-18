"""Bounded retry policy. One outer budget — callers must not nest extra loops."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import httpx

from app.backend.services.reliability.errors import ErrorCategory, RetryClass

T = TypeVar("T")


@dataclass(frozen=True)
class FailureDecision:
    retry_class: RetryClass
    category: ErrorCategory
    should_retry: bool
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.2
    max_delay: float = 8.0
    jitter: float = 0.1


def classify_failure(exc: BaseException, *, category_hint: ErrorCategory | None = None) -> FailureDecision:
    if category_hint == ErrorCategory.STALE_VERSION:
        return FailureDecision(RetryClass.STALE, ErrorCategory.STALE_VERSION, False)
    if category_hint == ErrorCategory.CANCELLED:
        return FailureDecision(RetryClass.CANCELLED, ErrorCategory.CANCELLED, False)
    if category_hint == ErrorCategory.LEASE_LOST:
        return FailureDecision(RetryClass.CANCELLED, ErrorCategory.LEASE_LOST, False)

    if isinstance(exc, httpx.TimeoutException):
        return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.PROVIDER_TIMEOUT, True)
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError, ConnectionError, TimeoutError)):
        return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.DEPENDENCY_UNAVAILABLE, True)

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code
        retry_after = _retry_after(exc.response)
        if status == 429:
            return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.PROVIDER_RATE_LIMIT, True, retry_after)
        if status >= 500:
            return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.PROVIDER_5XX, True, retry_after)
        if status in (401, 403, 400, 404, 422):
            return FailureDecision(RetryClass.PERMANENT, ErrorCategory.INVALID_INPUT, False)

    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "deadlock" in msg or "serialization" in msg or "could not serialize" in msg:
        return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.DB_TRANSIENT, True)
    if isinstance(exc, ValueError) or "invalid" in msg or "malformed" in msg:
        return FailureDecision(RetryClass.PERMANENT, ErrorCategory.INVALID_INPUT, False)
    if "timeout" in name or "timeout" in msg:
        return FailureDecision(RetryClass.TRANSIENT, ErrorCategory.PROVIDER_TIMEOUT, True)
    return FailureDecision(RetryClass.PERMANENT, ErrorCategory.UNKNOWN, False)


def _retry_after(response) -> float | None:
    raw = response.headers.get("Retry-After") if response is not None else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def run_with_retry(fn: Callable[[], T], *, policy: RetryPolicy) -> T:
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            decision = classify_failure(exc)
            if not decision.should_retry or attempt >= policy.max_attempts:
                raise
            delay = decision.retry_after_seconds
            if delay is None:
                delay = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
            else:
                delay = min(policy.max_delay, delay)
            if policy.jitter:
                delay += random.uniform(0, policy.jitter)
            if delay:
                time.sleep(delay)
    assert last is not None
    raise last
