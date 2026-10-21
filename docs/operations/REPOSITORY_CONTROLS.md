# Repository controls

Fetched 2026-10-21 from `GET /repos/Thetalogics/aireume/branches/main/protection` after the protection update. The account used for the call has admin on the repository. Rulesets were an empty list before the update.

`main` now has:

- Required status checks, strict: `test-backend`, `test-frontend`, `test-e2e`, `integration-e2e`, `migration-check`, `dependency-scan`, `reliability-postgres`, `reliability-redis`, `gdpr-object-storage`, `compose-validate`, `filesystem-scan`, `secret-scan`.
- Force pushes disabled.
- Branch deletion disabled.
- Admin enforcement disabled. The repository admin can still push directly to `main`.
- No required approving review.

`secret-scan-history` is not required. That job runs only on the Monday schedule and on manual dispatch, and a skipped required check would block pull requests.

`toolchain-contract` is in the working tree and is not required yet. Add it to the required contexts after that workflow job has run on `main`.

## Remaining control

A second approving review is not configured. With one maintainer, requiring one approval blocks that maintainer from merging their own pull request. Turn it on when a second reviewer exists:

```powershell
gh api --method PATCH repos/Thetalogics/aireume/branches/main/protection/required_pull_request_reviews --input review.json
```

`review.json`:

```json
{
  "required_approving_review_count": 1,
  "dismiss_stale_reviews": true,
  "require_code_owner_reviews": false
}
```

To stop the admin bypass:

```powershell
gh api --method POST repos/Thetalogics/aireume/branches/main/protection/enforce_admins
```

That also blocks direct pushes to `main`. Pull requests become the only update path.
