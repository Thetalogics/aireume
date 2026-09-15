# Audit Remediation Phase A — Design Spec

**Date:** 2026-09-15  
**Status:** Approved for implementation planning  
**Reference:** `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md` (AUD-001–010)  
**Code baseline:** `main` at `81a7dee9a27ef12784f50fe1895b815b047f97cb`  
**Note:** That commit’s message claims P0–P3 audit fixes. The issues in this spec are **still open** in that tree. Do not skip an item because of the commit message.

## Problem

Identity, billing, tenant isolation, screening quota, candidate selection, and hiring-manager access are enforced inconsistently. A tenant admin can activate paid features without checkout; an expired trial can keep a paid `plan_id`; ATS and queue paths skip tenant or quota rules that sync analyze applies; `use_existing` can attach the wrong candidate; hiring managers can read or mutate beyond assigned requisitions.

## Program context (not this spec’s implementation)

AUD-001–050 plus four guide-document edits are a **phased program**. This spec is **Phase A only**. Later specs: B (outbound trust), C (queue execution identity), D (scoring/AI governance), E (price IDs + multi-workspace identity including AUD-029), F (privacy/compliance), G (scale/CI/docs).

## In scope

| ID | Outcome |
|---|---|
| Guide hygiene | Agent-safe remediation guide (see below) |
| AUD-001 | python3-saml verification; IdP pin; Redis request binding and replay protection |
| AUD-002 | Desired paid plan ≠ entitlement; checkout/webhook activates paid features |
| AUD-003 | Expired unconverted trial falls back to free features and free `plan_id` |
| AUD-004 | ATS service loads always include `connection.tenant_id` |
| AUD-005 | Queue accept/complete/fail uses the same usage reservation as sync analyze |
| AUD-006 | `use_existing` requires an explicit in-tenant candidate; no guessing |
| AUD-007–010 | Shared HM/candidate/requisition access and assignment validators |

## Out of scope

- AUD-011–050
- Full `ScreeningCommand` domain rewrite (quota helper only)
- New payment provider, microservices, Redis replacement
- GitHub branch-protection org settings
- The older `audit-remediation-todos` canvas backlog (different audit)

## Global constraints

- Work on current `main` unless a branch is explicitly requested.
- Do not weaken CSRF, JWT validation, MFA, ClamAV, CORS, rate limits, audit logging, or tenant JWT binding.
- Do not invent public API routes unless a regression cannot be expressed otherwise.
- Tests ship in the same change as the behavior.
- Never log SAML assertions, access tokens, provider secrets, share tokens, or candidate PII in new logs.
- Prefer shared helpers over per-route copies of the same rule.

## Architecture

Keep the modular monolith. Each invariant has one owner:

```
SSO routes ──► sso_service (python3-saml) ──► Redis authn state
Onboarding / subscription ──► entitlement resolver ──► feature flags / require_active_subscription
Queue + analyze ──► usage reservation helper ──► existing usage rows
ATS routes ──► ats_connector (tenant predicates)
Analyze use_existing ──► explicit candidate_id + tenant check
Candidate/interview/comment/requisition routes ──► access policy + assignment validators
```

### AUD-001 — SAML

**Library:** `python3-saml` (`OneLogin_Saml2_Auth`). System package `xmlsec1` (and Python `xmlsec` as required by the library) in the backend image/dev docs.

**Keep:** `GET /api/sso/login/{tenant_slug}`, `POST /api/sso/callback/{tenant_slug}`, metadata URL, existing user provisioning and role mapping from `SSOConfig`.

**Trust:** Only the tenant-configured IdP certificate/metadata. Certificates inside the response are ignored unless they exactly match the configured pin. If production has no IdP cert/metadata, SSO is disabled (login returns 404/403); never skip signature verification.

**Request binding:** On login, persist `{request_id, tenant_id, issued_at, expires_at}` in Redis with TTL 10 minutes. Callback must validate `InResponseTo` against that key for the same tenant, then delete the key. Missing, expired, or already-consumed key → 400. Also enforce signature, issuer, audience, ACS recipient/destination, `NotBefore` / `NotOnOrAfter`.

**Fail closed:** Attacker-signed assertions, replay, wrong audience, and clock-invalid assertions all fail. Valid configured IdP login still issues the existing session cookies/JWT.

### AUD-002 / AUD-003 — Entitlement

**Rule:** Feature gating and paid limits use **effective entitlement**, not “tenant has a paid `plan_id`.”

| State | Effective features |
|---|---|
| Free/starter plan | Free immediately |
| Paid plan selected, no verified checkout/trial | Free only; return/create existing checkout |
| `trialing` and `trial_ends_at` in the future | Paid trial |
| `active` with provider-confirmed subscription | Paid |
| Unconverted trial ended | Free fallback: `expire_trials` sets status `expired` (not `past_due`) and `plan_id` to default free/starter |
| Real paid subscription payment failure | Existing `past_due` dunning unchanged |

**Desired vs effective:** Add `Tenant.desired_plan_id` (nullable FK to `subscription_plans`). Onboarding `select-plan` and tenant plan choice may set `desired_plan_id`. They may set `plan_id` only for free/starter. Paid selection starts checkout/trial intent and returns HTTP 200 with `checkout_url` plus `effective_plan` still free; paid `plan_id` and `active`/`trialing` come only from trusted provider webhooks or platform-admin override. After trial fallback to free, keep `desired_plan_id` so the tenant can resume checkout.

**Tenant `POST /api/subscription/admin/change-plan/{plan_id}`:** Must not grant paid entitlement. Recruiter → 403. Tenant admin → same as select-plan (desired + checkout), not a direct paid write.

**Platform override:** `POST /api/admin/tenants/{tenant_id}/change-plan` remains platform-admin only, requires a reason, writes an audit row (actor, tenant, from/to plan, reason).

**Existing paying customers:** `subscription_status == active` (provider-backed) stays entitled. No mass downgrade.

**Single resolver:** Extend `plan_entitlement_service.get_tenant_plan` / feature helpers so they consult plan **and** subscription/trial state. `feature_flag_service` and `require_active_subscription` must use that resolver; `past_due` from a **trial expiry** must not keep paid features.

### AUD-004 — ATS tenant bind

Every candidate, screening result, and requisition load in `ats_connector.py` filters by resource id **and** `connection.tenant_id`. Cross-tenant ids → 404/403 and **zero** outbound HTTP. Route-level connection checks are not sufficient.

### AUD-005 — Queue quota

Extract `_check_and_increment_usage` into a shared usage reservation helper used by sync analyze and queue.

- **Accept job:** reserve one unit or reject (same error shape as sync quota exhaustion).
- **Success:** reservation is consumed (not a second increment).
- **Cancel / permanent failure:** release reservation.
- **Retry / replay of an already-billed job:** no additional unit.

Store reservation identity on the queue job row (or equivalent FK) so retries can see “already reserved/consumed.” Do not build the full screening command bus.

### AUD-006 — `use_existing`

Client must send the `candidate_id` from the duplicate-match response. Server verifies the candidate belongs to `current_user.tenant_id` and matches the duplicate the server offered (file hash and/or prior match). If unresolved → 409/422. Remove `.first()` and null-email fallbacks. Frontend must pass `candidate_id` in the same change if it does not already.

### AUD-007–010 — Access and assignment

Introduce (or extract into) shared helpers, e.g. in `middleware/rbac.py` and/or a small `candidate_access` module:

- `scope_candidates_for_user(db, user)` — hiring manager: candidates on assigned requisitions only; recruiter/admin/ta_lead: tenant-wide.
- `can_read_candidate` / `can_read_screening_result` / `can_read_interview` — 404/403 if HM cannot see the related candidate. Apply to list, advanced search, detail, exports, interviews, transcripts, comments, scorecards.
- Split `require_requisition_write`: recruiter/ta_lead/admin keep generic requisition update. HM may only perform explicit intake/approval/outcome actions on **assigned** requisitions via a narrow Pydantic model (no scoring weights, recruiter assignment, or generic status/JD rewrite unless already a dedicated HM workflow field).
- `get_tenant_user_or_404(db, tenant_id, user_id)` plus role checks before writes to `primary_hiring_manager_id`, `assigned_recruiter_id`, `opened_on_behalf_of_hm_id`, and `assign_hiring_managers`.

Unassigned resources are absent (403/404), not redacted payloads.

## Guide document edits (first change in Phase A)

In `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md`:

1. Replace the identical “Why it matters” paragraph with one sentence naming the broken invariant per AUD id.
2. Keep Cursor implementation notes once in section 0; delete the per-item copies.
3. Add a short `Depends on` line where sequencing matters (AUD-005 after shared usage helper; AUD-007–010 share the access module).
4. State that `81a7dee` did not close these findings.
5. AUD-041 path is `app/frontend/src/lib/api.ts`.
6. AUD-049: GitHub branch protection is org settings; mutable `staging`/`latest` tags are in `.github/workflows/cd.yml`.

## Data / migrations

- Alembic: `tenants.desired_plan_id` nullable FK to `subscription_plans`.
- Alembic: queue job reservation column (nullable FK or opaque reservation id) as required by the usage helper.
- Expand-and-contract: add columns, write new fields, then stop using `plan_id` alone as the paid switch. No backfill that grants paid access.
- Redis SAML keys: no Postgres table.

## Error handling

| Case | Response |
|---|---|
| Bad SAML (untrusted cert, replay, wrong InResponseTo, expired) | 400 or 403 |
| SSO missing IdP trust material in production | SSO disabled; no unsigned accept |
| Recruiter selects paid plan | 403 |
| Tenant admin selects paid without checkout | 200 with checkout URL; effective features remain free |
| Quota exhausted on queue submit | Same rejection as sync analyze |
| Cross-tenant ATS id | 404/403; HTTP adapter not called |
| Ambiguous `use_existing` | 409/422 |
| HM forbidden requisition field | 403/422; stored row unchanged |

Webhook signature failures remain fail-closed (existing billing behavior).

## Testing

New (or extended) backend files:

- `app/backend/tests/test_audit_sso_security.py`
- `app/backend/tests/test_audit_p0_entitlements.py`
- `app/backend/tests/test_audit_tenant_boundaries.py`
- `app/backend/tests/test_audit_hm_access.py`
- Queue quota cases in `test_audit_queue_consistency.py` or the existing queue test module

Named tests from the remediation guide for AUD-001–010 are required. ATS tests mock the HTTP client. SAML tests use signed fixtures, not a live IdP. Frontend: Vitest/Playwright for plan-select (no paid UI without entitlement) and `use_existing` candidate_id if UI is touched.

Run the new file first, then the affected existing suites (`test_subscription.py`, `test_admin_api.py`, analyze/queue/requisition tests).

**Exit gate:** no Phase A P0 test fails; HM and cross-tenant negative tests are green; existing paying-tenant and free-plan paths still pass.

## Implementation order

1. Guide hygiene  
2. Entitlement resolver + checkout-only paid activation (AUD-002, AUD-003)  
3. SAML library swap (AUD-001)  
4. ATS tenant predicates (AUD-004)  
5. `use_existing` (AUD-006)  
6. Queue quota reservation (AUD-005)  
7. Access policy and assignment validators (AUD-007–010)

## Definition of done (each item)

Use the remediation guide completion record: issue id, root cause, files, migration, before/after behavior, tests, command, result, compatibility, deferred follow-ups. Do not mark complete because the code “looks fixed.”
