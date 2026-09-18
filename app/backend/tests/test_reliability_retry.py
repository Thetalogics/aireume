"""Retry classification and bounded retry policy (unit, hermetic)."""
from __future__ import annotations

import httpx
import pytest

from app.backend.services.reliability.errors import ErrorCategory, RetryClass
from app.backend.services.reliability.retry import RetryPolicy, classify_failure, run_with_retry


class TimeoutLike(Exception):
    pass


def test_timeout_is_transient():
    decision = classify_failure(httpx.TimeoutException("t"))
    assert decision.retry_class == RetryClass.TRANSIENT
    assert decision.category == ErrorCategory.PROVIDER_TIMEOUT


def test_rate_limit_is_transient_with_retry_after():
    resp = httpx.Response(429, headers={"Retry-After": "2"}, request=httpx.Request("GET", "https://x"))
    decision = classify_failure(httpx.HTTPStatusError("429", request=resp.request, response=resp))
    assert decision.retry_class == RetryClass.TRANSIENT
    assert decision.category == ErrorCategory.PROVIDER_RATE_LIMIT
    assert decision.retry_after_seconds == pytest.approx(2.0)


def test_auth_failure_is_permanent():
    resp = httpx.Response(401, request=httpx.Request("GET", "https://x"))
    decision = classify_failure(httpx.HTTPStatusError("401", request=resp.request, response=resp))
    assert decision.retry_class == RetryClass.PERMANENT
    assert decision.should_retry is False


def test_stale_is_not_retried():
    decision = classify_failure(LookupError("stale"), category_hint=ErrorCategory.STALE_VERSION)
    assert decision.retry_class == RetryClass.STALE
    assert decision.should_retry is False


def test_validation_is_permanent_zero_retry():
    decision = classify_failure(ValueError("invalid resume"))
    assert decision.retry_class == RetryClass.PERMANENT


def test_bounded_retries_do_not_multiply():
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise httpx.TimeoutException("t")

    policy = RetryPolicy(max_attempts=3, base_delay=0, max_delay=0, jitter=0)
    with pytest.raises(httpx.TimeoutException):
        run_with_retry(boom, policy=policy)
    assert attempts["n"] == 3


def test_permanent_error_not_retried():
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise ValueError("malformed")

    with pytest.raises(ValueError):
        run_with_retry(boom, policy=RetryPolicy(max_attempts=5, base_delay=0, jitter=0))
    assert attempts["n"] == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_http_statuses_are_transient(status):
    resp = httpx.Response(status, request=httpx.Request("GET", "https://x"))
    decision = classify_failure(
        httpx.HTTPStatusError(str(status), request=resp.request, response=resp)
    )
    assert decision.retry_class == RetryClass.TRANSIENT


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_http_statuses_are_permanent(status):
    resp = httpx.Response(status, request=httpx.Request("GET", "https://x"))
    decision = classify_failure(
        httpx.HTTPStatusError(str(status), request=resp.request, response=resp)
    )
    assert decision.retry_class == RetryClass.PERMANENT
    assert decision.should_retry is False


def test_unknown_programming_error_is_terminal():
    decision = classify_failure(RuntimeError("unexpected invariant violation"))
    assert decision.retry_class == RetryClass.PERMANENT
    assert decision.should_retry is False


def test_lost_lease_is_not_owned_retry(monkeypatch):
    decision = classify_failure(
        RuntimeError("lost"), category_hint=ErrorCategory.LEASE_LOST
    )
    assert decision.retry_class in (RetryClass.STALE, RetryClass.CANCELLED)
    assert decision.should_retry is False


def test_retry_after_is_clamped_to_policy_max(monkeypatch):
    delays = []
    monkeypatch.setattr("app.backend.services.reliability.retry.time.sleep", delays.append)
    attempts = {"n": 0}

    def rate_limited():
        attempts["n"] += 1
        response = httpx.Response(
            429,
            headers={"Retry-After": "864000"},
            request=httpx.Request("GET", "https://x"),
        )
        raise httpx.HTTPStatusError("429", request=response.request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        run_with_retry(
            rate_limited,
            policy=RetryPolicy(max_attempts=2, base_delay=0, max_delay=3, jitter=0),
        )
    assert delays == [3]
