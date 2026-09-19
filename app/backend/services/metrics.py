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
    ["route_class"]
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

SCREENING_DURATION_SECONDS = Histogram(
    "aria_screening_duration_seconds",
    "Screening persist/execute duration",
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15, 30],
)

QUEUE_WAIT_SECONDS = Histogram(
    "aria_queue_wait_seconds",
    "Time from enqueue to processing start",
    buckets=[1, 5, 15, 30, 60, 120, 300],
)

QUEUE_PROCESSING_SECONDS = Histogram(
    "aria_queue_processing_seconds",
    "Time from processing start to completion",
    buckets=[1, 5, 15, 30, 60, 120, 300, 600],
)

QUEUE_RETRY_TOTAL = Counter(
    "aria_queue_retry_total",
    "Queue job retries",
    ["reason"],
)

JOB_STALE_DISCARD_TOTAL = Counter(
    "aria_job_stale_discard_total",
    "Background jobs discarded because the target generation changed",
    ["job_type"],
)

LEASE_RECOVERY_TOTAL = Counter(
    "aria_lease_recovery_total",
    "Expired processing leases reclaimed",
)

QUOTA_RECONCILE_RELEASE_TOTAL = Counter(
    "aria_quota_reconcile_release_total",
    "Abandoned quota reservations released",
)

QUOTA_RECONCILE_FAILURE_TOTAL = Counter(
    "aria_quota_reconcile_failure_total",
    "Quota reservation reconciliation failures",
)

QUOTA_RELEASE_FAILURE_TOTAL = Counter(
    "aria_quota_release_failure_total",
    "Quota reservation releases that could not safely decrement usage",
    ["reason"],
)

LEASE_LOST_BEFORE_COMMIT_TOTAL = Counter(
    "aria_lease_lost_before_commit_total",
    "Workers prevented from committing after losing their lease",
)

QUOTA_REJECTED_TOTAL = Counter(
    "aria_quota_rejected_total",
    "Quota rejections",
    ["surface"],
)

LLM_REQUEST_TOTAL = Counter(
    "aria_llm_request_total",
    "LLM requests",
    ["provider", "outcome"],
)

LLM_DURATION_SECONDS = Histogram(
    "aria_llm_duration_seconds",
    "LLM call duration",
    ["provider"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

LLM_SATURATION_TOTAL = Counter(
    "aria_llm_saturation_total",
    "LLM concurrency slot exhaustion",
    ["provider"],
)

LLM_SATURATION = Gauge(
    "aria_llm_slot_exhausted",
    "Current LLM slot saturation (1=exhausted last acquire)",
    ["provider"],
)

ATS_SYNC_TOTAL = Counter(
    "aria_ats_sync_total",
    "ATS push/pull operations",
    ["direction", "outcome"],
)

WEBHOOK_DELIVERY_TOTAL = Counter(
    "aria_webhook_delivery_total",
    "Outbound webhook deliveries",
    ["outcome"],
)

BILLING_WEBHOOK_TOTAL = Counter(
    "aria_billing_webhook_total",
    "Billing provider webhook handling",
    ["outcome"],
)

SAML_AUTH_TOTAL = Counter(
    "aria_saml_auth_total",
    "SAML authentication outcomes",
    ["outcome"],
)

GDPR_DELETION_FAILURE_TOTAL = Counter(
    "aria_gdpr_deletion_failure_total",
    "GDPR hard-delete failures",
    ["reason"],
)

GDPR_DELETION_RETRY_TOTAL = Counter(
    "aria_gdpr_deletion_retry_total",
    "GDPR object-deletion retries queued",
)

# Custom metrics for batch operations
BATCH_SIZE = Histogram(
    "aria_batch_size",
    "Number of resumes in batch requests",
    buckets=[1, 5, 10, 20, 30, 50]
)
