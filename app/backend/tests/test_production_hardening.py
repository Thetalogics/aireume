"""Production-hardening tests: tenant login, upload IDOR, SSO verify, handoff PII, DLQ."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.backend.routes.auth import _hash_password
from app.backend.tests.test_helpers import _verify_user_via_api


class TestLoginTenantBinding:
    def test_same_email_two_tenants_requires_workspace(self, client, db):
        from app.backend.models.db_models import Tenant, User

        a = Tenant(name="Alpha", slug="alpha")
        b = Tenant(name="Beta", slug="beta")
        db.add_all([a, b])
        db.commit()
        db.refresh(a)
        db.refresh(b)
        pwd = _hash_password("SharedPass123!")
        db.add_all([
            User(tenant_id=a.id, email="shared@example.com", hashed_password=pwd, role="admin", is_active=True, email_verified=True),
            User(tenant_id=b.id, email="shared@example.com", hashed_password=pwd, role="admin", is_active=True, email_verified=True),
        ])
        db.commit()

        ambiguous = client.post("/api/auth/login", json={
            "email": "shared@example.com",
            "password": "SharedPass123!",
        })
        assert ambiguous.status_code == 400
        assert "workspace" in str(ambiguous.json()["detail"]).lower()

        alpha = client.post("/api/auth/login", json={
            "email": "shared@example.com",
            "password": "SharedPass123!",
            "tenant_slug": "alpha",
        })
        assert alpha.status_code == 200
        assert alpha.json()["tenant"]["slug"] == "alpha"

        beta = client.post("/api/auth/login", json={
            "email": "shared@example.com",
            "password": "SharedPass123!",
            "tenant_slug": "beta",
        })
        assert beta.status_code == 200
        assert beta.json()["tenant"]["slug"] == "beta"

    def test_wrong_workspace_returns_401(self, client, db):
        from app.backend.models.db_models import Tenant, User

        t = Tenant(name="OnlyCo", slug="onlyco")
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(User(
            tenant_id=t.id,
            email="only@example.com",
            hashed_password=_hash_password("OnlyPass123!"),
            role="admin",
            is_active=True,
            email_verified=True,
        ))
        db.commit()

        resp = client.post("/api/auth/login", json={
            "email": "only@example.com",
            "password": "OnlyPass123!",
            "tenant_slug": "does-not-exist",
        })
        assert resp.status_code == 401


class TestAuthTokensNotInBody:
    def test_login_sets_http_only_cookie_without_body_tokens(self, client):
        payload = {
            "company_name": "CookieCorp",
            "email": "hr@cookiecorp.com",
            "password": "HRPass123!",
        }
        client.post("/api/auth/register", json=payload)
        _verify_user_via_api(payload["email"])
        resp = client.post("/api/auth/login", json={
            "email": payload["email"],
            "password": payload["password"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" not in data
        assert "refresh_token" not in data
        assert resp.cookies.get("access_token")
        assert data["user"]["email"] == payload["email"]


class TestRefreshRotation:
    def test_old_refresh_token_is_revoked_after_rotation(self, client):
        payload = {
            "company_name": "RotateCorp",
            "email": "hr@rotatecorp.com",
            "password": "HRPass123!",
        }
        client.post("/api/auth/register", json=payload)
        _verify_user_via_api(payload["email"])
        login = client.post("/api/auth/login", json={
            "email": payload["email"],
            "password": payload["password"],
        })
        old_refresh = login.cookies.get("refresh_token") or login.json().get("refresh_token")
        assert old_refresh

        rotated = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert rotated.status_code == 200

        reuse = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert reuse.status_code == 401

        rotated_refresh = rotated.cookies.get("refresh_token") or rotated.json().get("refresh_token")
        family = client.post("/api/auth/refresh", json={"refresh_token": rotated_refresh})
        assert family.status_code == 401


class TestUploadOwnership:
    def test_cancel_upload_rejects_other_tenant(self, auth_client, tmp_path, monkeypatch):
        from app.backend.routes import upload as upload_mod

        monkeypatch.setattr(upload_mod, "CHUNK_STORAGE_DIR", tmp_path)
        upload_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        upload_dir = tmp_path / upload_id
        upload_dir.mkdir(parents=True)
        (upload_dir / "metadata.json").write_text(json.dumps({
            "upload_id": upload_id,
            "filename": "resume.pdf",
            "total_chunks": 1,
            "user_id": 99999,
            "tenant_id": 99999,
        }))

        resp = auth_client.delete(f"/api/upload/cancel/{upload_id}")
        assert resp.status_code == 404
        assert upload_dir.exists()

    def test_later_chunk_rejects_other_tenant(self, auth_client, tmp_path, monkeypatch):
        from app.backend.routes import upload as upload_mod

        monkeypatch.setattr(upload_mod, "CHUNK_STORAGE_DIR", tmp_path)
        upload_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        upload_dir = tmp_path / upload_id
        upload_dir.mkdir(parents=True)
        (upload_dir / "metadata.json").write_text(json.dumps({
            "upload_id": upload_id,
            "filename": "resume.pdf",
            "total_chunks": 2,
            "user_id": 99999,
            "tenant_id": 99999,
        }))

        resp = auth_client.post(
            "/api/upload/chunk",
            data={
                "upload_id": upload_id,
                "chunk_index": 1,
                "total_chunks": 2,
                "filename": "resume.pdf",
            },
            files={"chunk": ("chunk.bin", b"stolen-chunk", "application/octet-stream")},
        )
        assert resp.status_code == 404
        assert not (upload_dir / "chunk_0001").exists()

    def test_chunk_zero_cannot_overwrite_foreign_metadata(self, auth_client, tmp_path, monkeypatch):
        from app.backend.routes import upload as upload_mod

        monkeypatch.setattr(upload_mod, "CHUNK_STORAGE_DIR", tmp_path)
        upload_id = "cccccccc-dddd-eeee-ffff-000000000000"
        upload_dir = tmp_path / upload_id
        upload_dir.mkdir(parents=True)
        original = {
            "upload_id": upload_id,
            "filename": "owner.pdf",
            "total_chunks": 2,
            "user_id": 99999,
            "tenant_id": 99999,
        }
        (upload_dir / "metadata.json").write_text(json.dumps(original))

        resp = auth_client.post(
            "/api/upload/chunk",
            data={
                "upload_id": upload_id,
                "chunk_index": 0,
                "total_chunks": 2,
                "filename": "attacker.pdf",
            },
            files={"chunk": ("chunk.bin", b"overwrite", "application/octet-stream")},
        )
        assert resp.status_code == 404
        meta = json.loads((upload_dir / "metadata.json").read_text())
        assert meta["tenant_id"] == 99999
        assert meta["filename"] == "owner.pdf"


class TestSsoAutoProvisionVerified:
    def test_auto_provisioned_sso_user_is_email_verified(self, db):
        from app.backend.models.db_models import SSOConfig, Tenant
        from app.backend.services.sso_service import sso_service

        tenant = Tenant(name="SSO Corp", slug="sso-corp")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        config = SSOConfig(
            tenant_id=tenant.id,
            provider_type="saml2",
            idp_entity_id="https://idp.example.com/entity",
            idp_sso_url="https://idp.example.com/sso",
            idp_certificate="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
            sp_entity_id="https://aria.example.com/metadata",
            sp_acs_url="https://aria.example.com/acs",
            auto_provision=True,
            default_role="viewer",
            is_active=True,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

        user = sso_service.get_or_create_user(
            db, tenant.id, config,
            {"email": "new.sso@example.com", "name": "SSO User", "name_id": "new.sso@example.com"},
        )
        assert user.email_verified is True


class TestPublicHandoffMinimization:
    def test_public_handoff_omits_recruiter_notes_and_full_name(self, auth_client, db):
        import secrets
        from fastapi.testclient import TestClient

        from app.backend.main import app
        from app.backend.models.db_models import (
            Candidate,
            HandoffShareLink,
            OverallAssessment,
            RoleTemplate,
            ScreeningResult,
            User,
        )

        user = db.query(User).filter(User.email == "admin@testcorp.com").first()
        template = RoleTemplate(
            tenant_id=user.tenant_id,
            name="HM Role",
            jd_text="Senior PM role",
            created_by=user.id,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

        cand = Candidate(tenant_id=user.tenant_id, name="Jane Secret Doe", email="jane@secret.test")
        db.add(cand)
        db.commit()
        db.refresh(cand)

        result = ScreeningResult(
            tenant_id=user.tenant_id,
            candidate_id=cand.id,
            role_template_id=template.id,
            resume_text="resume",
            jd_text="Senior PM role",
            parsed_data="{}",
            analysis_result=json.dumps({
                "fit_score": 88,
                "strengths": ["leadership"],
                "weaknesses": ["gaps"],
                "final_recommendation": "advance",
            }),
            status="shortlisted",
            is_active=True,
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        db.add(OverallAssessment(
            result_id=result.id,
            user_id=user.id,
            overall_assessment="Private recruiter notes about salary",
            recruiter_recommendation="hire",
        ))
        token = secrets.token_urlsafe(16)
        db.add(HandoffShareLink(
            token=token,
            tenant_id=user.tenant_id,
            role_template_id=template.id,
            created_by=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()

        public_client = TestClient(app)
        resp = public_client.get(f"/api/public/handoff/{token}")
        assert resp.status_code == 200
        blob = json.dumps(resp.json())
        assert "Private recruiter notes" not in blob
        assert "Jane Secret Doe" not in blob

    def test_public_handoff_requires_passcode_when_set(self, auth_client, db):
        import hashlib
        import secrets
        from fastapi.testclient import TestClient

        from app.backend.main import app
        from app.backend.models.db_models import AuditLog, HandoffShareLink, RoleTemplate, User

        user = db.query(User).filter(User.email == "admin@testcorp.com").first()
        template = RoleTemplate(
            tenant_id=user.tenant_id,
            name="Passcode Role",
            jd_text="Role with passcode",
            created_by=user.id,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        token = secrets.token_urlsafe(16)
        db.add(HandoffShareLink(
            token=token,
            tenant_id=user.tenant_id,
            role_template_id=template.id,
            created_by=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            passcode_hash=hashlib.sha256(b"s3cret").hexdigest(),
        ))
        db.commit()

        public_client = TestClient(app)
        denied = public_client.get(f"/api/public/handoff/{token}")
        assert denied.status_code == 401
        ok = public_client.get(
            f"/api/public/handoff/{token}",
            headers={"X-Handoff-Passcode": "s3cret"},
        )
        assert ok.status_code == 200
        viewed = db.query(AuditLog).filter(AuditLog.action == "handoff.view").first()
        assert viewed is not None


class TestDeadLetterQueue:
    def test_permanent_failure_moves_job_to_dead_letter(self, db):
        import asyncio

        from app.backend.models.db_models import AnalysisJob, DeadLetterJob, Tenant
        from app.backend.services.queue_manager import QueueManager

        tenant = Tenant(name="Q", slug="q-tenant")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        job = AnalysisJob(
            tenant_id=tenant.id,
            status="processing",
            retry_count=3,
            max_retries=3,
            job_type="resume_screening",
            resume_hash="a" * 64,
            jd_hash="b" * 64,
            input_hash=uuid.uuid4().hex,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        mgr = QueueManager()
        asyncio.run(
            mgr.move_to_dead_letter(
                db, job, "retries exhausted", error_type="RuntimeError", error_message="poison"
            )
        )
        dlq = db.query(DeadLetterJob).filter(DeadLetterJob.original_job_id == job.id).first()
        assert dlq is not None


class TestMfaRequiredAtLogin:
    def test_admin_must_enroll_mfa_before_using_app(self, client, db, monkeypatch):
        from app.backend.models.db_models import Tenant, User
        from app.backend.middleware import auth as auth_mw
        from app.backend.routes import auth as auth_mod

        monkeypatch.setattr(auth_mod, "_mfa_required_for", lambda user: True)
        monkeypatch.setattr(auth_mw, "_mfa_enrollment_blocking", lambda user: True)
        t = Tenant(name="MfaCo", slug="mfaco")
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(User(
            tenant_id=t.id,
            email="admin@mfaco.com",
            hashed_password=_hash_password("AdminPass123!"),
            role="admin",
            is_active=True,
            email_verified=True,
            mfa_enabled=False,
        ))
        db.commit()

        resp = client.post("/api/auth/login", json={
            "email": "admin@mfaco.com",
            "password": "AdminPass123!",
            "tenant_slug": "mfaco",
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["mfa_required"] is True
        assert resp.cookies.get("access_token")

        blocked = client.get("/api/candidates")
        assert blocked.status_code == 403
        detail = blocked.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["error_code"] == "MFA_SETUP_REQUIRED"


class TestEnvExampleSecrets:
    def test_example_env_uses_placeholders(self):
        from pathlib import Path

        text = Path(".env.example").read_text(encoding="utf-8")
        assert "ecb77c4575f6d63fadf832346867c00fbd6888e9090b2d772ec44fe653abc3ad" not in text
        assert "LIVEKIT_API_KEY=devkey" not in text
        assert "LIVEKIT_API_SECRET=devsecret" not in text
        assert "+18722789563" not in text
        assert "2e628a56f9634a198afd35b1d0145384" not in text


class TestCsvImportNotAnAts:
    def test_csv_import_does_not_point_at_ats(self, auth_client):
        import inspect

        from app.backend.routes import candidates as cmod

        source = inspect.getsource(cmod.import_candidates_csv)
        assert "ATS sync" not in source
        assert "ATS" not in source
        resp = auth_client.post(
            "/api/candidates/import/csv",
            files={"file": ("people.csv", b"name,email\nAda,ada@example.com\n", "text/csv")},
        )
        assert resp.status_code in (200, 403, 422)
        if resp.status_code == 200:
            assert "created" in resp.json()


class TestProductionStorageFailClosed:
    def test_s3_upload_failure_in_production_does_not_use_bytea(self, monkeypatch):
        from fastapi import HTTPException

        from app.backend.routes import analyze_helpers as helpers
        from app.backend.services.object_storage import ObjectStorageService

        class _Cand:
            tenant_id = 1
            id = 1
            resume_file_data = None
            resume_file_key = None

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setattr(ObjectStorageService, "is_available", staticmethod(lambda: True))
        monkeypatch.setattr(ObjectStorageService, "build_key", staticmethod(lambda *a, **k: "k"))
        monkeypatch.setattr(ObjectStorageService, "upload", staticmethod(lambda *a, **k: False))

        cand = _Cand()
        with pytest.raises(HTTPException) as ei:
            helpers.persist_resume_file_bytes(cand, b"%PDF", "resume.pdf")
        assert ei.value.status_code == 503
        assert cand.resume_file_data is None


class TestIdempotencyTenantScope:
    def test_lookup_does_not_replay_across_tenants(self, db):
        from datetime import datetime, timedelta, timezone

        from app.backend.middleware import idempotency as idem
        from app.backend.models.db_models import IdempotencyKey

        now = datetime.now(timezone.utc)
        fp = idem.request_fingerprint("POST", "/api/example", "", "application/json", b'{"ok":true}')
        db.merge(IdempotencyKey(
            key="same-key",
            tenant_id=1,
            endpoint="POST:/api/example",
            request_fingerprint=fp,
            response_status=200,
            response_body={"ok": True, "tenant": 1},
            expires_at=now + timedelta(hours=1),
        ))
        db.commit()

        replay = idem._lookup("same-key", "2", "POST:/api/example", fp)
        assert replay is None
        own = idem._lookup("same-key", "1", "POST:/api/example", fp)
        assert own is not None
        assert own[1]["tenant"] == 1
