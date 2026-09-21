# Trivy Dockerfile exceptions

These are path-scoped misconfiguration exceptions in `.trivyignore.yaml`.
Whole-class ignores (all Dockerfiles, all DS-0002) are not used.

| Rule | File | Why | Compensating control | Review |
| --- | --- | --- | --- | --- |
| AVD-DS-0002 | `nginx/Dockerfile` | Official `nginx:alpine` master binds :80 as root; workers drop to `nginx` | Baked `nginx.prod.conf` only; prod image tagged with `RELEASE_SHA` | 2027-03-21 |
| AVD-DS-0002 | `app/voice_agent/Dockerfile.livekit` | Upstream LiveKit server image has no supported non-root user | Config written to `/tmp`; prod image `resume-livekit:${RELEASE_SHA}` | 2027-03-21 |

Application images (`app/backend/Dockerfile`, `Dockerfile.agent`,
`app/voice_agent/Dockerfile.cloud`, `app/voice_agent/Dockerfile`,
`app/speech_service/Dockerfile`, `app/frontend/Dockerfile`) run as a
non-root user. Apt installs use `--no-install-recommends` and delete
`/var/lib/apt/lists/*`.
