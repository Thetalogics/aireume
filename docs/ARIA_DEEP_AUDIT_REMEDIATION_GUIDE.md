# ARIA / aireume — Deep Audit Remediation Guide for Cursor

**Repository:** `Thetalogics/aireume`  
**Reference branch:** `main`  
**Reference commit:** `81a7dee9a27ef12784f50fe1895b815b047f97cb`  
**Purpose:** implementation reference for fixing the confirmed deep-audit findings area by area without inventing new product behavior.

---

## 0. How Cursor must use this document

This document is intentionally prescriptive. For every item below:

1. **Verify the referenced file/function exists in the current working tree before editing.**
2. If the current code has moved or already changed, **map the old reference to the new location and preserve the intent**. Do not recreate deleted/obsolete code simply because this guide references it.
3. **Do not redesign unrelated features.** Fix the stated invariant and the directly affected call paths only.
4. **Add or update regression tests in the same change.** A fix is incomplete without a test that would have failed before the fix.
5. **Prefer shared domain/service helpers over repeated route-specific fixes** when the same business rule appears in multiple endpoints.
6. **Do not weaken existing security controls** such as CSRF, JWT validation, MFA, ClamAV, CORS restrictions, object-storage requirements, Sentry PII settings, rate limiting, audit logging, or tenant scoping.
7. **Do not invent new API behavior** unless this guide explicitly specifies the new behavior.
8. When a fix requires a database schema change, add an **Alembic migration**, update ORM models, and include migration tests where appropriate.
9. Keep public API compatibility unless the current behavior itself is unsafe. If a breaking change is required, document it in code comments/changelog and update frontend callers/tests in the same change.
10. For every completed issue, record:
   - files changed;
   - tests added/updated;
   - test command executed;
   - result;
   - any intentionally deferred follow-up.

Cursor implementation notes live only here: inspect callers before signature changes; update backend and frontend together; do not add silent unsafe fallbacks; never log SAML assertions, tokens, or candidate PII; run the narrow test file then the relevant suite.

Commit `81a7dee` did **not** close these findings. Verify current behavior against this guide before skipping an item.

AUD-041 path is `app/frontend/src/lib/api.ts`. AUD-049: GitHub branch protection is org settings; mutable tags are in `.github/workflows/cd.yml`.

### Required completion standard

An issue is considered fixed only when:

- the faulty behavior is removed;
- the required target behavior is implemented;
- the stated regression tests pass;
- existing relevant tests still pass;
- tenant boundaries and authorization are preserved;
- no new silent fallback is introduced;
- audit/logging behavior remains safe;
- the result matches the acceptance criteria in this file.

---

## 1. Audit invariants

The remediation work must converge on these non-negotiable invariants:

### Identity and authorization
- A user can only read or mutate data they are explicitly authorized to access.
- Tenant membership, role, and requisition/candidate assignment rules are applied consistently across every route and service.
- The same email may exist in multiple tenant memberships, so identity flows must not pick an arbitrary membership.

### Screening
- The same screening request must behave the same whether executed synchronously, streamed, batched, or queued.
- Screening identity must include all inputs that can change the result: resume, JD/requisition criteria, scoring configuration, skill overrides, algorithm/version, and tenant.
- Exactly one completed billable screening consumes exactly one entitlement unit.

### Billing
- Tenant plan entitlements must represent verified subscription/trial state.
- Tenant users cannot directly grant themselves paid entitlements.
- Provider price IDs must map deterministically to internal plans.

### Privacy
- Deleting a candidate must remove all required database and object-storage copies.
- Anonymizing a candidate must eliminate PII from retained derived records, not only the canonical candidate row.

### AI governance
- Deterministic score logic must remain separated from LLM narrative generation unless deliberately changed by a future product decision.
- “Accuracy,” “bias,” “compliance,” and “training” labels must describe what the code actually measures or performs.

---

## 2. Priority map

| ID | Severity | Area | Short title |
|---|---|---|---|
| AUD-001 | P0 | SSO | SAML trust anchor can come from untrusted response |
| AUD-002 | P0 | Billing | Onboarding/direct plan assignment can grant paid entitlement |
| AUD-003 | P0 | Billing | Expired paid trial can retain paid-plan access |
| AUD-004 | P0 | ATS | Candidate/result lookup is not tenant-bound inside ATS service |
| AUD-005 | P0 | Screening/Billing | Async queue can bypass analysis quota |
| AUD-006 | P0 | Screening | `use_existing` can select the wrong candidate |
| AUD-007 | P1 | RBAC | Hiring-manager advanced candidate search is over-broad |
| AUD-008 | P1 | RBAC | Results/interviews/comments do not consistently enforce HM assignment |
| AUD-009 | P1 | Requisitions | HM write permission is broader than intended |
| AUD-010 | P1 | Requisitions | Assigned users are not centrally tenant/role validated |
| AUD-011 | P1 | ATS | ATS outbound URLs are an SSRF surface |
| AUD-012 | P1 | ATS | ATS webhook verification fails open without a secret |
| AUD-013 | P1 | JD Import | Redirect chain can bypass initial SSRF validation |
| AUD-014 | P1 | Webhooks | Outbound webhook DNS/private-network validation is incomplete |
| AUD-015 | P1 | Secrets | ATS/integration credentials lack a clear encryption boundary |
| AUD-016 | P1 | Queue | Requisition linkage is dropped in async screening |
| AUD-017 | P1 | Queue | Queue deduplication key ignores result-changing configuration |
| AUD-018 | P1 | Queue | `QUEUE_MAX_CONCURRENT` is configured but not implemented |
| AUD-019 | P1 | Queue | Processing jobs are not heartbeated while running |
| AUD-020 | P1 | Queue | Stale-job recovery runs through duplicate paths |
| AUD-021 | P1 | Queue | DLQ retry does not reliably restore analysis artifact |
| AUD-022 | P1 | API | Idempotency key is not bound to request body |
| AUD-023 | P2 | Scoring | Multiple scoring-weight schemas remain active |
| AUD-024 | P2 | Scoring/AI | Historical hiring outcomes create an unsafe feedback loop |
| AUD-025 | P2 | Scoring/Compliance | Gap/tenure/overqualification signals need governance |
| AUD-026 | P2 | Skills | Tenant custom skills can leak into shared registry |
| AUD-027 | P2 | AI Training | “Fine-tuning” feature is prompt personalization and not wired reliably |
| AUD-028 | P2 | Billing | Internal plan names are not mapped cleanly to provider price IDs |
| AUD-029 | P2 | Identity | OAuth/reset/resend can choose the wrong tenant membership |
| AUD-030 | P2 | GDPR | Candidate hard delete can leave object-storage resumes |
| AUD-031 | P2 | GDPR | Anonymization leaves PII in screening/derived records |
| AUD-032 | P2 | GDPR | Portability export is narrower than its contract |
| AUD-033 | P2 | Compliance | Adverse-action report hard-codes legal/compliance conclusions |
| AUD-034 | P2 | Bias Audit | Bias methodology is mislabeled and errors return “no risk” |
| AUD-035 | P2 | AI Privacy | Resume PII redaction is not wired into active production pipeline |
| AUD-036 | P2 | Accuracy/A-B | Accuracy and experiment service is not production-valid |
| AUD-037 | P2 | Share Links | Bearer tokens/passcodes need stronger access and storage controls |
| AUD-038 | P3 | Candidate Import | CSV import route returns HTTP 501 |
| AUD-039 | P3 | Search | Candidate search will degrade at scale |
| AUD-040 | P3 | Data Model | Candidate PII/profile data is heavily duplicated across projections |
| AUD-041 | P3 | Frontend | `src/lib/api.ts` is an API god module |
| AUD-042 | P3 | Frontend/Auth | CSRF recovery behavior and client assumption are inconsistent |
| AUD-043 | P3 | Analytics | Dashboard has GET-side effects and N+1/in-memory aggregation |
| AUD-044 | P3 | Runtime | Sync SQLAlchemy work runs inside many `async def` routes |
| AUD-045 | P3 | AI Runtime | LLM concurrency controls are process-local |
| AUD-046 | P3 | HTTP | Request-size protection relies too heavily on `Content-Length` |
| AUD-047 | P3 | Observability | Business integrity SLO/alerts are missing |
| AUD-048 | P3 | Testing | Authenticated cross-domain E2E and coverage gates are incomplete |
| AUD-049 | P3 | CI/CD | Branches are unprotected and production promotion uses mutable tags |
| AUD-050 | P3 | Docs | Product/version/capability documentation has drifted |

---

# 3. Detailed remediation items

## AUD-001 — SAML trust anchor can come from the untrusted SAML response

    **Severity:** P0  
    **Area:** SSO / Authentication

    ### Where to fix
    `app/backend/services/sso_service.py` (`_verify_signature`, `process_saml_response`, `generate_saml_request`) and `app/backend/routes/sso.py` (`sso_login`, `sso_callback`).

    ### What is wrong
    The custom SAML implementation is a simplified verifier. `_verify_signature()` prefers a certificate extracted from the incoming XML (`cert_text`) over the tenant-configured IdP certificate. The response therefore participates in choosing the key used to trust itself. The verifier also uses naive XML serialization instead of full XML canonicalization/reference validation. Login request IDs are generated but are not persisted and the callback does not validate `InResponseTo`, so responses are not strongly bound to a login request. When no IdP certificate is configured, signature verification can effectively be skipped.

    ### Why it matters
    If the IdP certificate is taken from the assertion itself, an attacker can mint a trusted SSO login.

    ### Depends on
    None

    ### Required fix
    Do not patch this with more ad-hoc XML code. Replace response verification with a mature SAML implementation (`python3-saml` or `pysaml2`) and keep the existing route contract where possible. Trust must be pinned only to the configured IdP metadata/certificate. Persist a short-lived SAML request state containing request ID, tenant, issue time and expiry (Redis is appropriate). On callback validate signature, issuer, audience, recipient, destination, time conditions, `InResponseTo`, assertion/response IDs and one-time replay state. Remove trust in certificates embedded by the incoming response unless they are explicitly matched to configured trusted metadata.

    ### How it should behave
    A SAML response is accepted only when it is cryptographically signed by the tenant's configured IdP, belongs to the expected tenant/SP, is valid for the current time, corresponds to an outstanding login request, and has not already been consumed.

    ### Required regression tests
    - `test_sso_rejects_response_signed_by_unconfigured_certificate`: attacker-generated cert/signature must return 400/403.
- `test_sso_accepts_response_signed_by_configured_idp`: known valid fixture must authenticate.
- `test_sso_rejects_replayed_response`: same SAML response submitted twice; second request must fail.
- `test_sso_rejects_wrong_in_response_to`: validly signed response for another request must fail.
- `test_sso_rejects_wrong_audience_or_recipient`: must fail.
- `test_sso_rejects_expired_and_not_yet_valid_assertions`: must fail.
- `test_sso_requires_trusted_signing_configuration_in_production`: startup/configuration should fail or SSO remain disabled when required trust material is missing.

    ### Expected result
    No attacker-controlled certificate can establish SSO trust. Valid configured IdP login still succeeds and existing user provisioning/role mapping continues to work.

    ### Acceptance criteria
    Custom naïve signature verification is no longer the security boundary; replay and request binding are enforced; all adversarial SAML tests pass.


---

## AUD-002 — Onboarding/direct plan assignment can grant paid entitlement without payment

    **Severity:** P0  
    **Area:** Billing / Entitlements

    ### Where to fix
    `app/backend/routes/onboarding.py` plan-selection endpoint; `app/backend/routes/subscription.py` `/admin/change-plan/{plan_id}`; `app/backend/services/plan_entitlement_service.py`; frontend callers such as `app/frontend/src/lib/api.ts`.

    ### What is wrong
    Plan selection can write `tenant.plan_id` directly. Feature gating treats that plan as the entitlement source. The onboarding path does not reliably enforce “only during onboarding” and is not limited tightly enough to the tenant admin/payment workflow. `/api/subscription/admin/change-plan/{plan_id}` is callable by a tenant-level admin and directly changes subscription/plan state. A tenant user must never be able to create paid entitlement simply by invoking an internal-looking route.

    ### Why it matters
    Tenant users must not activate paid features by writing plan_id; paid access requires verified checkout or trial.

    ### Depends on
    None

    ### Required fix
    Separate **desired plan selection** from **effective entitlement**. For free plans, assignment can be immediate. For paid plans, create checkout/trial intent and only set effective paid plan after trusted provider confirmation or an explicit platform-admin support action. Remove the tenant-accessible direct paid-plan mutation endpoint or change it to require platform-super-admin authorization. If support needs manual plan overrides, create an explicit audited platform operation with reason and actor. Ensure onboarding completion cannot be used to bypass checkout.

    ### How it should behave
    Tenant users may choose a plan, but paid entitlements become active only through verified payment/trial state. Only platform administrators can perform a manual entitlement override.

    ### Required regression tests
    - `test_recruiter_cannot_select_paid_plan_for_tenant` -> 403.
- `test_tenant_admin_plan_selection_does_not_activate_paid_entitlement_without_checkout`.
- `test_direct_subscription_change_requires_platform_admin`.
- `test_platform_admin_manual_override_is_audited`.
- `test_free_plan_selection_remains_functional`.
- `test_paid_feature_gate_denies_when desired plan is paid but provider/subscription state is not active/trialing`.

    ### Expected result
    Calling onboarding or subscription mutation endpoints cannot grant Pro/Growth/Enterprise features without a valid entitlement source.

    ### Acceptance criteria
    All direct tenant-paid-plan mutation paths are removed or platform-only; entitlements use verified state.


---

## AUD-003 — Expired paid trials can retain paid-plan features

    **Severity:** P0  
    **Area:** Billing / Trials

    ### Where to fix
    `app/backend/services/trial_service.py` (`expire_trials`, `start_trial`), subscription middleware such as `require_active_subscription`, and `app/backend/services/plan_entitlement_service.py`.

    ### What is wrong
    Trial start assigns the paid `plan_id`. Trial expiry only changes `subscription_status` to `past_due` while leaving the paid plan in place. Existing subscription guards permit states that can include `past_due`, and feature resolution is largely plan-based. This can leave an expired trial with paid entitlements.

    ### Why it matters
    An expired unconverted trial must not keep paid-plan feature flags after the trial window ends.

    ### Depends on
    AUD-002

    ### Required fix
    Define one subscription state machine. At minimum: `trialing -> active` after successful conversion; `trialing -> expired/read_only` (or free-plan fallback) when trial ends without payment; `active -> past_due -> suspended` for payment failure. Entitlement checks must evaluate both plan and state. If the product decision is to fall back to Free/Starter after trial, update `plan_id` transactionally on expiry. If the decision is read-only lockout, preserve the plan for display but entitlement resolver must deny paid features. Pick one behavior and encode it once.

    ### How it should behave
    After `trial_ends_at`, a tenant that did not convert cannot continue using paid functionality.

    ### Required regression tests
    - `test_active_trial_has_paid_features_before_expiry`.
- `test_expired_unconverted_trial_loses_paid_features_immediately`.
- `test_trial_conversion_to_paid_remains_active_after_original_trial_end`.
- `test_expire_trials_is_idempotent`.
- `test_trial_expiry_does_not_start_normal dunning unless a real payment subscription exists`.

    ### Expected result
    Expired trial receives the agreed fallback/read-only behavior; no paid API succeeds merely because the old paid `plan_id` remains stored.

    ### Acceptance criteria
    Entitlement resolver has explicit trial-expiry tests and cannot return paid features for an expired unconverted trial.


---

## AUD-004 — ATS candidate/result lookup is not tenant-bound inside service layer

    **Severity:** P0  
    **Area:** ATS / Multi-tenancy

    ### Where to fix
    `app/backend/services/ats_connector.py`, especially candidate/status push paths; corresponding ATS routes under `app/backend/routes/`.

    ### What is wrong
    The route validates that the ATS connection belongs to the current tenant, but service-level candidate/result lookups can use only a candidate/result ID. That means a connection from Tenant A can be combined with an ID belonging to Tenant B if a caller reaches that service path. Candidate PII can then be sent to the external ATS.

    ### Why it matters
    ATS connectors must not fetch or push another tenant's candidate or screening result.

    ### Depends on
    None

    ### Required fix
    Every service method that accepts a tenant-owned resource ID must also accept/derive the tenant ID and include it in the database predicate. For ATS operations, the authoritative tenant is `connection.tenant_id`. Query candidates/results/requisitions with both resource ID and that tenant ID. Add helper methods if necessary, e.g. `_get_candidate_for_connection(db, connection, candidate_id)`. Never rely on the route having checked a different object.

    ### How it should behave
    No ATS operation can read or transmit a candidate/result/requisition outside the ATS connection's tenant.

    ### Required regression tests
    - `test_tenant_a_ats_connection_cannot_push_tenant_b_candidate` -> 404/403 and no outbound request.
- `test_tenant_a_ats_connection_cannot_read_tenant_b_screening_result`.
- `test_valid_same_tenant_push_still_sends expected payload`.
- Mock the HTTP adapter and assert it is not called on cross-tenant input.

    ### Expected result
    Cross-tenant IDs are treated as not found/unauthorized and zero external data is transmitted.

    ### Acceptance criteria
    Tenant predicate exists in the service layer, not only the route.


---

## AUD-005 — Async queue path can bypass analysis quota

    **Severity:** P0  
    **Area:** Screening / Billing

    ### Where to fix
    `app/backend/routes/queue_api.py`; `app/backend/services/queue_manager.py`; `app/backend/services/queue_analysis_service.py`; shared usage functions in `app/backend/routes/subscription.py` or future centralized usage service.

    ### What is wrong
    Synchronous analysis records/enforces usage, while queue submission/completion does not consistently reserve or increment the same quota. A tenant can therefore obtain completed screenings through a path that is not reflected in monthly analysis limits.

    ### Why it matters
    Each new queued analysis must consume exactly one quota unit, and cancelled/failed jobs must release that reservation.

    ### Depends on
    AUD-002

    ### Required fix
    Move billing/usage enforcement out of the synchronous HTTP route and into a shared screening execution/entitlement service. Preferred model: reserve one analysis unit when a new billable job is accepted, store the reservation/job relation, and finalize consumption on successful completion. Release the reservation on permanent failure/cancellation. If the product instead bills on completion, use a transaction-safe atomic quota check/increment at completion and prevent overcommit; reservation is safer. Deduplicated replays of the same already-billed job must not consume again.

    ### How it should behave
    Every distinct completed screening consumes exactly one quota unit regardless of sync/stream/batch/queue entry point.

    ### Required regression tests
    - `test_queue_submission_rejected_when_quota_exhausted`.
- `test_queue_job_success_consumes_one_unit`.
- `test_queue_retry_does_not_consume_multiple_units`.
- `test_deduplicated_existing_completed_job_does_not_double_charge`.
- `test_permanent_failed_job_releases_reservation` if reservation model is used.
- `test_sync_and_queue_share_same quota rules`.

    ### Expected result
    Queue cannot produce unpaid/unmetered analyses and retries cannot double-charge.

    ### Acceptance criteria
    A single central usage path is used by all screening execution modes.


---

## AUD-006 — `use_existing` duplicate flow can analyze the wrong candidate

    **Severity:** P0  
    **Area:** Screening / Candidate Integrity

    ### Where to fix
    Single resume analysis route/helpers in `app/backend/routes/analyze*.py` / `analyze_helpers.py` where `action == "use_existing"` is resolved.

    ### What is wrong
    When an uploaded file hash does not find the expected candidate, the duplicate flow can fall back to an unrelated tenant candidate whose email is null. That can load a different person's stored resume/profile and score it against the requested JD. This is a hiring-decision data-integrity failure.

    ### Why it matters
    use_existing must bind to an explicit tenant-scoped candidate_id so a hash collision cannot attach the wrong profile.

    ### Depends on
    None

    ### Required fix
    Never infer the candidate for `use_existing`. The client must supply an explicit candidate ID produced by the duplicate-match response, or a short-lived signed/server-side duplicate token that maps to exactly one candidate. Verify that candidate belongs to the tenant and that it is the same duplicate the server previously offered. If the candidate cannot be resolved exactly, return 409/422 and require user choice. Remove any fallback to `.first()` or null-email candidates.

    ### How it should behave
    `use_existing` either uses the exact user-selected duplicate candidate or fails. It can never guess.

    ### Required regression tests
    - `test_use_existing_requires_explicit_duplicate_candidate_when_hash_not_resolved`.
- `test_use_existing_candidate_must_belong_to_current_tenant`.
- `test_use_existing_does_not_select_first_null_email_candidate`.
- `test_use_existing_selected_candidate_scores_selected_resume_only`.
- `test_duplicate_token_is_single-candidate and expires` if token design is used.

    ### Expected result
    No report can be produced from another candidate's stored profile through duplicate fallback.

    ### Acceptance criteria
    All guessing/fallback candidate selection is removed.


---

## AUD-007 — Hiring-manager advanced candidate search ignores assigned-requisition scope

    **Severity:** P1  
    **Area:** RBAC / Candidates

    ### Where to fix
    `app/backend/routes/candidates.py` advanced `/candidates/search` path and existing normal candidate-list/detail HM filtering logic.

    ### What is wrong
    Normal candidate list/detail logic restricts hiring managers to candidates attached to requisitions they are assigned to. Advanced search applies tenant scope but not the same assignment rule, allowing a hiring manager to enumerate candidates elsewhere in the tenant.

    ### Why it matters
    Hiring managers must only search candidates on requisitions they are assigned to.

    ### Depends on
    None

    ### Required fix
    Extract the existing HM candidate-scope query into one reusable policy/query helper. Apply it to normal list, advanced search, candidate detail, exports and any autocomplete endpoints. Do not duplicate different SQL fragments in each endpoint.

    ### How it should behave
    A hiring manager sees the same candidate universe from every candidate endpoint.

    ### Required regression tests
    - `test_hm_search_only_returns_candidates_from_assigned_requisitions`.
- `test_hm_search_cannot_find_unassigned_candidate_by_exact_email_or_name`.
- `test_recruiter_search_remains_tenant-wide`.
- `test_admin_search_remains_tenant-wide`.

    ### Expected result
    Unassigned candidates are absent, not partially redacted.

    ### Acceptance criteria
    All candidate-query endpoints call a shared access-scope helper.


---

## AUD-008 — Result, interview, transcript and comment access is only tenant-scoped in some endpoints

    **Severity:** P1  
    **Area:** RBAC / Screening / Interviews

    ### Where to fix
    `app/backend/routes/candidates.py` screening-result retrieval; `app/backend/routes/interviews.py`; `app/backend/routes/team.py` comments; any result/transcript export routes.

    ### What is wrong
    Hiring-manager restrictions are not consistently propagated from candidate access into screening results, interview sessions, transcripts and comments. A tenant-level check alone is insufficient because an HM should generally see only candidates/requisitions assigned to them.

    ### Why it matters
    Interview sessions, transcripts, scorecards, and result comments must use the same HM assignment rule as candidate reads.

    ### Depends on
    AUD-007

    ### Required fix
    Introduce shared policies such as `can_read_candidate`, `scope_candidates_for_user`, `can_read_screening_result`, and `can_read_interview_session`. For HM users, resolve the candidate/requisition relation and require assignment. For recruiters/admins preserve current broader tenant access. Apply the policy before returning comments, transcripts, scorecards, exports or result payloads.

    ### How it should behave
    If an HM cannot open a candidate, the HM cannot obtain that candidate's result, comments, interview transcript or scorecard by direct ID.

    ### Required regression tests
    - Direct-ID tests for unassigned result, interview session, transcript, scorecard and comments -> 404/403.
- Assigned HM can access the same resources.
- Recruiter/admin access remains unchanged.

    ### Expected result
    No endpoint leaks an unassigned candidate through an alternate resource ID.

    ### Acceptance criteria
    Resource-specific routes use the central access policy.


---

## AUD-009 — Hiring-manager write permission grants generic requisition mutation

    **Severity:** P1  
    **Area:** Requisition RBAC

    ### Where to fix
    Requisition routes/middleware containing `require_requisition_write` and generic requisition update endpoints.

    ### What is wrong
    Assigned HMs need limited participation in intake/approval, but the same write permission is reused for broad requisition updates. That can expose title, JD, status, scoring weights, skills, routing or other recruiter-owned configuration to HM mutation.

    ### Why it matters
    Assigned hiring managers may update intake, not generic requisition fields such as JD, status, or scoring weights.

    ### Depends on
    AUD-007

    ### Required fix
    Split permissions by action. Keep recruiter/TA/admin permission for generic requisition mutation. Create HM-specific operations for intake answers, approval/sign-off, outcome or notes according to current product workflows. Do not expose recruiter configuration fields through the HM schema. Use narrow Pydantic models per action rather than a single catch-all update model.

    ### How it should behave
    An HM can perform only the explicit HM workflow actions on assigned requisitions.

    ### Required regression tests
    - `test_hm_cannot_change_scoring_weights`.
- `test_hm_cannot_change_assigned_recruiter_or_status_unless explicitly allowed`.
- `test_hm_can_update_allowed_intake_fields_on_assigned_requisition`.
- `test_unassigned_hm_cannot mutate anything`.
- `test_recruiter_can_use generic update`.

    ### Expected result
    Forbidden fields remain unchanged and request returns 403/422.

    ### Acceptance criteria
    HM and recruiter update schemas/permissions are separate.


---

## AUD-010 — Assigned hiring-manager/recruiter user IDs are not centrally tenant/role validated

    **Severity:** P1  
    **Area:** Requisition Data Integrity

    ### Where to fix
    Requisition service/route functions such as `assign_hiring_managers` and fields `primary_hiring_manager_id`, `assigned_recruiter_id`, `opened_on_behalf_of_hm_id`.

    ### What is wrong
    Database foreign keys guarantee that a user ID exists, but not that the user belongs to the same tenant or has an appropriate role. Cross-tenant user IDs can therefore be persisted if not checked in each caller.

    ### Why it matters
    Recruiter and hiring-manager assignment IDs must resolve to active users in the same tenant with allowed roles.

    ### Depends on
    AUD-007

    ### Required fix
    Create reusable validators: `get_tenant_user_or_404(db, tenant_id, user_id)` and role-aware variants. Validate every user reference before write. HM assignments should require permitted HM roles; recruiter assignment should require recruiter/TA/admin roles according to current role model. Add database-level defense where practical, but tenant equality usually requires application logic.

    ### How it should behave
    Every user reference stored on a tenant-owned record points to a valid member of that same tenant with an appropriate role.

    ### Required regression tests
    - Assign user from another tenant -> 422/404 and no row created.
- Assign inactive user -> reject if current policy requires active membership.
- Assign wrong role -> reject.
- Valid same-tenant assignments remain successful.

    ### Expected result
    Cross-tenant FK values cannot be created through any requisition endpoint.

    ### Acceptance criteria
    All assignment code uses one tenant-member validator.


---

## AUD-011 — ATS base URLs and callback URLs can target internal network resources

    **Severity:** P1  
    **Area:** ATS / SSRF

    ### Where to fix
    `app/backend/services/ats_connector.py` and ATS connection creation/update routes. Reuse the stronger URL/DNS validation already present elsewhere in the codebase.

    ### What is wrong
    Tenant-controlled ATS URLs are used for server-side HTTP requests without a single centralized outbound-network policy. Literal or DNS-resolved private addresses, cloud metadata, loopback or internal services can become reachable.

    ### Why it matters
    ATS outbound URLs must not be usable as an SSRF trampoline to internal networks.

    ### Depends on
    AUD-004

    ### Required fix
    Before persisting and again before connecting, validate scheme/hostname and resolve DNS. Reject private, loopback, link-local, multicast, reserved and metadata destinations. Revalidate redirects rather than automatically following them. Consider an allowlist for supported commercial ATS adapters. Route all integration HTTP through a shared `SafeOutboundHttpClient`.

    ### How it should behave
    Tenant-configured integration URLs can reach only permitted public destinations.

    ### Required regression tests
    - Reject `localhost`, `127.0.0.1`, `::1`, RFC1918 addresses, link-local and metadata addresses.
- Reject hostname resolving to private IP.
- Reject public URL redirecting to private IP.
- Allow valid public HTTPS ATS URL.
- Revalidation must happen at delivery time to reduce DNS-rebinding risk.

    ### Expected result
    No outbound socket is opened to rejected destinations.

    ### Acceptance criteria
    ATS no longer performs raw tenant-controlled outbound HTTP without centralized validation.


---

## AUD-012 — ATS inbound webhook verification fails open when no secret is configured

    **Severity:** P1  
    **Area:** ATS / Webhook Authentication

    ### Where to fix
    ATS webhook verification in `app/backend/services/ats_connector.py` and its public webhook route.

    ### What is wrong
    When the connection has no webhook secret, verification can return success instead of rejecting the request. This creates an unauthenticated state-changing webhook.

    ### Why it matters
    ATS webhooks must fail closed when the shared secret is missing rather than accepting unsigned callbacks.

    ### Depends on
    AUD-004

    ### Required fix
    Production must fail closed. If an ATS provider uses signatures, require configured secret/cert and validate it. If a supported provider authenticates differently, implement that explicit adapter. If no authentication mechanism is configured, mark webhook ingestion disabled and return 403/503 rather than accepting the event.

    ### How it should behave
    Every inbound ATS event has a trusted authentication mechanism.

    ### Required regression tests
    - No secret/auth config -> webhook request rejected.
- Wrong signature -> rejected.
- Correct signature -> accepted.
- Replay protection if provider supplies event IDs/timestamps.
- Ensure rejected webhook does not mutate candidate/pipeline state.

    ### Expected result
    Unauthenticated ATS webhooks cannot change application data.

    ### Acceptance criteria
    There is no `if no secret: return True` behavior in production.


---

## AUD-013 — Redirects can bypass initial JD URL safety validation

    **Severity:** P1  
    **Area:** JD Import / SSRF

    ### Where to fix
    JD URL import/scraper service where the initial URL is validated and `httpx` uses `follow_redirects=True`.

    ### What is wrong
    The first URL can be public and safe while returning a redirect to an internal/private host. Automatic redirect following bypasses the initial safety check.

    ### Why it matters
    JD import redirects must not skip the original SSRF allowlist.

    ### Depends on
    AUD-011

    ### Required fix
    Disable automatic redirects. Follow a small bounded number manually. For every `Location`, normalize and run the same public-host/DNS validation before the next request. Reject protocol changes to unsupported schemes and credentials in URLs.

    ### How it should behave
    Every network hop in the redirect chain is independently validated.

    ### Required regression tests
    - Public URL -> private redirect is rejected.
- Public -> public redirect succeeds.
- Redirect loop/excessive chain rejected.
- Redirect to non-HTTP(S) scheme rejected.

    ### Expected result
    No internal request is sent after a malicious redirect.

    ### Acceptance criteria
    No code path uses unrestricted `follow_redirects=True` for tenant/user supplied URLs.


---

## AUD-014 — Webhook URL validation does not fully protect against DNS/private destinations

    **Severity:** P1  
    **Area:** Outbound Webhooks / SSRF

    ### Where to fix
    `app/backend/services/webhook_service.py` (`validate_webhook_url`, `_send_webhook`).

    ### What is wrong
    Validation rejects obvious localhost/literal private IPs but a hostname is accepted without resolving and checking the resulting addresses. Delivery also does not revalidate the destination, leaving DNS rebinding/private-resolution risk.

    ### Why it matters
    Outbound webhooks must re-validate DNS and block private destinations after redirect resolution.

    ### Depends on
    AUD-011

    ### Required fix
    Use the same centralized outbound HTTP safety component as ATS/JD import. Require HTTPS, resolve A/AAAA records, reject unsafe address classes, validate redirects, apply strict connect/read timeouts and response size limits, and revalidate immediately before connection.

    ### How it should behave
    User-configured webhooks can only reach approved public destinations.

    ### Required regression tests
    - Hostname -> `127.0.0.1`/private IP rejected.
- Literal private IPv4/IPv6 rejected.
- Safe public HTTPS URL accepted.
- Redirect to unsafe address rejected.
- Failed validation creates no delivery attempt.

    ### Expected result
    No SSRF through webhook hostname resolution.

    ### Acceptance criteria
    Webhook sender and validator share one outbound network policy.


---

## AUD-015 — ATS/integration credentials need an application encryption boundary

    **Severity:** P1  
    **Area:** Secrets / Integrations

    ### Where to fix
    ATS connection models/schemas/routes and any other integration secret fields.

    ### What is wrong
    Credential fields are persisted through ordinary model assignments and the audit did not identify a consistent encrypt/decrypt service in the ATS path. Database compromise should not immediately expose all third-party tokens/secrets.

    ### Why it matters
    ATS and integration credentials need a single encryption boundary so plaintext secrets are not scattered.

    ### Depends on
    None

    ### Required fix
    Introduce an integration-secret service using envelope encryption. Preferred: KMS/Vault-managed key; acceptable interim: strong application master key outside the DB with authenticated encryption (AES-GCM/Fernet-style, rotation-aware). Store ciphertext + key version. Decrypt only at outbound call time. Never return secret values from serializers after creation; return only `configured: true` / masked metadata. Add rotation support.

    ### How it should behave
    Database rows do not contain plaintext third-party credentials.

    ### Required regression tests
    - Create connection -> stored DB value differs from plaintext.
- Read connection API never returns plaintext secret.
- Outbound adapter receives decrypted value.
- Wrong/rotated key handling is explicit and logged without secret leakage.
- Logs/errors must not include the secret.

    ### Expected result
    Secrets remain usable but are encrypted at rest at application level.

    ### Acceptance criteria
    All integration secret persistence goes through one encryption abstraction.


---

## AUD-016 — Requisition linkage is dropped from queue job to completed result

    **Severity:** P1  
    **Area:** Async Screening

    ### Where to fix
    `app/backend/routes/queue_api.py`, helper preparing queue job configuration, `app/backend/services/queue_manager.py`, `app/backend/services/queue_analysis_service.py`.

    ### What is wrong
    Queue submission accepts/validates `requisition_id`, but the full requisition context is not consistently persisted into job configuration/result creation. The completed screening can therefore be unlinked from the requisition that required it.

    ### Why it matters
    Queued screening must keep requisition linkage or scores will be computed against the wrong JD.

    ### Depends on
    AUD-005

    ### Required fix
    Define a versioned `ScreeningCommand`/job payload containing `requisition_id`, resolved role/template IDs, criteria version, scoring config hash, skill overrides and user/tenant context. Persist that payload on the job. Completion must write `ScreeningResult.requisition_id` and update/create the corresponding `RequisitionCandidate` linkage exactly like the synchronous path.

    ### How it should behave
    Queued and synchronous screening create equivalent domain records.

    ### Required regression tests
    - Queue screening for requisition -> completed `ScreeningResult.requisition_id` equals submitted requisition.
- `RequisitionCandidate.screening_result_id` links to result where current sync behavior does so.
- Invalid/cross-tenant requisition is rejected before enqueue.
- Retry preserves same requisition context.

    ### Expected result
    Analytics/pipeline immediately see queued screenings under the correct requisition.

    ### Acceptance criteria
    No queue completion path can lose requisition identity.


---

## AUD-017 — Queue deduplication ignores configuration that changes the result

    **Severity:** P1  
    **Area:** Queue Deduplication

    ### Where to fix
    `app/backend/services/queue_manager.py` (`compute_hash`, `enqueue_job`).

    ### What is wrong
    Current input hash is effectively tenant + resume hash + JD hash. It ignores requisition criteria version, scoring weights, required/nice-to-have overrides, screening algorithm/version and other job configuration. A completed old job can be returned even when the user deliberately changed scoring criteria.

    ### Why it matters
    Queue dedupe keys must include every input that can change the score, including weights and skill overrides.

    ### Depends on
    AUD-005

    ### Required fix
    Create a canonical `analysis_fingerprint` from every deterministic input that can alter the screening result. Serialize configuration with sorted keys and stable numeric normalization, then hash: tenant, resume content/version, JD text or requisition criteria version, normalized scoring weights, overrides, relevant feature/algorithm version. Persist the fingerprint version so hash rules can evolve.

    ### How it should behave
    Only semantically identical screening requests deduplicate.

    ### Required regression tests
    - Same resume/JD/config -> same job can deduplicate.
- Change scoring weights -> new job.
- Change requisition criteria version -> new job.
- Change skill override -> new job.
- Change only irrelevant transport metadata -> may still deduplicate.

    ### Expected result
    Recalibration always produces a fresh screening result.

    ### Acceptance criteria
    Dedup hash includes a stable canonical analysis configuration.


---

## AUD-018 — `QUEUE_MAX_CONCURRENT` is read but worker executes serially

    **Severity:** P1  
    **Area:** Queue Worker

    ### Where to fix
    `app/backend/services/queue_manager.py` (`__init__`, `worker_loop`).

    ### What is wrong
    The service advertises/configures max concurrency but awaits one `process_job` before claiming another. Operational tuning therefore does not correspond to actual throughput.

    ### Why it matters
    QUEUE_MAX_CONCURRENT must actually limit in-flight workers or the queue can overrun the host.

    ### Depends on
    AUD-005

    ### Required fix
    Either remove the setting and explicitly support one job per worker, or implement bounded concurrency. Preferred: use an `asyncio.Semaphore`, create task(s) up to configured limit, each with its own DB session, and preserve `SELECT ... FOR UPDATE SKIP LOCKED` claiming. Ensure graceful shutdown waits/cancels safely and heartbeats remain per-job. Do not share SQLAlchemy sessions across concurrent tasks.

    ### How it should behave
    Configured concurrency has a real, testable meaning.

    ### Required regression tests
    - With concurrency=2, two slow jobs overlap in execution.
- Never exceed configured concurrency.
- Each job uses separate DB session/transaction.
- Graceful shutdown does not abandon claimed jobs silently.

    ### Expected result
    Throughput scales predictably or configuration is simplified to truthful serial behavior.

    ### Acceptance criteria
    No unused “max concurrent” production knob.


---

## AUD-019 — Long-running jobs are not heartbeated while processing

    **Severity:** P1  
    **Area:** Queue Leases

    ### Where to fix
    `app/backend/services/queue_manager.py` (`get_next_job`, `update_heartbeat`, `process_job`, `worker_loop`).

    ### What is wrong
    A job receives a heartbeat/lease when claimed, but `process_job` does not periodically renew it. Another worker/recovery pass can mark a legitimate long job stale and run it again.

    ### Why it matters
    Long-running jobs must heartbeat so a dead worker is recovered instead of blocking the lease forever.

    ### Depends on
    AUD-005

    ### Required fix
    Start a heartbeat coroutine for each processing job. Update heartbeat and `leased_until` at an interval significantly below stale timeout (for example every 20–30s for a 10-minute lease). Stop it in `finally`. Stale recovery should compare both heartbeat and lease consistently. Make result persistence idempotent as a final defense.

    ### How it should behave
    An actively processing job never becomes stale solely because analysis is slow.

    ### Required regression tests
    - Simulate job longer than stale threshold while heartbeat runs -> recovery does not reclaim.
- Stop heartbeat -> recovery requeues after timeout.
- Heartbeat stops when job finishes/fails.
- Two workers cannot create two active results for same job.

    ### Expected result
    No duplicate long-running analysis caused by lease expiry.

    ### Acceptance criteria
    Heartbeat is active for the full processing lifetime.


---

## AUD-020 — Stale-job recovery runs both inside queue loop and APScheduler

    **Severity:** P1  
    **Area:** Queue Recovery

    ### Where to fix
    `app/backend/services/queue_manager.py` worker loop and `app/backend/services/scheduler.py` `recover_stale_jobs`/`start_scheduler`.

    ### What is wrong
    Both paths run approximately every five minutes in the dedicated worker process. Two recovery passes can race on the same stale job and double-increment retry state or produce confusing transitions.

    ### Why it matters
    Stale-job recovery must have one owner path so two recoveries cannot double-process the same job.

    ### Depends on
    AUD-005

    ### Required fix
    Choose exactly one recovery owner. Recommended: keep stale recovery in APScheduler/worker scheduler and remove the internal periodic call from `worker_loop`, or vice versa. Add row locking/conditional update so even a future second scheduler instance cannot transition the same job twice.

    ### How it should behave
    One stale event creates one state transition and one retry increment.

    ### Required regression tests
    - Run recovery twice concurrently against one stale row -> retry count increments once.
- Only one configured scheduler path invokes recovery.
- Non-stale jobs untouched.

    ### Expected result
    Deterministic stale-job state machine.

    ### Acceptance criteria
    No duplicate 5-minute recovery mechanisms remain.


---

## AUD-021 — DLQ retry does not reliably restore the original analysis artifact

    **Severity:** P1  
    **Area:** Dead Letter Queue

    ### Where to fix
    `app/backend/services/queue_manager.py` (`move_to_dead_letter`, `retry_dead_letter_job`, `process_job`), queue ORM models.

    ### What is wrong
    `process_job` requires an `AnalysisArtifact`. The DLQ row/retry construction primarily copies hashes/config but does not clearly preserve/re-attach the artifact ID/payload. A retried DLQ job can therefore fail because the original resume/JD artifact is unavailable.

    ### Why it matters
    DLQ retry must restore the analysis artifact or retried jobs will fail closed with missing resume text.

    ### Depends on
    AUD-005

    ### Required fix
    Persist immutable retryable input identity in the DLQ: preferably `artifact_id` with retention extended while DLQ is pending, or a versioned copy/reference of the payload. `retry_dead_letter_job` must attach a valid artifact and validate it exists before creating the new queued job. Artifact cleanup must not delete payloads still referenced by pending DLQ jobs.

    ### How it should behave
    A DLQ job can be replayed with exactly the same original inputs.

    ### Required regression tests
    - Fail job -> move to DLQ -> retry -> job completes using original resume/JD.
- Retry when artifact missing -> explicit non-destructive error; DLQ remains pending/error, not falsely reprocessed.
- Artifact retention skips pending DLQ references.
- Reprocessed link is written only after new job is successfully created.

    ### Expected result
    DLQ is a real replay mechanism, not only a failure archive.

    ### Acceptance criteria
    End-to-end DLQ retry test passes.


---

## AUD-022 — Idempotency key is not bound to the request payload

    **Severity:** P1  
    **Area:** API Idempotency

    ### Where to fix
    `app/backend/middleware/idempotency.py`; `IdempotencyKey` ORM model.

    ### What is wrong
    The lookup identity is key + tenant + endpoint. Request body/query semantics are not hashed. Reusing the same key for a different payload returns the old successful response with `X-Idempotent-Replay`, hiding client misuse and potentially replaying the wrong operation.

    ### Why it matters
    Idempotency keys must be bound to the request body so a reused key cannot apply a different payload.

    ### Depends on
    None

    ### Required fix
    Compute a canonical request fingerprint before calling the endpoint. Include method, normalized path, relevant query parameters, content type and request body bytes/canonical JSON. Persist the fingerprint with the idempotency record. On lookup: same key + same fingerprint -> replay; same key + different fingerprint -> return `409 Idempotency key reused with different request` (or equivalent stable error). Ensure reading/replaying request body is compatible with Starlette middleware and upload/stream exceptions are handled deliberately.

    ### How it should behave
    An idempotency key identifies one semantic mutation, not just one endpoint.

    ### Required regression tests
    - Same key + same JSON body -> replay old response.
- Same key + different JSON body -> 409, no endpoint execution.
- Same key in different tenant -> independent.
- Same key on different endpoint -> independent if current design retains endpoint in identity.
- Expired key can be reused according to TTL policy.

    ### Expected result
    Clients cannot accidentally receive a successful response for a different mutation.

    ### Acceptance criteria
    `IdempotencyKey` stores/validates request fingerprint.


---

## AUD-023 — Scoring weights are persisted in multiple schemas

    **Severity:** P2  
    **Area:** Scoring

    ### Where to fix
    `app/backend/services/weight_mapper.py`, tenant/template settings, requisition update routes/services, deterministic scoring pipeline.

    ### What is wrong
    Tenant/template flows normalize weights, while requisition updates can persist raw `scoring_weights`. Downstream code still understands legacy representations. The same apparent weights can therefore behave differently depending on entry point.

    ### Why it matters
    Multiple scoring-weight schemas must not silently produce different fit scores for the same inputs.

    ### Depends on
    None

    ### Required fix
    Define one canonical Pydantic scoring-weight schema and normalize at every write boundary. Reject unknown fields/out-of-range values and normalize totals according to current scoring rules. Add an Alembic/data migration to convert existing requisition/template/tenant JSON to canonical format. After migration, remove legacy interpretation gradually rather than continuing multi-schema writes.

    ### How it should behave
    All persisted scoring weights have one schema and one interpretation.

    ### Required regression tests
    - Tenant, template and requisition APIs store identical canonical JSON for equivalent input.
- Legacy rows migrate correctly.
- Invalid totals/negative/unknown weights rejected or normalized exactly per product rule.
- Scoring same resume/JD with same canonical weights yields same deterministic score regardless of source.

    ### Expected result
    No source-dependent scoring semantics.

    ### Acceptance criteria
    One canonical schema is enforced at persistence boundaries.


---

## AUD-024 — Historical hiring outcomes can create a self-reinforcing score feedback loop

    **Severity:** P2  
    **Area:** Scoring / AI Governance

    ### Where to fix
    Deterministic/scoring services that incorporate previous hiring outcomes, calibration/accuracy services.

    ### What is wrong
    Previous hiring outcomes can modify future skill/fit scores after very small sample counts. Historical recruiter preference may therefore be encoded into future rankings and reinforced. This is especially risky in employment decision support.

    ### Why it matters
    Historical hiring outcomes must not train a feedback loop that launders biased decisions into scores.

    ### Depends on
    None

    ### Required fix
    Disable automatic outcome-derived score adjustment by default until statistically governed. If retained: require tenant opt-in, minimum meaningful sample sizes, confidence intervals, time decay, versioned calibration records, before/after score delta audit, protected/proxy disparity monitoring and an easy rollback switch. Do not let a handful of outcomes modify production ranking.

    ### How it should behave
    Historical outcomes are advisory/validated calibration, not an opaque automatic feedback loop.

    ### Required regression tests
    - Below minimum sample threshold -> zero score adjustment.
- Feature disabled -> zero adjustment regardless of history.
- Enabled calibrated adjustment records version/input/delta.
- Rollback restores prior scoring behavior.
- Calibration cannot cross tenant boundary.

    ### Expected result
    A few hiring decisions cannot silently shift future candidate rankings.

    ### Acceptance criteria
    Outcome calibration has explicit governance, versioning and thresholds.


---

## AUD-025 — Employment gaps, short tenure and overqualification signals need controlled use

    **Severity:** P2  
    **Area:** Scoring / Compliance

    ### Where to fix
    Risk-signal and deterministic scoring services dealing with gaps, tenure/stability, experience/overqualification.

    ### What is wrong
    These signals can act as proxies for age, caregiving, disability or life events. They are not necessarily invalid features, but using them as automatic rejection gates creates explainability and fairness risk.

    ### Why it matters
    Gap, tenure, and overqualification signals need explicit governance so they are not treated as hidden penalties.

    ### Depends on
    None

    ### Required fix
    Keep them as transparent advisory signals unless a validated tenant policy explicitly uses them. Remove/avoid hard automatic rejection solely from these proxy-prone factors. Record the exact job-related evidence, factor weight and score effect. Allow tenant policy to disable categories. Bias analytics should analyze decisions with/without these factors.

    ### How it should behave
    Proxy-prone signals are explainable, configurable and non-dispositive by default.

    ### Required regression tests
    - Candidate is not auto-rejected solely for a career gap/short tenure/overqualification unless an explicit validated rule exists.
- Signal contribution is visible in score breakdown/audit.
- Tenant can disable the category and score changes predictably.

    ### Expected result
    Scoring remains job-related and reviewable.

    ### Acceptance criteria
    No hidden hard gate from these factors.


---

## AUD-026 — Tenant custom skill overrides can modify shared skill registry

    **Severity:** P2  
    **Area:** Skill Registry / Multi-tenancy

    ### Where to fix
    Analysis helpers that promote required/nice-to-have custom skills into global/shared registry.

    ### What is wrong
    A term introduced by Tenant A can alter parsing/matching behavior for Tenant B if written into a shared global registry.

    ### Why it matters
    Tenant custom skills must not leak into the shared skill registry used by other tenants.

    ### Depends on
    None

    ### Required fix
    Separate curated global skills from tenant extensions. Persist tenant-defined aliases/custom skills with `tenant_id`, and make matching resolve `global + current tenant extensions`. Never mutate the global registry from normal tenant screening. Provide a separate platform-admin curation workflow if custom terms should become global.

    ### How it should behave
    Tenant-specific vocabulary stays tenant-specific.

    ### Required regression tests
    - Tenant A custom skill is recognized for A.
- Same custom skill is not automatically recognized as a global curated skill for B.
- Global curated skills work for all tenants.
- Platform-admin promotion to global, if implemented, is explicit/audited.

    ### Expected result
    No cross-tenant behavior change from another customer's override.

    ### Acceptance criteria
    Shared registry is read-only for ordinary tenant workflows.


---

## AUD-027 — Current “fine-tuning” feature is prompt personalization and not reliably used for inference

    **Severity:** P2  
    **Area:** AI Training

    ### Where to fix
    `app/backend/routes/training.py` and LLM selection services (`app_llm_client.py`, `llm_service.py`, etc.).

    ### What is wrong
    Training builds an Ollama Modelfile with labelled outcomes embedded in a system prompt (up to a bounded number of examples), stores training status in process memory and creates `aria-{tenant_id}`. The audit did not find a production inference path that reliably selects that generated tenant model. This is not conventional fine-tuning.

    ### Why it matters
    Copy that says fine-tuning must match the actual prompt-personalization behavior that is wired in production.

    ### Depends on
    None

    ### Required fix
    Choose one truthful product path. Preferred short-term: rename to “experimental tenant prompt personalization,” persist model/prompt version/status in DB, and explicitly select it in tenant inference only when ready. If true fine-tuning is required, design a real training/evaluation/deployment pipeline separately. Do not call the current operation fine-tuning. Persist failures/status across restarts.

    ### How it should behave
    Feature name, implementation and inference behavior agree.

    ### Required regression tests
    - Training status survives process restart.
- Tenant A personalization is never selected for Tenant B.
- Successful training/personalization produces a version record.
- Inference selects the trained/personalized version only when active.
- Fallback to base model is explicit/audited.

    ### Expected result
    No UI/API claim of fine-tuning when only prompt examples are created.

    ### Acceptance criteria
    Either the feature is truthfully renamed/wired or a real training pipeline exists.


---

## AUD-028 — Internal plan names are passed where provider price IDs are expected

    **Severity:** P2  
    **Area:** Billing Provider Integration

    ### Where to fix
    `app/frontend/src/components/OnboardingWizard.jsx`, `app/frontend/src/lib/api.ts`, `app/backend/services/billing/stripe_provider.py`, billing webhook/checkout routes and `SubscriptionPlan` model.

    ### What is wrong
    The frontend passes internal plan names such as `growth`; Stripe checkout uses the received `plan` value as `line_items[].price`. Provider price identifiers should be mapped explicitly. Webhook processing also needs a deterministic mapping back to the internal plan.

    ### Why it matters
    Provider price IDs must map deterministically to internal plans so webhooks cannot activate the wrong SKU.

    ### Depends on
    AUD-002

    ### Required fix
    Create provider price mapping storage, e.g. `SubscriptionPlanProviderPrice(plan_id, provider, billing_interval, external_price_id, active)`, or equivalent config. Checkout accepts an internal `plan_id/name + interval`, resolves external price server-side, and never trusts a client-supplied provider price. Webhook reads provider price/subscription and updates internal plan through the same mapping. Unknown prices must fail closed and alert.

    ### How it should behave
    Client chooses an internal plan; server owns provider identifiers and reconciliation.

    ### Required regression tests
    - Growth monthly checkout resolves configured Stripe `price_...` ID.
- Unknown/unmapped internal plan -> 422/503, no checkout.
- Client cannot inject arbitrary external price ID.
- Webhook for configured price activates correct internal plan.
- Webhook for unknown price does not grant entitlement and creates alert/audit.

    ### Expected result
    Provider billing and ARIA entitlement state cannot drift due to name/price confusion.

    ### Acceptance criteria
    Explicit provider-price mapping exists and is covered by checkout/webhook tests.


---

## AUD-029 — OAuth, password reset and verification resend can select an arbitrary membership by global email

    **Severity:** P2  
    **Area:** Identity / Multi-workspace

    ### Where to fix
    `app/backend/routes/oauth.py`; `app/backend/routes/auth.py` forgot-password and resend-verification flows; current `User` uniqueness model.

    ### What is wrong
    The database allows the same email in multiple tenants. Password login correctly asks for workspace slug when ambiguous, but OAuth/reset/resend use global email lookup and `.first()` in some paths. The wrong tenant membership can therefore be linked/reset/resent.

    ### Why it matters
    OAuth, reset, and resend flows must not pick an arbitrary tenant membership for a repeated email.

    ### Depends on
    None

    ### Required fix
    Adopt one consistent identity model. Minimal fix: require/resolve `tenant_slug` for ambiguous email flows, and never select `.first()` globally when multiple active matches exist. Better long-term: introduce a global Identity table keyed by email/provider and separate tenant Membership rows. Do not perform the large migration unless deliberately planned; first make current flows deterministic.

    ### How it should behave
    Every identity operation knows which tenant membership it is acting on, or operates on a true global identity.

    ### Required regression tests
    - Same email in Tenant A and B; forgot-password without workspace -> generic ambiguity flow/no arbitrary token.
- With Tenant A slug -> only A membership reset token changes.
- OAuth login to an existing multi-workspace email does not arbitrarily link to first user.
- Verification resend targets selected membership only.

    ### Expected result
    No arbitrary membership selection.

    ### Acceptance criteria
    Global `.first()` identity lookups are removed from ambiguous flows.


---

## AUD-030 — Candidate GDPR hard delete can leave resume objects in S3/MinIO

    **Severity:** P2  
    **Area:** GDPR / Erasure

    ### Where to fix
    `app/backend/services/gdpr_service.py` (`hard_delete_candidate`), `app/backend/services/erasure_service.py`, `app/backend/services/object_storage.py`, tenant privacy route.

    ### What is wrong
    Database records are deleted, but the GDPR hard-delete path does not call object-storage deletion for `resume_file_key` / `resume_pdf_key`. A separate erasure service already contains related deletion logic, so privacy behavior has diverged.

    ### Why it matters
    Hard-delete must remove object-storage resume copies, not only the candidate row.

    ### Depends on
    None

    ### Required fix
    Unify deletion into one candidate-erasure service used by both tenant GDPR request and platform/retention workflows. Before deleting the candidate row, collect object keys and all dependent records. Delete objects, verify result, then delete/anonymize DB records according to policy. Decide transaction behavior for storage failure: safest is mark erasure `pending/error` and retry rather than falsely reporting completion. Keep a PII-free audit record.

    ### How it should behave
    A successful hard-delete response means all required database and object-storage candidate data has been removed.

    ### Required regression tests
    - Candidate with resume and converted PDF keys -> `ObjectStorageService.delete` called for both.
- Storage deletion failure -> operation reports incomplete/fails and does not claim success.
- Retry succeeds idempotently.
- Candidate from another tenant cannot be erased.
- Audit record contains no PII.

    ### Expected result
    No orphaned resume object after successful GDPR deletion.

    ### Acceptance criteria
    One erasure engine owns all candidate deletion paths.


---

## AUD-031 — Retention anonymization leaves PII in screening and derived records

    **Severity:** P2  
    **Area:** GDPR / Anonymization

    ### Where to fix
    `app/backend/services/gdpr_service.py` (`anonymize_candidate`, `cleanup_expired_data`), `ScreeningResult` and interview/transcript data models.

    ### What is wrong
    Only selected fields on `Candidate` are overwritten while retained screening rows still contain resume text, parsed data, analysis/narrative content and potentially transcripts/notes. The record can remain re-identifiable even though candidate status says anonymized.

    ### Why it matters
    Anonymization must strip PII from screening and derived records, not only the canonical candidate fields.

    ### Depends on
    AUD-030

    ### Required fix
    Create a PII inventory for candidate-linked tables. On anonymization, remove or transform PII-bearing fields while retaining only the minimum aggregate metrics needed for analytics/legal audit. For text blobs, either delete them, replace with irreversible de-identified aggregates, or run a tested anonymization transform. Break direct identifying relationships if policy permits. Do not retain a reversible mapping in normal application tables.

    ### How it should behave
    An anonymized candidate cannot be reasonably re-identified from ordinary retained application records.

    ### Required regression tests
    - Seed name/email/phone/employer into candidate, resume_text, parsed_data, narrative and transcript; after anonymization none of the seeded identifiers remain where policy requires removal.
- Aggregate fit score/status remains if retention policy says preserve it.
- Repeated anonymization is idempotent.
- Analytics still works on allowed de-identified fields.

    ### Expected result
    Status `anonymized` reflects actual derived-data anonymization.

    ### Acceptance criteria
    PII inventory + tests cover all candidate-linked high-risk text fields.


---

## AUD-032 — Candidate portability export is narrower than “all candidate data” contract

    **Severity:** P2  
    **Area:** GDPR / Data Portability

    ### Where to fix
    `app/backend/services/gdpr_service.py` (`export_candidate_data`) and privacy route.

    ### What is wrong
    Export returns selected candidate fields, screening summaries and voice-session metadata, while ARIA stores other candidate-linked information such as notes, requisition memberships, analysis payloads, transcripts, evaluations and potentially files.

    ### Why it matters
    Portability export must include the records the contract promises, not a narrower convenience dump.

    ### Depends on
    None

    ### Required fix
    Define the exact portability scope with product/legal input and implement a structured versioned export. Include all applicable personal data categories, provenance/timestamps and human-readable field names. Do not blindly include internal secrets, other users' private data or security logs. If original resume files are included, provide a secure archive/download workflow rather than embedding binary into JSON.

    ### How it should behave
    The export accurately represents the documented data portability scope.

    ### Required regression tests
    - Fixture with candidate note/requisition/result/interview/transcript -> export includes all in-scope categories.
- Cross-tenant export denied.
- Export omits credentials/internal security secrets.
- Export JSON schema/version is stable.

    ### Expected result
    No “all data” claim with major missing candidate data categories.

    ### Acceptance criteria
    Documented scope and implementation match.


---

## AUD-033 — Adverse-action report hard-codes legal conclusions

    **Severity:** P2  
    **Area:** Compliance Reporting

    ### Where to fix
    `app/backend/services/adverse_action_service.py`.

    ### What is wrong
    The generated report sets fields such as `eeoc_compliant: True` and `job_related: True` regardless of whether those legal conclusions have actually been established. Software-generated analysis cannot certify legal compliance solely from its own JSON inputs.

    ### Why it matters
    Adverse-action reports must not hard-code legal conclusions the product is not qualified to make.

    ### Depends on
    None

    ### Required fix
    Replace legal-certification booleans with factual evidence/check fields: e.g. `decision_factors_documented`, `pii_redaction_applied`, `evidence_present`, `human_review_status`, `job_requirement_links_present`. If a legal review/certification process exists, represent it as a separately recorded human/legal attestation with actor/time/version. Update UI/export language accordingly.

    ### How it should behave
    Reports describe evidence and process; they do not self-certify legal compliance.

    ### Required regression tests
    - Generated report no longer sets unconditional `eeoc_compliant=True`.
- Missing evidence produces corresponding false/unknown evidence field.
- Human attestation, if implemented, requires authorized actor and is auditable.

    ### Expected result
    Compliance language is accurate and defensible.

    ### Acceptance criteria
    No unconditional legal certification remains.


---

## AUD-034 — Bias score-disparity methodology is mislabeled and audit errors return `risk_level=none`

    **Severity:** P2  
    **Area:** Bias Audit

    ### Where to fix
    `app/backend/services/bias_audit_service.py`.

    ### What is wrong
    The function described as Cohen's d does not calculate Cohen's d; it uses percentage deviation of group means. In addition, an exception returns a result with `risk_level="none"`, making “audit failed” indistinguishable from “no risk detected.”

    ### Why it matters
    Bias-audit errors must not report 'no risk'; methodology labels must match what the code measures.

    ### Depends on
    None

    ### Required fix
    Either implement the stated statistical method correctly (with per-candidate score distributions, pooled standard deviation, minimum sample rules and interpretation) or rename the metric to what it actually is. Add `status: success|insufficient_data|error` and `risk_level: unknown` on failures. Never map an execution error to no-risk. Keep the four-fifths check clearly labeled as a screening heuristic, not legal certification.

    ### How it should behave
    Metric names match formulas, and system failure is explicit.

    ### Required regression tests
    - Known numeric fixture validates the implemented statistic.
- DB/service exception -> status `error`, risk `unknown`.
- Insufficient data -> status `insufficient_data`, not misleading `none`.
- Successful no-disparity fixture -> status `success`, risk `none`.

    ### Expected result
    Consumers can distinguish no detected disparity from no valid audit.

    ### Acceptance criteria
    No exception path returns `risk_level=none` as a success-like result.


---

## AUD-035 — Resume PII redaction is not wired into the active production screening pipeline

    **Severity:** P2  
    **Area:** AI Privacy

    ### Where to fix
    `app/backend/services/pii_redaction_service.py`, active hybrid/deterministic/LLM analysis pipeline, and deprecated/WIP agent pipeline.

    ### What is wrong
    Transcript analysis actively calls the PII redaction service, but resume redaction was found in a WIP pipeline that production packaging removes. Active resume-derived prompts can therefore contain PII when sent to configured external AI providers.

    ### Why it matters
    Resume PII redaction is only a privacy control if it runs on the production screening path.

    ### Depends on
    None

    ### Required fix
    Make the privacy behavior explicit. If product policy requires resume redaction before external LLM narrative/kit generation, insert redaction in the active LLM-boundary service—not in each route. Preserve the original canonical resume only in the authorized storage layer; send a redacted view to external providers. If some tasks require identity/employer context, document exactly which fields are allowed and why. Record redaction version/count without storing the redaction map if that map itself exposes PII.

    ### How it should behave
    Every external AI call receives data according to a documented, enforced privacy policy.

    ### Required regression tests
    - Seed name/email/phone/Aadhaar/PAN into resume; mocked external LLM prompt must not contain disallowed PII.
- Deterministic local scoring still receives required non-redacted structured data if intended.
- Redaction fallback behavior is explicit when Presidio unavailable.
- Tenant/provider policy cannot silently disable required production redaction.

    ### Expected result
    External-provider data handling matches product/privacy documentation.

    ### Acceptance criteria
    PII transform is at the shared AI gateway boundary.


---

## AUD-036 — Accuracy metric and A/B test framework are not production-valid

    **Severity:** P2  
    **Area:** Accuracy / Experiments

    ### Where to fix
    `app/backend/services/accuracy_tracking_service.py`.

    ### What is wrong
    “Accuracy” is defined as `1 - recruiter override rate`, which measures agreement rather than predictive correctness. `total_screens` calculation is broken by iterating dictionary keys and using them as dicts. Experiments live in an in-memory global registry and disappear on restart. The audit found no active caller for `get_accuracy_metrics`.

    ### Why it matters
    Accuracy and A/B services must not claim production validity when they are stubs or offline experiments.

    ### Depends on
    None

    ### Required fix
    Do not expose this as model accuracy. Rename recruiter agreement/override metrics accordingly. Fix total-count aggregation from actual result records. If experiments are needed, persist experiment, variant assignment, impression and outcome rows in the database and define a real success metric. Remove or hide unused prototype API/UI until it has a consumer and tests.

    ### How it should behave
    Metrics are mathematically truthful and durable.

    ### Required regression tests
    - Override fixture calculates exact agreement/override rates.
- Total screens equals number of eligible records.
- Restart does not erase persisted experiment state.
- Variant assignment is stable for a subject/experiment.
- Metric name/UI does not claim predictive accuracy unless ground-truth metric exists.

    ### Expected result
    No misleading “accuracy” percentage derived from recruiter agreement.

    ### Acceptance criteria
    Prototype in-memory experiment state is removed from production feature path.


---

## AUD-037 — Share-link bearer tokens and passcodes need stronger controls

    **Severity:** P2  
    **Area:** Share Links

    ### Where to fix
    `app/backend/routes/share_links.py`, `HandoffShareLink` model.

    ### What is wrong
    Listing share links is available broadly and returns the bearer token, which itself grants public access. Optional passcodes are stored as unsalted fast SHA-256, making short passcodes easy to brute-force after DB compromise.

    ### Why it matters
    Share-link tokens and passcodes need hashed storage and authorization checks comparable to session auth.

    ### Depends on
    None

    ### Required fix
    Restrict create/list/revoke/token viewing to the roles that manage the handoff (recruiter/admin/appropriate TA role). Prefer storing only a token hash and returning the raw token only at creation; public lookup hashes presented token. Existing rows may require migration/compatibility. Hash passcodes using Argon2/bcrypt/scrypt with per-value salt, or enforce a high-entropy generated secondary secret. Never log raw tokens/passcodes.

    ### How it should behave
    Bearer secrets are exposed minimally and offline guessing is expensive.

    ### Required regression tests
    - Viewer/HM without management permission cannot list raw share-link tokens.
- Raw token returned on create once; subsequent listing returns masked metadata if adopting token-hash model.
- Passcode verification works with strong password hash.
- Wrong passcode rejected; rate limiting remains active.
- Revoked/expired links still return 410.

    ### Expected result
    Compromise of a normal tenant user/list endpoint does not reveal all public handoff bearer tokens.

    ### Acceptance criteria
    Share secrets follow secret-storage/access-control practices.


---

## AUD-038 — Candidate CSV import endpoint exists but returns HTTP 501

    **Severity:** P3  
    **Area:** Candidate Import

    ### Where to fix
    `app/backend/routes/candidates.py` CSV import route.

    ### What is wrong
    The capability is routed/documented but intentionally returns `501 CSV import is not available`. This creates product/documentation mismatch.

    ### Why it matters
    A CSV import route that returns 501 is unfinished product surface and should be completed or removed.

    ### Depends on
    None

    ### Required fix
    Choose one: implement it fully or hide/remove it from production UI/API documentation. If implementing, define CSV schema, size/row limits, validation report, duplicate behavior, tenant quota impact, asynchronous processing for large files, and per-row errors. Do not silently create partially invalid candidates.

    ### How it should behave
    The capability advertised to users is executable and tested, or it is explicitly unavailable.

    ### Required regression tests
    - If hidden: route/docs/UI no longer advertise import.
- If implemented: valid CSV imports expected candidates; invalid rows return structured errors; duplicate policy tested; cross-tenant impossible; limits enforced.

    ### Expected result
    No production feature button/API contract ends in a placeholder 501.

    ### Acceptance criteria
    Implementation and product specification agree.


---

## AUD-039 — Broad `ILIKE` candidate search will degrade with tenant size

    **Severity:** P3  
    **Area:** Candidate Search / Performance

    ### Where to fix
    Candidate advanced search queries in `app/backend/routes/candidates.py` / candidate service.

    ### What is wrong
    Searching multiple text/profile/resume fields with wildcard `ILIKE` will become increasingly expensive and can force large sequential scans.

    ### Why it matters
    Public marketing and in-app capability claims must match gated features on the current plan.

    ### Depends on
    None

    ### Required fix
    Add a dedicated searchable projection and PostgreSQL search indexes. Appropriate options: `pg_trgm` GIN indexes for partial name/email/title/company matching, PostgreSQL full-text vector for resume/profile text, and tenant-first composite/index strategy. Keep existing API semantics. Use `EXPLAIN ANALYZE` fixtures to validate index use on representative volume.

    ### How it should behave
    Search latency grows reasonably as candidate count grows.

    ### Required regression tests
    - Functional search results remain unchanged.
- Performance test/EXPLAIN demonstrates index use for common query patterns.
- HM access scope is applied before/within search, not after fetching.

    ### Expected result
    No full tenant resume scan for normal searches at scale.

    ### Acceptance criteria
    Indexed search path exists with tenant scoping.


---

## AUD-040 — Candidate identity/profile data is duplicated across canonical and derived JSON records

    **Severity:** P3  
    **Area:** Candidate Data Model

    ### Where to fix
    `Candidate`, `ScreeningResult`, parser snapshots, narrative/analysis payloads, candidate intelligence projections.

    ### What is wrong
    Name/profile/resume-derived information appears in multiple mutable/immutable places. Updating a canonical field can lead to conflicting historical JSON copies, while privacy operations must chase many copies.

    ### Why it matters
    Error responses must not leak stack traces, internals, or other tenants' identifiers.

    ### Depends on
    None

    ### Required fix
    Do not attempt a massive rewrite immediately. Establish rules: canonical identity/contact/profile lives on candidate/profile tables; immutable analysis records keep only the input snapshot fields needed for reproducibility and explicitly identify them as historical snapshots; UI should prefer canonical candidate fields where current identity is intended. Add schema/version fields to projections. Future writes should avoid unnecessary identity duplication.

    ### How it should behave
    The system can distinguish canonical current profile from historical analysis snapshot.

    ### Required regression tests
    - Candidate display uses canonical current name where intended.
- Historical screening snapshot remains reproducible without silently mutating history.
- Privacy erasure/anonymization knows all snapshot locations.

    ### Expected result
    Fewer contradictory sources of truth.

    ### Acceptance criteria
    New code follows documented canonical-vs-snapshot rules.


---

## AUD-041 — `src/lib/api.ts` is a monolithic API client

    **Severity:** P3  
    **Area:** Frontend Maintainability

    ### Where to fix
    `app/frontend/src/lib/api.ts`.

    ### What is wrong
    One large module owns many unrelated domains, increasing merge conflicts, accidental coupling and difficulty mapping backend changes to frontend consumers.

    ### Why it matters
    Frontend API helpers must send the same auth, CSRF, and tenant context the backend enforces.

    ### Depends on
    None

    ### Required fix
    Keep the shared Axios instance/interceptors in a small core module, then split domain APIs: `authApi`, `candidateApi`, `requisitionApi`, `screeningApi`, `billingApi`, `analyticsApi`, `interviewApi`, etc. Preserve exported compatibility during migration or update all imports in one controlled change. Prefer generated TypeScript types from OpenAPI where feasible, but do not block the split on full code generation.

    ### How it should behave
    Each business domain has a small typed client built on one shared transport.

    ### Required regression tests
    - Existing frontend unit tests pass.
- API interceptor/refresh tests still cover the shared client.
- Build has no circular imports or duplicated Axios instances.

    ### Expected result
    No behavior change; maintainability improvement only.

    ### Acceptance criteria
    Shared transport separated from domain request functions.


---

## AUD-042 — Client CSRF recovery assumption does not match `/auth/me` behavior

    **Severity:** P3  
    **Area:** Frontend / CSRF

    ### Where to fix
    Frontend Axios/auth recovery logic and backend `auth.py` `/auth/me`, login/refresh cookie creation.

    ### What is wrong
    Frontend comments/logic imply `/auth/me` can recreate a missing CSRF cookie, while the backend path responsible for setting auth/CSRF cookies is login/refresh. A valid session that loses only the CSRF cookie can enter an inconsistent recovery state.

    ### Why it matters
    Background workers must use the same entitlement and tenant predicates as interactive routes.

    ### Depends on
    None

    ### Required fix
    Choose one explicit mechanism. Recommended: create a safe authenticated `GET /api/auth/csrf` endpoint (or make `/auth/me` deliberately set only a fresh CSRF cookie) and let the client call it when access authentication is valid but CSRF cookie is absent. Do not rotate refresh/session unnecessarily. Keep SameSite/Secure settings identical to existing policy.

    ### How it should behave
    Browser session can recover a missing CSRF token without logout or unsafe mutation.

    ### Required regression tests
    - Valid access/refresh session + missing CSRF -> recovery endpoint sets CSRF and next mutation succeeds.
- Unauthenticated request cannot obtain a misleading authenticated state.
- CSRF mismatch still rejects mutation.
- Existing login/refresh behavior unchanged.

    ### Expected result
    Client and backend implement the same documented recovery flow.

    ### Acceptance criteria
    No comment/logic relies on an endpoint that does not set the cookie.


---

## AUD-043 — Dashboard GET performs migration writes and uses N+1/in-memory aggregation

    **Severity:** P3  
    **Area:** Analytics / Dashboard

    ### Where to fix
    `app/backend/routes/dashboard.py` / analytics dashboard summary, `migrate_legacy_data` call.

    ### What is wrong
    A GET request invokes legacy migration and commits. The dashboard also loads broad result collections and performs per-requisition queries/grouping in Python. This creates side effects, locking risk and performance degradation.

    ### Why it matters
    Object-storage keys must be tenant-prefixed so one tenant cannot guess another's resume object.

    ### Depends on
    None

    ### Required fix
    Remove migration from GET. Run legacy migration through Alembic/data migration or a controlled one-time admin/background job. Replace per-requisition queries with SQL aggregations/joins or a projection service; prefetch memberships in one query. Add appropriate tenant/requisition/status indexes. Keep response contract stable.

    ### How it should behave
    Dashboard GET is read-only and executes a bounded number of efficient queries.

    ### Required regression tests
    - GET dashboard does not commit or create/update rows.
- Query-count test prevents N+1 regression.
- Response values match prior behavior on fixture data.
- Legacy data migration is tested separately.

    ### Expected result
    Dashboard remains correct while scaling to larger tenants.

    ### Acceptance criteria
    No data migration side effect from GET.


---

## AUD-044 — Synchronous SQLAlchemy sessions are used inside many `async def` request handlers

    **Severity:** P3  
    **Area:** Backend Runtime

    ### Where to fix
    FastAPI routes using `async def` with synchronous `Session`/ORM calls.

    ### What is wrong
    Synchronous DB calls block the event loop when executed inside async handlers. With concurrency this reduces throughput and can create latency spikes.

    ### Why it matters
    Rate limits must apply per tenant and identity, not only per process, or one actor can starve others.

    ### Depends on
    None

    ### Required fix
    Do not mix paradigms casually. For handlers that are primarily synchronous DB/business logic and do not require `await`, change them to normal `def` so FastAPI executes them in its threadpool. For genuinely async flows, isolate blocking DB work or plan a deliberate SQLAlchemy AsyncSession migration by domain. Avoid a partial ad-hoc conversion.

    ### How it should behave
    Blocking database work does not block the main asyncio event loop.

    ### Required regression tests
    - Existing API tests pass after signature changes.
- Load/concurrency test shows no event-loop starvation for representative endpoints.
- No SQLAlchemy session is shared across async tasks.

    ### Expected result
    Improved predictable concurrency without semantic API changes.

    ### Acceptance criteria
    Each route follows one clear sync/async execution model.


---

## AUD-045 — LLM concurrency limits are process-local while production uses multiple workers

    **Severity:** P3  
    **Area:** AI Runtime / Concurrency

    ### Where to fix
    Tenant LLM concurrency helper(s), `app/backend/services/llm_service.py` Gemini semaphore, production Uvicorn worker configuration.

    ### What is wrong
    In-memory locks/semaphores apply per process. With six Uvicorn workers (and future horizontal replicas), a configured limit of one can become six or more actual concurrent calls, defeating provider/rate/resource controls.

    ### Why it matters
    Audit logs must record entitlement and access-sensitive mutations without storing secrets or SAML assertions.

    ### Depends on
    None

    ### Required fix
    Move tenant/provider concurrency leases to Redis. Use a bounded semaphore/counter with TTL/lease ownership so crashes release capacity. Keep a small in-process semaphore only as a secondary optimization. Key by provider/model and tenant where appropriate. Instrument current/queued counts.

    ### How it should behave
    Concurrency limit is global across all web/worker processes in the deployment.

    ### Required regression tests
    - Two simulated processes/clients sharing Redis respect one global limit.
- Lease expires/recover after worker crash.
- Different tenants can use independent limits if configured.
- Metrics reflect global concurrency.

    ### Expected result
    Provider bursts do not multiply by process count.

    ### Acceptance criteria
    Production concurrency controls are distributed.


---

## AUD-046 — Application request-size guard relies too heavily on `Content-Length`

    **Severity:** P3  
    **Area:** HTTP / Upload Limits

    ### Where to fix
    Request-size middleware and Nginx/ASGI deployment configuration.

    ### What is wrong
    Clients can use chunked transfer without a trustworthy `Content-Length`. Application middleware that only checks the header cannot guarantee a hard body limit.

    ### Why it matters
    Feature flags must read effective entitlement, not a stale cache of an unpaid plan_id.

    ### Depends on
    AUD-002

    ### Required fix
    Enforce `client_max_body_size` (or equivalent) at Nginx for global and upload-specific limits. For endpoints that read streams directly, enforce a running byte counter while consuming. Keep file-specific size validation after upload parsing as defense-in-depth.

    ### How it should behave
    Oversized bodies are rejected regardless of transfer encoding.

    ### Required regression tests
    - Normal body under limit succeeds.
- `Content-Length` over limit -> 413.
- Chunked/streamed body exceeding limit -> 413 at proxy/integration test.
- Upload-specific limits remain enforced.

    ### Expected result
    No memory/disk exhaustion by omitting the length header.

    ### Acceptance criteria
    Proxy and application streaming limits both exist.


---

## AUD-047 — Business-integrity SLOs and alerts are missing

    **Severity:** P3  
    **Area:** Observability

    ### Where to fix
    Prometheus/Sentry/metrics services and operational dashboards.

    ### What is wrong
    Technical telemetry exists, but the highest-risk failures in ARIA are domain inconsistencies: wrong-candidate duplicate resolution, entitlement drift, queue age, quota mismatches, SAML failures, privacy erasure failures, etc. These may not surface as HTTP 500s.

    ### Why it matters
    Admin impersonation must be short-lived, audited, and unusable with a non-admin JWT.

    ### Depends on
    None

    ### Required fix
    Add metrics/alerts for: screening completion/failure by mode, queue oldest age, DLQ depth, duplicate-resolution conflicts, entitlement state vs plan mismatches, provider webhook failures, SSO validation failures, erasure pending/error, narrative/kit fallback rates, cross-tenant authorization denials, and quota reservation/consumption mismatch. Never include candidate PII in metric labels.

    ### How it should behave
    Operational dashboards can detect business correctness failures before customer reports.

    ### Required regression tests
    - Metric unit tests for increment/state transitions.
- Alert integration test where feasible.
- Static test ensures high-cardinality/PII values are not metric labels.

    ### Expected result
    Integrity failures are observable as first-class production signals.

    ### Acceptance criteria
    Runbook/metric names exist for P0/P1 domain invariants.


---

## AUD-048 — Authenticated cross-domain E2E and quality gates are incomplete

    **Severity:** P3  
    **Area:** Testing / CI

    ### Where to fix
    `.github/workflows/` CI; backend pytest config; Playwright/Vitest suites.

    ### What is wrong
    CI has broad unit/security coverage, but authenticated end-to-end workflows are not the default PR gate. Coverage can decline if no fail-under is enforced. Security-action/dependency exceptions require tighter governance.

    ### Why it matters
    Health and metrics endpoints must not expose tenant PII or internal credentials.

    ### Depends on
    None

    ### Required fix
    Add a deterministic authenticated E2E suite for the critical invariants: two tenants, HM/recruiter/admin roles, plan/trial states, sync vs queue screening, duplicate resolution, GDPR delete. Seed data through fixtures or test API. Add `--cov-fail-under` at a realistic current baseline and ratchet upward. Pin third-party CI actions to stable versions/commits. Document every dependency-audit ignore with owner/reason/expiry.

    ### How it should behave
    The exact classes of bugs found by this audit fail CI if reintroduced.

    ### Required regression tests
    - Tenant A cannot access Tenant B across candidate/result/ATS routes.
- HM cannot access unassigned candidate via any alternate endpoint.
- Expired trial cannot use paid feature.
- Queue and sync consume equivalent quota.
- `use_existing` cannot guess candidate.
- GDPR delete removes object storage.

    ### Expected result
    Future changes cannot silently reopen these cross-domain invariants.

    ### Acceptance criteria
    Authenticated E2E is a required PR check.


---

## AUD-049 — Protected promotion and immutable image deployment are missing

    **Severity:** P3  
    **Area:** CI/CD / Supply Chain

    ### Where to fix
    GitHub branch settings/rulesets, `.github/workflows/cd.yml`, `docker-compose.prod.yml`.

    ### What is wrong
    `main` and `production` were reported unprotected with required status checks off. CD publishes mutable `staging`/`latest` tags. Manual production workflow dispatch is not inherently tied to the exact artifact that passed staging/CI. Image provenance is disabled.

    ### Why it matters
    Production promotion must use immutable artifacts and protected branches, not mutable tags alone.

    ### Depends on
    None

    ### Required fix
    Protect `main` and `production`: PR-only changes, required reviews, required CI/security checks, no force push, restricted admins as appropriate. Build one immutable image per commit SHA, scan it, deploy that digest to staging, then promote the exact same digest to production through an approved environment. Keep friendly tags only as aliases, never as the authoritative deployed identity. Enable provenance/SBOM where supported.

    ### How it should behave
    Production runs a known immutable artifact that passed required CI and staging approval.

    ### Required regression tests
    - Workflow dry-run/test verifies production job references SHA/digest output rather than rebuilding.
- Branch/ruleset checks documented as required setup.
- Deployment summary records commit SHA and image digest.

    ### Expected result
    No unreviewed direct branch change or mutable-tag drift becomes production unintentionally.

    ### Acceptance criteria
    Protected branches + immutable artifact promotion.


---

## AUD-050 — Versions and capability documentation have drifted from runtime implementation

    **Severity:** P3  
    **Area:** Documentation / Product Truth

    ### Where to fix
    `README`, `PRODUCT_SPECIFICATION.md`, generated repo wiki/docs, app version constant, dependency files, UI feature labels.

    ### What is wrong
    Examples observed during audit include product-version mismatch, dependency/version mismatch, “fine-tuning” terminology for prompt personalization, CSV import advertised while returning 501, queue concurrency config not matching execution, and compliance wording stronger than implementation.

    ### Why it matters
    Product, version, and capability docs must not claim behavior the current code does not implement.

    ### Depends on
    None

    ### Required fix
    Create a single source of truth for application version/build SHA and expose it through health/build metadata. Generate dependency references from lock/requirements files rather than hand-copying versions. Add a tested capability manifest or documentation checklist tied to feature flags. Update marketing/product docs to distinguish production, beta and unavailable features. Remove legal/technical claims that tests cannot demonstrate.

    ### How it should behave
    A developer, customer and Cursor see the same feature reality.

    ### Required regression tests
    - Health/version endpoint equals build version injected by CI.
- Docs/capability manifest test fails if a disabled/501 feature is marked production-ready where feasible.
- Frontend labels for beta features match backend feature flags.

    ### Expected result
    No major capability is overstated relative to executable code.

    ### Acceptance criteria
    Version/capability source of truth documented and used.


---

# 4. Area-by-area execution order

Cursor should fix issues in the following sequence to minimize conflicting refactors.

## Phase A — Stop entitlement, identity and cross-tenant failures
1. AUD-001 SAML
2. AUD-002 direct paid-plan mutation
3. AUD-003 expired trials
4. AUD-004 ATS tenant isolation
5. AUD-005 queue quota
6. AUD-006 duplicate/use-existing correctness
7. AUD-007 to AUD-010 authorization/assignment consistency

**Exit gate:** no P0 test fails; cross-tenant/HM regression suite is green.

## Phase B — Centralize outbound integration trust
1. AUD-011 ATS SSRF
2. AUD-012 ATS webhook authentication
3. AUD-013 JD redirect SSRF
4. AUD-014 outbound webhook SSRF
5. AUD-015 integration-secret encryption

**Recommended shared component:** a single outbound network policy/client used by ATS, JD scraping, webhooks, video URL fetches and future integrations. Do not create several slightly different validators again.

## Phase C — Make screening execution one domain
1. AUD-016 requisition propagation
2. AUD-017 analysis fingerprint/dedup
3. AUD-018 concurrency
4. AUD-019 heartbeat
5. AUD-020 stale recovery
6. AUD-021 DLQ replay
7. AUD-022 idempotency body binding
8. Re-run AUD-005 quota tests after the queue refactor

**Recommended end state:** route handlers construct a `ScreeningCommand`; one shared service validates entitlement, tenant/requisition context and deterministic configuration; sync/stream/batch/queue call the same domain execution primitives.

## Phase D — Scoring and AI governance
1. AUD-023 weight schema
2. AUD-024 historical-outcome calibration
3. AUD-025 proxy-prone signals
4. AUD-026 tenant skill extensions
5. AUD-027 training/product truth
6. AUD-035 AI privacy
7. AUD-036 accuracy/experiments

## Phase E — Billing and identity correctness
1. AUD-028 provider price mapping
2. AUD-029 multi-workspace identity
3. Re-run AUD-002/AUD-003 full billing/identity regression suites

## Phase F — Privacy and compliance
1. AUD-030 erasure/object storage
2. AUD-031 anonymization
3. AUD-032 portability
4. AUD-033 adverse-action language
5. AUD-034 bias-audit semantics
6. AUD-037 share-link secrets

## Phase G — Scale, maintainability and delivery
1. AUD-038 to AUD-046
2. AUD-047 observability
3. AUD-048 CI/E2E
4. AUD-049 CI/CD governance
5. AUD-050 documentation truth

---

# 5. Regression-test matrix

The following matrix should exist by the end of remediation even if individual tests live in different files.

| Scenario | Expected invariant |
|---|---|
| Tenant A candidate ID passed through Tenant B ATS connection | Rejected; no outbound call |
| HM searches exact email of unassigned candidate | No result |
| HM opens unassigned screening result/interview/transcript/comment | 403/404 |
| Tenant user calls hidden/direct paid plan change | Cannot obtain paid entitlement |
| Trial expires without payment | Paid feature denied |
| Queue screening at quota limit | Rejected/reservation denied |
| Queue job retries | One quota unit at most |
| Same resume/JD but different scoring weights | New analysis job/result |
| Queue job takes longer than stale threshold while healthy | Not reclaimed |
| DLQ retry | Replays original artifact and can complete |
| Same idempotency key, same body | Replay |
| Same idempotency key, different body | 409; no mutation |
| Duplicate `use_existing` without exact candidate | Fails; never guesses |
| ATS/JD/webhook target resolves private IP | Rejected before connection |
| ATS webhook with no/wrong signature | Rejected; no mutation |
| OAuth/reset for same email in two tenants | Deterministic workspace resolution; no arbitrary `.first()` |
| GDPR hard delete with S3 resume | DB + S3 removed or operation explicitly incomplete |
| Anonymize candidate with PII in result blobs | Required PII no longer present |
| Bias audit service exception | `status=error`, `risk=unknown` |
| Adverse-action report | Does not self-certify legal compliance |
| External LLM prompt with seeded resume PII | Disallowed PII absent |
| Share-link list by unauthorized tenant user | Token not disclosed |
| Production image promotion | Same tested digest promoted |

---

# 6. Tests that should be created or expanded

Use existing test organization where possible. Suggested files below are names, not mandatory architecture.

### Backend integration
- `app/backend/tests/test_audit_p0_entitlements.py`
- `app/backend/tests/test_audit_tenant_boundaries.py`
- `app/backend/tests/test_audit_hm_access.py`
- `app/backend/tests/test_audit_queue_consistency.py`
- `app/backend/tests/test_audit_sso_security.py`
- `app/backend/tests/test_audit_outbound_network.py`
- `app/backend/tests/test_audit_privacy_erasure.py`
- `app/backend/tests/test_audit_billing_mapping.py`
- `app/backend/tests/test_audit_identity_multitenant.py`
- `app/backend/tests/test_audit_ai_governance.py`

Do not create duplicate fixtures if `conftest.py` already provides tenant/user/client factories.

### Frontend
Extend current Vitest/Playwright suites for:
- expired-trial/feature-gate rendering;
- plan selection and checkout flow;
- HM authorization behavior;
- CSRF recovery;
- no production UI path to unfinished CSV import if the endpoint remains disabled;
- beta labels for training/compliance features until production-ready.

### Security regression
Add focused tests for:
- SSRF through DNS and redirects;
- SAML certificate trust/replay;
- integration secret serialization;
- share-link token/passcode controls;
- request-body idempotency binding.

---

# 7. Definition of done for each Cursor fix

For every issue, Cursor should produce a short completion record in the PR/commit description:

```text
Issue: AUD-XXX
Root cause:
Files changed:
Schema migration:
Behavior before:
Behavior after:
Tests added:
Test command:
Test result:
Backward compatibility impact:
Follow-up intentionally deferred:
```

Do not mark an issue complete solely because the code “looks fixed.”

---

# 8. Architecture guardrails while fixing

## Keep the modular monolith
This audit does **not** require microservices. The current product can remain a modular monolith. Prefer extracting domain services/policies inside the existing backend.

## Centralize these rules
The following must have one owner/service rather than route-specific copies:

- candidate/requisition/result access;
- subscription entitlement state;
- quota reservation/consumption;
- screening configuration/fingerprint;
- outbound URL/network validation;
- candidate erasure/anonymization;
- integration secret encryption;
- LLM provider privacy/concurrency;
- scoring-weight normalization.

## Avoid these anti-patterns
- `.first()` as a fallback for an ambiguous identity or candidate;
- tenant check only in the route but not service;
- `except Exception: return safe-looking-success`;
- “if secret missing, accept anyway”;
- GET endpoint that writes/migrates data;
- per-process locks for deployment-wide quotas;
- direct client-supplied payment-provider price IDs;
- legal/compliance booleans hard-coded to true;
- new fallback that silently preserves unsafe old behavior.

---

# 9. Existing controls that must be preserved

The remediation should not regress the stronger areas already present:

- JWT secret validation;
- access/refresh token revocation behavior;
- MFA support;
- CSRF double-submit protection;
- production CORS restrictions;
- Redis-backed rate limiting where configured;
- deterministic scoring separated from LLM narrative;
- PostgreSQL + Alembic migration discipline;
- object storage abstraction;
- ClamAV/fail-closed production upload scanning;
- non-root backend container;
- Sentry with `send_default_pii=False`;
- Prometheus/metrics infrastructure;
- request IDs / structured logging;
- secret redaction in logs;
- billing webhook event uniqueness/idempotency at the provider-event level;
- migration CI, Gitleaks, dependency scanning and Trivy;
- dedicated background worker process rather than running schedulers in every API worker.

---

# 10. Final target state

After all items in this document are resolved, the application should satisfy the following:

1. **Authorization is policy-driven** rather than endpoint-specific.
2. **Screening is one domain operation** exposed through multiple transports/execution modes.
3. **Billing is state-driven**: plan + verified subscription/trial status determine entitlement.
4. **Quota is transactional** and consistent across sync/batch/queue.
5. **Outbound networking is centralized and SSRF-safe.**
6. **Candidate privacy has one erasure/anonymization engine.**
7. **AI claims are truthful**: deterministic score, LLM narrative, personalization, bias diagnostics and legal evidence are clearly separated.
8. **Queue execution is replayable, leased, heartbeated, deduplicated by full analysis identity and correctly linked to requisitions.**
9. **Multi-workspace identity is deterministic.**
10. **CI protects the exact invariants discovered by this audit.**
11. **Production artifacts are immutable and traceable to a required-CI commit.**
12. **Documentation describes executable reality, not planned capability.**

---

# 11. Reference commit note

This guide was created from the deep audit of `main` at:

`81a7dee9a27ef12784f50fe1895b815b047f97cb`

If Cursor is run against a later commit, it must first verify each referenced function/path. A later commit may already contain a partial fix. In that case:

- retain the issue ID;
- compare the current behavior with the acceptance criteria here;
- add any missing regression test;
- do not reintroduce obsolete implementation just to match the old file path.

This document defines the **required behavior**, not a mandate to preserve the exact old implementation structure.
