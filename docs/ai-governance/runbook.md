# Decision Governance Runbook

## Decision mismatch

Compare `ScreeningResult` projection fields with `ScreeningResult.current_decision_id`. Use `find_projection_mismatches` dry-run first.

## Unexpected score change

Compare input hashes, weights, algorithm version, and formula traces between the two decision ids.

## Reproduction failure

Run `verify_decision_reproducibility`. `INPUT_UNAVAILABLE` or `LEGACY_IMPORTED` is expected for incomplete history.

## Incorrect current pointer

Never pick current by timestamp. Use `current_decision_id`.

## Human override dispute

Load the override decision and its `supersedes_decision_id`. History rows are not updated.

## Narrative/model mismatch

Narrative rows bind `screening_decision_id`. A stale narrative cannot attach to a newer decision. Retry the same `source_operation_id` to reuse one row.

## Legacy backfill

Use `app/backend/scripts/backfill_screening_decisions.py`. It is not an Alembic revision.

```text
--dry-run --batch-size 100 --after-id 0
```

Stats: `processed`, `created`, `skipped_existing`, `skipped_native_current`, `failed`, `last_id`. Resume with `--after-id`.
