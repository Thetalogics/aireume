"""
Authentication routes: register, login, refresh, me, logout.
"""
import logging
import time
import os
import re
import secrets
import smtplib
import uuid
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, SECRET_KEY, ALGORITHM
from app.backend.models.db_models import Tenant, User, RevokedToken, SSOConfig, PasswordResetToken
from app.backend.models.schemas import (
    RegisterRequest, LoginRequest, TokenResponse, RefreshRequest
)
from app.backend.services.security_event_service import (
    record_login_success,
    record_login_failure,
    record_suspicious_activity,
    is_suspicious,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES  = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES",  "15"))
REFRESH_TOKEN_EXPIRE_DAYS    = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS",    "30"))
EMAIL_VERIFICATION_EXPIRE_HOURS = int(os.getenv("EMAIL_VERIFICATION_EXPIRE_HOURS", "24"))


# ─── Per-IP Rate Limiter ─────────────────────────────────────────────────────

class AuthRateLimitUnavailable(Exception):
    pass


class InMemoryRateLimiter:
    """Simple per-key rate limiter with sliding window."""

    def __init__(self):
        self._attempts = defaultdict(list)  # key -> [timestamps]
        self._lock = Lock()

    def is_rate_limited(self, key: str, max_attempts: int, window_seconds: int) -> tuple:
        """Returns (is_limited, retry_after_seconds)."""
        try:
            from app.backend.services.shared_cache import cache_incr, _client
            from redis.exceptions import RedisError
            client = _client()
            required = os.getenv("ENVIRONMENT", "").lower() == "production" or os.getenv(
                "REDIS_REQUIRED", ""
            ).lower() in ("1", "true", "yes")
            if client is None:
                if required:
                    raise AuthRateLimitUnavailable("redis unavailable")
            else:
                count = cache_incr(f"rl:{key}", ttl_seconds=window_seconds)
                if count > max_attempts:
                    return True, window_seconds
                return False, 0
        except ImportError as e:
            if os.getenv("ENVIRONMENT", "").lower() == "production" or os.getenv("REDIS_REQUIRED", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                raise AuthRateLimitUnavailable("redis unavailable") from e
        except (OSError, RedisError, RuntimeError, ValueError, TypeError) as e:
            if os.getenv("ENVIRONMENT", "").lower() == "production" or os.getenv("REDIS_REQUIRED", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                logger.error("auth rate limiter redis unavailable")
                raise AuthRateLimitUnavailable("redis unavailable") from e
            logger.warning(
                "Rate limiter cache unavailable; using in-memory: %s", e,
                extra={"error_code": "CACHE_ERROR"},
            )
        now = time.time()
        with self._lock:
            # Clean old entries
            self._attempts[key] = [t for t in self._attempts[key] if now - t < window_seconds]
            if len(self._attempts[key]) >= max_attempts:
                oldest = self._attempts[key][0]
                retry_after = int(window_seconds - (now - oldest)) + 1
                return True, retry_after
            self._attempts[key].append(now)
            return False, 0

    def cleanup(self):
        """Remove stale entries (call periodically)."""
        now = time.time()
        with self._lock:
            stale_keys = [k for k, v in self._attempts.items() if not v or now - v[-1] > 300]
            for k in stale_keys:
                del self._attempts[k]


auth_rate_limiter = InMemoryRateLimiter()


def _identity_fragment(value: str) -> str:
    return hashlib.sha256((value or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def _token_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _store_plain_auth_tokens_for_tests() -> bool:
    return os.getenv("TESTING", "").lower() in {"1", "true", "yes"}


def _guard_auth_limit(key: str, max_attempts: int, window_seconds: int) -> tuple:
    try:
        return auth_rate_limiter.is_rate_limited(key, max_attempts, window_seconds)
    except AuthRateLimitUnavailable:
        raise HTTPException(status_code=503, detail="Authentication temporarily unavailable")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict, expires_delta: timedelta, include_jti: bool = True) -> str:
    payload = {**data, "exp": datetime.now(timezone.utc) + expires_delta}
    if include_jti and "jti" not in payload:
        payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _make_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _tenant_for_workspace(db: Session, raw: str) -> Tenant | None:
    """Match a typed workspace to a tenant by slug or company name."""
    text = (raw or "").strip()
    if not text:
        return None
    lowered = text.lower()
    tenant = db.query(Tenant).filter(func.lower(Tenant.slug) == lowered).first()
    if tenant:
        return tenant
    normalized = _make_slug(text)
    if normalized and normalized != lowered:
        tenant = db.query(Tenant).filter(Tenant.slug == normalized).first()
        if tenant:
            return tenant
    return db.query(Tenant).filter(func.lower(Tenant.name) == lowered).first()


def _tenant_dict(tenant: Tenant) -> dict:
    from app.backend.services.trial_service import trial_days_remaining, is_trial_active
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "onboarding_completed": tenant.onboarding_completed,
        "subscription_status": tenant.subscription_status,
        "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
        "trial_days_remaining": trial_days_remaining(tenant),
        "is_trial_active": is_trial_active(tenant),
        "brand_name": tenant.brand_name,
        "brand_logo_url": tenant.brand_logo_url,
        "brand_primary_color": tenant.brand_primary_color,
        "custom_domain": tenant.custom_domain,
    }


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "tenant_id": user.tenant_id,
        "email_verified": user.email_verified,
        "is_platform_admin": user.is_platform_admin or (user.platform_role is not None),
        "platform_role": user.platform_role,
        "mfa_enabled": bool(getattr(user, "mfa_enabled", False)),
        "mfa_required": _mfa_required_for(user),
    }


def _mfa_required_for(user: User) -> bool:
    if os.getenv("TESTING", "").lower() in {"1", "true", "yes"}:
        return False
    return (
        (user.role or "") == "admin"
        or bool(user.is_platform_admin)
        or bool(user.platform_role)
    )


def _verification_expired(user: User) -> bool:
    if not user.email_verification_sent_at:
        return False
    sent_at = user.email_verification_sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    expires_at = sent_at + timedelta(hours=EMAIL_VERIFICATION_EXPIRE_HOURS)
    return datetime.now(timezone.utc) > expires_at


def _send_verification_email(email: str, token: str, tenant: Tenant) -> bool:
    try:
        from app.backend.services.email_service import email_service
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
        verify_url = f"{frontend_url}/verify-email/{token}"
        login_url = f"{frontend_url}/login?workspace={tenant.slug}"
        html_body = (
            f"<h2>Welcome to ARIA — Verify Your Email</h2>"
            f"<p>Please verify your email address to activate your workspace "
            f"<strong>{tenant.name}</strong>.</p>"
            f'<p><a href="{verify_url}">Verify Email Address</a></p>'
            f"<p>Your workspace slug is <strong>{tenant.slug}</strong>. "
            f"You'll need it to sign in: <a href=\"{login_url}\">{login_url}</a></p>"
            f"<p>If you didn't create this account, you can safely ignore this email.</p>"
            f"<hr><p style='color:gray;font-size:12px;'>"
            f"This is an automated message from ARIA Resume Intelligence.</p>"
        )
        return email_service.send_email(email, "Verify Your Email — ARIA Platform", html_body)
    except (smtplib.SMTPException, OSError, ValueError, RuntimeError) as e:
        logger.error(
            "Failed to send verification email: %s", e,
            extra={"error_code": "EMAIL_SEND_ERROR"},
        )
        return False


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request headers or connection info."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else ""


def _create_auth_response(
    user: User,
    tenant: Tenant,
    access_token: str,
    refresh_token: str,
    request: Request | None = None,
) -> JSONResponse:
    """Set httpOnly cookies. Omit tokens from JSON unless an API client opts in."""
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    
    # Generate CSRF token for non-httpOnly cookie
    csrf_token = secrets.token_hex(32)

    body = {
        "token_type": "bearer",
        "user": _user_dict(user),
        "tenant": _tenant_dict(tenant) if tenant else None,
    }
    grant = (request.headers.get("X-ARIA-Token-Grant") if request else None) or ""
    if grant.lower() == "api":
        body["access_token"] = access_token
        body["refresh_token"] = refresh_token

    response = JSONResponse(content=body)
    
    # Set httpOnly cookies for browser clients
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/"
    )
    
    # Set CSRF token cookie (NOT httpOnly - JS needs to read it)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=is_production,
        samesite="lax",
        max_age=3600,  # 1 hour
        path="/"
    )
    
    return response


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/register")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)
    is_limited, retry_after = _guard_auth_limit(f"register:{client_ip}", max_attempts=5, window_seconds=60)
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many registration attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)}
        )

    # Same email may exist in another workspace; uniqueness is (tenant_id, email).
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    try:
        from app.backend.services.password_policy import validate_password_strength
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Create tenant
    base_slug = _make_slug(body.company_name)
    slug      = base_slug
    counter   = 1
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    tenant = Tenant(name=body.company_name, slug=slug, contact_email=body.email)
    db.add(tenant)
    db.flush()  # get tenant.id

    # Create admin user
    verification_token = str(uuid.uuid4())
    user = User(
        tenant_id=tenant.id,
        email=body.email,
        hashed_password=_hash_password(body.password),
        role="admin",
        email_verified=False,
        email_verification_token=verification_token if _store_plain_auth_tokens_for_tests() else None,
        email_verification_token_hash=_token_hash(verification_token),
        email_verification_sent_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _send_verification_email(body.email, verification_token, tenant)

    return {
        "requires_verification": True,
        "message": "Registration successful. Please check your email to verify your account.",
        "email": body.email,
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
        },
    }


@router.get("/verify-email/{token}")
def verify_email_legacy_get(token: str):
    """GET verification is disabled — tokens in URLs leak via logs and Referer."""
    raise HTTPException(
        status_code=405,
        detail="Use POST /api/auth/verify-email with {\"token\": \"...\"}",
    )


class VerifyEmailBody(BaseModel):
    token: str


@router.post("/verify-email")
def verify_email_post(body: VerifyEmailBody, db: Session = Depends(get_db)):
    """Verify email with token in the request body (avoids leaking tokens in logs/Referer)."""
    return _complete_email_verification(body.token, db)


def _complete_email_verification(token: str, db: Session):
    token_digest = _token_hash(token)
    user = db.query(User).filter(User.email_verification_token_hash == token_digest).first()
    if not user:
        # Rolling-deployment compatibility for tokens issued before hashing.
        user = db.query(User).filter(User.email_verification_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    if _verification_expired(user):
        raise HTTPException(
            status_code=400,
            detail="Verification link has expired. Please request a new verification email.",
        )
    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_token_hash = None
    db.commit()

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return {
        "message": "Email verified successfully",
        "tenant": {
            "slug": tenant.slug if tenant else None,
            "name": tenant.name if tenant else None,
        },
    }


@router.post("/resend-verification")
def resend_verification(request: Request, request_data: dict, db: Session = Depends(get_db)):
    """Resend verification email by address (no auth — user cannot log in until verified)."""
    client_ip = _get_client_ip(request)
    is_limited, retry_after = _guard_auth_limit(
        f"resend-verify:{client_ip}", max_attempts=3, window_seconds=300
    )
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many resend attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    email = request_data.get("email", "").strip().lower()
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or user.email_verified:
        return {"message": "If an unverified account exists for that email, a verification link was sent."}

    verification_token = str(uuid.uuid4())
    user.email_verification_token = verification_token if _store_plain_auth_tokens_for_tests() else None
    user.email_verification_token_hash = _token_hash(verification_token)
    user.email_verification_sent_at = datetime.now(timezone.utc)
    db.commit()

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    sent = _send_verification_email(user.email, verification_token, tenant) if tenant else False
    return {
        "message": "If an unverified account exists for that email, a verification link was sent.",
        "email_sent": sent,
    }


@router.post("/login")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    ip = _get_client_ip(request)
    is_limited, retry_after = _guard_auth_limit(f"login:{ip}", max_attempts=5, window_seconds=60)
    if not is_limited:
        is_limited, retry_after = _guard_auth_limit(
            f"login:{ip}:{_identity_fragment(body.email)}", max_attempts=5, window_seconds=60
        )
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)}
        )

    user_agent = request.headers.get("User-Agent")

    email = (body.email or "").strip().lower()
    query = db.query(User).filter(func.lower(User.email) == email, User.is_active == True)
    if (body.tenant_slug or "").strip():
        tenant_row = _tenant_for_workspace(db, body.tenant_slug)
        if tenant_row is None:
            record_login_failure(db, email=body.email, ip_address=ip, user_agent=user_agent, reason="Unknown workspace")
            raise HTTPException(status_code=400, detail="Unknown workspace")
        user = query.filter(User.tenant_id == tenant_row.id).first()
    else:
        matches = query.all()
        if len(matches) > 1:
            raise HTTPException(
                status_code=400,
                detail="Multiple workspaces found for this email. Provide a workspace slug.",
            )
        user = matches[0] if matches else None

    if not user or not _verify_password(body.password, user.hashed_password):
        # Record failed login
        record_login_failure(db, email=body.email, ip_address=ip, user_agent=user_agent, reason="Invalid credentials")
        # Check for brute-force
        if is_suspicious(db, ip_address=ip, email=body.email, window_minutes=30, threshold=5):
            record_suspicious_activity(db, ip_address=ip, email=body.email, details={"reason": "brute_force_detected"})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if getattr(user, "mfa_enabled", False):
        from app.backend.services.mfa_service import verify_code
        if not verify_code(user.mfa_secret, body.mfa_code):
            raise HTTPException(status_code=401, detail="MFA code required")

    # Check email verification
    if not user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before logging in. Check your inbox."
        )

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

    # Check SSO enforcement
    if tenant:
        sso_config = db.query(SSOConfig).filter(SSOConfig.tenant_id == tenant.id).first()
        if sso_config and sso_config.is_active and sso_config.enforce_sso:
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "SSO_ENFORCED",
                    "message": "Password login is disabled. Please use SSO.",
                    "sso_login_url": f"/api/sso/login/{tenant.slug}",
                },
            )

    # Record successful login
    record_login_success(db, user=user, ip_address=ip, user_agent=user_agent)

    access_token  = _create_token({"sub": str(user.id), "tenant_id": str(user.tenant_id)}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    refresh_token = _create_token({
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "type": "refresh",
        "rfv": getattr(user, "refresh_epoch", 0) or 0,
    }, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

    return _create_auth_response(user, tenant, access_token, refresh_token, request)


@router.post("/refresh")
def refresh_token(request: Request, body: RefreshRequest = None, db: Session = Depends(get_db)):
    from jose import JWTError
    
    # Try to get refresh token from body first (for API clients), then from cookie
    refresh_token_value = None
    if body and body.refresh_token:
        refresh_token_value = body.refresh_token
    else:
        refresh_token_value = request.cookies.get("refresh_token")
    
    if not refresh_token_value:
        raise HTTPException(status_code=401, detail="Refresh token required")
    
    try:
        payload = jwt.decode(refresh_token_value, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        user_id = int(payload.get("sub"))
        jti = payload.get("jti")
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Check if refresh token's JTI is revoked
    if jti:
        revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        if revoked:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.refresh_epoch = (getattr(user, "refresh_epoch", 0) or 0) + 1
                db.commit()
            raise HTTPException(status_code=401, detail="Token has been revoked")

    # Check if user exists at all (regardless of active status)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    user_rfv = getattr(user, "refresh_epoch", 0) or 0
    token_rfv = payload.get("rfv", 0)
    if token_rfv != user_rfv:
        raise HTTPException(status_code=401, detail="Token has been revoked")

    if not user.is_active:
        # User was deactivated — revoke this refresh token permanently
        if jti:
            existing_revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
            if not existing_revoked:
                revoked_entry = RevokedToken(jti=jti, expires_at=datetime.now(timezone.utc) + timedelta(days=30))
                db.add(revoked_entry)
                db.commit()
        raise HTTPException(status_code=401, detail="User account has been deactivated")

    if not user.email_verified and not (user.is_platform_admin or user.platform_role):
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before accessing the application.",
        )

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

    if jti:
        existing_revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        if not existing_revoked:
            db.add(RevokedToken(jti=jti, expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)))
            db.commit()

    access_token  = _create_token({"sub": str(user.id), "tenant_id": str(user.tenant_id)}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    new_refresh   = _create_token({
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "type": "refresh",
        "rfv": getattr(user, "refresh_epoch", 0) or 0,
    }, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

    return _create_auth_response(user, tenant, access_token, new_refresh, request)


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    csrf_token = secrets.token_hex(32)
    response = JSONResponse(content={
        "user": _user_dict(current_user),
        "tenant": _tenant_dict(tenant) if tenant else None,
    })
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=is_production,
        samesite="lax",
        max_age=3600,
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Revoke refresh token and clear httpOnly auth cookies."""
    from jose import JWTError
    
    # Try to get refresh token from cookie or body to revoke it
    refresh_token_value = request.cookies.get("refresh_token")
    
    if not refresh_token_value:
        # Try to get from body for API clients
        try:
            body = await request.json()
            refresh_token_value = body.get("refresh_token")
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(
                "Logout body was not valid JSON: %s", e,
                extra={"error_code": "VALIDATION_ERROR"},
            )
    
    # If we have a refresh token, decode it and store JTI in revoked_tokens
    if refresh_token_value:
        try:
            payload = jwt.decode(refresh_token_value, SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            exp = payload.get("exp")
            
            if jti:
                # Check if already revoked
                existing = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
                if not existing:
                    # Store the revoked token with its expiration time
                    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
                    revoked_token = RevokedToken(
                        jti=jti,
                        expires_at=expires_at
                    )
                    db.add(revoked_token)
                    db.commit()
        except (JWTError, ValueError, Exception):
            # If token is invalid, just proceed with logout
            pass
    
    # Also revoke the access token JTI so it cannot be reused before natural expiry
    access_token_value = request.cookies.get("access_token")
    if not access_token_value:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            access_token_value = auth_header[7:]
    if access_token_value:
        try:
            payload = jwt.decode(access_token_value, SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
                db.add(RevokedToken(jti=jti, expires_at=expires_at))
                db.commit()
        except (JWTError, ValueError, Exception):
            pass

    response = JSONResponse(content={"message": "Logged out successfully"})
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("csrf_token", path="/")
    return response


@router.post("/forgot-password")
def forgot_password(request: Request, request_data: dict, db: Session = Depends(get_db)):
    """Generate password reset token. Always returns 200 to prevent email enumeration."""
    # Rate limit password reset requests
    client_ip = _get_client_ip(request)
    is_limited, retry_after = _guard_auth_limit(f"forgot:{client_ip}", max_attempts=3, window_seconds=60)
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many password reset attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)}
        )

    email = request_data.get("email", "").strip().lower()

    users = db.query(User).filter(User.email == email, User.is_active == True).all()
    if not users:
        return {"message": "If an account with that email exists, a reset link has been sent."}

    for user in users:
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
        token = secrets.token_urlsafe(32)
        token_digest = _token_hash(token)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token_digest,
            token_hash=token_digest,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        db.add(reset_token)
        db.commit()
        try:
            from app.backend.services.email_service import email_service, get_tenant_email_service
            frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
            reset_url = f"{frontend_url}/reset-password/{token}"
            html_body = (
                f"<h2>Password Reset Request</h2>"
                f"<p>Click the link below to reset your password. This link expires in 1 hour.</p>"
                f'<p><a href="{reset_url}">Reset Password</a></p>'
                f"<p>If you didn't request this, you can safely ignore this email.</p>"
                f"<hr><p style='color:gray;font-size:12px;'>"
                f"This is an automated message from ARIA Resume Intelligence.</p>"
            )
            tenant_svc = get_tenant_email_service(db, user.tenant_id) if user.tenant_id else None
            if tenant_svc:
                tenant_svc.send_email(user.email, "Password Reset - ARIA Platform", html_body)
            else:
                email_service.send_email(user.email, "Password Reset - ARIA Platform", html_body)
        except (smtplib.SMTPException, OSError, ValueError, RuntimeError) as e:
            logger.error(
                "Failed to send password reset email: %s", e,
                extra={"error_code": "EMAIL_SEND_ERROR"},
            )
        logger.info("Password reset token generated for user %s", user.id)

    return {"message": "If an account with that email exists, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(request: Request, request_data: dict, db: Session = Depends(get_db)):
    """Reset password using token."""
    is_limited, retry_after = _guard_auth_limit(
        f"reset:{_get_client_ip(request)}", max_attempts=5, window_seconds=60
    )
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail="Too many reset attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    token = request_data.get("token", "")
    new_password = request_data.get("new_password", "")

    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required")

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    try:
        from app.backend.services.password_policy import validate_password_strength
        validate_password_strength(new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Find valid token
    token_digest = _token_hash(token)
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_digest,
        PasswordResetToken.expires_at > datetime.now(timezone.utc)
    ).first()
    if not reset_token:
        # Rolling-deployment compatibility for tokens issued before hashing.
        reset_token = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.expires_at > datetime.now(timezone.utc)
        ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Update password
    user = db.query(User).filter(User.id == reset_token.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = _hash_password(new_password)
    user.refresh_epoch = (getattr(user, "refresh_epoch", 0) or 0) + 1

    # Delete used token
    db.delete(reset_token)
    db.commit()

    return {"message": "Password has been reset successfully"}


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    try:
        from app.backend.services.password_policy import validate_password_strength
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    current_user.hashed_password = _hash_password(body.new_password)
    current_user.refresh_epoch = (getattr(current_user, "refresh_epoch", 0) or 0) + 1
    db.commit()
    return {"message": "Password updated"}


class MfaCodeBody(BaseModel):
    code: str


@router.post("/mfa/setup")
def mfa_setup(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.backend.services.mfa_service import new_secret, provisioning_uri
    secret = new_secret()
    current_user.mfa_secret = secret
    current_user.mfa_enabled = False
    db.commit()
    return {"secret": secret, "otpauth_url": provisioning_uri(current_user.email, secret)}


@router.post("/mfa/enable")
def mfa_enable(
    body: MfaCodeBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.mfa_service import verify_code
    if not verify_code(current_user.mfa_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    current_user.mfa_enabled = True
    db.commit()
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
def mfa_disable(
    body: MfaCodeBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.backend.services.mfa_service import verify_code
    if not current_user.mfa_enabled or not verify_code(current_user.mfa_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    db.commit()
    return {"mfa_enabled": False}


# ─── E2E / test helpers (disabled in production) ─────────────────────────────

@router.post("/test/verify-email")
def test_verify_email_by_address(request_data: dict, db: Session = Depends(get_db)):
    """E2E only: verify a user by email when E2E_TEST_MODE=1 or TESTING=1."""
    import os
    if os.environ.get("E2E_TEST_MODE", "").lower() not in ("1", "true") and \
       os.environ.get("TESTING", "").lower() not in ("1", "true"):
        raise HTTPException(status_code=404, detail="Not found")

    email = request_data.get("email", "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.email_verified = True
    user.email_verification_token = None
    db.commit()
    return {"message": "Email verified for testing", "email": email}
