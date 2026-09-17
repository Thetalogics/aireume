# Production readiness

This product is **not** claimed “enterprise ready,” “FCRA compliant,” or “AI fine-tuned.”

Operational requirements still include:

- Redis required for production SAML request state and recommended for rate limits/queues.
- PostgreSQL for production (SQLite is local/test).
- Object storage (S3/MinIO) for resumes; BYTEA is legacy.
- Branch protection on `main` (see `BRANCH_PROTECTION_REQUIRED.md`).
- Promote immutable SHA-tagged images; do not treat `latest`/`staging` as the only identity.
- Counsel review of adverse-action notices; the product provides workflow assistance only.
- Remaining audit items in `AUDIT_CLOSURE_STATUS.md` marked PARTIAL/OPEN.
