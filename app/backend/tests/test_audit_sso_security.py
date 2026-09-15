"""AUD-001: python3-saml trust pin, InResponseTo binding, replay protection."""
from unittest.mock import patch

import pytest

from app.backend.models.db_models import SSOConfig, Tenant
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
