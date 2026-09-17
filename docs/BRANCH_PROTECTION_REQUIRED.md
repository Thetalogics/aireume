# Branch protection required (AUD-049)

`main` on `Thetalogics/aireume` is **protected** (`protected: true` via GitHub API).

Configured:

- Require pull request reviews (1 approval)
- Dismiss stale reviews
- Require conversation resolution
- Require status checks and branch up to date (`strict: true`)
- Checks: `test-backend`, `test-frontend`, `migration-check`, `dependency-scan`, `secret-scan`, `filesystem-scan`, `test-e2e`
- Enforce admins
- Force pushes disabled
- Deletion disabled

If these ever drift, restore them in GitHub → Settings → Branches, or PUT `/repos/Thetalogics/aireume/branches/main/protection`.

Deploy only SHA-tagged or digest-pinned images (`docs/DEPLOYMENT_PROMOTION.md`).
