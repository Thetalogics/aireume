# Deployment reliability

## Alembic drift gate (Option B)

CI does **not** treat generic `alembic check` as authoritative. `alembic/env.py`
detaches Phase 2 columns (`current_decision_id`, `screening_decision_id`) from live
ORM metadata so revision `001` `create_all` cannot create 081-owned columns.
Autogenerate/check therefore compares a deliberately incomplete metadata graph
with the migrated database and can return nonzero without meaning “head is
behind,” or miss real post-080 drift.

The hard gate is `app/backend/tests/test_alembic_drift.py`: checksum immutability
of revisions through `080_phase1_reliability_closure` via
`alembic/historical_migration_manifest.json` (SHA-256 of exact file bytes, no Git
history at runtime), plus an explicit post-080 schema contract and a
downgrade 082→081 / upgrade 081→082 cycle.

## Migration owner

`RUN_DB_MIGRATIONS=1` runs `python -m app.backend.services.migration_owner` before the process serves.
Production backend and worker stay at `0` and wait for the one-shot `migrate` service (`service_completed_successfully`) because `docker compose up` recreates it.
The Portainer stack `aria-staging-main` has no migrate service. Its backend sets `RUN_DB_MIGRATIONS=1`, so the image entrypoint runs `upgrade head` before uvicorn binds. After publishing images, CD prefers the configured Portainer stack webhook and otherwise authenticates to the staging VPS and invokes its existing Watchtower image once to reconcile `staging-backend`, `staging-frontend`, and `staging-nginx`. Persistent Watchtower remains a secondary drift-recovery mechanism, not the release trigger.
Command is `upgrade head` (singular). A PostgreSQL advisory lock prevents concurrent manual upgrades.

## Connection budget (PostgreSQL `max_connections=200`)

```
6 uvicorn workers × (pool_size 3 + overflow 2) = 30
dedicated worker × (3 + 2) = 5
reserved ops/migrations/monitoring ≈ 40
worst-case app ≈ 35
```

Production Compose defaults are `DATABASE_POOL_SIZE=3` and `DATABASE_MAX_OVERFLOW=2`.
PgBouncer is not assumed.

## Redis

Production sets `REDIS_REQUIRED=1`. `/ready` and worker health fail when Redis is down.
Auth and LLM concurrency are Redis-backed and fail closed in production.

## Idempotency

`X-Idempotency-Key` uses a PostgreSQL state machine (`processing` / `completed` / `failed`).
Voice schedule and unified quick interview reuse that primitive.
Multipart uploads are excluded from generic body fingerprinting.

## Uploads

Nginx `client_max_body_size 500M` is the outer cap. Application limits:
- JSON default 25 MB
- resume/analyze 25 MB
- video 200 MB
- batch 500 MB

Multipart routes are not fully buffered by global middleware.

## Images

CD publishes a `release-manifest-<sha>` artifact containing a digest reference
for every ARIA application image. Production Compose rejects missing image
variables. Deploy and roll back with an approved manifest:

```
docker compose --env-file release-manifest.env -f docker-compose.prod.yml pull
docker compose --env-file release-manifest.env -f docker-compose.prod.yml up -d
```

## Ollama

Local Ollama is the `local-ollama` Compose profile. Cloud default does not start it.

## Backup

`scripts/backup_postgres.sh` writes `pg_dump -Fc`. Restore requires `CONFIRM_RESTORE=YES`.
PITR/WAL archiving is not implemented.

## Authenticated E2E

CI first runs `playwright.integration.config.ts` against the checked-out frontend and
backend with disposable PostgreSQL and Redis services. That suite performs real
registration, email verification, TOTP enrollment, login, onboarding, session
reload, and logout without mocking browser-to-API traffic.

CD calls `.github/workflows/deploy-staging.yml` after publishing the candidate
images, then calls `.github/workflows/e2e-staging.yml`. Deployment requires
either `PORTAINER_WEBHOOK_URL`, or the SSH fallback set `VPS_HOST`,
`VPS_USERNAME`, `VPS_SSH_KEY`, `VPS_SSH_KNOWN_HOSTS`,
`VPS_SSH_KEY_FINGERPRINT`, `DOCKERHUB_USERNAME`, and `DOCKERHUB_TOKEN`.
The SSH identity is a dedicated `aria-deploy` account whose authorized key is
bound to `/usr/local/sbin/aria-staging-deploy` with OpenSSH `restrict`. The
workflow pins both the private-key fingerprint and the VPS host key; it never
learns a host key on first use. Provision or rotate the identity with
`scripts/bootstrap_staging_deploy_user.sh` and
`scripts/aria-staging-deploy-host.sh` from a trusted VPS console.
The host command pulls both the immutable commit tag and `staging` for every
main-server image and refuses deployment unless their image IDs match. The
candidate SHA therefore remains authoritative even though the existing
Watchtower-managed containers follow the mutable staging tag.
The one-shot Watchtower process receives the Docker daemon's advertised minimum
API version, avoiding client-version failures after Docker Engine upgrades.
The webhook is preferred when both transports are configured. Browser validation
requires GitHub environment `staging-e2e`
variable `E2E_BASE_URL` plus `E2E_WORKSPACE`, `E2E_EMAIL`, and `E2E_PASSWORD`
secrets for a dedicated non-privileged account. It waits for
both `/ready` and `/api/version`, requires the deployed backend and frontend to
report the candidate SHA, and runs the authenticated critical flow. Missing
secrets, transport failures, deployment drift, and test failures all block release-manifest creation;
none are converted to skips.

## Internal header

Canonical: `X-Internal-Service-Secret`. `X-Internal-Secret` is still accepted with a deprecation warning.
