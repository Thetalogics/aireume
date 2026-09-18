"""Test tenant scoring weights functionality."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

from app.backend.main import app
from app.backend.db.database import Base
from app.backend.models.db_models import Tenant, User

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def test_db():
    """Create test database."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(test_db):
    """Create a new database session for a test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """Create a test client with database override."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    from app.backend.db.database import get_db
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


class TestTenantScoringWeights:
    """Test tenant-level scoring weights."""

    def test_tenant_has_scoring_weights_column(self, db_session):
        """Verify tenant model has scoring_weights column."""
        tenant = Tenant(
            name="Test Tenant",
            slug="test-tenant",
            scoring_weights='{"skills": 0.40, "experience": 0.30}'
        )
        db_session.add(tenant)
        db_session.commit()
        db_session.refresh(tenant)
        
        assert tenant.scoring_weights is not None
        assert "skills" in tenant.scoring_weights

    def test_tenant_scoring_weights_can_be_null(self, db_session):
        """Verify scoring_weights can be null (uses defaults)."""
        tenant = Tenant(
            name="Test Tenant 2",
            slug="test-tenant-2",
        )
        db_session.add(tenant)
        db_session.commit()
        db_session.refresh(tenant)
        
        assert tenant.scoring_weights is None

    def test_update_tenant_scoring_weights(self, client, db_session):
        """Test updating tenant scoring weights via admin API."""
        # Create tenant
        tenant = Tenant(
            name="Weight Test Tenant",
            slug="weight-test",
        )
        db_session.add(tenant)
        db_session.commit()
        db_session.refresh(tenant)
        
        # Create admin user
        from passlib.context import CryptContext
        # Use sha256_crypt instead of bcrypt for better cross-platform compatibility
        pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
        admin_user = User(
            tenant_id=1,
            email="admin@test.com",
            hashed_password=pwd_context.hash("password123"),
            role="admin",
            is_platform_admin=True,
            platform_role="super_admin",
            email_verified=True,
        )
        db_session.add(admin_user)
        db_session.commit()
        
        # Login
        response = client.post("/api/auth/login", json={
            "email": "admin@test.com",
            "password": "password123"
        })
        assert response.status_code == 200
        token = (response.cookies.get("access_token") or response.json().get("access_token"))
        
        # Update tenant weights
        new_weights = {
            "core_competencies": 0.35,
            "experience": 0.25,
            "domain_fit": 0.15,
            "education": 0.10,
            "career_trajectory": 0.10,
            "role_excellence": 0.05,
        }
        
        response = client.put(
            f"/api/admin/tenants/{tenant.id}",
            json={
                "scoring_weights": new_weights
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        
        # Verify weights were saved
        db_session.refresh(tenant)
        import json
        saved_weights = json.loads(tenant.scoring_weights)
        assert saved_weights["core_competencies"] == 0.35
        assert saved_weights["experience"] == 0.25


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
