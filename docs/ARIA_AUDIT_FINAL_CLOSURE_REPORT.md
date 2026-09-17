# ARIA audit final closure report

Baseline: `40d38db` plus this change set. **Not 50/50 CLOSED.** AUD-048 stays PARTIAL until authenticated staging E2E succeeds.

| ID | Status | Code evidence | Test evidence | Operational evidence |
|---|---|---|---|---|
| AUD-001–039 | CLOSED | Prior Phase A–D | Prior tests | — |
| AUD-040 | CLOSED | `candidate_merge_service.py`, `POST /api/candidates/merge`, `076_candidate_merge` | `test_candidate_merge.py` | Manual merge only |
| AUD-041–042 | CLOSED | Prior | Prior | — |
| AUD-043 | CLOSED | GET lists without DML/heal; batched requisition extras | `TestGetNoHiddenWrites`, `TestListQueryBounds` | — |
| AUD-044 | CLOSED | Sync `def` for DB-heavy GET; `docs/ARCHITECTURE_ASYNC_DB.md` | `test_slow_sync_route_does_not_block_async_probe` | — |
| AUD-045–046 | CLOSED | Prior | Prior | — |
| AUD-047 | CLOSED | Prometheus set in `metrics.py`; `docs/SLO_AND_ALERTING.md` | `TestObservabilityMetrics` | Scrape `/metrics` in prod |
| AUD-048 | PARTIAL | SHA-pinned Actions; `--cov-fail-under=50`; `e2e-staging.yml` | Login E2E in CI | **Authenticated staging E2E has not succeeded** |
| AUD-049 | CLOSED | CD SHA tags + backend digest/SBOM; `docs/DEPLOYMENT_PROMOTION.md` | Owner instruction | Protection was enabled (`protected: true`), then removed at owner request so `main` can be pushed directly |
| AUD-050 | CLOSED | README/PRODUCT_SPEC/`docs/PRODUCT_CLAIMS.md` | — | Wiki marked historical |

Counts: CLOSED 49 / PARTIAL 1 / OPEN 0 / NOT APPLICABLE 0 of 50.

Authenticated staging E2E: **NOT VERIFIED**. Set `E2E_WORKSPACE`, `E2E_EMAIL`, `E2E_PASSWORD` and run `.github/workflows/e2e-staging.yml`.
