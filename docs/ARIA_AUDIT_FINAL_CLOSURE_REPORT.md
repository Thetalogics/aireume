# ARIA audit final closure report

Baseline reviewed: `4e255e1`. **Not every finding is CLOSED.** Branch protection (AUD-049) remains PARTIAL until GitHub settings are enabled. Authenticated staging E2E is NOT VERIFIED.

| ID | Status | Code evidence | Test evidence | Operational evidence |
|---|---|---|---|---|
| AUD-001 | CLOSED | `sso_service.consume_saml_authn_request`, `shared_cache.cache_getdel` | `test_saml_consume_is_atomic_under_concurrency` | Redis required for production SAML |
| AUD-002 | CLOSED | plan entitlement | Phase A billing tests | — |
| AUD-003 | CLOSED | trial expiry | Phase A tests | — |
| AUD-004 | CLOSED | ATS tenant bind | `test_audit_bug_pack.py` | — |
| AUD-005 | CLOSED | queue quota | `test_audit_queue_quota.py` | — |
| AUD-006 | CLOSED | `execute_screening` candidate_id | Phase C + AUD-006 tests | — |
| AUD-007–010 | CLOSED | HM/requisition RBAC | `test_audit_hm_access.py` | — |
| AUD-011–015 | CLOSED | url_safety, secrets | Phase B tests | — |
| AUD-016–022 | CLOSED | ScreeningCommand, fingerprint, DLQ | Phase C tests | — |
| AUD-023 | CLOSED | `scoring_weights.py` | `test_canonical_weights_reject_unknown_and_bad_total` | — |
| AUD-024 | CLOSED | fit_scorer historical flag | `test_historical_outcomes_do_not_adjust_when_disabled` | — |
| AUD-025 | CLOSED | risk_calculator + default timeline 0 | `test_gaps_and_short_tenure_do_not_change_fit_score` | — |
| AUD-026 | CLOSED | persist overrides tenant-only; JD cache tenant key | `test_tenant_skill_overrides_do_not_mutate_global_registry` | — |
| AUD-027 | CLOSED | training personalization copy | `test_training_gate.py` | — |
| AUD-028 | CLOSED | Stripe subscription item price | `test_stripe_prorate_updates_subscription_item_price` | Stripe price env mapping |
| AUD-029 | CLOSED | tenant-scoped email identity | `test_same_email_can_register_second_workspace` | — |
| AUD-030 | CLOSED | object storage delete + pending retries | `test_object_storage_delete_is_invoked_on_hard_delete` | MinIO/S3 must be configured in prod |
| AUD-031 | CLOSED | centralized anonymize paths | `test_anonymize_removes_pii_marker` | — |
| AUD-032 | CLOSED | export_version 1.0 sections | same test asserts sections | — |
| AUD-033 | CLOSED | legal_review disclaimer | `test_adverse_action_does_not_claim_legal_compliance` | counsel review still required |
| AUD-034 | CLOSED | adverse impact ratio + n≥30 | `test_adverse_impact_ratio_on_known_distribution` | — |
| AUD-035 | CLOSED | `external_ai_boundary` + `generate_app_llm` | `test_external_ai_boundary_redacts_email_before_provider` | — |
| AUD-036 | CLOSED | agreement metrics | `test_reviewer_agreement_metrics_not_called_accuracy` | — |
| AUD-037 | CLOSED | token_hash + scrypt passcode | `test_share_passcode_uses_bcrypt`, `TestReqShareLinkPasscode` | — |
| AUD-038 | CLOSED | CSV import | `test_csv_import_creates_tenant_scoped_candidates` | — |
| AUD-039 | CLOSED | indexed candidate search | `test_candidate_search_does_not_query_raw_resume` | — |
| AUD-040 | PARTIAL | unique email/file-hash | existing unique-constraint tests | merge UX not built |
| AUD-041 | CLOSED | `app/frontend/src/lib/api/*.ts` domain clients | frontend unit tests/lint | — |
| AUD-042 | CLOSED | GET `/auth/me` CSRF cookie | `test_auth_me_sets_csrf_cookie` | — |
| AUD-043 | PARTIAL | dashboard GET no hidden migrate | missing query-count suite | — |
| AUD-044 | PARTIAL | dashboard summary is sync | missing event-loop tests | — |
| AUD-045 | CLOSED | `cache_try_acquire_slot` | `test_llm_slots_are_shared_across_process_identities` | Redis in production |
| AUD-046 | CLOSED | streamed body cap | existing 413 tests | — |
| AUD-047 | PARTIAL | extra Prometheus metrics | not fully wired | alert docs incomplete |
| AUD-048 | PARTIAL | CI + SHA-pinned checkout | CI workflow | staging E2E NOT VERIFIED |
| AUD-049 | PARTIAL | SHA image tags in `cd.yml` | `docs/BRANCH_PROTECTION_REQUIRED.md` | **branch protection not verified** |
| AUD-050 | PARTIAL | `SECURITY_ARCHITECTURE.md`, `PRODUCTION_READINESS.md` | — | README claim sweep remaining |

Counts: CLOSED 42 / PARTIAL 8 / OPEN 0 / NOT APPLICABLE 0 of 50.

Authenticated staging E2E: **NOT VERIFIED**.
