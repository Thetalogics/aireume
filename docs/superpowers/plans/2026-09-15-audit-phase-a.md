# Audit Remediation Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close AUD-001–010 and the four guide-document edits from `docs/superpowers/specs/2026-09-15-audit-phase-a-design.md`.

**Architecture:** One helper per invariant in the modular monolith: entitlement resolver, python3-saml + Redis request state, ATS tenant predicates, shared usage reservation, explicit `use_existing` candidate_id, shared HM access/assignment policy.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Redis, python3-saml/xmlsec, pytest, existing Stripe/manual billing providers.

## Global Constraints

- Work on current `main` unless a branch is requested.
- Do not weaken CSRF, JWT, MFA, ClamAV, CORS, rate limits, or tenant JWT binding.
- Tests in the same change as behavior. TDD: failing test first.
- Never log SAML assertions, tokens, provider secrets, or candidate PII.
- `81a7dee` did not close these items; verify current code.

## File map

- Modify: `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md`
- Modify: `app/backend/models/db_models.py` (`Tenant.desired_plan_id`; queue reservation column in Task 6)
- Create: `alembic/versions/072_tenant_desired_plan_id.py`
- Modify: `app/backend/services/plan_entitlement_service.py`, `trial_service.py`, `feature_flag_service.py`
- Modify: `app/backend/routes/onboarding.py`, `subscription.py`, `admin.py`
- Modify: `app/backend/services/sso_service.py`, `routes/sso.py`
- Modify: `app/backend/services/ats_connector.py`
- Modify: `app/backend/routes/analyze.py` (`use_existing`)
- Modify: queue routes/services + usage helper
- Modify: `app/backend/middleware/rbac.py`, requisition/candidate/interview routes
- Test: `app/backend/tests/test_audit_p0_entitlements.py`, `test_audit_sso_security.py`, `test_audit_tenant_boundaries.py`, `test_audit_hm_access.py`, queue quota tests
- Update existing: `test_subscription.py` tenant change-plan, `test_enterprise_suite.py` expire_trials, `test_admin_api.py` if reason required

---

### Task 1: Guide hygiene

**Files:** `docs/ARIA_DEEP_AUDIT_REMEDIATION_GUIDE.md`

- [ ] Unique one-line Why it matters per AUD id
- [ ] Cursor notes only in section 0
- [ ] Depends-on lines; 81a7dee still-open note; AUD-041 path; AUD-049 org vs cd.yml

---

### Task 2: Entitlement resolver (AUD-002, AUD-003)

**Files:** models, alembic 072, plan_entitlement_service, trial_service, feature_flag_service, onboarding, subscription, admin, tests

**Interfaces:**
- `is_paid_plan(plan) -> bool`
- `tenant_has_paid_entitlement(tenant) -> bool` — `active` or `past_due` (dunning) or active `trialing`
- `get_tenant_plan` returns default free plan when stored plan is paid but entitlement is false
- Paid select-plan / tenant change-plan: set `desired_plan_id`, start checkout, do not set paid `plan_id` or `start_trial`
- Recruiter paid select → 403
- `expire_trials`: status `expired`, `plan_id` = default free
- Platform `change-plan` requires `reason`, `log_audit`

- [ ] Write `test_audit_p0_entitlements.py` (named tests from the spec)
- [ ] Run pytest; expect FAIL
- [ ] Implement minimal code
- [ ] Update `test_subscription.py` `test_change_plan_success` and `test_expire_trials_marks_past_due`
- [ ] Re-run new tests + subscription/onboarding/enterprise trial tests; expect PASS

---

### Task 3: SAML (AUD-001)

Replace custom verifier with python3-saml; Redis `{request_id, tenant_id}` TTL 10m; pin IdP cert; replay + InResponseTo. Tests in `test_audit_sso_security.py` with fixtures. Add xmlsec to backend image. Production missing cert → SSO disabled.

---

### Task 4: ATS tenant bind (AUD-004)

`ats_connector` queries include `connection.tenant_id`. Tests mock HTTP; zero outbound on cross-tenant.

---

### Task 5: `use_existing` (AUD-006)

Require `candidate_id`; tenant + match verification; 409 otherwise. Frontend Form field if missing.

---

### Task 6: Queue quota (AUD-005)

Shared reservation helper used by sync analyze and queue submit/complete/fail. Alembic column on analysis jobs.

---

### Task 7: HM access (AUD-007–010)

`scope_candidates_for_user`, `can_read_*`, split requisition write, `get_tenant_user_or_404` on assignment writes.

---

### Exit

All Phase A named tests green; no silent auth/billing fallback.
