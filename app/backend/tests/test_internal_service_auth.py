"""
Tests for internal service-to-service authentication (require_internal_service).

The voice-agent container calls a set of `/internal/*` endpoints on the backend.
These must be protected by the shared X-Internal-Secret header — never open.
In the test environment INTERNAL_SERVICE_SECRET resolves to
"test-internal-service-secret" (see middleware/auth.py).
"""
import pytest

INTERNAL_SECRET = "test-internal-service-secret"


class TestInternalServiceAuth:
    def test_voice_internal_config_rejects_missing_secret(self, client):
        """No X-Internal-Secret header → 403."""
        resp = client.get("/api/voice/internal/config/1")
        assert resp.status_code == 403
        assert "internal service" in resp.json()["detail"].lower()

    def test_voice_internal_config_rejects_wrong_secret(self, client):
        """Wrong secret → 403 (constant-time compare)."""
        resp = client.get(
            "/api/voice/internal/config/1",
            headers={"X-Internal-Secret": "totally-wrong"},
        )
        assert resp.status_code == 403

    def test_voice_internal_config_accepts_correct_secret(self, client):
        """Correct secret passes the guard (not 403 — may 200/404 for the tenant)."""
        resp = client.get(
            "/api/voice/internal/config/1",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        assert resp.status_code != 403

    def test_interviews_internal_complete_requires_secret(self, client):
        """POST /api/interviews/internal/complete is guarded."""
        resp = client.post("/api/interviews/internal/complete", json={})
        assert resp.status_code == 403

    def test_recruiter_internal_complete_requires_secret(self, client):
        """POST /api/recruiter/internal/complete is guarded."""
        resp = client.post("/api/recruiter/internal/complete", json={})
        assert resp.status_code == 403

    def test_require_internal_service_dependency_directly(self):
        """Unit-test the dependency: empty header raises 403, correct passes."""
        from fastapi import HTTPException
        from starlette.requests import Request
        from app.backend.middleware.auth import require_internal_service

        def _make_request(headers: dict) -> Request:
            raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
            return Request({"type": "http", "headers": raw})

        with pytest.raises(HTTPException) as exc:
            require_internal_service(_make_request({}))
        assert exc.value.status_code == 403

        # Correct secret → no exception
        require_internal_service(_make_request({"X-Internal-Secret": INTERNAL_SECRET}))
        require_internal_service(_make_request({"X-Internal-Service-Secret": INTERNAL_SECRET}))
