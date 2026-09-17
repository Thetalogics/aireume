"""Platform OAuth (Google + Microsoft) for social signup/login."""
import json
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.models.db_models import Tenant, User, UserOAuthIdentity, SSOConfig
from app.backend.routes.auth import (
    _create_auth_response,
    _create_token,
    _hash_password,
    _make_slug,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    auth_rate_limiter,
    _get_client_ip,
)
from app.backend.services.security_event_service import record_login_success, record_login_failure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oauth", tags=["oauth"])

PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "openid email profile",
        "client_id_env": "GOOGLE_CLIENT_ID",
        "client_secret_env": "GOOGLE_CLIENT_SECRET",
    },
    "microsoft": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scope": "openid email profile",
        "client_id_env": "MICROSOFT_CLIENT_ID",
        "client_secret_env": "MICROSOFT_CLIENT_SECRET",
    },
}


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")


def _redirect_base(request: Request) -> str:
    base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
    if base:
        return base
    return str(request.base_url).rstrip("/")


def _provider_config(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    cfg = PROVIDERS[provider]
    client_id = os.environ.get(cfg["client_id_env"])
    client_secret = os.environ.get(cfg["client_secret_env"])
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail=f"{provider.title()} OAuth is not configured on this platform.",
        )
    return {**cfg, "client_id": client_id, "client_secret": client_secret}


def _encode_state(payload: dict) -> str:
    from jose import jwt
    from app.backend.middleware.auth import SECRET_KEY, ALGORITHM
    return jwt.encode({**payload, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)}, SECRET_KEY, algorithm=ALGORITHM)


def _decode_state(state: str) -> dict:
    from jose import JWTError, jwt
    from app.backend.middleware.auth import SECRET_KEY, ALGORITHM
    try:
        return jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")


def _get_or_create_oauth_user(
    db: Session,
    *,
    provider: str,
    provider_user_id: str,
    email: str,
    mode: str,
    company_name: str | None,
) -> tuple[User, Tenant, bool]:
    """Returns (user, tenant, is_new_signup)."""
    identity = (
        db.query(UserOAuthIdentity)
        .filter(
            UserOAuthIdentity.provider == provider,
            UserOAuthIdentity.provider_user_id == provider_user_id,
        )
        .first()
    )
    if identity:
        user = db.query(User).filter(User.id == identity.user_id, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=400, detail="Linked account is inactive")
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        return user, tenant, False

    matches = db.query(User).filter(User.email == email, User.is_active == True).all()
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple workspaces exist for this email. Sign in with email and password, then link OAuth from that workspace.",
        )
    existing = matches[0] if matches else None
    if existing:
        if mode == "signup":
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please sign in with your password or link OAuth from settings.",
            )
        db.add(UserOAuthIdentity(
            user_id=existing.id,
            provider=provider,
            provider_user_id=provider_user_id,
            email_at_link=email,
        ))
        if not existing.email_verified:
            existing.email_verified = True
        db.commit()
        tenant = db.query(Tenant).filter(Tenant.id == existing.tenant_id).first()
        return existing, tenant, False

    if mode != "signup":
        raise HTTPException(status_code=404, detail="No account found. Please create a workspace first.")

    if not company_name or len(company_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Company name is required for signup")

    base_slug = _make_slug(company_name)
    slug = base_slug
    counter = 1
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    tenant = Tenant(name=company_name.strip(), slug=slug, contact_email=email)
    db.add(tenant)
    db.flush()

    # Plan selection happens in onboarding wizard (parity with email signup).
    user = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=_hash_password(secrets.token_urlsafe(32)),
        role="admin",
        email_verified=True,
    )
    db.add(user)
    db.flush()

    db.add(UserOAuthIdentity(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        email_at_link=email,
    ))
    db.commit()
    db.refresh(user)
    db.refresh(tenant)
    return user, tenant, True


def _check_sso_enforcement(db: Session, tenant: Tenant) -> None:
    if not tenant:
        return
    sso = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant.id, SSOConfig.is_active == True).first()
    if sso and sso.enforce_sso:
        raise HTTPException(
            status_code=403,
            detail="This workspace requires enterprise SSO. Social login is not permitted.",
        )


@router.get("/{provider}/start")
def oauth_start(
    provider: str,
    request: Request,
    mode: str = Query("login", pattern="^(login|signup)$"),
    company_name: str = Query(""),
    db: Session = Depends(get_db),
):
    client_ip = _get_client_ip(request)
    is_limited, retry_after = auth_rate_limiter.is_rate_limited(f"oauth:{client_ip}", max_attempts=10, window_seconds=60)
    if is_limited:
        raise HTTPException(status_code=429, detail="Too many OAuth attempts", headers={"Retry-After": str(retry_after)})

    cfg = _provider_config(provider)
    redirect_uri = f"{_redirect_base(request)}/api/auth/oauth/{provider}/callback"
    state = _encode_state({
        "mode": mode,
        "company_name": company_name.strip() if company_name else "",
        "nonce": secrets.token_hex(16),
        "provider": provider,
    })
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = f"{cfg['authorize_url']}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(f"{_frontend_url()}/login?oauth_error={urllib.parse.quote(error)}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")

    payload = _decode_state(state)
    if payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail="OAuth provider mismatch")

    cfg = _provider_config(provider)
    redirect_uri = f"{_redirect_base(request)}/api/auth/oauth/{provider}/callback"

    try:
        with httpx.Client(timeout=15.0) as client:
            token_resp = client.post(
                cfg["token_url"],
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            access = tokens.get("access_token")
            if not access:
                raise HTTPException(status_code=400, detail="OAuth token exchange failed")

            userinfo_resp = client.get(
                cfg["userinfo_url"],
                headers={"Authorization": f"Bearer {access}"},
            )
            userinfo_resp.raise_for_status()
            profile = userinfo_resp.json()
    except httpx.HTTPError as exc:
        logger.error("OAuth token/userinfo error for %s: %s", provider, exc)
        return RedirectResponse(f"{_frontend_url()}/login?oauth_error=provider_error")

    email = (profile.get("email") or "").lower().strip()
    provider_user_id = str(profile.get("sub") or profile.get("id") or "")
    if not email or not provider_user_id:
        return RedirectResponse(f"{_frontend_url()}/login?oauth_error=missing_email")

    mode = payload.get("mode", "login")
    company_name = payload.get("company_name") or email.split("@")[1].split(".")[0]

    try:
        user, tenant, is_new = _get_or_create_oauth_user(
            db,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            mode=mode,
            company_name=company_name if mode == "signup" else None,
        )
        _check_sso_enforcement(db, tenant)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        return RedirectResponse(f"{_frontend_url()}/login?oauth_error={urllib.parse.quote(str(detail)[:200])}")

    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")
    record_login_success(db, user=user, ip_address=ip, user_agent=ua)

    access_token = _create_token(
        {"sub": str(user.id), "tenant_id": str(user.tenant_id)},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = _create_token(
        {"sub": str(user.id), "tenant_id": str(user.tenant_id), "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    dest = "/" if tenant.onboarding_completed else "/onboarding"
    if is_new:
        dest = "/onboarding"

    response = _create_auth_response(user, tenant, access_token, refresh_token)
    response.headers["location"] = f"{_frontend_url()}{dest}"
    response.status_code = 302
    return response


@router.get("/providers")
def list_oauth_providers():
    """Public: which social providers are configured."""
    available = []
    for name, cfg in PROVIDERS.items():
        if os.environ.get(cfg["client_id_env"]) and os.environ.get(cfg["client_secret_env"]):
            available.append(name)
    return {"providers": available}
