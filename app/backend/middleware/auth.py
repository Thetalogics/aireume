"""
JWT authentication dependency for FastAPI routes.
"""
import hashlib
import os
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session, joinedload

from app.backend.db.database import get_db
from app.backend.models.db_models import User, ImpersonationSession, RevokedToken

SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
# List of insecure default values that should never be used in production
INSECURE_SECRET_KEYS = {
    "your-secret-key-change-in-production",
    "dev-only-change-in-production",
    "change-me-in-production",
    "change-this-in-production",
    "your-secret-key",
    "secret",
    "password",
    "12345",
}
if not SECRET_KEY or SECRET_KEY in INSECURE_SECRET_KEYS:
    env = os.environ.get("ENVIRONMENT", "development")
    if os.environ.get("TESTING", "").lower() in ("1", "true"):
        SECRET_KEY = "test-secret-key-for-testing-only"
    elif env == "production":
        import sys
        import logging
        logging.getLogger(__name__).critical(
            f"FATAL: JWT_SECRET_KEY environment variable not set or using insecure default. Refusing to start in production."
        )
        sys.exit(1)
    else:
        import logging
        logging.getLogger(__name__).warning(
            "WARNING: Using insecure JWT_SECRET_KEY in development mode. This is acceptable for local development only."
        )
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer(auto_error=False)

# ─── Platform Role Constants ──────────────────────────────────────────────────

PLATFORM_ROLE_SUPER_ADMIN = "super_admin"
PLATFORM_ROLE_PRODUCT_OWNER = "product_owner"
PLATFORM_ROLE_BILLING_ADMIN = "billing_admin"
PLATFORM_ROLE_SUPPORT = "support"
PLATFORM_ROLE_SECURITY_ADMIN = "security_admin"
PLATFORM_ROLE_READONLY = "readonly"

ALL_PLATFORM_ROLES = [
    PLATFORM_ROLE_SUPER_ADMIN,
    PLATFORM_ROLE_PRODUCT_OWNER,
    PLATFORM_ROLE_BILLING_ADMIN,
    PLATFORM_ROLE_SUPPORT,
    PLATFORM_ROLE_SECURITY_ADMIN,
    PLATFORM_ROLE_READONLY,
]

READ_PLATFORM_ROLES = [
    PLATFORM_ROLE_SUPER_ADMIN,
    PLATFORM_ROLE_PRODUCT_OWNER,
    PLATFORM_ROLE_BILLING_ADMIN,
    PLATFORM_ROLE_SUPPORT,
    PLATFORM_ROLE_SECURITY_ADMIN,
    PLATFORM_ROLE_READONLY,
]

WRITE_PLATFORM_ROLES = [
    PLATFORM_ROLE_SUPER_ADMIN,
    PLATFORM_ROLE_PRODUCT_OWNER,
    PLATFORM_ROLE_BILLING_ADMIN,
    PLATFORM_ROLE_SUPPORT,
    PLATFORM_ROLE_SECURITY_ADMIN,
]

# Paths accessible before email verification (authenticated but unverified)
UNVERIFIED_ALLOWED_PREFIXES = (
    "/api/auth/me",
    "/api/auth/logout",
)

MFA_ENROLL_ALLOWED_PREFIXES = (
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/mfa",
)


def _mfa_enrollment_blocking(user: User) -> bool:
    if os.getenv("TESTING", "").lower() in {"1", "true", "yes"}:
        return False
    if getattr(user, "mfa_enabled", False):
        return False
    return (
        (user.role or "") == "admin"
        or bool(user.is_platform_admin)
        or bool(user.platform_role)
    )


# ─── User Loading ─────────────────────────────────────────────────────────────

def _load_user_from_token(db: Session, token: str) -> User:
    """Decode JWT and load user with tenant."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Reject tokens whose JTI has been explicitly revoked (e.g. on logout)
    jti = payload.get("jti")
    if jti:
        revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        if revoked:
            raise HTTPException(status_code=401, detail="Token revoked")

    user = (
        db.query(User)
        .options(joinedload(User.tenant))
        .filter(User.id == int(user_id), User.is_active == True)
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Check for user-level revocation (e.g., user deactivated while token was live)
    user_revocation = db.query(RevokedToken).filter(
        RevokedToken.jti.like(f"user_deactivated_{user.id}_%"),
        RevokedToken.expires_at > datetime.now(timezone.utc),
    ).first()
    if user_revocation:
        raise HTTPException(status_code=401, detail="Token revoked")

    return user


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    # Try Authorization header first (for API clients)
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        # Fall back to httpOnly cookie (for browser clients)
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = _load_user_from_token(db, token)

    # Block unverified users from app routes (platform admins exempt)
    if not user.email_verified and not user.is_platform_admin_compat:
        path = request.url.path
        if not any(path.startswith(prefix) for prefix in UNVERIFIED_ALLOWED_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "detail": "Please verify your email before accessing the application.",
                    "error_code": "EMAIL_NOT_VERIFIED",
                },
            )

    if _mfa_enrollment_blocking(user):
        path = request.url.path
        if not any(path.startswith(prefix) for prefix in MFA_ENROLL_ALLOWED_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "MFA_SETUP_REQUIRED",
                    "message": "Set up multi-factor authentication to continue.",
                },
            )

    # Check for impersonation token header
    impersonation_token = request.headers.get("X-Impersonation-Token")
    if impersonation_token:
        token_hash = hashlib.sha256(impersonation_token.encode()).hexdigest()
        session = (
            db.query(ImpersonationSession)
            .filter(
                ImpersonationSession.token_hash == token_hash,
                ImpersonationSession.revoked_at.is_(None),
                ImpersonationSession.expires_at > datetime.now(timezone.utc),
            )
            .first()
        )
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired impersonation session")
        if session.admin_user_id != user.id:
            raise HTTPException(
                status_code=403,
                detail="Impersonation token is not bound to this user",
            )

        # Load target user (the impersonated account)
        target_user = (
            db.query(User)
            .options(joinedload(User.tenant))
            .filter(User.id == session.target_user_id, User.is_active == True)
            .first()
        )
        if not target_user:
            raise HTTPException(status_code=401, detail="Impersonation target user not found")

        # Mark the request context so downstream code knows this is impersonated
        target_user._impersonated_by = session.admin_user_id
        target_user._impersonation_session_id = session.id
        user = target_user

    # Check tenant suspension (platform admins bypass)
    if user.tenant and getattr(user.tenant, "suspended_at", None) is not None:
        if not getattr(user, "is_platform_admin", False) and getattr(user, "platform_role", None) is None:
            raise HTTPException(status_code=403, detail="Account suspended. Contact support.")

    return user


# ─── Subscription Status Enforcement ─────────────────────────────────────────

def require_active_subscription(
    current_user: User = Depends(get_current_user),
    response: Response = None,
) -> User:
    """
    Dependency that blocks write access for tenants with suspended or cancelled subscriptions.
    Returns the current user if subscription is active/trialing/past_due.
    Adds a warning header for past_due subscriptions.
    """
    tenant = current_user.tenant
    if tenant and tenant.subscription_status in ("suspended", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Your subscription is suspended. Please update your payment method to restore access.",
                "error_code": "SUBSCRIPTION_SUSPENDED",
                "subscription_status": tenant.subscription_status,
            },
        )
    if response and tenant and tenant.subscription_status == "past_due":
        response.headers["X-Subscription-Warning"] = "past_due"
    return current_user


# ─── Tenant-Level RBAC ────────────────────────────────────────────────────────

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ─── Platform-Level RBAC ──────────────────────────────────────────────────────

def _check_platform_role(current_user: User, allowed_roles: list[str]) -> User:
    """Internal helper to validate platform role."""
    user_role = getattr(current_user, "platform_role", None)
    legacy_flag = getattr(current_user, "is_platform_admin", False)

    # Backward compatibility: legacy is_platform_admin=true counts as super_admin
    if legacy_flag and PLATFORM_ROLE_SUPER_ADMIN in allowed_roles:
        return current_user
    # product_owner legacy: treat as product_owner when that role is allowed
    if legacy_flag and PLATFORM_ROLE_PRODUCT_OWNER in allowed_roles and user_role == PLATFORM_ROLE_PRODUCT_OWNER:
        return current_user

    if user_role in allowed_roles:
        return current_user

    raise HTTPException(status_code=403, detail="Platform admin access required")


def require_platform_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require any platform-level admin access (backward compatible)."""
    return _check_platform_role(current_user, ALL_PLATFORM_ROLES)


def require_platform_write(current_user: User = Depends(get_current_user)) -> User:
    """Require a platform role that may perform write operations (excludes readonly)."""
    return _check_platform_role(current_user, WRITE_PLATFORM_ROLES)


def require_platform_role(roles: list[str]):
    """Generic decorator requiring one of the specified platform roles."""
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        return _check_platform_role(current_user, roles)
    return dependency


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, [PLATFORM_ROLE_SUPER_ADMIN])


def require_product_owner(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, [PLATFORM_ROLE_SUPER_ADMIN, PLATFORM_ROLE_PRODUCT_OWNER])


def require_billing_admin(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, [PLATFORM_ROLE_SUPER_ADMIN, PLATFORM_ROLE_BILLING_ADMIN])


def require_support(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, [PLATFORM_ROLE_SUPER_ADMIN, PLATFORM_ROLE_SUPPORT])


def require_security_admin(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, [PLATFORM_ROLE_SUPER_ADMIN, PLATFORM_ROLE_SECURITY_ADMIN])


def require_readonly_platform(current_user: User = Depends(get_current_user)) -> User:
    return _check_platform_role(current_user, READ_PLATFORM_ROLES)


# ─── Feature Gating ───────────────────────────────────────────────────────────

def require_feature(feature_key: str):
    """Dependency that checks if a feature is enabled for the current user's tenant."""
    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        from app.backend.services.feature_flag_service import is_feature_enabled
        from app.backend.services.plan_entitlement_service import plan_feature_detail
        if not is_feature_enabled(db, current_user.tenant_id, feature_key):
            detail = plan_feature_detail(db, current_user.tenant_id, feature_key)
            raise HTTPException(
                status_code=403,
                detail={
                    "detail": detail.get("upgrade_hint") or f"Feature '{feature_key}' is not available on your plan",
                    "error_code": "PLAN_FEATURE_LOCKED",
                    "feature": feature_key,
                    "plan": detail.get("plan"),
                },
            )
        return current_user
    return dependency


# ─── Internal Service-to-Service Auth ─────────────────────────────────────────

import secrets as _secrets

INTERNAL_SERVICE_SECRET = os.environ.get("INTERNAL_SERVICE_SECRET")
if not INTERNAL_SERVICE_SECRET:
    _env = os.environ.get("ENVIRONMENT", "development")
    if os.environ.get("TESTING", "").lower() in ("1", "true"):
        INTERNAL_SERVICE_SECRET = "test-internal-service-secret"
    elif _env == "production":
        import sys as _sys
        import logging as _logging
        _logging.getLogger(__name__).critical(
            "FATAL: INTERNAL_SERVICE_SECRET not set. Refusing to start in production; "
            "internal service endpoints (voice/recruiter/interview callbacks) require it."
        )
        _sys.exit(1)
    else:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "WARNING: INTERNAL_SERVICE_SECRET not set. Internal callbacks will be rejected."
        )
        INTERNAL_SERVICE_SECRET = ""


def require_internal_service(request: Request) -> None:
    """
    Dependency guarding service-to-service internal endpoints (called by the
    voice-agent container). Validates the X-Internal-Secret header against
    INTERNAL_SERVICE_SECRET using a constant-time comparison.
    """
    provided = request.headers.get("X-Internal-Service-Secret") or request.headers.get("X-Internal-Secret", "")
    if request.headers.get("X-Internal-Secret") and not request.headers.get("X-Internal-Service-Secret"):
        import logging as _log
        _log.getLogger(__name__).warning("deprecated_internal_header X-Internal-Secret")
    if not INTERNAL_SERVICE_SECRET or not provided or not _secrets.compare_digest(provided, INTERNAL_SERVICE_SECRET):
        raise HTTPException(status_code=403, detail="Invalid or missing internal service credentials")
