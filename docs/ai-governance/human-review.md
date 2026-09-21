# Human Review

AI recommendations are advisory. Final employment action remains subject to authorized human review.

Human overrides create a new immutable `HUMAN_OVERRIDE` decision. They preserve historical deterministic scores and the prior AI recommendation. They require an actor, reason code, and the expected current decision id. Stale overrides return 409.

Override is available to active recruiters and admins. Cross-tenant access is blocked.
