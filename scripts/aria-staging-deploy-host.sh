#!/usr/bin/env bash
set -euo pipefail

# This command is bound to the staging deployment key in authorized_keys. The
# key cannot request a shell, forwarding, or any command other than this one.
if [[ "${SSH_ORIGINAL_COMMAND:-}" != "deploy-staging" ]]; then
  echo "This key may only run the ARIA staging deployment." >&2
  exit 64
fi

IFS= read -r registry_user
IFS= read -r registry_token
IFS= read -r expected_sha

if [[ ! "$registry_user" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
  echo "Invalid registry username." >&2
  exit 65
fi
if [[ -z "$registry_token" ]]; then
  echo "Missing registry token." >&2
  exit 65
fi
if [[ ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid release SHA." >&2
  exit 65
fi

readonly services=(staging-backend staging-frontend staging-nginx)
readonly image_repositories=(
  "$registry_user/resume-backend"
  "$registry_user/resume-frontend"
  "$registry_user/resume-nginx"
)

for container in "${services[@]}" staging-watchtower; do
  docker inspect "$container" >/dev/null
done

printf '%s' "$registry_token" |
  docker login --username "$registry_user" --password-stdin
unset registry_token

restore_monitor() {
  docker start staging-watchtower >/dev/null 2>&1 || true
  docker logout >/dev/null 2>&1 || true
}
trap restore_monitor EXIT

for repository in "${image_repositories[@]}"; do
  immutable_image="$repository:$expected_sha"
  staging_image="$repository:staging"
  docker pull "$immutable_image"
  docker pull "$staging_image"
  immutable_id="$(docker image inspect "$immutable_image" --format '{{.Id}}')"
  staging_id="$(docker image inspect "$staging_image" --format '{{.Id}}')"
  if [[ "$immutable_id" != "$staging_id" ]]; then
    echo "Mutable staging tag does not match candidate $expected_sha for $repository." >&2
    exit 66
  fi
done

watchtower_image="$(docker inspect staging-watchtower --format '{{.Image}}')"
docker_config="$HOME/.docker/config.json"
docker_min_api="$(docker version --format '{{.Server.MinAPIVersion}}')"
test -s "$docker_config"
if [[ ! "$docker_min_api" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Docker daemon did not report a valid minimum API version." >&2
  exit 67
fi

docker stop staging-watchtower >/dev/null
docker rm -f aria-staging-release >/dev/null 2>&1 || true
docker run --rm \
  --name aria-staging-release \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$docker_config:/config.json:ro" \
  -e DOCKER_API_VERSION="$docker_min_api" \
  "$watchtower_image" \
  --run-once --cleanup --rolling-restart \
  "${services[@]}"

restore_monitor
trap - EXIT
echo "Staging containers reconciled for candidate $expected_sha."
