# Candidate duplicate hierarchy (AUD-040)

Merges are **explicit and manual**. Nothing auto-merges on name similarity.

| Rank | Match | Action |
|---|---|---|
| 1 | Same tenant + same stored email | Suggest merge; recruiter/admin confirms |
| 2 | Same tenant + same resume file hash | Suggest merge; recruiter/admin confirms |
| 3 | Same tenant + same phone **and** same normalized name | Suggest merge; recruiter/admin confirms |
| 4 | Name-only or other weak similarity | Ambiguous — human review only |

API: `POST /api/candidates/merge` (`require_recruiter_or_admin`).

Service: `merge_candidates(db, tenant_id, source_candidate_id, target_candidate_id, actor_user_id, reason=None)`.

Conflict rule: keep target identity fields when both sides are set; fill blanks from source; record discarded values on `candidate_merge_events`.
