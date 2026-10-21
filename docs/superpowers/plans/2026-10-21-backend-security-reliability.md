# Backend Security and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the upload, training, and feature-flag defects with adversarial regression coverage and accurate capability behavior.

**Architecture:** Centralize upload identifier and filename validation at the path boundary, keep training state transitions explicit around HTTP success, and make feature-flag invalidation address both Redis and memory using exact cache keys. Preserve route contracts unless security requires rejection.

**Tech Stack:** Python 3.11, FastAPI, httpx, SQLAlchemy, Redis cache helpers, pytest

**Spec:** `docs/superpowers/specs/2026-10-21-production-readiness-ui-ux-remediation-design.md`

## Global Constraints

- Reject unsafe input before filesystem access.
- Do not expose server paths or provider response bodies to clients.
- Keep tenant ownership checks mandatory for upload continuation, finalization, and cancellation.
- Use exact cache keys; never invalidate by substring matching.
- Do not describe prompt personalization as model fine-tuning.

## Review Focus

- Encoded, mixed-separator, absolute, dot-segment, and prefix-confusion upload identifiers must remain contained.
- A valid-looking upload ID owned by another tenant/user must still be rejected.
- Provider 4xx/5xx, timeout, disconnect, and malformed success responses must end in `failed`, not `completed`.
- Redis failures must fall back safely without preventing local invalidation.
- Tenant `1` invalidation must not evict tenant `11`, and feature `report` must not match `report_builder`.

---

### Task 1: Enforce safe chunk-upload paths

**Files:**
- Modify: `app/backend/routes/upload.py`
- Modify: `app/backend/tests/test_upload_limits.py`
- Create: `app/backend/tests/test_upload_path_security.py`

**Interfaces:**
- Produces: `_validate_upload_id(upload_id: str) -> str`, `_safe_client_filename(filename: str) -> str`, `_contained_path(root: Path, *parts: str) -> Path`.

- [ ] **Step 1: Add failing parameterized tests** for `../`, `..\\`, absolute paths, drive prefixes, URL-encoded traversal after request decoding, separator insertion, empty/oversized IDs, and filenames containing path components.
- [ ] **Step 2: Add failing ownership tests** for later chunk, finalize, and cancel operations using another user's valid upload ID.
- [ ] **Step 3: Run** `python -m pytest app/backend/tests/test_upload_path_security.py -v`; confirm traversal or unsafe-name cases fail before implementation.
- [ ] **Step 4: Implement** strict UUID-format upload IDs (or the exact format emitted by `src/lib/uploadChunked.js`), basename-only display filenames, server-controlled assembled names, and resolved-path containment.
- [ ] **Step 5: Route every filesystem operation** through the validated helpers; remove the current character-stripping sanitizer.
- [ ] **Step 6: Run** upload security, upload limit, and production-hardening tests; expect all to pass.
- [ ] **Step 7: Commit** with `fix: contain chunk upload paths`.

### Task 2: Make training failures durable

**Files:**
- Modify: `app/backend/routes/training.py`
- Modify: `app/backend/tests/test_training_gate.py`

**Interfaces:**
- Consumes: `httpx.Response.raise_for_status()`.
- Produces: `_train_model(...)` transitions a run to `completed` only after a successful provider response.

- [ ] **Step 1: Add failing async tests** for 400, 500, timeout, connect error, and successful 2xx responses; assert final state and sanitized error presence.
- [ ] **Step 2: Run** the focused tests and confirm non-2xx currently completes.
- [ ] **Step 3: Store the response, call `raise_for_status()`,** and retain the existing exception-to-failed transition; bound/sanitize persisted error text.
- [ ] **Step 4: Run** `python -m pytest app/backend/tests/test_training_gate.py -v`.
- [ ] **Step 5: Commit** with `fix: fail training runs on provider errors`.

### Task 3: Invalidate feature flags across cache tiers

**Files:**
- Modify: `app/backend/services/shared_cache.py`
- Modify: `app/backend/services/feature_flag_service.py`
- Modify: `app/backend/tests/test_feature_flags.py`

**Interfaces:**
- Produces: `cache_delete(key: str) -> bool`, `cache_delete_prefix(prefix: str) -> int`, and exact `_matching_cache_keys(tenant_id: int | None, feature_key: str | None) -> set[str]` behavior.

- [ ] **Step 1: Add failing tests** for exact tenant, exact feature, tenant+feature, global invalidation, tenant-prefix confusion, feature substring confusion, and Redis/local failure fallback.
- [ ] **Step 2: Run** the focused tests and confirm Redis entries remain stale.
- [ ] **Step 3: Implement** cache deletion using Redis `SCAN`, never blocking `KEYS`; clear local entries under the lock using parsed tenant/feature components.
- [ ] **Step 4: Run** feature-flag, entitlement, and admin feature-management tests.
- [ ] **Step 5: Commit** with `fix: invalidate distributed feature flag cache`.

### Task 4: Align training capability claims

**Files:**
- Modify: `PRODUCT_SPECIFICATION.md`
- Modify: `README.md`
- Modify: frontend training-related copy located with `rg -n "fine.?tun|training|personalization" app/frontend/src`
- Test: relevant frontend copy/route tests and `app/backend/tests/test_training_gate.py`

**Interfaces:**
- Produces: one capability name: tenant prompt personalization; production default remains disabled unless explicitly enabled.

- [ ] **Step 1: Add or update assertions** for the disabled production response and any surfaced UI label.
- [ ] **Step 2: Replace unsupported fine-tuning claims** with exact implemented behavior, limits, enablement, and failure semantics.
- [ ] **Step 3: Run** focused backend and frontend tests plus repository documentation link checks if present.
- [ ] **Step 4: Commit** with `docs: align training claims with implementation`.

