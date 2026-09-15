# Audit Remediation Phase B — Design Spec

**Date:** 2026-09-16  
**Status:** Approved for implementation planning  
**Reference:** `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md` (AUD-011–014)  
**Code baseline:** `main` at `642129a` (Phase A CI green)

## Problem

Tenant- and user-controlled URLs are fetched by the server without a single hop-by-hop network policy. JD import and video download validate the first URL, then `httpx` follows redirects. ATS adapters and outbound webhooks use raw `httpx`. Webhook URL checks skip DNS. Inbound ATS webhooks succeed when `webhook_secret` is empty, so an unauthenticated POST can write sync logs and change screening status.

## In scope

| ID | Outcome |
|---|---|
| AUD-011 | ATS outbound `base_url` / adapter HTTP uses shared public-URL policy at persist and at connect |
| AUD-012 | Inbound ATS webhook fails closed without a secret; missing or wrong HMAC returns 403 and does not mutate state |
| AUD-013 | JD import follows at most 3 redirects; each `Location` is re-validated before the next request |
| AUD-014 | Outbound tenant webhooks require HTTPS, resolve DNS, re-validate before connect and on redirects |
| Video fetch | Same fetch helper as JD (same redirect SSRF class; already named in `url_safety.py`) |

## Out of scope

- **AUD-015** — application encryption of integration secrets (new feature; deferred)
- ATS commercial hostname allowlist
- Replay-ID store for inbound ATS events
- Per-provider Greenhouse/Lever signature dialects
- Wrapping OAuth, LLM, LiveKit, or health-check `httpx` clients
- Envelope encryption, KMS/Vault, key rotation
- Feature branch / PR (work on current `main`; commit/push only when asked)

## Global constraints

- Work on current `main`. Test locally; do not commit or push until asked.
- Do not weaken CSRF, JWT, MFA, ClamAV, CORS, rate limits, AUD-004 tenant predicates, or entitlement checks.
- Tests ship in the same change as the behavior. TDD: failing tests first.
- Never log webhook secrets, HMAC keys, or candidate PII in new logs.
- One policy module. Do not copy SSRF checks into ATS/JD/webhook/video.

## Architecture

```
ATS persist/connect ──┐
JD scraper           ──┼── url_safety.validate_public_url
Outbound webhooks    ──┤         + url_safety.safe_request / safe_request_async
Video downloader     ──┘
Inbound ATS webhook  ──── ats_connector.verify_inbound_webhook (fail closed) ── routes/ats.py
```

OAuth, LLM, LiveKit, and health checks keep their existing clients.

## Outbound policy (AUD-011, 013, 014, video)

**Owner:** `app/backend/services/url_safety.py` (extend; do not add a parallel validator).

**`validate_public_url(url, *, require_https=False)`**

- Scheme `http` or `https` only. If `require_https`, reject `http`.
- Reject userinfo (`user:pass@host`).
- Host required. Reject `localhost`, `ip6-localhost`, `metadata.google.internal`, and literal private / loopback / link-local / multicast / reserved / unspecified IPs.
- `socket.getaddrinfo` all A/AAAA; every address must be public (`ipaddress` flags as in current `_is_public_ip`). Resolve failure → `UnsafeURLError`.
- Return the stripped URL on success.

**`safe_request` / `safe_request_async`**

- `httpx` with `follow_redirects=False`.
- Validate the URL, then connect. Timeouts match callers: 10s webhooks, 20s JD/video, 30s ATS.
- Read at most **1 MiB** of response body by default; larger → `UnsafeURLError`. Video `_http_download` uses the same hop validation but keeps the existing **500 MiB** stream cap. Stored webhook delivery bodies stay truncated to 1000 characters as today.
- On 3xx: read `Location`, `urljoin` against the previous URL, validate, then fetch. **Initial request plus at most 3 redirects** (4 requests max). Missing Location, loop, hop overflow, or non-http(s) Location → `UnsafeURLError` and **no** next socket.
- Callers of tenant/user URLs must not construct their own `httpx` client for those URLs.

**Call sites**

| Caller | Persist | Fetch |
|---|---|---|
| ATSConnection `base_url` and `webhook_url` | validate (http+https) | `safe_request_async` |
| `jd_scraper.py` | route already validates | replace `follow_redirects=True` |
| `webhook_service.py` | `validate_webhook_url` = HTTPS + `validate_public_url(..., require_https=True)` | `safe_request` |
| `video_downloader.py` | route already validates | replace `follow_redirects=True` |

**Errors:** `UnsafeURLError` → 400 on configure/import APIs. Webhook delivery: log without secret/PII, skip connect, record failed delivery as today.

## Inbound ATS webhook (AUD-012)

**Route:** `POST /api/ats/webhook/{connection_id}` (no JWT; HMAC only).

| Condition | HTTP | DB |
|---|---|---|
| Unknown or inactive connection | 404 | no writes |
| Missing/blank `webhook_secret` | **403** | no writes (ingestion disabled) |
| Secret set, missing/wrong `X-ATS-Signature` | **403** | no writes |
| Secret set, matching SHA-256 HMAC of raw body (`compare_digest`) | 200 | existing log + optional status map |

Keep AUD-004: status updates only for `ScreeningResult` with `tenant_id == conn.tenant_id`.

Applies in all environments. Tests that posted unsigned bodies must send a configured secret and signature.

Existing connections with no secret stop ingesting until an admin sets one (generate path already exists).

## Testing

**File:** `app/backend/tests/test_audit_phase_b_outbound.py`

DNS/`getaddrinfo` mocked. No real sockets to private IPs. Mock httpx and assert **not called** on rejection.

- Reject localhost, `127.0.0.1`, `::1`, RFC1918, link-local, multicast, metadata hosts.
- Reject hostname whose resolution includes a private IP.
- Reject public URL redirecting to private / `file:` / 4th hop.
- Allow public https; webhook helper rejects http.
- JD and video: public first hop, private `Location` → reject, no second connect.
- ATS persist/send: same policy.
- No secret → 403; screening unchanged; no `ATSSyncLog`.
- Wrong signature → 403; no mutation.
- Correct HMAC → 200 and current processing.
- Cross-tenant `candidate_id` still must not update.

Also re-run existing ATS tenant-boundary, JD, video, and webhook tests.

**Local gate:** pytest on the new file plus those related suites. CI on GitHub after an explicit commit/push to `main`.

## Compatibility

Public routes stay (`/api/ats/webhook/{id}`, JD URL import, video URL, tenant webhook CRUD). Behavior tightens. No new public APIs unless a test cannot be expressed otherwise.

## Deferred (not this spec)

AUD-015 encryption-at-rest; AUD-018 concurrent workers; CSV import; KMS; SLO dashboards; digest promotion; wrapping first-party HTTP.
