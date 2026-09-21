# AI Governance Architecture

ARIA treats deterministic scoring as the authoritative hiring-assessment basis. LLM output is advisory narrative. Authorized humans remain accountable for employment actions.

## Source of truth

- `ScreeningDecision` is the immutable historical decision.
- `ScreeningResult` is the current projection, pointed to by `current_decision_id`.
- `AIDecisionLog` is the mandatory audit event for each new authoritative decision.
- `DecisionNarrative` is optional enrichment bound to one `screening_decision_id`. Identity is `(screening_decision_id, narrative_type, source_operation_id)`.

Interview scorecards and auto-status mappings are post-screening evaluations. They do not rewrite a screening decision.

## Retention and deletion

`ScreeningDecision` is immutable while retained. It is not undeletable forever.

- Ordinary reanalysis, rescore, and refresh create new ledger rows. They do not delete history.
- There is no public `DELETE /decisions/{id}` endpoint.
- Deleting a `ScreeningResult` (GDPR/candidate erasure) CASCADE-deletes its `ScreeningDecision` rows and `DecisionNarrative` rows.
- `AIDecisionLog.screening_decision_id` and `TrainingExample.screening_decision_id` are SET NULL.
- `TrainingExample.screening_result_id` has no ON DELETE CASCADE; privacy workflows must remove or reassign those rows before deleting the result.

## Known open reliability item

`PHASE1_2_VOICE_SCHEDULING_IDEMPOTENCY` remains open and is not part of this architecture.

This document describes designed auditability and explainability support. It does not claim regulatory certification.
