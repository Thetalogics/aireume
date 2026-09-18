"""Shared env gates so reliability CI cannot silently skip."""
from __future__ import annotations

import os

import pytest


def postgres_url() -> str | None:
    return os.environ.get("RELIABILITY_POSTGRES_URL") or os.environ.get("PHASE0_POSTGRES_URL")


def redis_url() -> str | None:
    return os.environ.get("RELIABILITY_REDIS_URL") or os.environ.get("REDIS_URL")


def require_postgres() -> str:
    url = postgres_url()
    if url:
        return url
    if os.environ.get("RELIABILITY_REQUIRED") == "1":
        pytest.fail("RELIABILITY_POSTGRES_URL or PHASE0_POSTGRES_URL is required")
    pytest.skip("PostgreSQL reliability tests require PHASE0_POSTGRES_URL")


def require_redis() -> str:
    url = redis_url()
    if url:
        return url
    if os.environ.get("RELIABILITY_REDIS_REQUIRED") == "1":
        pytest.fail("RELIABILITY_REDIS_URL or REDIS_URL is required")
    pytest.skip("Redis reliability tests require REDIS_URL")
