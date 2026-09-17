"""
Prometheus metrics for ARIA observability.

This module defines custom metrics used across the application.
Import metrics from here to avoid circular imports.
"""

from prometheus_client import Histogram, Counter, Gauge

# Custom metrics for LLM operations
LLM_CALL_DURATION = Histogram(
    "aria_llm_call_duration_seconds",
    "Duration of LLM calls in seconds",
    buckets=[5, 10, 20, 30, 60, 120, 180, 300]
)

LLM_FALLBACK_TOTAL = Counter(
    "aria_llm_fallback_total",
    "Total number of LLM fallbacks triggered"
)

# Guardrail Tier 1+2+4 metrics
GUARDRAIL_HALLUCINATION_TOTAL = Counter(
    "aria_guardrail_hallucination_total",
    "Total hallucinations detected and blocked",
    ["node"]
)

GUARDRAIL_INJECTION_BLOCKED_TOTAL = Counter(
    "aria_guardrail_injection_blocked_total",
    "Total prompt injection attempts blocked"
)

GUARDRAIL_SCHEMA_VALIDATION_FAILED_TOTAL = Counter(
    "aria_guardrail_schema_validation_failed_total",
    "Total schema validation failures",
    ["node"]
)

GUARDRAIL_INCONSISTENCY_FIXED_TOTAL = Counter(
    "aria_guardrail_inconsistency_fixed_total",
    "Total cross-node inconsistencies auto-fixed"
)

GUARDRAIL_HITL_FLAG_TOTAL = Counter(
    "aria_guardrail_hitl_flag_total",
    "Total HITL flags generated",
    ["severity"]
)

GDPR_PURGE_TOTAL = Counter(
    "aria_gdpr_purge_total",
    "Expired personal data purge runs",
)

DLQ_MOVED_TOTAL = Counter(
    "aria_dlq_moved_total",
    "Jobs moved to the dead-letter queue",
)

DLQ_DEPTH = Gauge(
    "aria_dlq_depth",
    "Pending dead-letter jobs awaiting retry or discard",
)

GUARDRAIL_CIRCUIT_BREAKER_TOTAL = Counter(
    "aria_guardrail_circuit_breaker_total",
    "Total circuit breaker activations",
    ["node"]
)

GUARDRAIL_TOKEN_BUDGET_EXCEEDED_TOTAL = Counter(
    "aria_guardrail_token_budget_exceeded_total",
    "Total token budget exceedances",
    ["tenant_id"]
)

# Custom metrics for resume parsing
RESUME_PARSE_DURATION = Histogram(
    "aria_resume_parse_duration_seconds",
    "Duration of resume parsing in seconds",
    buckets=[0.5, 1, 2, 5, 10, 30]
)

SCREENING_TOTAL = Counter(
    "aria_screening_total",
    "Screening completions",
    ["result"],
)

QUEUE_WAIT_SECONDS = Histogram(
    "aria_queue_wait_seconds",
    "Time from enqueue to processing start",
    buckets=[1, 5, 15, 30, 60, 120, 300],
)

QUOTA_REJECTED_TOTAL = Counter(
    "aria_quota_rejected_total",
    "Quota rejections",
    ["surface"],
)

# Custom metrics for batch operations
BATCH_SIZE = Histogram(
    "aria_batch_size",
    "Number of resumes in batch requests",
    buckets=[1, 5, 10, 20, 30, 50]
)
