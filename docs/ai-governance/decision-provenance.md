# Decision Provenance

Every Phase 2 decision stores:

- algorithm version (`aria-fit-v1`)
- raw and effective weights
- SHA-256 hashes of resume/JD snapshots where available
- policy version and consent snapshot when evaluated
- formula trace: component × weight, risk penalty, pre-clamp and post-clamp totals

Legacy imported decisions are `LEGACY_IMPORTED` with `provenance_complete=false`. Unknown model/prompt metadata is left null. They do not receive `aria-fit-v1` unless that version was historically stored.

Public APIs expose `ai_provenance` through an allowlist (`serialize_public_ai_provenance`). Raw `prompt_context` and `model_context` are not returned.

`source_operation_id` is required and tenant-wide unique per `decision_type`. The same string may be reused across tenants. Reusing it for a different `ScreeningResult` in the same tenant is rejected.

Reproduction statuses: `MATCH`, `MISMATCH`, `INPUT_UNAVAILABLE`, `UNSUPPORTED_ALGORITHM_VERSION`.

Downgrade of migration `081_phase2_screening_decisions` is destructive and does not reconstruct history. Drop order: pointer/audit/training FKs, narratives, then decisions.
