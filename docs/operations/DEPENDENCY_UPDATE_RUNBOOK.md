# Production image update and rollback

Third-party images in the production and staging compose files are pinned to a registry manifest digest (`name@sha256:...`). First-party production images are not tags in git. `docker-compose.prod.yml` requires `BACKEND_IMAGE`, `FRONTEND_IMAGE`, `NGINX_IMAGE`, `LIVEKIT_IMAGE`, `SPEECH_SERVICE_IMAGE`, and `VOICE_AGENT_IMAGE` to be set to `repository@sha256:digest` values. Local `docker-compose.yml` stays on floating tags for development.

Digests below were read from the Docker Hub manifest list on 2026-10-21. A manifest-list digest covers the architectures in that index.

| Image | Digest |
|---|---|
| `postgres:16-alpine` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` |
| `redis:7-alpine` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` |
| `ollama/ollama:latest` | `sha256:2e6cc92ffa43b4b1a2e70e315407e00ac97ec125afc71277d73c7efe01c4f31b` |
| `containrrr/watchtower` and `containrrr/watchtower:1.7.1` | `sha256:6dd50763bbd632a83cb154d5451700530d1e44200b268a4e9488fefdfcf2b038` |
| `certbot/certbot` | `sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20` |
| `clamav/clamav:1.4` | `sha256:3bba6dd5ac6663d55b80f86a3126e146612d93b593cc2617188d13f637d91260` |
| `livekit/sip:latest` | `sha256:e675638b7678eadb19d48c7a8865149d1b7c23a95b2e3c6fae173684d70c62fc` |

## Update

1. Resolve the new manifest-list digest. Do not copy a single-platform blob digest.

```powershell
python -c "import json,urllib.request; repo='library/postgres'; tag='16-alpine'; auth=json.load(urllib.request.urlopen(f'https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull')); req=urllib.request.Request(f'https://registry-1.docker.io/v2/{repo}/manifests/{tag}', headers={'Authorization':'Bearer '+auth['token'],'Accept':'application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json'});
resp=urllib.request.urlopen(req); print(resp.headers['Docker-Content-Digest'])"
```

2. Replace the digest in every compose file that references that image. Keep the human-readable tag in front of `@sha256:`.
3. Run `python -m pytest app/backend/tests/test_deployment_config.py::test_production_and_staging_third_party_images_are_digest_pinned -q`.
4. Scan the new digest with the repository Trivy workflow before deploying.
5. Deploy by setting the first-party digest variables and recreating the stack:

```powershell
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml up -d
```

Watchtower stays monitor-only unless `WATCHTOWER_MONITOR_ONLY=false`. It cannot move a digest pin by itself. A newer image requires a committed digest change.

## Rollback

1. Check out the previous compose digest, or set the previous first-party `*_IMAGE` digest.
2. Run `docker compose -f docker-compose.prod.yml up -d`.
3. Confirm `docker compose -f docker-compose.prod.yml ps` and the backend health check before sending traffic back.
