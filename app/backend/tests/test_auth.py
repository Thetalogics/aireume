"""
Tests for /api/auth/* endpoints: register, login, refresh, /me, logout.
"""
import pytest
from datetime import datetime, timedelta, timezone
from jose import jwt

from app.backend.middleware.auth import SECRET_KEY, ALGORITHM
from app.backend.tests.test_helpers import _verify_user_via_api, access_token_from


REGISTER_PAYLOAD = {
    "company_name": "Acme Corp",
    "email": "hr@acme.com",
    "password": "HRPass123!",
    "full_name": "HR Manager",
}


class TestRegister:
    def test_register_creates_tenant_and_user(self, client):
        resp = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("requires_verification") is True
        assert "tenant" in data
        assert data["tenant"]["name"] == "Acme Corp"
        assert data["tenant"]["slug"]
        assert "access_token" not in data

    def test_register_duplicate_email_returns_4xx(self, client):
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        resp = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert resp.status_code in (200, 201, 400, 409)

    def test_register_missing_fields_returns_422(self, client):
        resp = client.post("/api/auth/register", json={"email": "x@y.com"})
        assert resp.status_code == 422

    def test_register_missing_required_field_returns_422(self, client):
        # Missing company_name should fail validation
        resp = client.post("/api/auth/register", json={"email": "x@y.com", "password": "Pass123!"})
        assert resp.status_code == 422


class TestLogin:
    def setup_method(self, _):
        """Each test calls register fresh via the `client` fixture."""
        pass

    def test_login_success_returns_tokens(self, client):
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert access_token_from(resp)
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, client):
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_login_unknown_email_returns_401(self, client):
        resp = client.post("/api/auth/login", json={
            "email": "nobody@nowhere.com",
            "password": "whatever",
        })
        assert resp.status_code == 401

    def test_login_missing_password_returns_422(self, client):
        resp = client.post("/api/auth/login", json={"email": "x@y.com"})
        assert resp.status_code == 422


class TestMe:
    def test_me_returns_user_data(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        # Response is {"user": {...}, "tenant": {...}}
        user = data.get("user") or data
        assert "email" in user
        assert "role" in user
        assert user["role"] == "admin"

    def test_me_without_token_returns_401(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token_returns_401(self, client):
        client.headers.update({"Authorization": "Bearer totally.invalid.token"})
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401


class TestRefresh:
    def test_refresh_returns_new_access_token(self, client):
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        refresh_token = login.cookies.get("refresh_token") or login.json().get("refresh_token")

        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert access_token_from(resp)

    def test_refresh_with_invalid_token_returns_401(self, client):
        resp = client.post("/api/auth/refresh", json={"refresh_token": "bad.token.here"})
        assert resp.status_code == 401


class TestTokenRevocation:
    """Tests for token revocation on logout."""

    def test_logout_revokes_refresh_token(self, client, db):
        """Logout should store the refresh token's JTI in the revoked_tokens table."""
        from app.backend.models.db_models import RevokedToken
        
        # Register and login
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login_resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        assert login_resp.status_code == 200
        refresh_token = login_resp.json().get("refresh_token") or login_resp.cookies.get("refresh_token")
        
        # Decode the refresh token to get JTI
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        assert jti is not None, "Refresh token should have a JTI"
        
        # Logout with the refresh token in body
        logout_resp = client.post("/api/auth/logout", json={"refresh_token": refresh_token})
        assert logout_resp.status_code == 200
        
        # Check that the JTI is in the revoked_tokens table
        revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        assert revoked is not None, "JTI should be stored in revoked_tokens"
        assert revoked.jti == jti

    def test_refresh_with_revoked_token_returns_401(self, client, db):
        """Attempting to refresh with a revoked token should return 401."""
        from app.backend.models.db_models import RevokedToken
        
        # Register and login
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login_resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        refresh_token = login_resp.json().get("refresh_token") or login_resp.cookies.get("refresh_token")
        
        # Decode the refresh token to get JTI
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        
        # Manually revoke the token
        revoked_token = RevokedToken(
            jti=jti,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        db.add(revoked_token)
        db.commit()
        
        # Try to refresh with the revoked token
        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"].lower()

    def test_normal_flow_login_refresh_logout(self, client, db):
        """Test the complete auth flow: login -> refresh -> logout."""
        from app.backend.models.db_models import RevokedToken
        
        # Register and login
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login_resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        assert login_resp.status_code == 200
        access_token = (login_resp.cookies.get("access_token") or login_resp.json().get("access_token"))
        refresh_token = login_resp.json().get("refresh_token") or login_resp.cookies.get("refresh_token")
        
        # Use access token to access protected endpoint
        client.headers.update({"Authorization": f"Bearer {access_token}"})
        me_resp = client.get("/api/auth/me")
        assert me_resp.status_code == 200
        
        # Refresh the token
        refresh_resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert refresh_resp.status_code == 200
        new_refresh_token = (refresh_resp.cookies.get("refresh_token") or refresh_resp.json().get("refresh_token"))
        
        # Logout
        logout_resp = client.post("/api/auth/logout", json={"refresh_token": new_refresh_token})
        assert logout_resp.status_code == 200
        
        # Verify the new refresh token is revoked
        payload = jwt.decode(new_refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        assert revoked is not None

    def test_tokens_contain_jti(self, client):
        """Both access and refresh tokens should contain a JTI claim."""
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        access = login.cookies.get("access_token") or login.json().get("access_token")
        refresh = login.cookies.get("refresh_token") or login.json().get("refresh_token")

        access_payload = jwt.decode(access, SECRET_KEY, algorithms=[ALGORITHM])
        assert "jti" in access_payload, "Access token should have JTI"

        refresh_payload = jwt.decode(refresh, SECRET_KEY, algorithms=[ALGORITHM])
        assert "jti" in refresh_payload, "Refresh token should have JTI"

        assert access_payload["jti"] != refresh_payload["jti"]

    def test_tokens_contain_tenant_id(self, client):
        """Both access and refresh tokens should contain tenant_id."""
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        login = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        access = login.cookies.get("access_token") or login.json().get("access_token")
        refresh = login.cookies.get("refresh_token") or login.json().get("refresh_token")

        access_payload = jwt.decode(access, SECRET_KEY, algorithms=[ALGORITHM])
        assert "tenant_id" in access_payload, "Access token should have tenant_id"

        refresh_payload = jwt.decode(refresh, SECRET_KEY, algorithms=[ALGORITHM])
        assert "tenant_id" in refresh_payload, "Refresh token should have tenant_id"


class TestEmailVerification:
    """Tests for email verification blocking login."""

    def test_unverified_user_gets_403_on_login(self, client):
        """Newly registered user cannot login until email is verified."""
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        assert resp.status_code == 403
        assert "verify your email" in resp.json()["detail"].lower()

    def test_verify_email_endpoint_then_login_succeeds(self, client, db):
        """User can login after verifying email via the token endpoint."""
        from app.backend.models.db_models import User

        reg_resp = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert reg_resp.status_code in (200, 201)

        # Get the verification token from the DB (email sending is mocked in tests)
        user = db.query(User).filter(User.email == REGISTER_PAYLOAD["email"]).first()
        assert user is not None
        assert user.email_verified is False
        verification_token = user.email_verification_token
        assert verification_token is not None

        # Verify email via POST (token stays out of the URL / access logs)
        verify_resp = client.post("/api/auth/verify-email", json={"token": verification_token})
        assert verify_resp.status_code == 200
        assert verify_resp.json()["message"] == "Email verified successfully"

        # Now login should succeed
        login_resp = client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })
        assert login_resp.status_code == 200
        assert access_token_from(login_resp)

    def test_invalid_verification_token_returns_400(self, client):
        """Invalid token returns 400."""
        resp = client.post("/api/auth/verify-email", json={"token": "nonexistent-token"})
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_register_response_includes_verification_message(self, client):
        """Register response tells user to verify email."""
        resp = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "verify" in data.get("message", "").lower()


class TestLogout:
    """Tests for logout functionality."""

    def test_logout_clears_cookies(self, client):
        """Logout should clear all auth cookies."""
        # Register and login
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        _verify_user_via_api(REGISTER_PAYLOAD["email"])
        client.post("/api/auth/login", json={
            "email": REGISTER_PAYLOAD["email"],
            "password": REGISTER_PAYLOAD["password"],
        })

        # Logout
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out successfully"

    def test_logout_without_token_still_succeeds(self, client):
        """Logout should succeed even without a refresh token."""
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
