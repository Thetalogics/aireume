# Audit Remediation Phase C — Design Spec

**Date:** 2026-09-16  
**Status:** Approved for implementation planning (pending user review of this file)  
**Reference:** `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md` (AUD-015–022 plus Phase B leftovers: DNS pin, stream cap, entitlement resolver)

**Code baseline:** `main` at `31a27ee` (Phase B CI green)

## Problem

Queued screening does not produce the same domain records as the synchronous path. Dedup keys ignore scoring config, so a recalibration can reuse a stale job. `QUEUE_MAX_CONCURRENT` is unused (serial `process_job`). Heartbeats stop at claim, so long jobs look dead. Stale recovery runs in both `worker_loop` and APScheduler and can double-increment retries. DLQ retry copies hashes but not `artifact_id`. Idempotency keys replay by key+tenant+endpoint even when the body changed. ATS credentials are stored as ordinary strings. `safe_request` re-resolves DNS at connect time (rebinding) and buffers `response.content` before the size check. Some capability checks still read `tenant.plan` instead of `get_tenant_plan`.

## In scope

| ID | Outcome |
|---|---|
| AUD-015 | Integration-secret service: AES-256-GCM ciphertext + key version; decrypt only at outbound ATS use; migrate existing ATS credentials |
| AUD-016 | Versioned `ScreeningCommand` persisted on the job; completion writes `ScreeningResult.requisition_id` and `RequisitionCandidate` like sync |
| AUD-017 | Dedup fingerprint v1 includes tenant, resume, JD, requisition criteria version, weights, overrides, algorithm version |
| AUD-018 | Worker runs up to `QUEUE_MAX_CONCURRENT` in-flight jobs (default 10, cap 32) via `asyncio.Semaphore` |
| AUD-019 | Per-job heartbeat every 30s; lease 10 minutes; actively heartbeating jobs are not recovered |
| AUD-020 | Single recovery owner: APScheduler only; row-locked transition; one retry increment |
| AUD-021 | DLQ stores `artifact_id`; replay attaches a tenant-valid artifact or fails non-destructively |
| AUD-022 | Idempotency bound to request fingerprint; same key + different body → 409, no handler |
| DNS pin | Connect only to IPs from the hop’s `getaddrinfo`; Host/SNI stay the original hostname |
| Stream cap | Read body in chunks; abort at `max_bytes` without buffering the rest |
| Entitlement | Capability reads use `get_tenant_plan`; do not use raw `tenant.plan` for current features/limits |

## Out of scope

- AWS/GCP/Azure KMS SDK or HashiCorp Vault (versioned env keyring now; same `Keyring` interface later)
- ATS hostname allowlist and inbound replay store (Phase B decision)
- Encrypting Stripe/LiveKit/SSO/`PlatformConfig` secrets (ATS `api_key`, `api_secret`, `webhook_secret` only)
- New public HTTP APIs (existing queue and analyze routes call the new service)
- Redis-backed queue, extra worker processes, or a separate job broker
- Feature branch / PR (work on current `main`; commit/push only when asked)

## Global constraints

- Work on current `main`. Test locally; do not commit or push until asked.
- Do not weaken CSRF, JWT, MFA, ClamAV, CORS, rate limits, AUD-004 tenant predicates, or entitlement.
- Queue and sync screening share AUD-005 quota reserve/release. Re-run `test_audit_queue_quota.py` after the refactor.
- Tests ship in the same change. TDD: failing tests first.
- Historic leaked credentials stay an incident (rotate separately); do not broadly ignore them.
- Never log integration secrets, HMAC keys, or candidate PII.
- Capability (limits/features) goes through `get_tenant_plan`. `tenant.plan` / `tenant.plan_id` remain for stored assignment, checkout metadata, and admin overrides.

## Architecture

```
analyze / queue_api
        │
        ▼
ScreeningCommand (v1) ── analysis_fingerprint v1
        │
        ├── execute_screening  (sync, same quota + domain writes)
        └── enqueue_screening  (persist command on AnalysisJob, quota reserve)
                    │
                    ▼
QueueManager.worker_loop ── Semaphore(N) ── process_job (own Session + heartbeat)
                    │
                    ▼
complete_queue_job ── ScreeningResult + RequisitionCandidate (same as sync)

APScheduler recover_stale_jobs ── get_queue_manager().recover_stale_jobs  (only owner)
DLQ move/retry ── artifact_id + command payload
IdempotencyMiddleware ── key + tenant + endpoint + body fingerprint
ATS CRUD ── integration_secrets.encrypt  |  adapters ── decrypt at send/HMAC
url_safety.safe_request ── resolve IPs ── connect to pinned IP ── stream until max_bytes
analyze/subscription capability ── get_tenant_plan (not tenant.plan)
```

## ScreeningCommand and fingerprint (AUD-016, AUD-017)

**Owner:** new `app/backend/services/screening_command.py` (dataclass + fingerprint + `execute_screening` / `enqueue_screening`). Existing `queue_analysis_service.prepare_file_for_queue` and `complete_queue_job` become callers, not a second command shape.

**`ScreeningCommand` v1 fields (all persisted on `AnalysisJob.job_config["command"]`):**

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | `"v1"` |
| `tenant_id` | yes | From JWT / job, never from client spoof |
| `user_id` | yes | Actor |
| `candidate_id` | no | Set when known (`use_existing` or after create) |
| `artifact_id` | queue yes | `AnalysisArtifact.id`; sync may skip if in-memory parse |
| `requisition_id` | no | Null when screening is JD-text-only |
| `role_template_id` | no | Resolved from requisition or request |
| `criteria_version` | no | Requisition criteria version used for this run; null if no requisition |
| `scoring_weights` | no | Normalized dict (sorted keys, numbers as canonical strings) |
| `skill_overrides` | no | Normalized dict |
| `algorithm_version` | yes | Current scoring pipeline version string already used in product (default `"1.0"` if none) |
| `resume_hash` | yes | Same hash as artifact / job |
| `jd_hash` | yes | Same hash as artifact / job |

Invalid or cross-tenant `requisition_id` is rejected **before** enqueue (400), with no job row and no quota reserve.

**Fingerprint v1:** SHA-256 hex of canonical JSON (UTF-8, `sort_keys=True`, no insignificant whitespace besides separators) of:

- `schema_version`, `tenant_id`, `resume_hash`, `jd_hash`
- `requisition_id`, `criteria_version`, `role_template_id`
- normalized `scoring_weights`, `skill_overrides`
- `algorithm_version`

**Excluded from fingerprint:** filename, priority, HTTP transport, idempotency key, request IDs, `user_id`, `candidate_id` (identity of the person does not change the score for the same resume+JD+config).

Store the hex as `AnalysisJob.input_hash` (existing unique column). Prefix the canonical payload with `"v1:"` before hashing so a future v2 cannot collide by accident. Dedup: if a non-terminal job (`queued` / `processing` / `retrying`) exists with the same `input_hash` for that tenant, return that job. A **completed** job with the same fingerprint may be returned as a cache hit only when the caller did not change config (same fingerprint). Changing weights, criteria version, or overrides **must** produce a new `input_hash` and a new job even if resume+JD match.

**`execute_screening`:** run the existing sync scoring + `_upsert_screening_result` path using the command. Must set `ScreeningResult.requisition_id` and upsert `RequisitionCandidate.screening_result_id` exactly as today’s analyze-with-requisition path.

**`enqueue_screening`:** AUD-005 reserve, create artifact if needed, persist command on the job, return `job_id`.

**`complete_queue_job`:** read command from `job_config`; pass `requisition_id` / template / weights / overrides into `_upsert_screening_result` (or the shared execute helper). Must not drop requisition identity. Result persistence for a given `AnalysisJob.id` is idempotent: a second completion must not insert a second `AnalysisResult` for that `job_id` (`AnalysisResult.job_id` is already unique).

No new public routes.

## Worker concurrency (AUD-018)

- Read `QUEUE_MAX_CONCURRENT`, default **10**, clamp to **1–32** inclusive. Values outside that range clamp (do not crash).
- `worker_loop` claims jobs while `in_flight < N` and the semaphore allows. Each claimed job is `asyncio.create_task(process_job(...))`.
- Each task opens its **own** `SessionLocal()`; never share a session or the loop’s session across tasks.
- Claim remains `SELECT … FOR UPDATE SKIP LOCKED` on Postgres. SQLite tests keep a compatible claim (existing test DB path); tests must still prove two jobs overlap under `N=2`.
- Shutdown: set `is_running = False`, then await in-flight tasks with a bounded timeout. Do not clear `processing` / `worker_id` in a way that silently drops a claimed row without heartbeat expiry (recovery owns reclaim).

## Heartbeat and leases (AUD-019)

- On claim: set `worker_id`, `worker_heartbeat = now`, `leased_until = now + 10 minutes` (`QUEUE_STALE_TIMEOUT` default 600s).
- During `process_job`, a background task updates `worker_heartbeat` and `leased_until` every **30s** (`QUEUE_HEARTBEAT_INTERVAL`).
- Cancel that task in `finally` on success, failure, or cancellation.
- Stale definition: `status = processing` AND (`worker_heartbeat` older than the stale window OR `leased_until < now`). A job whose heartbeat is still inside the window is **not** recovered, even if wall-clock analysis exceeds 10 minutes.
- Completing a result for `job_id` remains unique (`AnalysisResult.job_id`).

## Stale recovery (AUD-020)

- **Only** `scheduler.py` `recover_stale_jobs` (existing ~5 minute APScheduler job).
- **Remove** the periodic `recover_stale_jobs` call from `worker_loop`.
- Recovery must call `get_queue_manager()`, not `QueueManager()`.
- Transition with a conditional UPDATE / row lock: `WHERE id = :id AND status = 'processing' AND (heartbeat stale OR lease expired)`. If the row no longer matches, skip. One matching row → at most one `retry_count` increment even if two processes overlap.
- If `retry_count < max_retries`: `retrying`, increment retry, `next_retry_at`, clear `worker_id`. Else: existing permanent-fail / DLQ / AUD-005 quota release.
- Non-processing rows are untouched.

## DLQ artifact replay (AUD-021)

**Schema:** `dead_letter_jobs.artifact_id` nullable UUID FK to `analysis_artifacts.id` `ON DELETE SET NULL`. Alembic migration (next revision after current head).

**`move_to_dead_letter`:** copy `job.artifact_id`, hashes, `job_config` (includes `ScreeningCommand`). While a DLQ row is `pending`, artifact cleanup must not delete that artifact: skip `AnalysisArtifact` rows referenced by pending DLQ or by live `AnalysisJob`. If a time-based `expires_at` cleanup exists or is added, extend/skip expiry for those IDs.

**`retry_dead_letter_job`:**

1. Load DLQ; if missing return `None`; if status ≠ `pending`, keep current `ValueError`.
2. Load `AnalysisArtifact` where `id = artifact_id` AND `tenant_id = dlq.tenant_id`. Missing or mismatch → raise a clear error (`ArtifactUnavailable` or equivalent); **do not** set `reprocessed`; DLQ stays `pending`.
3. Build `ScreeningCommand` from stored `job_config` (same fingerprint inputs). Call `enqueue_screening` so AUD-005 applies.
4. Set `status=reprocessed`, `reprocessed_at`, `reprocessed_job_id` only after the new job insert succeeds, **same transaction**.

## Idempotency fingerprint (AUD-022)

**Owner:** `app/backend/middleware/idempotency.py` + `IdempotencyKey`.

**Request fingerprint:** SHA-256 hex of canonical concatenation:

- HTTP method
- normalized path (`request.url.path`)
- query string with keys sorted
- `Content-Type` (empty string if absent)
- body: if `application/json`, canonical JSON (`sort_keys=True`) when parseable, else raw bytes; otherwise raw body bytes

**Lookup identity:** `key` + `tenant_id` + `endpoint` (`METHOD:path`) + fingerprint.

| Case | Behavior |
|---|---|
| Same key + same fingerprint | Replay stored 2xx; `X-Idempotent-Replay: true` |
| Same key + different fingerprint | **409** JSON `{"detail": "Idempotency key reused with different request"}`; **do not** call the route |
| Same key, different tenant | Independent row |
| Same key, different endpoint | Independent row |
| Expired (`expires_at` in the past) | Treat as absent; key may be reused |

Persist fingerprint on the row. TTL remains 24 hours. Still store only **2xx** responses. Unauthenticated mutating requests remain tenant `0`.

**Schema:** replace primary key `key` with unique constraint `(key, tenant_id, endpoint)`. Add `request_fingerprint` `String(64) NOT NULL`. Multipart/uploads: hash raw body after buffering. If the body cannot be buffered (stream/SSE already skipped when `"stream"` is in the path), skip idempotency entirely — do not store a blank fingerprint.

Starlette: read `request.body()` once and cache so the route still sees the body.

## Integration secrets (AUD-015)

**Owner:** new `app/backend/services/integration_secrets.py`. Callers: ATS create/update/reset, `ats_connector` outbound headers, inbound HMAC verify. Do not decrypt in list/get serializers.

**Crypto:** AES-256-GCM via `cryptography` (already in requirements). Ciphertext format `enc:v{version}:{base64url(nonce || ciphertext || tag)}`. Nonce 12 bytes random per encrypt.

**Keyring (KMS-style, env-backed this phase):**

| Env | Role |
|---|---|
| `INTEGRATION_MASTER_KEY` | Current key, version **1** (32-byte urlsafe or 64-char hex). Required to encrypt. |
| `INTEGRATION_MASTER_KEY_PREVIOUS` | Optional version **0** for rotation overlap |

`encrypt(plaintext) -> str` uses current version. `decrypt(stored) -> str` selects key by version in the prefix. Wrong/missing key → explicit error, log without secret. Tests set a fixed key in `TESTING`.

**Fields:** `ATSConnection.api_key`, `api_secret`, `webhook_secret`. Empty/null stays null.

**Write path:** routes assign only through `encrypt_secret(plain)` (skip if already `enc:v` prefixed).

**Read path:** `decrypt_secret` only inside `ats_connector` when building HTTP/HMAC. Inbound webhook uses decrypt for `compare_digest`.

**API:** `ATSConnectionOut` never includes plaintext. Add `api_key_configured: bool`, `webhook_secret_configured: bool` (true if stored value non-empty). Update/create still accept plaintext once; response does not echo it.

**Migrate existing rows:** on first decrypt, if value is non-empty and not `enc:v`, treat as plaintext, return it, and **re-encrypt in place** (lazy). Plus Alembic 073 (or same revision) data step: if `INTEGRATION_MASTER_KEY` is set at migrate time, encrypt all non-prefixed ATS secret columns; if unset, skip data rewrite and rely on lazy encrypt (app must still encrypt on next write). Production app refuses **new** plaintext writes when the master key is missing (fail closed except `TESTING`, where a documented test key is used).

**Rotation:** decrypt with previous, encrypt with current. No AWS SDK.

## DNS pinning

**Owner:** `url_safety.py`. `validate_public_url` still returns the stripped URL and internally records the public IP set for that hostname.

Each hop: `getaddrinfo` once → keep `allowed_ips`. Connect by rewriting the URL host to one allowed IP (IPv6 in brackets) and set `Host` to the original hostname. For HTTPS, pass `server_hostname` / TLS SNI = original hostname (httpx: extra on request or mount transport). Do **not** let the HTTP client resolve the hostname again.

If connect fails for the first IP, try other allowed IPs; do not fall back to a fresh DNS lookup. Literal IP URLs: the IP must already be public; connect to that IP only.

Tests: mock `getaddrinfo` to a public IP; assert `client.request` URL hostname is that IP and `Host` is `example.com`. A second `getaddrinfo` returning a private IP during the request must not be used.

## Stream + abort at max_bytes

`safe_request` / `safe_request_async` use `stream=True` (or `client.stream`). Read `iter_bytes` / `aiter_bytes` into a buffer. If cumulative length would exceed `max_bytes`, close the stream and raise `UnsafeURLError("Response body exceeds maximum size")` **without** attaching the oversized body to a returned response. Redirects: drain/limit the redirect body the same way (do not call `response.content` first). Final success returns an `httpx.Response` whose `content` is the bounded bytes already read (at most `max_bytes`).

Video download keeps the 500 MiB cap but must use the same stream-abort helper, not a post-buffer check.

## Effective-plan capability reads

**Owner:** `get_tenant_plan(db, tenant_id)` in `plan_entitlement_service.py` (already downgrades unpaid `plan_id` to starter/free).

Replace `tenant.plan` where it means **current capability**:

- `app/backend/routes/analyze.py` batch limit reads (`_get_plan_limits(tenant.plan)`)
- `app/backend/routes/subscription.py` `get_my_subscription` limits/current-plan payload (the plan the tenant can use)

**Leave** `tenant.plan` / `tenant.plan_id` where it means stored assignment or mutation: select-plan persistence, webhook `apply_verified_paid_plan`, platform admin change-plan, audit “old plan name” of the row, listing a tenant’s assigned `plan_id`.

Grep `tenant.plan` after the change; remaining hits must be assignment/audit, not feature/limit checks.

## Testing

New/extended files (names may match plan later):

- `test_audit_phase_c_screening.py` — command persistence; queue completion sets `ScreeningResult.requisition_id`; `RequisitionCandidate` link; cross-tenant requisition rejected before enqueue; retry keeps command.
- Fingerprint: same resume/JD/config → same `input_hash`; weights change → new job; criteria version change → new job; skill override change → new job; filename/priority change → same hash.
- Concurrency: `QUEUE_MAX_CONCURRENT=2`, two slow jobs overlap; a third waits; each job a distinct session id/object.
- Heartbeat: job longer than stale window with heartbeat running → recovery does not reclaim; heartbeat cancelled → recovery requeues; heartbeat task gone after finish.
- Recovery: two concurrent `recover_stale_jobs` on one stale row → `retry_count` +1 once; `worker_loop` does not call recovery; non-stale untouched.
- DLQ: fail → DLQ has `artifact_id` → retry completes on original resume/JD; missing artifact → error, status still `pending`; GC/skip pending DLQ refs; `reprocessed` only after new job exists.
- Idempotency: same JSON replay; different JSON 409 and handler not run; other tenant independent; other endpoint independent; expired key reusable.
- Secrets: create ATS connection → DB value starts with `enc:v`; GET never returns plaintext; adapter decrypts for Authorization; wrong key errors without logging secret; plaintext row lazy-migrates.
- DNS pin: request URL host is the resolved public IP; `Host` header is the original hostname.
- Stream: chunked body larger than `max_bytes` raises; oversized tail is not kept.
- Entitlement: unpaid paid-`plan_id` tenant gets starter limits on batch analyze / subscription GET.
- Re-run `test_audit_queue_quota.py`, Phase B outbound tests, ATS tenant-boundary tests, and existing queue / production-hardening DLQ / idempotency tenant-scope tests.

**Local gate:** backend pytest for the new tests plus quota/queue/outbound/entitlement suites. Frontend unchanged unless a type breaks (not expected). CI on GitHub after explicit commit/push to `main`.

## Rollout

Ship as one `main` change after local tests pass and the user asks to commit/push. Clamp concurrency so production default 10 cannot be set to unbounded. Existing queued jobs without `job_config.command` complete with today’s fields; new enqueues always write v1 command. The idempotency migration **deletes existing `idempotency_keys` rows** (TTL is 24h; body binding cannot be reconstructed) then adds `request_fingerprint` and the composite unique key. Set `INTEGRATION_MASTER_KEY` in every environment that writes ATS credentials; rotate leaked git-history secrets separately.

## Non-goals reminder

Do not add ScreeningCommand HTTP endpoints, a second queue implementation, ATS allowlists, inbound replay IDs, cloud KMS SDKs, or Phase D scoring/search items.
