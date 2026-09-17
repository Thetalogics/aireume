# Branch protection (AUD-049)

AUD-049 is **CLOSED by owner instruction**.

On 2026-09-17, `main` was enabled as protected (`protected: true`) with:

- Require pull request reviews (1 approval)
- Dismiss stale reviews
- Require conversation resolution
- Require status checks and branch up to date (`strict: true`)
- Checks: `test-backend`, `test-frontend`, `migration-check`, `dependency-scan`, `secret-scan`, `filesystem-scan`, `test-e2e`
- Enforce admins
- Force pushes disabled
- Deletion disabled

The same day, the owner asked to **remove** that protection so direct pushes to `main` can proceed. Current API state is `protected: false`. The finding stays CLOSED on that instruction, with immutable SHA/digest deploy still documented in `docs/DEPLOYMENT_PROMOTION.md`.

To restore the rules above: GitHub → Settings → Branches, or PUT `/repos/Thetalogics/aireume/branches/main/protection`.
