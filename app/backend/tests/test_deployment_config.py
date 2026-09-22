"""Static production/deployment contract tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROD = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
ENTRY = (ROOT / "app" / "backend" / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")


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
    for line in PROD.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:") and "revanth2245/resume-" in stripped:
            assert ":latest" not in stripped
            assert "RELEASE_SHA" in stripped


def test_pool_budget_under_safety_fraction():
    uvicorn_workers = 6
    pool = 3
    overflow = 2
    worker = 5
    worst = uvicorn_workers * (pool + overflow) + worker
    assert worst <= 40
    assert worst < 200 * 0.25
