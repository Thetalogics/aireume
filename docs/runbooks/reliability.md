# Reliability runbooks (ARIA)

These are repository-specific. Do not dump secrets.

## Stuck screening job

Symptoms: `analysis_jobs.status=processing` longer than `QUEUE_STALE_TIMEOUT` (default 600s).
Verify: `SELECT id, worker_id, leased_until, worker_heartbeat FROM analysis_jobs WHERE status='processing';`
Safe recovery: wait for worker `recover_stale_jobs` (startup + loop) or run it via a worker restart.
Do not: delete the job row; do not reset `analyses_count_this_month` by hand unless quota reconciliation also ran.

## Queue backlog

Symptoms: many `queued`/`retrying` rows, rising wait histogram.
Verify: counts by status; `QUEUE_WAIT_SECONDS`.
Safe recovery: scale workers; check LLM/provider timeouts.
Do not: re-enqueue the same `input_hash` while a cacheable job exists.

## Redis outage

Symptoms: cache misses, SAML state errors if `REDIS_REQUIRED=1`, readiness 503 when Redis is required.
Verify: `redis-cli ping`; `/ready`.
Safe recovery: cache degrades; do not fail-open auth revocation (revocation is PostgreSQL).
Do not: disable CSRF/auth to "keep traffic flowing".

## PostgreSQL outage

Symptoms: `/ready` 503; `/health` still 200.
Verify: `pg_isready`; application logs `database disconnected`.
Safe recovery: restore primary; do not accept writes.
Do not: point workers at a replica for writes.

## Provider outage

Symptoms: `PROVIDER_TIMEOUT` / `PROVIDER_5XX` / `PROVIDER_RATE_LIMIT`; narrative fallback.
Verify: job `last_error_category`; LLM metrics.
Safe recovery: deterministic scores remain; narrative may be fallback/stale-discarded.
Do not: retry 401/403 in a loop.

## Quota mismatch

Symptoms: tenants blocked with remaining capacity, or usage above plan.
Verify: `quota_reservations` pending vs `analysis_jobs` status; `analyses_count_this_month`.
Safe recovery: `reconcile_expired_quota_reservations`.
Do not: decrement counters without a matching reservation/job.

## Stale-job surge

Symptoms: `aria_job_stale_discard_total` rising after reanalysis bursts.
Verify: `analysis_generation` vs narrative job expected generation.
Safe recovery: none required; stale discards are success for old work.
Do not: mark the current analysis failed.
