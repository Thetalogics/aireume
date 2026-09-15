"""AUD-001: python3-saml trust pin, InResponseTo binding, replay protection."""
from unittest.mock import patch

import pytest

from app.backend.models.db_models import SSOConfig, Tenant, User
from app.backend.services.sso_service import persist_saml_authn_request, sso_service
from app.backend.tests.test_sso import _TEST_CERT_PEM, _build_saml_response


@pytest.fixture
def sso_tenant(db):
    tenant = Tenant(name="SAML Sec", slug="saml-sec")
    db.add(tenant)
    db.flush()
    config = SSOConfig(
        tenant_id=tenant.id,
        provider_type="saml2",
        idp_entity_id="https://idp.example.com/entity",
        idp_sso_url="https://idp.example.com/sso",
        idp_certificate=_TEST_CERT_PEM,
        sp_entity_id="https://aria.example.com/api/sso/metadata/sso-test-corp",
        sp_acs_url="https://aria.example.com/api/sso/callback/sso-test-corp",
        auto_provision=True,
        default_role="viewer",
        is_active=True,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return tenant, config


def _process(config, saml_response):
    with patch(
        "app.backend.services.sso_service.authenticate_saml_response_with_onelogin"
    ):
        return sso_service.process_saml_response(saml_response, config)


def test_sso_rejects_response_signed_by_unconfigured_certificate(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    saml = _build_saml_response(in_response_to="ARIA123")
    with patch(
        "app.backend.services.sso_service.authenticate_saml_response_with_onelogin",
        side_effect=ValueError("SAML Response signature verification failed"),
    ):
        with pytest.raises(ValueError, match="signature"):
            sso_service.process_saml_response(saml, config)


def test_sso_accepts_response_signed_by_configured_idp(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    attrs = _process(
        config,
        _build_saml_response(email="ok@example.com", name_id="ok@example.com", in_response_to="ARIA123"),
    )
    assert attrs["email"] == "ok@example.com"


def test_sso_rejects_replayed_response(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    saml = _build_saml_response(email="replay@example.com", in_response_to="ARIA123")
    _process(config, saml)
    with pytest.raises(ValueError, match="InResponseTo|already been used"):
        _process(config, saml)


def test_sso_rejects_wrong_in_response_to(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA999DIFFERENT", tenant.id)
    with pytest.raises(ValueError, match="InResponseTo"):
        _process(config, _build_saml_response(in_response_to="ARIA123"))


def test_sso_rejects_wrong_audience_or_recipient(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    config.sp_entity_id = "https://other-sp.example.com/metadata"
    db_config = config
    with pytest.raises(ValueError, match="Audience"):
        _process(db_config, _build_saml_response(in_response_to="ARIA123"))


def test_sso_rejects_expired_and_not_yet_valid_assertions(sso_tenant):
    from datetime import datetime, timedelta, timezone
    import base64

    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                ID="RESPONSE-EXP" Version="2.0" IssueInstant="{now}" InResponseTo="ARIA123">
    <saml:Issuer>https://idp.example.com/entity</saml:Issuer>
    <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
    <saml:Assertion ID="ASSERT-EXP-UNIQUE" Version="2.0" IssueInstant="{now}">
        <saml:Issuer>https://idp.example.com/entity</saml:Issuer>
        <saml:Subject><saml:NameID>user@example.com</saml:NameID></saml:Subject>
        <saml:Conditions NotBefore="{past}" NotOnOrAfter="{past}"/>
    </saml:Assertion>
</samlp:Response>"""
    with pytest.raises(ValueError, match="expired"):
        _process(config, base64.b64encode(xml.encode()).decode())


def test_sso_requires_trusted_signing_configuration_in_production(client, db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    tenant = Tenant(name="No Cert", slug="no-cert-sso")
    db.add(tenant)
    db.flush()
    db.add(
        SSOConfig(
            tenant_id=tenant.id,
            provider_type="saml2",
            idp_entity_id="https://idp.example.com/entity",
            idp_sso_url="https://idp.example.com/sso",
            idp_certificate="",
            is_active=True,
        )
    )
    db.commit()
    resp = client.get(f"/api/sso/config/{tenant.slug}")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    login = client.get(f"/api/sso/login/{tenant.slug}", follow_redirects=False)
    assert login.status_code in (403, 404)


def test_production_saml_state_fails_closed_when_redis_unhealthy(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:9/0")
    monkeypatch.setattr("app.backend.services.shared_cache.redis_is_healthy", lambda: False)
    with pytest.raises(ValueError, match="state store"):
        persist_saml_authn_request("ARIAFAILCLOSED", 1)


def test_process_response_passes_stored_request_id(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    with patch(
        "app.backend.services.sso_service.authenticate_saml_response_with_onelogin"
    ) as auth:
        sso_service.process_saml_response(
            _build_saml_response(email="ok@example.com", name_id="ok@example.com", in_response_to="ARIA123"),
            config,
        )
    assert auth.call_args.kwargs.get("request_id") == "ARIA123" or (
        len(auth.call_args.args) > 2 and auth.call_args.args[2] == "ARIA123"
    )


def _signed_saml_b64(in_response_to="ARIA123", email="ok@example.com", key=None, cert=None):
    pytest.importorskip("xmlsec")
    import base64
    from lxml import etree
    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    from app.backend.tests.test_sso import _TEST_KEY_PEM

    key = key or _TEST_KEY_PEM
    cert = cert or _TEST_CERT_PEM
    raw = base64.b64decode(
        _build_saml_response(email=email, name_id=email, in_response_to=in_response_to)
    )
    root = etree.fromstring(raw)
    assertion = root.find("{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
    signed = OneLogin_Saml2_Utils.add_sign(etree.tostring(assertion), key, cert)
    if isinstance(signed, str):
        signed = signed.encode()
    assertion.getparent().replace(assertion, etree.fromstring(signed))
    return base64.b64encode(etree.tostring(root)).decode()


def test_real_signed_saml_accepted_with_idp_cert(sso_tenant):
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    attrs = sso_service.process_saml_response(
        _signed_saml_b64(email="signed-ok@example.com"),
        config,
    )
    assert attrs["email"] == "signed-ok@example.com"


def test_real_signed_saml_rejected_with_attacker_cert(sso_tenant):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "attacker-idp")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "attacker-idp")]))
        .public_key(attacker_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .sign(attacker_key, hashes.SHA256())
    )
    attacker_key_pem = attacker_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    attacker_cert_pem = attacker_cert.public_bytes(serialization.Encoding.PEM).decode()
    tenant, config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    with pytest.raises(ValueError, match="signature"):
        sso_service.process_saml_response(
            _signed_saml_b64(
                email="evil@example.com",
                key=attacker_key_pem,
                cert=attacker_cert_pem,
            ),
            config,
        )


def test_sso_acs_callback_provisions_user_from_signed_response(client, db, sso_tenant):
    tenant, _config = sso_tenant
    persist_saml_authn_request("ARIA123", tenant.id)
    saml = _signed_saml_b64(email="acs-user@example.com")
    resp = client.post(
        f"/api/sso/callback/{tenant.slug}",
        data={"SAMLResponse": saml},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text
    created = db.query(User).filter(User.email == "acs-user@example.com", User.tenant_id == tenant.id).first()
    assert created is not None
    assert created.role == "viewer"

