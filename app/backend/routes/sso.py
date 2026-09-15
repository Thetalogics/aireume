"""
SSO / SAML 2.0 authentication routes.
"""
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.models.db_models import Tenant, User, SSOConfig
from app.backend.services.sso_service import is_sso_trust_ready, sso_service
from app.backend.routes.auth import (
    _create_token,
    _create_auth_response,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)

sso_router = APIRouter(prefix="/api/sso", tags=["sso"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


def _get_sso_config_or_404(db: Session, tenant_slug: str) -> tuple:
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant.id).first()
    if not sso_config or not sso_config.is_active:
        raise HTTPException(status_code=404, detail="SSO not configured for this tenant")
    return sso_config, tenant


@sso_router.get("/config/{tenant_slug}")
def get_sso_config_public(tenant_slug: str, db: Session = Depends(get_db)):
    """
    Public endpoint — returns whether SSO is configured/enforced for a tenant.
    Used by the frontend login page to show 'Login with SSO' button.
    """
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    if not tenant:
        return {"enabled": False, "enforced": False}

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant.id).first()
    if not sso_config or not sso_config.is_active or not is_sso_trust_ready(sso_config):
        return {"enabled": False, "enforced": False}

    return {
        "enabled": True,
        "enforced": sso_config.enforce_sso,
        "provider_type": sso_config.provider_type,
        "login_url": f"/api/sso/login/{tenant_slug}",
    }


@sso_router.get("/login/{tenant_slug}")
def sso_login(tenant_slug: str, db: Session = Depends(get_db)):
    """
    Initiate SAML SSO login for a tenant.
    Generates a SAML AuthnRequest and redirects to the IdP.
    """
    sso_config, tenant = _get_sso_config_or_404(db, tenant_slug)
    if not is_sso_trust_ready(sso_config):
        raise HTTPException(status_code=403, detail="SSO is disabled: IdP certificate is not configured")

    if not sso_config.sp_entity_id or not sso_config.sp_acs_url:
        # Auto-generate SP settings if missing
        base_url = os.getenv("BASE_URL", "http://localhost:8080")
        sso_config.sp_entity_id = sso_config.sp_entity_id or f"{base_url}/api/sso/metadata/{tenant_slug}"
        sso_config.sp_acs_url = sso_config.sp_acs_url or f"{base_url}/api/sso/callback/{tenant_slug}"
        db.commit()

    redirect_url, request_id = sso_service.generate_saml_request(sso_config)
    return RedirectResponse(url=redirect_url)


@sso_router.post("/callback/{tenant_slug}")
def sso_callback(
    tenant_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    SAMLResponse: str = Form(""),
):
    """
    SAML ACS endpoint — receives POST from the IdP.
    Validates SAML Response, gets/creates user, issues JWT tokens, redirects to frontend.
    """
    sso_config, tenant = _get_sso_config_or_404(db, tenant_slug)

    if not SAMLResponse:
        raise HTTPException(status_code=400, detail="Missing SAMLResponse")

    try:
        user_attrs = sso_service.process_saml_response(SAMLResponse, sso_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        user = sso_service.get_or_create_user(db, tenant.id, sso_config, user_attrs)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    # Issue tokens (same as normal login)
    access_token = _create_token(
        {"sub": str(user.id), "tenant_id": str(user.tenant_id)},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = _create_token(
        {"sub": str(user.id), "tenant_id": str(user.tenant_id), "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    # Build redirect to frontend with cookies set
    response = _create_auth_response(user, tenant, access_token, refresh_token)
    # Change redirect target to frontend dashboard
    response.headers["location"] = f"{FRONTEND_URL}/"
    response.status_code = 302
    return response


@sso_router.get("/metadata/{tenant_slug}")
def sso_metadata(tenant_slug: str, db: Session = Depends(get_db)):
    """
    Return SP metadata XML for IdP configuration.
    """
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant.id).first()
    if not sso_config or not sso_config.is_active:
        raise HTTPException(status_code=404, detail="SSO not configured for this tenant")

    base_url = os.getenv("BASE_URL", "http://localhost:8080")
    entity_id = sso_config.sp_entity_id or f"{base_url}/api/sso/metadata/{tenant_slug}"
    acs_url = sso_config.sp_acs_url or f"{base_url}/api/sso/callback/{tenant_slug}"

    # Minimal SP metadata — no signing cert yet (future enhancement)
    metadata_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{entity_id}">
    <md:SPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
        <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                                     Location="{acs_url}"
                                     index="0"
                                     isDefault="true"/>
    </md:SPSSODescriptor>
</md:EntityDescriptor>"""

    return Response(content=metadata_xml, media_type="application/xml")
