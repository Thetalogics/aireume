# Production readiness

This product is **not** independently certified as enterprise-ready, FCRA/GDPR/SOC 2/HIPAA/ISO compliant, bias-free, or AI fine-tuned.

## Verified in this repository

- Tenant-scoped screening, merge, GDPR export/erasure **workflows**
- Automated backend/frontend tests and CI jobs (see `.github/workflows/ci.yml`)
- Alembic migrations with a single head
- Prometheus metrics and suggested alerts (`docs/SLO_AND_ALERTING.md`)
- SHA-tagged container builds and digest output (`docs/DEPLOYMENT_PROMOTION.md`)
- Authenticated staging E2E **workflow** (success depends on secrets + a live staging environment)

## Environment-dependent

- Redis (SAML request state, recommended rate limits/queues)
- PostgreSQL (SQLite is local/test only)
- Object storage (S3/MinIO) for resumes
- LLM provider credentials
- Stripe/billing webhook secrets
- Staging URL and E2E credentials
- GitHub branch protection on `main` (must be enabled in the GitHub UI/API)

## Required production services

PostgreSQL, Redis, object storage, reverse proxy/TLS, workers, metrics scrape of `/metrics`.

## Required secrets

`JWT_SECRET_KEY`, `DATABASE_URL` / `POSTGRES_PASSWORD`, `REDIS_URL`, `INTERNAL_SERVICE_SECRET`, LLM keys, `S3_*`, billing webhook secret, `INTEGRATION_MASTER_KEY`. Never commit them.

## Branch protection

Required: `docs/BRANCH_PROTECTION_REQUIRED.md`. AUD-049 stays PARTIAL until `gh api` shows `protected: true`.

## Monitoring

Scrape Prometheus metrics. Apply initial alerts from `docs/SLO_AND_ALERTING.md`.

## Backup / restore

PostgreSQL backups and object-storage versioning are operational requirements, not provided by application code.

## Staging E2E

`.github/workflows/e2e-staging.yml` needs `E2E_WORKSPACE`, `E2E_EMAIL`, `E2E_PASSWORD`, optional `E2E_BASE_URL`. A skip because secrets are missing is **not** a successful authenticated E2E run.
