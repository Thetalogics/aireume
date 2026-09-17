# SLO and alerting (AUD-047)

Prometheus metrics are defined in `app/backend/services/metrics.py`. Labels are bounded (`provider`, `outcome`, `result`, `surface`, `direction`, `reason`, `route_class`). Do **not** add tenant IDs, request IDs, emails, or names as labels.

## Metrics (minimum set)

| Signal | Metric |
|---|---|
| Screening requests/success/failure | `aria_screening_total{result}` |
| Screening duration | `aria_screening_duration_seconds` |
| Queue wait | `aria_queue_wait_seconds` |
| Queue processing | `aria_queue_processing_seconds` |
| Queue retries | `aria_queue_retry_total` |
| DLQ | `aria_dlq_depth`, `aria_dlq_moved_total` |
| Quota rejection | `aria_quota_rejected_total{surface}` |
| LLM request/failure/duration | `aria_llm_request_total`, `aria_llm_duration_seconds` |
| LLM saturation | `aria_llm_slot_exhausted`, `aria_llm_saturation_total` |
| ATS push/pull | `aria_ats_sync_total{direction,outcome}` |
| Webhooks | `aria_webhook_delivery_total{outcome}` |
| Billing webhooks | `aria_billing_webhook_total{outcome}` |
| SAML | `aria_saml_auth_total{outcome}` |
| GDPR deletion | `aria_gdpr_deletion_failure_total`, `aria_gdpr_deletion_retry_total` |

## Initial operational defaults (not universal)

These are starting points for a single-tenant staging/prod pair. Tune after two weeks of real traffic.

| Alert | Suggested initial condition |
|---|---|
| Screening success rate | `rate(aria_screening_total{result="success"}[15m]) / rate(aria_screening_total[15m]) < 0.95` for 15m |
| Screening p95 latency | histogram p95 of `aria_screening_duration_seconds` > 30s for 15m |
| Queue wait p95 | `aria_queue_wait_seconds` p95 > 60s for 15m |
| DLQ non-zero | `aria_dlq_depth > 0` for 10m |
| LLM saturation | `increase(aria_llm_saturation_total[10m]) > 5` |
| ATS failure rate | failure/total > 10% over 15m when total > 20 |
| Billing webhook failure | `increase(aria_billing_webhook_total{outcome="failure"}[15m]) > 0` |
| GDPR deletion backlog | `increase(aria_gdpr_deletion_failure_total[1h]) > 0` or pending object deletions > 0 |

Page on DLQ and billing webhook failure. Ticket (not page) on saturation unless it lasts >30m.
