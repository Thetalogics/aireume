"""Stable error categories for reliability-critical jobs."""
from __future__ import annotations

from enum import Enum


class RetryClass(str, Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    STALE = "STALE"
    CANCELLED = "CANCELLED"


class ErrorCategory(str, Enum):
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_5XX = "PROVIDER_5XX"
    DB_TRANSIENT = "DB_TRANSIENT"
    INVALID_INPUT = "INVALID_INPUT"
    POLICY_DENIED = "POLICY_DENIED"
    STALE_VERSION = "STALE_VERSION"
    LEASE_LOST = "LEASE_LOST"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
