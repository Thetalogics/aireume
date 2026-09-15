# Audit Remediation Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close AUD-011–014 (plus video URL fetch) from `docs/superpowers/specs/2026-09-16-audit-phase-b-design.md`: one outbound URL/DNS/redirect policy and fail-closed inbound ATS HMAC.

**Architecture:** Extend `url_safety.py` with `validate_public_url(..., require_https=)` and `safe_request` / `safe_request_async` that never set `follow_redirects=True`. ATS persist/connect, JD scraper, tenant webhooks, and video download must use that helper. Inbound ATS webhooks reject missing or wrong secrets with 403 and no DB writes.

**Tech Stack:** FastAPI, httpx, pytest, existing SQLite test `client`/`db` fixtures, `ipaddress`/`socket` (already in `url_safety.py`).

## Global Constraints

- Work on current `main`. No feature branch or PR unless the user asks.
- Test locally. **Do not git commit or push unless the user explicitly asks.** Skip every “Commit” step until then.
- Do not weaken CSRF, JWT, MFA, ClamAV, CORS, rate limits, AUD-004 tenant predicates, or entitlement checks.
- TDD: failing test first, then minimal code.
- Never log webhook secrets, HMAC keys, or candidate PII.
- One policy module: `app/backend/services/url_safety.py`. Do not copy SSRF checks into ATS/JD/webhook/video.
- No ATS hostname allowlist. No replay-ID store. No AUD-015 encryption. Do not wrap OAuth/LLM/LiveKit/health `httpx`.
- JD/video/ATS: `http` and `https`. Tenant outbound webhooks: HTTPS-only.
- Initial request + at most 3 redirects. Re-validate DNS before each connect. Reject userinfo and non-http(s) Location.
- Missing or wrong inbound ATS HMAC → **403**, no mutation, all environments.

## File map

- Modify: `app/backend/services/url_safety.py`
- Modify: `app/backend/services/webhook_service.py`
- Modify: `app/backend/services/jd_scraper.py`
- Modify: `app/backend/services/video_downloader.py`
- Modify: `app/backend/routes/ats.py` (persist validation; inbound 403)
- Modify: `app/backend/services/ats_connector.py` (fetch via `safe_request_async`; `verify_inbound_webhook` fail-closed)
- Create: `app/backend/tests/test_audit_phase_b_outbound.py`
- Modify: `app/backend/tests/test_audit_tenant_boundaries.py` (patch `safe_request_async` instead of raw `httpx.AsyncClient` if needed)

---

### Task 1: Public-URL policy (userinfo, HTTPS flag, DNS)

**Files:**
- Modify: `app/backend/services/url_safety.py`
- Create: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: existing `validate_public_url(url) -> str` and `UnsafeURLError`
- Produces: `validate_public_url(url: str, *, require_https: bool = False) -> str`

- [ ] **Step 1: Write failing tests**

```python
# app/backend/tests/test_audit_phase_b_outbound.py
from unittest.mock import patch
import pytest
from app.backend.services.url_safety import UnsafeURLError, validate_public_url

def test_rejects_userinfo():
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://user:pass@example.com/path")

def test_require_https_rejects_http():
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://example.com/hook", require_https=True)

def test_rejects_localhost_and_rfc1918():
    for url in ("http://localhost/x", "https://127.0.0.1/x", "https://10.0.0.1/x", "https://[::1]/x"):
        with pytest.raises(UnsafeURLError):
            validate_public_url(url)

def test_rejects_hostname_resolving_private():
    with patch("app.backend.services.url_safety.socket.getaddrinfo", return_value=[
        (None, None, None, None, ("10.1.2.3", 0)),
    ]):
        with pytest.raises(UnsafeURLError):
            validate_public_url("https://evil.example/x")

def test_allows_public_https():
    with patch("app.backend.services.url_safety.socket.getaddrinfo", return_value=[
        (None, None, None, None, ("93.184.216.34", 0)),
    ]):
        assert validate_public_url("https://example.com/a") == "https://example.com/a"
```

- [ ] **Step 2: Run tests; expect FAIL** (userinfo / `require_https` not implemented)

Run: `python -m pytest app/backend/tests/test_audit_phase_b_outbound.py::test_rejects_userinfo app/backend/tests/test_audit_phase_b_outbound.py::test_require_https_rejects_http -q`

- [ ] **Step 3: Implement**

- Add `require_https: bool = False`. If true and scheme is not `https`, raise `UnsafeURLError`.
- If `parsed.username` or `parsed.password` is set, raise `UnsafeURLError`.
- Keep existing scheme/host/DNS/`_is_public_ip` checks.

- [ ] **Step 4: Run Task 1 tests; expect PASS**

- [ ] **Step 5: Commit** — skip unless the user asked.

---

### Task 2: `safe_request` / `safe_request_async` (no auto-redirect)

**Files:**
- Modify: `app/backend/services/url_safety.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: `validate_public_url`
- Produces:

```python
MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 1 * 1024 * 1024

def safe_request(method: str, url: str, *, require_https: bool = False, timeout: float = 10.0,
                 max_bytes: int = DEFAULT_MAX_BYTES, headers: dict | None = None,
                 content: bytes | None = None, json: dict | None = None) -> "httpx.Response"

async def safe_request_async(method: str, url: str, *, require_https: bool = False, timeout: float = 20.0,
                             max_bytes: int = DEFAULT_MAX_BYTES, headers: dict | None = None,
                             content: bytes | None = None, json: dict | None = None) -> "httpx.Response"
```

Behavior: `follow_redirects=False`. Validate URL, then request. On 301/302/303/307/308: `urllib.parse.urljoin` previous URL with `Location`, validate, retry. Stop after 3 redirects. Missing Location, loop (seen URLs), non-http(s), or body longer than `max_bytes` → `UnsafeURLError` and do not issue the next request. Use one `httpx.Client` / `AsyncClient` per top-level call with `follow_redirects=False`.

- [ ] **Step 1: Write failing tests** using a fake httpx client / `Mock(side_effect=...)` on `httpx.Client.request`:
  - public URL → 200; client called once
  - public 302 Location `https://10.0.0.1/` → `UnsafeURLError`; **no** request to the private IP
  - public 302 Location `file:///etc/passwd` → `UnsafeURLError`
  - four 302 hops to public URLs → `UnsafeURLError` (4th redirect)
  - `getaddrinfo` mocked public for allowed hosts

- [ ] **Step 2: Run; expect FAIL** (`safe_request` missing)

- [ ] **Step 3: Implement** both sync and async helpers (async may call the same redirect loop with `await client.request`).

- [ ] **Step 4: Run Task 1+2 tests; expect PASS**

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 3: Outbound tenant webhooks (AUD-014)

**Files:**
- Modify: `app/backend/services/webhook_service.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: `validate_public_url(..., require_https=True)`, `safe_request`
- Produces: `validate_webhook_url(url) -> tuple[bool, str]` wrapping `UnsafeURLError`; `_send_webhook` uses `safe_request("POST", url, require_https=True, timeout=10.0, content=payload_bytes, headers=...)`

- [ ] **Step 1: Tests**
  - `validate_webhook_url("http://example.com")` is invalid
  - hostname resolving to `127.0.0.1` is invalid
  - `_send_webhook` with private hostname: mock `safe_request` / `getaddrinfo`; assert no successful connect (patch `httpx.Client` and assert not used, or patch `safe_request` after implementation — first fail on current `validate_webhook_url` accepting `https://evil.local` with private DNS)

```python
def test_validate_webhook_url_requires_https_and_public_dns():
    from app.backend.services.webhook_service import validate_webhook_url
    ok, _ = validate_webhook_url("http://example.com/h")
    assert ok is False
    with patch("app.backend.services.url_safety.socket.getaddrinfo", return_value=[
        (None, None, None, None, ("127.0.0.1", 0)),
    ]):
        ok, err = validate_webhook_url("https://internal.example/h")
        assert ok is False
```

- [ ] **Step 2: Run; expect FAIL** (hostname without DNS check currently returns True)

- [ ] **Step 3: Replace `validate_webhook_url` body with try/except `validate_public_url(..., require_https=True)`. Replace `httpx.Client` in `_send_webhook` with `safe_request`. Keep HMAC header `X-Webhook-Signature` and 1000-char body truncate. On `UnsafeURLError`, log warning without URL userinfo/secrets, return `(0, str(e)[:1000], False)`.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 4: JD scraper (AUD-013)

**Files:**
- Modify: `app/backend/services/jd_scraper.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: `safe_request_async("GET", url, timeout=20.0, headers=HEADERS)`
- Produces: `scrape_jd` uses helper; no `follow_redirects=True`

- [ ] **Step 1: Async test** — mock `safe_request_async` to raise `UnsafeURLError` on second hop, or mock httpx sequence: first 302 to `https://127.0.0.1/`, assert `scrape_jd` raises and second host never requested.

- [ ] **Step 2: Run; expect FAIL** (still `follow_redirects=True`)

- [ ] **Step 3: Replace AsyncClient block with `resp = await safe_request_async("GET", url, timeout=20.0, headers=HEADERS)` then existing BeautifulSoup logic. `raise_for_status` stays.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 5: Video downloader (same SSRF class)

**Files:**
- Modify: `app/backend/services/video_downloader.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: `safe_request_async` for HTML/JSON resolves (`resolve_zoom_url`, `resolve_loom_url`) with default 1 MiB
- Produces: `_http_download` must validate every hop then stream. Do **not** apply 1 MiB to the video body. Keep `MAX_DOWNLOAD_BYTES` (500 MiB). Options: (a) `safe_request_async(..., max_bytes=MAX_DOWNLOAD_BYTES)` if the helper streams until cap, or (b) export `iter_validated_hops` / `open_safe_stream_async` that returns a streaming response after redirect validation. Prefer extending `safe_request_async` with `max_bytes=` so video passes `max_bytes=MAX_DOWNLOAD_BYTES`. Still `follow_redirects=False` and re-validate Location.

- [ ] **Step 1: Test** `resolve_zoom_url` / `_http_download`: first URL public 302 to private → `UnsafeURLError` or `ValueError`; mock asserts no stream to private IP. Patch `app.backend.services.video_downloader.httpx.AsyncClient` today (will fail until switched).

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Replace all `httpx.AsyncClient(..., follow_redirects=True)` in this file. Loom `https://www.loom.com/...` still goes through `safe_request_async` (public host). Map `UnsafeURLError` to `ValueError` with a generic “URL is not allowed” message (no internal IPs in the client message if that leaks SSRF mapping — use a fixed string).

- [ ] **Step 4: Re-run new tests + `app/backend/tests/test_video_downloader.py` (update patches to `safe_request_async` if they mock `httpx.AsyncClient`).

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 6: ATS persist + outbound HTTP (AUD-011)

**Files:**
- Modify: `app/backend/routes/ats.py` (`create_connection`, `update_connection`)
- Modify: `app/backend/services/ats_connector.py` (every `httpx.AsyncClient` used against `connection.base_url` / `webhook_url`)
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`
- Modify: `app/backend/tests/test_audit_tenant_boundaries.py`

**Interfaces:**
- Consumes: `validate_public_url` on non-empty `base_url` and `webhook_url` at persist (http+https); `safe_request_async` for GET/POST in connector/adapters (`timeout=30.0`)
- Produces: 400 on unsafe persist; no raw tenant URL `httpx` in ATS path

Helper in `ats.py`:

```python
from app.backend.services.url_safety import UnsafeURLError, validate_public_url

def _validate_ats_urls(base_url, webhook_url):
    try:
        if base_url:
            validate_public_url(base_url)
        if webhook_url:
            validate_public_url(webhook_url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail="ATS URL is not allowed") from e
```

Replace `async with httpx.AsyncClient` in `push_candidate_status`, `pull_candidate_status`, `fetch_open_requisitions` (and any other adapter fetch) with `await safe_request_async(...)`.

- [ ] **Step 1: Tests**
  - Creating/updating connection with `base_url=http://127.0.0.1` as admin → 400 (use `client` + admin auth from existing ATS/admin tests; if no admin fixture, insert User role admin like other tests).
  - `push_candidate_status` with `base_url` whose DNS is private: `safe_request_async` / getaddrinfo mocked; **no** httpx connect (keep AUD-004 tests green).

- [ ] **Step 2: FAIL**

- [ ] **Step 3: Implement persist + fetch. Update `test_audit_tenant_boundaries.py` patches from `httpx.AsyncClient` to `app.backend.services.ats_connector.safe_request_async` (or wherever imported) so `assert_not_called` still holds on cross-tenant 404.

- [ ] **Step 4: PASS** including `test_audit_tenant_boundaries.py`

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 7: Inbound ATS webhook fail-closed (AUD-012)

**Files:**
- Modify: `app/backend/services/ats_connector.py` `verify_inbound_webhook`
- Modify: `app/backend/routes/ats.py` `ats_inbound_webhook` (map False → 403)
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Consumes: `hmac.compare_digest` SHA-256 hex of raw body vs `X-ATS-Signature`
- Produces: `verify_inbound_webhook` returns `False` if secret blank **or** signature mismatch; route returns **403** and does not write `ATSSyncLog` or change `ScreeningResult`

```python
def verify_inbound_webhook(self, connection, signature: str, body: bytes) -> bool:
    secret = (connection.webhook_secret or "").strip()
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    provided = (signature or "").strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:]
    return hmac.compare_digest(expected, provided)
```

Route: `if not connector.verify_inbound_webhook(...): raise HTTPException(403, "Invalid webhook signature")` — use this for both missing secret and bad HMAC (user rule: both 403).

- [ ] **Step 1: Tests with `client` + `db`**
  - Active connection, `webhook_secret=""` → POST `/api/ats/webhook/{id}` → 403; no `ATSSyncLog`; screening status unchanged
  - Secret set, wrong header → 403; no writes
  - Secret set, correct HMAC of body → 200
  - Cross-tenant `candidate_id` in payload still must not update (reuse AUD-004 predicate)

- [ ] **Step 2: FAIL** (empty secret currently allowed)

- [ ] **Step 3: Implement. Do not log secret or raw signature.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 8: Local gate (no GitHub until asked)

- [ ] **Step 1: Run**

```
python -m pytest app/backend/tests/test_audit_phase_b_outbound.py app/backend/tests/test_audit_tenant_boundaries.py app/backend/tests/test_video_downloader.py app/backend/tests/test_video_routes.py -q --tb=short
```

Also grep `app/backend/services/jd_scraper.py`, `webhook_service.py`, `ats_connector.py`, `video_downloader.py` for `follow_redirects=True` — must be gone.

- [ ] **Step 2: Fix any regressions in those files only.**

- [ ] **Step 3: Stop. Report results. Wait for the user to say commit/push.**

---

## Spec coverage

| Spec item | Task |
|---|---|
| `validate_public_url` userinfo, DNS, require_https | 1 |
| `safe_request` hops, no private redirect | 2 |
| Webhook HTTPS + DNS + safe POST | 3 |
| JD scraper | 4 |
| Video fetch / stream cap | 5 |
| ATS persist + outbound | 6 |
| Inbound 403 fail-closed | 7 |
| Related pytest + no commit until asked | 8 |
| No allowlist / replay / AUD-015 / first-party httpx | Global constraints |
