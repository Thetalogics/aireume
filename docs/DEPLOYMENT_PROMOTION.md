# Deployment promotion (AUD-049)

Images are built **once** in `.github/workflows/cd.yml` and tagged with:

- immutable git SHA: `:${{ github.sha }}`
- environment alias: `:staging` or `:latest`

The backend build also emits an image **digest** (`steps.backend.outputs.digest`) and SBOM/provenance when Buildx supports it.

## Build

1. CI on `main` / `production` must pass.
2. CD builds and pushes SHA-tagged images (do not treat `:latest` as identity).
3. Record `GIT_SHA` + backend digest from the workflow summary.

## Staging promotion

Pull and run the SHA (or digest) that CD just produced:

```bash
export SHA=<git sha>
docker compose -f docker-compose.staging.yml pull
docker compose -f docker-compose.staging.yml up -d
```

Prefer digest pin when the registry digest is known:

```yaml
image: <user>/resume-backend@sha256:<digest>
```

## Production promotion

Do **not** rebuild from source after approval. Promote the same digest/SHA that passed staging:

```bash
export SHA=<git sha that passed staging>
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Record deployed SHA and digest in the release notes / ops log.

## Rollback

Redeploy the previous known-good SHA or digest:

```bash
export SHA=<previous git sha>
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Verification

- `/api/version` `build_id` equals the git SHA.
- Image inspect: `docker image inspect <image> --format '{{index .RepoDigests 0}}'`.

Branch protection for `main` is required separately (`docs/BRANCH_PROTECTION_REQUIRED.md`).
