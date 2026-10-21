"""
Tests that the beta prompt-personalization endpoint is gated:
  - disabled by default in production
  - enabled by default in non-production
  - explicit TRAINING_ENABLED override wins
  - POST /api/training/train returns 403 when disabled
"""
from datetime import datetime, timezone
import json

import httpx
import pytest

from app.backend.db import database
from app.backend.models.db_models import Tenant, TrainingRun, User
from app.backend.routes import training
from app.backend.routes.training import _training_enabled


class TestTrainingGateFlag:
    def test_defaults_enabled_in_development(self, monkeypatch):
        monkeypatch.delenv("TRAINING_ENABLED", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert _training_enabled() is True

    def test_defaults_disabled_in_production(self, monkeypatch):
        monkeypatch.delenv("TRAINING_ENABLED", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _training_enabled() is False

    def test_explicit_true_overrides_production(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("TRAINING_ENABLED", "true")
        assert _training_enabled() is True

    def test_explicit_false_overrides_development(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("TRAINING_ENABLED", "false")
        assert _training_enabled() is False


class TestTrainingEndpointGate:
    def test_train_returns_403_when_disabled(self, auth_client, monkeypatch):
        monkeypatch.setenv("TRAINING_ENABLED", "false")
        resp = auth_client.post("/api/training/train")
        assert resp.status_code == 403
        assert "beta" in resp.json()["detail"].lower() or "disabled" in resp.json()["detail"].lower()

    def test_production_default_explains_disabled_prompt_personalization(self, auth_client, monkeypatch):
        monkeypatch.delenv("TRAINING_ENABLED", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")

        response = auth_client.post("/api/training/train")

        assert response.status_code == 403
        detail = response.json()["detail"].lower()
        assert "prompt personalization" in detail
        assert "beta" in detail
        assert "disabled" in detail


@pytest.fixture
def training_tenant(db):
    tenant = Tenant(name="Training tests", slug="training-tests")
    db.add(tenant)
    db.commit()
    return tenant.id


@pytest.fixture
def training_provider(monkeypatch):
    """Use the real HTTP client/response with only the provider transport replaced."""
    client_class = httpx.AsyncClient
    monkeypatch.setattr(training, "OLLAMA_BASE_URL", "https://training-provider.test")

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            training.httpx,
            "AsyncClient",
            lambda **kwargs: client_class(transport=transport, **kwargs),
        )

    return install


def persisted_run(tenant_id):
    with database.SessionLocal() as session:
        return session.query(TrainingRun).filter_by(tenant_id=tenant_id).one()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 500])
async def test_provider_http_failure_is_persisted(
    db, training_tenant, training_provider, status_code
):
    # A failed rerun must also discard a previous completion timestamp.
    db.add(TrainingRun(
        tenant_id=training_tenant,
        state="completed",
        completed_at=datetime.now(timezone.utc),
    ))
    db.commit()
    provider_states = []

    def respond(request):
        provider_states.append(persisted_run(training_tenant).state)
        return httpx.Response(
            status_code,
            text="RESPONSE_BODY_SECRET_SENTINEL" * 1000,
            request=request,
        )

    training_provider(respond)
    await training._train_model(training_tenant, "aria-test", [])

    run = persisted_run(training_tenant)
    assert provider_states == ["running"]
    assert run.state == "failed"
    assert run.completed_at is None
    assert run.error and len(run.error) <= 500
    assert "RESPONSE_BODY_SECRET_SENTINEL" not in run.error
    assert "training-provider.test" not in run.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.HTTPError,
    OSError,
    ValueError,
    RuntimeError,
])
async def test_provider_exception_is_persisted_safely(
    db, training_tenant, training_provider, error_type
):
    db.add(TrainingRun(
        tenant_id=training_tenant,
        state="completed",
        completed_at=datetime.now(timezone.utc),
    ))
    db.commit()
    secret_message = (
        "RESPONSE_BODY_SECRET_SENTINEL https://user:credential@provider.test/\n"
        "Traceback (most recent call last):\n" + "x" * 2000
    )

    def fail(_request):
        raise error_type(secret_message)

    training_provider(fail)
    await training._train_model(training_tenant, "aria-test", [])

    run = persisted_run(training_tenant)
    assert run.state == "failed"
    assert run.error and len(run.error) <= 500
    for sensitive in ("RESPONSE_BODY_SECRET_SENTINEL", "credential", "provider.test", "Traceback", "\n"):
        assert sensitive not in run.error
    assert run.completed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,content", [
    pytest.param(200, b"RESPONSE_BODY_SECRET_SENTINEL", id="malformed-json"),
    pytest.param(200, b"", id="empty-200"),
    pytest.param(204, b"", id="empty-204"),
    pytest.param(200, b'["success"]', id="array"),
    pytest.param(200, b'"success"', id="string"),
    pytest.param(200, b"null", id="null"),
    pytest.param(200, b"200", id="number"),
    pytest.param(200, b"true", id="boolean"),
    pytest.param(200, b'{"detail":"RESPONSE_BODY_SECRET_SENTINEL"}', id="missing-status"),
    pytest.param(200, b'{"status":"failed"}', id="failed-status"),
    pytest.param(200, b'{"status":"RESPONSE_BODY_SECRET_SENTINEL"}', id="unknown-status"),
    pytest.param(200, b'{"status":"writing manifest"}', id="incomplete-status"),
    pytest.param(200, b'{"error":{"message":"RESPONSE_BODY_SECRET_SENTINEL"}}', id="error-object"),
    pytest.param(200, b'{"status":"success","error":"RESPONSE_BODY_SECRET_SENTINEL"}', id="success-with-error"),
    pytest.param(200, b'{"status":"success","error":null}', id="success-with-null-error"),
])
async def test_invalid_provider_completion_is_persisted_safely(
    db, training_tenant, training_provider, status_code, content, caplog
):
    db.add(TrainingRun(
        tenant_id=training_tenant,
        state="completed",
        completed_at=datetime.now(timezone.utc),
    ))
    db.commit()
    training_provider(lambda request: httpx.Response(status_code, content=content, request=request))

    await training._train_model(training_tenant, "aria-test", [])

    run = persisted_run(training_tenant)
    assert run.state == "failed"
    assert run.completed_at is None
    assert run.error and len(run.error) <= 500
    assert "RESPONSE_BODY_SECRET_SENTINEL" not in run.error
    assert "RESPONSE_BODY_SECRET_SENTINEL" not in caplog.text
    assert "training-provider.test" not in run.error


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 201])
async def test_successful_provider_response_completes_run(
    db, training_tenant, training_provider, status_code
):
    db.add(TrainingRun(tenant_id=training_tenant, state="failed", error="old failure"))
    db.commit()
    provider_states = []
    requests = []

    def respond(request):
        provider_states.append(persisted_run(training_tenant).state)
        requests.append(request)
        return httpx.Response(status_code, json={"status": "success"}, request=request)

    training_provider(respond)
    await training._train_model(training_tenant, "aria-test", [])

    run = persisted_run(training_tenant)
    assert provider_states == ["running"]
    assert run.state == "completed"
    assert run.completed_at is not None
    assert run.error is None
    assert json.loads(requests[0].content).get("stream") is False
    assert all(timeout == 120.0 for timeout in requests[0].extensions["timeout"].values())


@pytest.mark.parametrize("state,trained", [
    ("failed", False),
    ("running", False),
    ("completed", True),
])
def test_status_reports_trained_only_for_completed(auth_client, db, state, trained):
    user = db.query(User).filter_by(email="admin@testcorp.com").one()
    db.add(TrainingRun(
        tenant_id=user.tenant_id,
        state=state,
        model_name="aria-test",
        error="safe error" if state == "failed" else None,
        completed_at=datetime.now(timezone.utc) if trained else None,
    ))
    db.commit()

    response = auth_client.get("/api/training/status")

    assert response.status_code == 200
    assert response.json()["trained"] is trained
    assert (response.json()["last_trained"] is not None) is trained
