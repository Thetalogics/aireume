"""
Tests for SSO/SAML integration.
"""
import base64
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from unittest.mock import patch

from app.backend.models.db_models import Tenant, User, SSOConfig
from app.backend.services.sso_service import sso_service


# ─── Generate a real self-signed cert for tests ───────────────────────────────

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_test_cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")]))
    .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")]))
    .public_key(_test_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
    .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    .sign(_test_key, hashes.SHA256())
)
_TEST_CERT_PEM = _test_cert.public_bytes(serialization.Encoding.PEM).decode()
_TEST_KEY_PEM = _test_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def test_tenant(db):
    """Create a test tenant."""
    tenant = Tenant(name="SSO Test Corp", slug="sso-test-corp")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@pytest.fixture
def sso_config_payload():
    """Valid SSO configuration payload for admin endpoints."""
    return {
        "idp_entity_id": "https://idp.example.com/entity",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_slo_url": "https://idp.example.com/slo",
        "idp_certificate": _TEST_CERT_PEM,
        "enforce_sso": False,
        "auto_provision": True,
        "default_role": "viewer",
        "is_active": True,
    }


@pytest.fixture
def sso_enabled_tenant(db, test_tenant):
    """Create a tenant with active SSO config."""
    config = SSOConfig(
        tenant_id=test_tenant.id,
        provider_type="saml2",
        idp_entity_id="https://idp.example.com/entity",
        idp_sso_url="https://idp.example.com/sso",
        idp_certificate=_TEST_CERT_PEM,
        sp_entity_id="https://aria.example.com/api/sso/metadata/sso-test-corp",
        sp_acs_url="https://aria.example.com/api/sso/callback/sso-test-corp",
        enforce_sso=False,
        auto_provision=True,
        default_role="viewer",
        is_active=True,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return test_tenant, config


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_saml_response(
    name_id="user@example.com",
    email="user@example.com",
    issuer=None,
    in_response_to="ARIA123",
    assertion_id=None,
    response_id=None,
):
    """Build a minimal SAML Response XML and base64-encode it."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_on_or_after = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issuer = issuer or "https://idp.example.com/entity"
    assertion_id = assertion_id or f"ASSERT{uuid.uuid4().hex[:20].upper()}"
    response_id = response_id or f"RESP{uuid.uuid4().hex[:20].upper()}"
    response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="{response_id}"
                Version="2.0"
                IssueInstant="{now}"
                Destination="https://aria.example.com/api/sso/callback/sso-test-corp"
                InResponseTo="{in_response_to}">
    <saml:Issuer>{issuer}</saml:Issuer>
    <samlp:Status>
        <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
    </samlp:Status>
    <saml:Assertion ID="{assertion_id}"
                    Version="2.0"
                    IssueInstant="{now}">
        <saml:Issuer>{issuer}</saml:Issuer>
        <saml:Subject>
            <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id}</saml:NameID>
            <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
                <saml:SubjectConfirmationData NotOnOrAfter="{not_on_or_after}"
                                              Recipient="https://aria.example.com/api/sso/callback/sso-test-corp"
                                              InResponseTo="{in_response_to}"/>
            </saml:SubjectConfirmation>
        </saml:Subject>
        <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">
            <saml:AudienceRestriction>
                <saml:Audience>https://aria.example.com/api/sso/metadata/sso-test-corp</saml:Audience>
            </saml:AudienceRestriction>
        </saml:Conditions>
        <saml:AuthnStatement AuthnInstant="{now}" SessionIndex="{assertion_id}">
            <saml:AuthnContext>
                <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
            </saml:AuthnContext>
        </saml:AuthnStatement>
        <saml:AttributeStatement>
            <saml:Attribute Name="email">
                <saml:AttributeValue>{email}</saml:AttributeValue>
            </saml:Attribute>
        </saml:AttributeStatement>
    </saml:Assertion>
</samlp:Response>"""
    return base64.b64encode(response_xml.encode()).decode()


# ─── Admin SSO CRUD Tests ─────────────────────────────────────────────────────

class TestAdminSSOCRUD:
    def test_get_sso_config_not_found(self, platform_admin_client, test_tenant):
        resp = platform_admin_client.get(f"/api/admin/tenants/{test_tenant.id}/sso")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    def test_create_sso_config(self, platform_admin_client, test_tenant, sso_config_payload):
        resp = platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=sso_config_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "SSO configuration saved"
        assert "sp_entity_id" in data
        assert "sp_acs_url" in data

    def test_update_sso_config(self, platform_admin_client, test_tenant, sso_config_payload):
        # Create first
        platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=sso_config_payload)
        # Update
        payload = {**sso_config_payload, "enforce_sso": True, "default_role": "admin"}
        resp = platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == test_tenant.id

        # Verify
        get_resp = platform_admin_client.get(f"/api/admin/tenants/{test_tenant.id}/sso")
        assert get_resp.json()["enforce_sso"] is True
        assert get_resp.json()["default_role"] == "admin"

    def test_delete_sso_config(self, platform_admin_client, test_tenant, sso_config_payload):
        platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=sso_config_payload)
        resp = platform_admin_client.delete(f"/api/admin/tenants/{test_tenant.id}/sso")
        assert resp.status_code == 200
        assert resp.json()["message"] == "SSO configuration deleted"

        get_resp = platform_admin_client.get(f"/api/admin/tenants/{test_tenant.id}/sso")
        assert get_resp.json()["enabled"] is False

    def test_test_sso_config_valid(self, platform_admin_client, test_tenant, sso_config_payload):
        platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=sso_config_payload)
        resp = platform_admin_client.post(f"/api/admin/tenants/{test_tenant.id}/sso/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True

    def test_test_sso_config_invalid_cert(self, platform_admin_client, test_tenant):
        bad_payload = {
            "idp_entity_id": "https://idp.example.com/entity",
            "idp_sso_url": "https://idp.example.com/sso",
            "idp_certificate": "not-a-valid-cert",
            "enforce_sso": False,
            "auto_provision": True,
            "default_role": "viewer",
            "is_active": True,
        }
        platform_admin_client.put(f"/api/admin/tenants/{test_tenant.id}/sso", json=bad_payload)
        resp = platform_admin_client.post(f"/api/admin/tenants/{test_tenant.id}/sso/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("Invalid X.509 certificate" in e for e in data["errors"])


# ─── SSO Login Enforcement Tests ──────────────────────────────────────────────

class TestSSOEnforcement:
    def test_login_blocked_when_sso_enforced(self, client, db, test_tenant):
        # Create a user in the tenant
        from app.backend.routes.auth import _hash_password
        user = User(
            tenant_id=test_tenant.id,
            email="sso-user@example.com",
            hashed_password=_hash_password("password123"),
            role="admin",
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        db.commit()

        # Create SSO config with enforce_sso=True
        config = SSOConfig(
            tenant_id=test_tenant.id,
            provider_type="saml2",
            idp_entity_id="https://idp.example.com/entity",
            idp_sso_url="https://idp.example.com/sso",
            idp_certificate=_TEST_CERT_PEM,
            enforce_sso=True,
            auto_provision=True,
            default_role="viewer",
            is_active=True,
        )
        db.add(config)
        db.commit()

        resp = client.post("/api/auth/login", json={
            "email": "sso-user@example.com",
            "password": "password123",
        })
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error_code"] == "SSO_ENFORCED"
        assert "/api/sso/login/" in data["detail"]["sso_login_url"]

    def test_login_allowed_when_sso_not_enforced(self, client, db, test_tenant):
        from app.backend.routes.auth import _hash_password
        user = User(
            tenant_id=test_tenant.id,
            email="normal-user@example.com",
            hashed_password=_hash_password("password123"),
            role="admin",
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        db.commit()

        # No SSO config
        resp = client.post("/api/auth/login", json={
            "email": "normal-user@example.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        token = resp.cookies.get("access_token") or resp.json().get("access_token")
        assert token

    def test_login_allowed_when_sso_config_inactive(self, client, db, test_tenant, sso_config_payload):
        from app.backend.routes.auth import _hash_password
        user = User(
            tenant_id=test_tenant.id,
            email="inactive-sso-user@example.com",
            hashed_password=_hash_password("password123"),
            role="admin",
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        db.commit()

        # Create inactive SSO config
        config = SSOConfig(
            tenant_id=test_tenant.id,
            provider_type="saml2",
            idp_entity_id="https://idp.example.com/entity",
            idp_sso_url="https://idp.example.com/sso",
            idp_certificate=_TEST_CERT_PEM,
            enforce_sso=True,
            auto_provision=True,
            default_role="viewer",
            is_active=False,
        )
        db.add(config)
        db.commit()

        resp = client.post("/api/auth/login", json={
            "email": "inactive-sso-user@example.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        token = resp.cookies.get("access_token") or resp.json().get("access_token")
        assert token


# ─── SSO Public Config Tests ──────────────────────────────────────────────────

class TestSSOPublicConfig:
    def test_get_sso_config_public_enabled(self, client, sso_enabled_tenant):
        tenant, _ = sso_enabled_tenant
        resp = client.get(f"/api/sso/config/{tenant.slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["enforced"] is False
        assert data["provider_type"] == "saml2"
        assert f"/api/sso/login/{tenant.slug}" in data["login_url"]

    def test_get_sso_config_public_disabled(self, client, test_tenant):
        resp = client.get(f"/api/sso/config/{test_tenant.slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["enforced"] is False

    def test_get_sso_config_public_unknown_tenant(self, client):
        resp = client.get("/api/sso/config/nonexistent-tenant")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False


# ─── SSO Login Initiation Tests ───────────────────────────────────────────────

class TestSSOLoginInitiation:
    def test_sso_login_redirect(self, client, sso_enabled_tenant):
        tenant, _ = sso_enabled_tenant
        resp = client.get(f"/api/sso/login/{tenant.slug}", follow_redirects=False)
        assert resp.status_code == 307  # RedirectResponse default
        assert "SAMLRequest=" in resp.headers["location"]
        assert "https://idp.example.com/sso" in resp.headers["location"]

    def test_sso_login_no_config(self, client, test_tenant):
        resp = client.get(f"/api/sso/login/{test_tenant.slug}")
        assert resp.status_code == 404


# ─── SSO Callback Tests ───────────────────────────────────────────────────────

class TestSSOCallback:
    def test_sso_callback_auto_provision(self, client, db, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        saml_response = _build_saml_response(name_id="newuser@example.com", email="newuser@example.com")

        # Bypass signature verification for this test
        with patch("app.backend.routes.sso.sso_service.process_saml_response") as mock_process:
            mock_process.return_value = {
                "email": "newuser@example.com",
                "name": "New User",
                "name_id": "newuser@example.com",
                "first_name": "New",
                "last_name": "User",
            }
            resp = client.post(f"/api/sso/callback/{tenant.slug}", data={"SAMLResponse": saml_response}, follow_redirects=False)

        assert resp.status_code == 302
        assert "access_token" in resp.headers.get("set-cookie", "")

        # Verify user was created
        user = db.query(User).filter(User.email == "newuser@example.com").first()
        assert user is not None
        assert user.tenant_id == tenant.id
        assert user.role == config.default_role

    def test_sso_callback_existing_user(self, client, db, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        from app.backend.routes.auth import _hash_password
        existing_user = User(
            tenant_id=tenant.id,
            email="existing@example.com",
            hashed_password=_hash_password("oldpassword"),
            role="recruiter",
            is_active=True,
            email_verified=True,
        )
        db.add(existing_user)
        db.commit()

        saml_response = _build_saml_response(name_id="existing@example.com", email="existing@example.com")

        with patch("app.backend.routes.sso.sso_service.process_saml_response") as mock_process:
            mock_process.return_value = {
                "email": "existing@example.com",
                "name": "Existing User",
                "name_id": "existing@example.com",
            }
            resp = client.post(f"/api/sso/callback/{tenant.slug}", data={"SAMLResponse": saml_response}, follow_redirects=False)

        assert resp.status_code == 302
        # Should not create duplicate user
        users = db.query(User).filter(User.email == "existing@example.com").all()
        assert len(users) == 1

    def test_sso_callback_no_auto_provision(self, client, db, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        config.auto_provision = False
        db.commit()

        saml_response = _build_saml_response(name_id="unknown@example.com", email="unknown@example.com")

        with patch("app.backend.routes.sso.sso_service.process_saml_response") as mock_process:
            mock_process.return_value = {
                "email": "unknown@example.com",
                "name": "Unknown User",
                "name_id": "unknown@example.com",
            }
            resp = client.post(f"/api/sso/callback/{tenant.slug}", data={"SAMLResponse": saml_response})

        assert resp.status_code == 403
        assert "auto-provisioning is disabled" in resp.json()["detail"].lower()

    def test_sso_callback_missing_saml_response(self, client, sso_enabled_tenant):
        tenant, _ = sso_enabled_tenant
        resp = client.post(f"/api/sso/callback/{tenant.slug}", data={})
        assert resp.status_code == 400
        assert "Missing SAMLResponse" in resp.json()["detail"]

    def test_sso_callback_invalid_signature(self, client, sso_enabled_tenant):
        tenant, _ = sso_enabled_tenant
        saml_response = _build_saml_response(name_id="user@example.com", email="user@example.com")
        # Signature verification should fail with a real but unmatching cert
        resp = client.post(f"/api/sso/callback/{tenant.slug}", data={"SAMLResponse": saml_response})
        # Our lightweight verifier returns False for missing/invalid signatures
        assert resp.status_code == 400
        assert "signature" in resp.json()["detail"].lower()


# ─── SSO Metadata Tests ───────────────────────────────────────────────────────

class TestSSOMetadata:
    def test_sso_metadata(self, client, sso_enabled_tenant):
        tenant, _ = sso_enabled_tenant
        resp = client.get(f"/api/sso/metadata/{tenant.slug}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/xml"
        body = resp.text
        assert "md:EntityDescriptor" in body
        assert "md:SPSSODescriptor" in body
        assert "md:AssertionConsumerService" in body
        assert tenant.slug in body

    def test_sso_metadata_no_config(self, client, test_tenant):
        resp = client.get(f"/api/sso/metadata/{test_tenant.slug}")
        assert resp.status_code == 404


# ─── SSO Service Unit Tests ───────────────────────────────────────────────────

class TestSSOService:
    def test_generate_saml_request(self, sso_enabled_tenant):
        _, config = sso_enabled_tenant
        redirect_url, request_id = sso_service.generate_saml_request(config)
        assert redirect_url.startswith(config.idp_sso_url)
        assert "SAMLRequest=" in redirect_url
        assert request_id.startswith("ARIA")

    def test_process_saml_response_requires_library_verification(self, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        from app.backend.services.sso_service import persist_saml_authn_request

        persist_saml_authn_request("ARIA123", tenant.id)
        saml_response = _build_saml_response(
            name_id="unit@example.com",
            email="unit@example.com",
            in_response_to="ARIA123",
        )
        with patch(
            "app.backend.services.sso_service.authenticate_saml_response_with_onelogin"
        ) as mock_auth:
            mock_auth.return_value = None
            attrs = sso_service.process_saml_response(saml_response, config)
        assert attrs["email"] == "unit@example.com"
        assert attrs["name_id"] == "unit@example.com"

    def test_process_saml_response_expired_assertion(self, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        from app.backend.services.sso_service import persist_saml_authn_request

        persist_saml_authn_request("ARIA123", tenant.id)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="RESPONSE123" Version="2.0" IssueInstant="{now}" InResponseTo="ARIA123">
    <saml:Issuer>https://idp.example.com/entity</saml:Issuer>
    <samlp:Status>
        <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
    </samlp:Status>
    <saml:Assertion ID="ASSERT-EXP-{tenant.id}" Version="2.0" IssueInstant="{now}">
        <saml:Issuer>https://idp.example.com/entity</saml:Issuer>
        <saml:Subject>
            <saml:NameID>user@example.com</saml:NameID>
        </saml:Subject>
        <saml:Conditions NotBefore="{past}" NotOnOrAfter="{past}"/>
    </saml:Assertion>
</samlp:Response>"""
        saml_response = base64.b64encode(response_xml.encode()).decode()
        with patch(
            "app.backend.services.sso_service.authenticate_saml_response_with_onelogin"
        ):
            with pytest.raises(ValueError, match="expired"):
                sso_service.process_saml_response(saml_response, config)

    def test_get_or_create_user_creates_new(self, db, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        user = sso_service.get_or_create_user(
            db, tenant.id, config,
            {"email": "auto@example.com", "name": "Auto User", "name_id": "auto@example.com"}
        )
        assert user.email == "auto@example.com"
        assert user.tenant_id == tenant.id
        assert user.role == "viewer"

    def test_get_or_create_user_finds_existing(self, db, sso_enabled_tenant):
        tenant, config = sso_enabled_tenant
        from app.backend.routes.auth import _hash_password
        existing = User(
            tenant_id=tenant.id,
            email="exists@example.com",
            hashed_password=_hash_password("pw"),
            role="admin",
            is_active=True,
            email_verified=True,
        )
        db.add(existing)
        db.commit()

        user = sso_service.get_or_create_user(
            db, tenant.id, config,
            {"email": "exists@example.com", "name": "Exists", "name_id": "exists@example.com"}
        )
        assert user.id == existing.id
        assert user.role == "admin"  # unchanged
