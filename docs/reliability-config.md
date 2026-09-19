# Phase 1 reliability configuration

Environment (no secrets):

| Name | Default | Meaning |
|------|---------|---------|
| QUEUE_HEARTBEAT_INTERVAL | 30 | Lease renewal interval (seconds) |
| QUEUE_STALE_TIMEOUT | 600 | Lease length / reclaim threshold |
| QUEUE_MAX_CONCURRENT | 10 | In-flight jobs per worker |
| QUEUE_POLL_INTERVAL | 2 | Idle poll |
| QUEUE_SHUTDOWN_TIMEOUT | 30 | Drain window on SIGTERM |
| QUOTA_RESERVATION_TTL_SECONDS | 3600 | Abandoned reservation expiry |
| REDIS_REQUIRED | unset | If `1`, `/ready` requires Redis |
| RELIABILITY_POSTGRES_URL / PHASE0_POSTGRES_URL | unset | Integration tests |
| RELIABILITY_REQUIRED | unset | CI: fail instead of skip PG tests |
| LLM_NARRATIVE_TIMEOUT | 500 | Background narrative wait |
| GEMINI_MAX_RETRIES | 0 | Provider-internal retries; queue owns durable retries |
| OBJECT_STORAGE_CONNECT_TIMEOUT | 5 | S3/MinIO connection timeout |
| OBJECT_STORAGE_READ_TIMEOUT | 30 | S3/MinIO operation timeout |

Retry policy (`RetryPolicy`): max_attempts=3, exponential backoff, jitter, and
bounded `Retry-After` (clamped to `max_delay`). It is for non-queued provider
operations only and must not be nested inside queue retries.

The PostgreSQL queue owns durable retry accounting. `retry_count` is persisted,
`max_retries=3` permits one initial execution plus three retries, and each queue
execution performs one provider attempt. The maximum is therefore **4 provider
calls per queued operation**, including worker restarts. Unknown/programming
errors are terminal. Provider 429/5xx, connection failures, and timeouts are
retryable.

PostgreSQL is required for correctness (queue, quota, webhooks, revocation).
Redis is cache / optional SAML state, not the durable job queue.

## Provider timeout and degradation inventory

| Provider/path | Connect | Read/operation | Retry owner / maximum | Classification |
|---|---:|---:|---|---|
| Gemini REST and structured generation | 5s | 60s | queue or caller / 4 queued, 1 inline | DEGRADABLE for narrative; REQUIRED for configured structured tasks |
| Ollama REST and structured generation | 5s | 120s | queue or caller / 4 queued, 1 inline | DEGRADABLE |
| OpenRouter app LLM fallback | caller budget (default 120s total) | caller budget | caller / 1 | OPTIONAL |
| Stripe SDK | SDK bounded default; provider package not installed by backend requirements | SDK bounded default | SDK/caller | NOT ACTIVE in default runtime |
| Razorpay SDK | SDK bounded default; provider package not installed by backend requirements | SDK bounded default | SDK/caller | NOT ACTIVE in default runtime |
| LiveKit dispatch | 5s internal HTTP | 15s overall cloud dispatch; 60s self-hosted HTTP | queue scheduler / 1 per dispatch | REQUIRED when voice is enabled |
| JD/public URL fetch | 3s | 10s | caller / 1 | REQUIRED for requested URL import |
| SMTP email | 5s | 20s socket | caller / 1 | OPTIONAL; returns failure without rolling back core work |
| S3/MinIO object storage | 5s | 30s | botocore / 1 | OPTIONAL; DB storage fallback remains authoritative |

Deterministic screening is authoritative. Narrative provider failure may retry
independently and must not roll back a valid deterministic decision.

## Quota reservation lifecycle

Queue enqueue creates the reservation, increments the tenant counter, writes
the usage log, and inserts the job in one PostgreSQL transaction. The job UUID
is the durable operation identity. A retry reuses the existing job and hold.
Successful completion marks the hold `consumed`; terminal pre-billable failure
marks it `released`; retrying keeps it `pending`. Released operations are never
reactivated.

Reservation primitives are caller-transaction-owned. A release locks the
reservation before conditionally decrementing tenant usage; only a successful
decrement may transition the reservation to `released` or set a job's
`quota_released` marker.

## Voice attempt and scheduler lifecycle

`result_generation` is the immutable voice-attempt token. Reschedule, retry,
cancel, consent denial, and terminal escalation increment it exactly once.
`scheduled` is dispatchable; `failed`, `no_answer`, and `pending` are
retry-eligible outcomes; `completed`, `cancelled`, `ended`, `escalated`, and
`voicemail` are terminal.

APScheduler jobs use `voice_call_{session_id}_{generation}` and carry both
values as callback arguments. Dispatch checks generation/status before
claiming `scheduled -> ringing`, immediately before the provider request, and
before applying the provider result. PostgreSQL remains the durable scheduling
source; the elected scheduler leader reconciles missing in-memory jobs every
two minutes and at startup.
