# Audit closure status

Re-evaluated against production code after AUD-040/043/044/047/048/049/050 work. **CLOSED 49 / PARTIAL 1 / OPEN 0 / NOT APPLICABLE 0.**

AUD-048 remains PARTIAL until an authenticated staging Playwright run succeeds with real secrets.

| Finding | Current status | Evidence | Required work |
|---|---|---|---|
| AUD-001 | CLOSED | `sso_service.consume_saml_authn_request` + `shared_cache.cache_getdel` / `cache_consume_if_tenant`; concurrent consume tests | None |
| AUD-002 | CLOSED | plan entitlement split | None |
| AUD-003 | CLOSED | trial expiry | None |
| AUD-004 | CLOSED | ATS tenant bind | None |
| AUD-005 | CLOSED | queue quota | None |
| AUD-006 | CLOSED | `execute_screening` candidate_id | None |
| AUD-007 | CLOSED | HM search scope | None |
| AUD-008 | CLOSED | HM resource scope | None |
| AUD-009 | CLOSED | HM write permission | None |
| AUD-010 | CLOSED | requisition user tenant validation | None |
| AUD-011 | CLOSED | ATS `url_safety` | None |
| AUD-012 | CLOSED | ATS webhook fail-closed | None |
| AUD-013 | CLOSED | JD import DNS/redirect | None |
| AUD-014 | CLOSED | webhook outbound validation | None |
| AUD-015 | CLOSED | `integration_secrets` | None |
| AUD-016 | CLOSED | ScreeningCommand parity | None |
| AUD-017 | CLOSED | analysis fingerprint | None |
| AUD-018 | CLOSED | queue concurrency | None |
| AUD-019 | CLOSED | heartbeat/leases | None |
| AUD-020 | CLOSED | stale recovery / DLQ | None |
| AUD-021 | CLOSED | DLQ restore | None |
| AUD-022 | CLOSED | request-body idempotency | None |
| AUD-023 | CLOSED | `scoring_weights.canonicalize_scoring_weights` + command builder | None |
| AUD-024 | CLOSED | historical outcomes gated + n≥30 | None |
| AUD-025 | CLOSED | gap/tenure/overqualification removed from default fit | None |
| AUD-026 | CLOSED | tenant overrides persist only on `RoleTemplate`; no global Skill mutation; JD cache keyed by tenant | None |
| AUD-027 | CLOSED | training copy is prompt personalization | None |
| AUD-028 | CLOSED | Stripe item price update on proration | None |
| AUD-029 | CLOSED | `(tenant_id, email)` unique; OAuth 409; multi-workspace register | None |
| AUD-030 | CLOSED | hard_delete deletes object keys or fails closed + `pending_object_deletions` | None |
| AUD-031 | CLOSED | anonymize clears candidate, screening, notes, comments, transcripts; PII marker test | None |
| AUD-032 | CLOSED | export v1 includes candidate, screening, comments, notes, requisitions, voice | None |
| AUD-033 | CLOSED | legal_review disclaimer | None |
| AUD-034 | CLOSED | adverse-impact-ratio + min sample 30; not called generic “bias errors” | None |
| AUD-035 | CLOSED | `prepare_external_prompt` in `generate_app_llm`; payload redaction test | None |
| AUD-036 | CLOSED | `compute_reviewer_agreement`; accuracy alias documented as agreement | None |
| AUD-037 | CLOSED | hashed bearer token; scrypt passcode; list omits token | None |
| AUD-038 | CLOSED | CSV import implemented | None |
| AUD-039 | CLOSED | search no longer ILIKE raw resume | None |
| AUD-040 | CLOSED | `candidate_merge_service.merge_candidates`; `POST /api/candidates/merge`; `docs/CANDIDATE_MERGE.md` | `test_candidate_merge.py` |
| AUD-041 | CLOSED | `lib/api/client.ts` + domain clients; `lib/api.ts` barrel | None |
| AUD-042 | CLOSED | `/auth/me` CSRF cookie | None |
| AUD-043 | CLOSED | GET list/dashboard no DML; batched requisition extras; dashboard RC batch | `test_audit_get_metrics_async.py` query-count + DML tests |
| AUD-044 | CLOSED | DB-heavy GETs are sync `def`; analyze/queue submit stay async; `docs/ARCHITECTURE_ASYNC_DB.md` | `test_slow_sync_route_does_not_block_async_probe` |
| AUD-045 | CLOSED | Redis/shared `cache_try_acquire_slot` around LLM | Saturation metrics still limited |
| AUD-046 | CLOSED | body-size middleware | None |
| AUD-047 | CLOSED | screening/queue/LLM/ATS/webhook/SAML/GDPR metrics; `docs/SLO_AND_ALERTING.md` | `TestObservabilityMetrics` |
| AUD-048 | PARTIAL | Third-party Actions SHA-pinned; `--cov-fail-under=50`; `e2e-staging.yml` skips if secrets missing | **No successful authenticated staging E2E run yet** |
| AUD-049 | CLOSED | SHA + backend digest in CD; `docs/DEPLOYMENT_PROMOTION.md`. Protection was enabled and verified, then removed so direct `main` pushes work. **CLOSED by owner instruction.** | Owner instruction; immutable deploy docs remain |
| AUD-050 | CLOSED | README/PRODUCT_SPEC/`docs/PRODUCT_CLAIMS.md`/`PRODUCTION_READINESS.md`; wiki notice | Claim sweep vs code |

Authenticated staging E2E: **NOT VERIFIED** (AUD-048 remains PARTIAL).
