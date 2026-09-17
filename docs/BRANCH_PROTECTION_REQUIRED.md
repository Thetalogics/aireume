# Branch protection required (AUD-049)

GitHub App permissions in this workspace cannot enable protection. Until the settings below are actually on `Thetalogics/aireume`, AUD-049 remains **PARTIAL**.

Required on `main` (and `production`):

1. Require a pull request before merging.
2. Require at least one approving review.
3. Require status checks to pass: backend pytest, frontend tests/lint/build, Alembic, Gitleaks, pip-audit, Trivy.
4. Do not allow bypass of those checks for administrators except a documented break-glass role.
5. Disable direct pushes if organization policy allows.
6. Deploy only from images tagged with the git SHA (`:${{ github.sha }}`) in addition to environment tags; record the digest in the release notes.
7. Rollback: `docker compose pull` the previous SHA tag and `up -d`.

Do not mark AUD-049 CLOSED until a screenshot or `gh api` dump shows these rules are active.
