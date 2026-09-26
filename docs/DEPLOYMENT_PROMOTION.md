# Deployment promotion (AUD-049)

Images are built **once** in `.github/workflows/cd.yml` and tagged with:

- immutable git SHA: `:${{ github.sha }}`
- staging alias on `main`: `:staging`

Every ARIA application build emits an image digest. CD writes those digests to
the `release-manifest-<sha>` artifact. Backend SBOM/provenance is also enabled
when Buildx supports it.

## Build

1. CI on `main` / `production` must pass.
2. CD builds and pushes SHA-tagged images (do not treat an environment alias as identity).
3. The staging deployment must report that exact SHA from both `/api/version`
   and the frontend build marker, then pass the authenticated critical flow.
4. Only after that gate passes does CD emit the digest-pinned release manifest.
5. Retain the generated release manifest with the release evidence.

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

The `staging-e2e` GitHub environment must target a deployment mechanism that
updates staging from the `:staging` alias on `main`. The release gate waits up to
six minutes for the candidate SHA and fails if staging remains on an older build.

## Production promotion

Do **not** rebuild from source after approval. Download the approved
`release-manifest-<sha>` artifact and deploy those exact digests:

```bash
docker compose --env-file release-manifest.env -f docker-compose.prod.yml pull
docker compose --env-file release-manifest.env -f docker-compose.prod.yml up -d
```

Record the manifest artifact and deployed SHA in the release notes / ops log.

## Rollback

Redeploy the previous known-good manifest:

```bash
docker compose --env-file previous-release-manifest.env -f docker-compose.prod.yml pull
docker compose --env-file previous-release-manifest.env -f docker-compose.prod.yml up -d
```

## Verification

- `/api/version` `build_id` equals the git SHA.
- Frontend `<html data-build-id>` equals the same git SHA.
- Image inspect: `docker image inspect <image> --format '{{index .RepoDigests 0}}'`.

Branch protection for `main` is required separately (`docs/BRANCH_PROTECTION_REQUIRED.md`).
