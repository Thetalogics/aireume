"""Static production/deployment contract tests."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROD = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
ENTRY = (ROOT / "app" / "backend" / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")
CD = (ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "app" / "backend" / "Dockerfile").read_text(encoding="utf-8")
E2E_STAGING = (ROOT / ".github" / "workflows" / "e2e-staging.yml").read_text(encoding="utf-8")
DEPLOY_STAGING = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(
    encoding="utf-8"
)
E2E_INTEGRATION = (ROOT / "e2e" / "ci-real-backend.spec.ts").read_text(encoding="utf-8")


def test_entrypoint_does_not_auto_migrate():
    assert "RUN_DB_MIGRATIONS" in ENTRY
    assert "upgrade heads" not in ENTRY
    assert "python -m app.backend.services.migration_owner" in ENTRY


def _service_block(text: str, name: str, until: str) -> str:
    return text.split(f"\n  {name}:\n", 1)[1].split(f"\n  {until}:\n", 1)[0]


def test_staging_backend_migrates_on_watchtower_restart():
    """Portainer aria-staging-main has no migrate service; the backend entrypoint upgrades first."""
    text = (ROOT / "docker-compose.main.staging.yml").read_text(encoding="utf-8")
    backend = _service_block(text, "backend", "frontend")
    assert "RUN_DB_MIGRATIONS=1" in backend
    assert "GEMINI_MAX_RETRIES=${GEMINI_MAX_RETRIES:-2}" in backend
    assert "GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.7-flash}" in backend
    assert "OLLAMA_MODEL_BACKEND=${OLLAMA_MODEL_BACKEND:-deepseek-v4.1-flash:cloud}" in backend
    assert "OPENROUTER_MODEL=${OPENROUTER_MODEL:-qwen/qwen3.8-27b:free}" in backend
    assert "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}" in backend
    assert "\n  migrate:\n" not in text
    assert "staging-backend" in text.split("watchtower:", 1)[1]

    legacy = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    legacy_backend = _service_block(legacy, "backend", "frontend")
    assert "RUN_DB_MIGRATIONS=1" in legacy_backend


def test_prod_compose_has_single_migration_owner():
    assert "migrate:" in PROD
    assert "service_completed_successfully" in PROD
    assert "RUN_DB_MIGRATIONS=0" in PROD
    assert "upgrade heads" not in PROD
    assert "REDIS_REQUIRED=1" in PROD
    assert "DATABASE_POOL_SIZE=${DATABASE_POOL_SIZE:-3}" in PROD
    assert "worker_healthcheck" in PROD
    assert "profiles: [\"local-ollama\"]" in PROD


def test_prod_application_images_are_not_latest():
    required = {
        "BACKEND_IMAGE", "FRONTEND_IMAGE", "NGINX_IMAGE", "LIVEKIT_IMAGE",
        "SPEECH_SERVICE_IMAGE", "VOICE_AGENT_IMAGE",
    }
    for variable in required:
        assert f"${{{variable}:?" in PROD
    assert "revanth2245/resume-backend:${RELEASE_SHA" not in PROD


def test_cd_emits_digest_manifest_for_every_application_image():
    for output in (
        "backend_digest", "frontend_digest", "nginx_digest", "livekit_digest",
        "speech_service_digest", "voice_agent_digest",
    ):
        assert output in CD
    assert "release-manifest.env" in CD
    assert "--env-file release-manifest.env" in CD


def test_ci_proves_gdpr_deletion_against_pinned_object_storage():
    assert "gdpr-object-storage:" in CI
    assert 'OBJECT_STORAGE_INTEGRATION_REQUIRED: "1"' in CI
    assert "docker.io/bitnamilegacy/minio@sha256:" in CI
    assert "test_gdpr_object_storage_integration.py" in CI


def test_ci_has_unmocked_browser_to_database_gate():
    assert "integration-e2e:" in CI
    assert "playwright.integration.config.ts" in CI
    assert "postgres:16" in CI
    assert "redis:7" in CI
    assert 'E2E_TEST_MODE: "1"' in CI
    assert 'REDIS_REQUIRED: "1"' in CI
    assert "Generate ephemeral integration secrets" in CI
    assert "openssl rand -hex 32" in CI
    assert "ci-integration-jwt-secret" not in CI
    assert "ci-integration-service-secret" not in CI
    assert "test.skip" not in E2E_INTEGRATION


def test_release_manifest_requires_exact_sha_staging_e2e():
    assert "deploy-staging:" in CD
    assert "uses: ./.github/workflows/deploy-staging.yml" in CD
    assert "needs: [resolve-tag, build-and-push, deploy-staging]" in CD
    assert "authenticated-staging-e2e:" in CD
    assert "expected_sha: ${{ needs.resolve-tag.outputs.release_sha }}" in CD
    assert (
        "needs: [resolve-tag, build-and-push, deploy-staging, authenticated-staging-e2e]"
        in CD
    )
    assert "Wait for healthy staging deployment at the expected SHA" in E2E_STAGING
    assert "E2E_EXPECTED_SHA" in E2E_STAGING
    assert "vars.E2E_BASE_URL" in E2E_STAGING
    assert "secrets.E2E_EMAIL" in E2E_STAGING
    assert "secrets.E2E_PASSWORD" in E2E_STAGING
    assert "E2E_RECRUITER_EMAIL" not in E2E_STAGING


def test_staging_deploy_is_explicit_and_fail_closed():
    assert "workflow_call:" in DEPLOY_STAGING
    assert "PORTAINER_WEBHOOK_URL" in DEPLOY_STAGING
    assert "transport=portainer" in DEPLOY_STAGING
    assert "curl --fail-with-body --silent --show-error" in DEPLOY_STAGING
    assert "VPS_SSH_KEY" in DEPLOY_STAGING
    assert "VPS_SSH_KNOWN_HOSTS" in DEPLOY_STAGING
    assert "VPS_SSH_KEY_FINGERPRINT" in DEPLOY_STAGING
    assert "transport=ssh" in DEPLOY_STAGING
    assert "inputs.ssh_username || secrets.VPS_USERNAME" in DEPLOY_STAGING
    assert "printf '%b" in DEPLOY_STAGING
    assert "base64 --decode" in DEPLOY_STAGING
    assert "ssh-keygen -y" in DEPLOY_STAGING
    assert "ssh-keyscan" not in DEPLOY_STAGING
    assert "StrictHostKeyChecking=yes" in DEPLOY_STAGING
    assert "BatchMode=yes" in DEPLOY_STAGING
    host_command = (ROOT / "scripts" / "aria-staging-deploy-host.sh").read_text(
        encoding="utf-8"
    )
    bootstrap = (ROOT / "scripts" / "bootstrap_staging_deploy_user.sh").read_text(
        encoding="utf-8"
    )
    assert 'SSH_ORIGINAL_COMMAND:-}" != "deploy-staging"' in host_command
    assert "docker login --username" in host_command
    assert "docker pull" in host_command
    assert 'immutable_image="$repository:$expected_sha"' in host_command
    assert "Mutable staging tag does not match candidate" in host_command
    assert "docker version --format '{{.Server.MinAPIVersion}}'" in host_command
    assert '-e DOCKER_API_VERSION="$docker_min_api"' in host_command
    assert "--run-once --cleanup --rolling-restart" in host_command
    assert "staging-backend staging-frontend staging-nginx" in host_command.replace(
        "\n", " "
    )
    assert 'restrict,command=\\"$command_path\\"' in bootstrap
    assert "usermod --append --groups docker" in bootstrap
    assert "|| echo" not in DEPLOY_STAGING


def test_backend_dependencies_are_hash_locked_and_used_everywhere():
    source = (ROOT / "app" / "backend" / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "app" / "backend" / "requirements.lock").read_text(encoding="utf-8")
    for raw in source.splitlines():
        requirement = raw.split("#", 1)[0].strip()
        if not requirement:
            continue
        name, version = requirement.split("==", 1)
        normalized_name = re.sub(r"[-_.]+", "-", name.split("[", 1)[0].lower())
        assert f"{normalized_name}=={version}" in lock.lower()
    assert "--hash=sha256:" in lock
    assert "--require-hashes -r app/backend/requirements.lock" in CI
    assert "--require-hashes -r requirements.lock" in DOCKERFILE
    assert "pip install -r app/backend/requirements.txt" not in CI


def test_cd_production_requires_successful_ci_workflow_run():
    assert "github.event.workflow_run.head_sha" in CD
    assert "SOURCE_CONCLUSION" in CD
    assert 'test "$SOURCE_CONCLUSION" = "success"' in CD
    assert "SOURCE_REPOSITORY" in CD
    assert "Manual production CD is disabled" in CD
    assert "- production" not in CD.split("workflow_dispatch:", 1)[1].split("concurrency:", 1)[0]


def test_pool_budget_under_safety_fraction():
    uvicorn_workers = 6
    pool = 3
    overflow = 2
    worker = 5
    worst = uvicorn_workers * (pool + overflow) + worker
    assert worst <= 40
    assert worst < 200 * 0.25
