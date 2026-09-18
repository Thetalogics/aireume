"""
Pytest fixtures shared across all backend test modules.
"""
import sys
import os
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, AsyncMock, MagicMock

# Ensure project root is on the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

# Set testing mode so JWT secret enforcement bypasses for tests
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("INTEGRATION_MASTER_KEY", "0" * 64)
# LLM model names are env-only in application code; tests set defaults here.
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:7b")
os.environ.setdefault("OLLAMA_MODEL_BACKEND", "qwen2.5:7b")
os.environ.setdefault("OLLAMA_MODEL_VOICE", "qwen2.5:3b")
# Existing production env knobs — keep retries/delays short if a test leaks a provider call.
os.environ.setdefault("LLM_JSON_TIER_DELAY", "0")
os.environ.setdefault("GUARDRAIL_RETRY_DELAY", "0")
os.environ.setdefault("GUARDRAIL_MAX_RETRIES", "1")
os.environ.setdefault("GUARDRAIL_PER_CALL_TIMEOUT", "2")
os.environ.setdefault("OUTLINES_STRUCTURED_JSON", "0")
os.environ.setdefault("LLM_NARRATIVE_TIMEOUT", "2")

from app.backend.db import database
from app.backend.main import app

# ─── In-memory SQLite DB ──────────────────────────────────────────────────────
#
# Guard against double-import.  pytest loads conftest as a plugin under one
# module name, but ``from app.backend.tests.conftest import X`` triggers a
# second load under a *different* qualified name, which would create a NEW
# in-memory SQLite and break the test DB ("no such table" errors).
_conftest_initialized = getattr(database, '_conftest_initialized', False)

if not _conftest_initialized:
    database._conftest_initialized = True

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

    test_engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared in-memory DB for all connections
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()


    database.engine = test_engine
    database.SessionLocal = TestingSessionLocal
    database.ReplicaSessionLocal = TestingSessionLocal
    app.dependency_overrides[database.get_db] = override_get_db
    app.dependency_overrides[database.get_read_db] = override_get_db
else:
    # Re-import: reuse the already-initialized objects so that fixture
    # references stay consistent with the first-load module scope.
    test_engine = database.engine
    TestingSessionLocal = database.SessionLocal
    database.ReplicaSessionLocal = TestingSessionLocal


# Import all models
from app.backend.models.db_models import *  # noqa: F401, F403

# Import queue manager models - they have their own Base
# We need to handle them specially for test database setup
from app.backend.services import queue_manager as _qm

# Mock queue worker functions to prevent database access during tests
from unittest.mock import MagicMock
_qm.start_queue_worker = AsyncMock()
_qm.stop_queue_worker = AsyncMock()


def _create_all_tables():
    """Create all tables including queue tables."""
    from sqlalchemy import text
    
    # First create main tables (tenants, users, candidates, etc.)
    database.Base.metadata.create_all(bind=test_engine)
    
    # Create queue tables using raw SQL to avoid FK resolution issues
    # The queue tables reference main tables (tenants, candidates, users)
    with test_engine.connect() as conn:
        # Create analysis_jobs table without FK constraints
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id INTEGER NOT NULL,
                candidate_id INTEGER,
                user_id INTEGER,
                job_type VARCHAR(50) NOT NULL DEFAULT 'resume_screening',
                resume_hash VARCHAR(64) NOT NULL,
                jd_hash VARCHAR(64) NOT NULL,
                input_hash VARCHAR(64) NOT NULL UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 5,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                failed_at TIMESTAMP,
                next_retry_at TIMESTAMP,
                worker_id VARCHAR(100),
                worker_heartbeat TIMESTAMP,
                artifact_id VARCHAR(36),
                processing_stage VARCHAR(50),
                progress_percent INTEGER NOT NULL DEFAULT 0,
                estimated_completion TIMESTAMP,
                error_message TEXT,
                error_type VARCHAR(100),
                error_stack_trace TEXT,
                error_context TEXT,
                result_id VARCHAR(36),
                job_config TEXT
            )
        """))
        
        # Create analysis_results table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id VARCHAR(36) PRIMARY KEY,
                job_id VARCHAR(36) NOT NULL,
                tenant_id INTEGER NOT NULL,
                candidate_id INTEGER NOT NULL,
                fit_score INTEGER,
                recommendation VARCHAR(50),
                strengths TEXT,
                weaknesses TEXT,
                skill_analysis TEXT,
                experience_analysis TEXT,
                education_analysis TEXT,
                risk_signals TEXT,
                interview_questions TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Create analysis_artifacts table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                id VARCHAR(36) PRIMARY KEY,
                job_id VARCHAR(36) NOT NULL,
                tenant_id INTEGER NOT NULL,
                artifact_type VARCHAR(50) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                storage_path VARCHAR(500) NOT NULL,
                size_bytes INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Create job_metrics table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS job_metrics (
                id VARCHAR(36) PRIMARY KEY,
                job_id VARCHAR(36) NOT NULL,
                tenant_id INTEGER NOT NULL,
                queue_time_ms INTEGER,
                processing_time_ms INTEGER,
                total_time_ms INTEGER,
                retry_count INTEGER NOT NULL DEFAULT 0,
                worker_id VARCHAR(100),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        conn.commit()


def _drop_all_tables():
    """Drop all tables safely, tolerating background threads that may hold cursors."""
    import time
    from sqlalchemy import text
    
    # Brief pause to let any in-flight background DB operations complete
    # (e.g. _background_llm_narrative thread writing to SQLite)
    time.sleep(0.05)
    
    # Drop tables with retry - SQLite in-memory with StaticPool uses a single
    # connection, so background threads may briefly hold locks
    for attempt in range(3):
        try:
            with test_engine.connect() as conn:
                conn.execute(text("DROP TABLE IF EXISTS job_metrics"))
                conn.execute(text("DROP TABLE IF EXISTS analysis_artifacts"))
                conn.execute(text("DROP TABLE IF EXISTS field_audit_logs"))
                conn.execute(text("DROP TABLE IF EXISTS analysis_results"))
                conn.execute(text("DROP TABLE IF EXISTS analysis_jobs"))
                conn.commit()
            database.Base.metadata.drop_all(bind=test_engine)
            return  # Success
        except Exception:
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
            else:
                # Final attempt: just drop via metadata (best-effort)
                try:
                    database.Base.metadata.drop_all(bind=test_engine)
                except Exception:
                    pass  # Teardown failure is non-fatal for test results

# ─── Patch bcrypt → sha256_crypt for local test compatibility ────────────────
# passlib 1.7.4 + bcrypt 4.x has a known incompatibility. Use sha256_crypt in
# tests (same API, no C-library issues, not for production).
try:
    from passlib.context import CryptContext as _CC
    import app.backend.routes.auth as _auth_mod
    _auth_mod.pwd_context = _CC(schemes=["sha256_crypt"], deprecated="auto")
except Exception:
    pass  # If patching fails, fall through and let tests error naturally


# ─── Helpers ──────────────────────────────────────────────────────────────────

from app.backend.tests.test_helpers import access_token_from


def _verify_user_via_api(email: str):
    """Mark a user's email as verified via the DB session.

    Uses ``database.SessionLocal`` which is set to ``TestingSessionLocal``
    by conftest.py at startup, so it always targets the in-memory test DB
    regardless of which module scope the caller lives in.
    """
    from app.backend.models.db_models import User
    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.email_verified = True
            db.commit()
    finally:
        db.close()


# ─── Base fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    # Create all tables including queue tables
    _create_all_tables()
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        _drop_all_tables()


@pytest.fixture(autouse=True)
def _clear_rate_limit_buckets():
    """Clear rate-limit buckets before every test to prevent 429s in CI."""
    from app.backend.middleware.rate_limit import RateLimitMiddleware
    middleware = RateLimitMiddleware._instance
    if middleware is not None:
        middleware.buckets.clear()
        middleware.config_cache.clear()
    from app.backend.middleware.rate_limit import _concurrent_llm
    _concurrent_llm.clear()
    # Also clear the auth route's per-IP rate limiter
    try:
        from app.backend.routes.auth import auth_rate_limiter
        auth_rate_limiter._attempts.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _clear_feature_flag_cache():
    """Clear plan/feature flag cache between tests (module-level TTL cache)."""
    from app.backend.services.feature_flag_service import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


_ADAPTER_LLM_TEST_FILES = frozenset({
    "test_app_llm_client.py",
    "test_recruiter_llm.py",
})
_HTTP_PASSTHROUGH_LLM_TEST_FILES = _ADAPTER_LLM_TEST_FILES | frozenset({
    "test_llm_service.py",
})


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.external unless the run explicitly selected that marker."""
    markexpr = (getattr(config.option, "markexpr", None) or "").strip()
    if "external" in markexpr:
        return
    skip_ext = pytest.mark.skip(reason="external/live provider test (run with -m external)")
    for item in items:
        if item.get_closest_marker("external"):
            item.add_marker(skip_ext)


@pytest.fixture(autouse=True)
def _hermetic_llm_providers(monkeypatch, request):
    """Block accidental Gemini/Ollama/OpenRouter calls in ordinary tests."""
    if request.node.get_closest_marker("external"):
        return
    path = getattr(request.node, "path", None) or getattr(request.node, "fspath", None)
    filename = getattr(path, "name", None) or (str(path).rsplit("/", 1)[-1] if path else "")
    if filename in _ADAPTER_LLM_TEST_FILES:
        return

    async def _no_provider(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.backend.services.app_llm_client._try_gemini", _no_provider)
    monkeypatch.setattr("app.backend.services.app_llm_client._try_ollama", _no_provider)
    monkeypatch.setattr("app.backend.services.app_llm_client._try_openrouter", _no_provider)
    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_gemini",
        _no_provider,
    )
    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_ollama",
        _no_provider,
    )
    if filename != "test_structured_llm_service.py":
        monkeypatch.setattr(
            "app.backend.services.structured_llm_service.invoke_outlines_json_resilient",
            _no_provider,
        )

    dummy = MagicMock(name="hermetic_llm")
    dummy.ainvoke = AsyncMock(side_effect=RuntimeError("hermetic tests: LLM provider blocked"))
    dummy.invoke = MagicMock(side_effect=RuntimeError("hermetic tests: LLM provider blocked"))

    monkeypatch.setattr(
        "app.backend.services.wip.agent_pipeline.get_fast_llm",
        lambda *a, **k: dummy,
    )
    monkeypatch.setattr(
        "app.backend.services.wip.agent_pipeline.get_reasoning_llm",
        lambda *a, **k: dummy,
    )
    monkeypatch.setattr(
        "app.backend.services.hybrid_pipeline._get_llm",
        lambda *a, **k: dummy,
    )
    monkeypatch.setattr("app.backend.services.hybrid_pipeline._REASONING_LLM", None, raising=False)
    monkeypatch.setattr("app.backend.services.wip.agent_pipeline._fast_llm", None, raising=False)
    monkeypatch.setattr("app.backend.services.wip.agent_pipeline._reasoning_llm", None, raising=False)

    if filename in _HTTP_PASSTHROUGH_LLM_TEST_FILES:
        return

    import httpx

    blocked_needles = (
        "generativelanguage.googleapis.com",
        "openrouter.ai",
        ":11434",
        "ollama:",
        "localhost:11434",
        "127.0.0.1:11434",
    )

    orig_async_request = httpx.AsyncClient.request

    def _blocked(url: object) -> bool:
        target = str(url).lower()
        return any(needle in target for needle in blocked_needles)

    async def _async_request(self, method, url, *args, **kwargs):
        if _blocked(url):
            req = httpx.Request(method, str(url))
            raise httpx.ConnectError("hermetic tests blocked LLM host", request=req)
        return await orig_async_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", _async_request)


@pytest.fixture(scope="function")
def client():
    # Create all tables including queue tables
    _create_all_tables()
    with TestClient(app) as c:
        yield c
    _drop_all_tables()


# ─── Authenticated client fixture ────────────────────────────────────────────

@pytest.fixture(scope="function")
def auth_client(client, db, seed_subscription_plans):
    """
    Returns a TestClient already configured with a valid JWT Bearer token.
    Registers a tenant + admin user, logs in, injects the Authorization header.
    """
    from app.backend.tests.test_helpers import assign_tenant_plan

    register_payload = {
        "company_name": "TestCorp",
        "email": "admin@testcorp.com",
        "password": "TestPass123!",
        "full_name": "Test Admin",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    # Register no longer returns tokens — verify then login
    _verify_user_via_api("admin@testcorp.com")
    assign_tenant_plan(db, "growth", "pro", slug="testcorp")
    from app.backend.tests.test_helpers import allow_ad_hoc_screening
    allow_ad_hoc_screening(db, slug="testcorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "admin@testcorp.com",
        "password": "TestPass123!",
    })
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = access_token_from(login_resp)

    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture(scope="function")
def viewer_client(client, db, seed_subscription_plans):
    """Authenticated client with tenant role=viewer (read-only)."""
    from app.backend.models.db_models import User
    from app.backend.routes.auth import _hash_password
    from app.backend.tests.test_helpers import assign_tenant_plan, allow_ad_hoc_screening

    register_payload = {
        "company_name": "ViewerCorp",
        "email": "admin@viewercorp.com",
        "password": "TestPass123!",
        "full_name": "Viewer Corp Admin",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"
    _verify_user_via_api("admin@viewercorp.com")
    assign_tenant_plan(db, "growth", "pro", slug="viewercorp")
    allow_ad_hoc_screening(db, slug="viewercorp")

    admin_user = db.query(User).filter(User.email == "admin@viewercorp.com").first()
    assert admin_user is not None

    viewer = User(
        tenant_id=admin_user.tenant_id,
        email="viewer@viewercorp.com",
        hashed_password=_hash_password("ViewerPass123!"),
        role="viewer",
        is_active=True,
        email_verified=True,
    )
    db.add(viewer)
    db.commit()

    _verify_user_via_api("viewer@viewercorp.com")

    login_resp = client.post("/api/auth/login", json={
        "email": "viewer@viewercorp.com",
        "password": "ViewerPass123!",
    })
    assert login_resp.status_code == 200, f"Viewer login failed: {login_resp.text}"
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture(scope="function")
def auth_client_with_token(client):
    """
    Like auth_client but also returns the raw token for cases where tests need it.
    """
    register_payload = {
        "company_name": "TokenCorp",
        "email": "user@tokencorp.com",
        "password": "SecurePass456!",
        "full_name": "Token User",
    }
    client.post("/api/auth/register", json=register_payload)

    # Mark user as verified for testing
    _verify_user_via_api("user@tokencorp.com")

    login_resp = client.post("/api/auth/login", json={
        "email": "user@tokencorp.com",
        "password": "SecurePass456!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, token


@pytest.fixture(scope="function")
def platform_admin_client(client, db):
    """
    Returns a TestClient configured with a platform admin JWT Bearer token.
    Registers a user, logs in, then sets is_platform_admin=True directly in DB.
    """
    from app.backend.models.db_models import User

    register_payload = {
        "company_name": "PlatformAdminCorp",
        "email": "platformadmin@test.com",
        "password": "PlatformAdmin123!",
        "full_name": "Platform Admin",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    # Mark user as verified for testing
    _verify_user_via_api("platformadmin@test.com")

    login_resp = client.post("/api/auth/login", json={
        "email": "platformadmin@test.com",
        "password": "PlatformAdmin123!",
    })
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = access_token_from(login_resp)

    # Set is_platform_admin=True directly on the user
    user = db.query(User).filter(User.email == "platformadmin@test.com").first()
    user.is_platform_admin = True
    db.commit()

    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture(scope="function")
def platform_admin_client_with_plans(platform_admin_client, seed_subscription_plans):
    """Platform admin client with subscription plans seeded."""
    return platform_admin_client


# ─── Mock Ollama ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ollama_communication():
    """Mock communication analysis LLM to return a valid JSON result."""
    with patch(
        "app.backend.services.app_llm_client.generate_app_json",
        new_callable=AsyncMock,
        return_value={
            "communication_score": 78,
            "confidence_level": "high",
            "clarity_score": 82,
            "articulation_score": 75,
            "key_phrases": ["strong technical background", "team player"],
            "strengths": ["Clear articulation", "Good pacing"],
            "red_flags": [],
            "summary": "Candidate communicates clearly and confidently.",
        },
    ) as mock:
        yield mock


@pytest.fixture
def mock_ollama_malpractice():
    """Mock malpractice analysis LLM to return a valid JSON result."""
    with patch(
        "app.backend.services.app_llm_client.generate_app_json",
        new_callable=AsyncMock,
        return_value={
            "malpractice_score": 15,
            "malpractice_risk": "low",
            "reliability_rating": "trustworthy",
            "flags": [],
            "positive_signals": ["Natural filler words present", "Self-corrections observed"],
            "overall_assessment": "No significant malpractice signals detected.",
            "follow_up_questions": [],
        },
    ) as mock:
        yield mock


@pytest.fixture
def mock_whisper():
    """Mock faster-whisper transcription."""
    def fake_transcribe(video_path: str) -> dict:
        return {
            "transcript": (
                "Hello, I have five years of experience in Python development. "
                "I worked at TechCorp where I led a team of three engineers. "
                "I am proficient in FastAPI, React, and PostgreSQL."
            ),
            "segments": [
                {"start": 0.0, "end": 3.5, "text": "Hello, I have five years of experience in Python development.", "avg_logprob": -0.3, "no_speech_prob": 0.01},
                {"start": 3.8, "end": 8.1, "text": "I worked at TechCorp where I led a team of three engineers.", "avg_logprob": -0.25, "no_speech_prob": 0.02},
                {"start": 8.5, "end": 12.0, "text": "I am proficient in FastAPI, React, and PostgreSQL.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
            ],
            "language": "en",
            "duration_s": 12.0,
        }

    with patch("app.backend.services.video_service.transcribe_video", side_effect=fake_transcribe):
        yield fake_transcribe


@pytest.fixture
def mock_hybrid_pipeline():
    """Mock the hybrid pipeline to avoid Ollama calls in analyze route tests."""
    pipeline_result = {
        "fit_score": 75,
        "job_role": "Senior Python Engineer",
        "strengths": ["Strong Python skills", "Good communication"],
        "weaknesses": ["Limited cloud experience"],
        "education_analysis": "Solid CS background — relevant degree.",
        "risk_signals": [],
        "final_recommendation": "Consider",
        "employment_gaps": [],
        "score_breakdown": {
            "skill_match": {"score": 80, "confidence_weighted": False, "avg_confidence": 1.0}, "experience_match": 70,
            "stability": 90, "education": 70,
            "architecture": 65, "domain_fit": 78, "timeline": 90, "risk_penalty": 5,
        },
        "matched_skills": ["python", "react"],
        "missing_skills": ["kubernetes"],
        "adjacent_skills": ["docker"],
        "risk_level": "Low",
        "interview_questions": {
            "technical_questions": ["Explain FastAPI dependency injection."],
            "behavioral_questions": ["Tell me about a challenging project."],
            "culture_fit_questions": ["How do you handle tight deadlines?"],
        },
        "required_skills_count": 3,
        "jd_analysis": {
            "role_title": "Senior Python Engineer", "domain": "backend",
            "seniority": "senior", "required_skills": ["python", "kubernetes"],
            "required_years": 5, "nice_to_have_skills": ["docker"], "key_responsibilities": [],
        },
        "candidate_profile": {
            "name": "John Doe", "skills_identified": ["python", "react"],
            "career_summary": "Senior Dev at TechCorp — 6 years total experience",
            "total_effective_years": 6.0,
            "current_role": "Senior Dev", "current_company": "TechCorp",
            "education": [{"degree": "BSc Computer Science", "field": "computer science"}],
        },
        "skill_analysis": {
            "matched_skills": ["python", "react"], "missing_skills": ["kubernetes"],
            "adjacent_skills": ["docker"], "skill_score": 80, "required_count": 3,
        },
        "edu_timeline_analysis": {
            "education_score": 70, "timeline_text": "Continuous employment — no significant gaps.",
            "employment_gaps": [], "overlapping_jobs": [], "short_stints": [],
        },
        "explainability": {
            "skill_rationale": "2 of 3 required skills matched.",
            "experience_rationale": "6 years vs 5 required.",
            "overall_rationale": "Solid candidate with a minor skills gap.",
        },
        "recommendation_rationale": "Score 75/100 — Consider.",
        "work_experience": [
            {"title": "Senior Dev", "company": "TechCorp",
             "start_date": "Jan 2020", "end_date": "present"},
        ],
        "contact_info": {"name": "John Doe", "email": "john@example.com"},
        "analysis_quality": "high",
        "narrative_pending": False,
        "pipeline_errors": [],
    }
    with patch("app.backend.routes.analyze.run_hybrid_pipeline", new_callable=AsyncMock) as mock:
        mock.return_value = pipeline_result
        yield mock


@pytest.fixture
def mock_agent_pipeline():
    """Legacy fixture — now wraps mock_hybrid_pipeline for backward compatibility."""
    pipeline_result = {
        "fit_score": 75,
        "job_role": "Senior Python Engineer",
        "strengths": ["Strong Python skills"], "weaknesses": ["Limited cloud experience"],
        "education_analysis": "Solid CS background.", "risk_signals": [],
        "final_recommendation": "Consider", "employment_gaps": [],
        "score_breakdown": {"skill_match": {"score": 80, "confidence_weighted": False, "avg_confidence": 1.0}, "experience_match": 70, "stability": 90, "education": 70},
        "matched_skills": ["python"], "missing_skills": ["kubernetes"],
        "adjacent_skills": [], "risk_level": "Low",
        "interview_questions": {"technical_questions": [], "behavioral_questions": [], "culture_fit_questions": []},
        "required_skills_count": 3,
        "jd_analysis": {}, "candidate_profile": {}, "skill_analysis": {},
        "edu_timeline_analysis": {}, "explainability": {}, "recommendation_rationale": "",
        "work_experience": [], "contact_info": {},
        "analysis_quality": "high", "narrative_pending": False, "pipeline_errors": [],
    }
    with patch("app.backend.routes.analyze.run_hybrid_pipeline", new_callable=AsyncMock) as mock:
        mock.return_value = pipeline_result
        yield mock


# ─── Sample data ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_resume_text():
    return """
John Doe
Software Engineer
john.doe@email.com | +1-555-123-4567 | linkedin.com/in/johndoe

SUMMARY
Experienced software engineer with 8 years in full-stack development.

SKILLS
Python, JavaScript, React, Node.js, PostgreSQL, AWS, Docker, Kubernetes

WORK EXPERIENCE

Senior Software Engineer | TechCorp Inc.
January 2020 - Present
- Led development of microservices architecture
- Managed team of 5 engineers

Software Engineer | StartupXYZ
June 2017 - December 2019
- Built React frontend and Node.js backend
- Implemented CI/CD pipelines

Junior Developer | SmallCo
March 2015 - May 2017
- Developed internal tools using Python
- Maintained legacy systems

EDUCATION
Bachelor of Science in Computer Science
University of Technology, 2015
"""


@pytest.fixture
def sample_job_description():
    return """
Senior Software Engineer

We are looking for an experienced software engineer with:
- 5+ years of Python experience
- Strong React and JavaScript skills
- Experience with cloud platforms (AWS/GCP)
- Knowledge of Docker and Kubernetes
- PostgreSQL database experience

The ideal candidate will have leadership experience.
"""


@pytest.fixture
def sample_resume_bytes(sample_resume_text):
    return sample_resume_text.encode("utf-8")


@pytest.fixture
def sample_mp4_bytes():
    """Minimal fake MP4 bytes (just enough to pass file extension checks)."""
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100


@pytest.fixture
def mock_ollama_transcript():
    """Mock transcript analysis LLM to return a valid analysis JSON."""
    with patch(
        "app.backend.services.app_llm_client.generate_app_json",
        new_callable=AsyncMock,
        return_value={
            "fit_score": 78,
            "technical_depth": 72,
            "communication_quality": 80,
            "jd_alignment": [
                {"requirement": "Python", "demonstrated": True,  "evidence": "5 years Python"},
                {"requirement": "AWS",    "demonstrated": False, "evidence": None},
            ],
            "strengths": ["Strong Python skills", "Good communication"],
            "areas_for_improvement": ["Cloud experience limited"],
            "bias_note": "Evaluation based solely on demonstrated skills and knowledge in the transcript.",
            "recommendation": "proceed",
        },
    ) as mock:
        yield mock


@pytest.fixture
def sample_vtt_transcript():
    return """\
WEBVTT

1
00:00:01.000 --> 00:00:06.000
Interviewer: Tell me about your Python experience.

2
00:00:07.000 --> 00:00:15.000
Jane: I have five years of Python building REST APIs with FastAPI.
"""


@pytest.fixture
def sample_srt_transcript():
    return """\
1
00:00:01,000 --> 00:00:05,000
Can you describe your AWS experience?

2
00:00:06,000 --> 00:00:14,000
I have three years of hands-on AWS experience with EC2, S3, and Lambda.
"""


@pytest.fixture
def sample_plain_transcript():
    return (
        "I have been working with Python for five years, building REST APIs with FastAPI. "
        "I led a team migrating a monolith to microservices using Docker and Kubernetes."
    )


@pytest.fixture
def mock_ollama_email():
    """Mock email generation LLM to return a valid JSON result."""
    with patch(
        "app.backend.services.app_llm_client.generate_app_json",
        new_callable=AsyncMock,
        return_value={
            "subject": "Your Application — Senior Software Engineer",
            "body": "Dear John,\n\nCongratulations! We'd like to move forward.\n\nBest regards",
        },
    ) as mock:
        yield mock


# ─── Subscription Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def seed_subscription_plans(db):
    """Seed subscription plans for testing."""
    from app.backend.models.db_models import SubscriptionPlan
    
    starter_limits = {
        "analyses_per_month": 5, "batch_size": 3, "team_members": 1, "storage_gb": 1,
        "api_access": False, "custom_weights": False,
        "requisitions": False, "pipeline": False, "compare": False, "analytics": False,
        "ai_interviews": False, "video_analysis": False, "export_excel": False,
        "transcript_analysis": False, "email_generation": False, "white_label": False, "hm_workflow": False,
    }
    growth_limits = {
        "analyses_per_month": 100, "batch_size": 20, "team_members": 5, "storage_gb": 10,
        "api_access": False, "custom_weights": False,
        "requisitions": True, "pipeline": True, "compare": True, "analytics": False,
        "ai_interviews": False, "video_analysis": False, "export_excel": True,
        "transcript_analysis": False, "email_generation": True, "white_label": False, "hm_workflow": True,
    }
    agency_limits = {
        "analyses_per_month": 1000, "batch_size": 50, "team_members": 15, "storage_gb": 25,
        "api_access": True, "custom_weights": False,
        "requisitions": True, "pipeline": True, "compare": True, "analytics": True,
        "ai_interviews": False, "video_analysis": False, "export_excel": True,
        "transcript_analysis": False, "email_generation": True, "white_label": False, "hm_workflow": True,
    }
    plans = [
        {
            "name": "starter",
            "display_name": "Starter",
            "description": "Starter tier",
            "limits": json.dumps(starter_limits),
            "price_monthly": 0,
            "price_yearly": 0,
            "currency": "USD",
            "features": json.dumps(["5 analyses", "1 team member"]),
            "is_active": True,
            "sort_order": 1,
        },
        {
            "name": "growth",
            "display_name": "Growth",
            "description": "Growth tier",
            "limits": json.dumps(growth_limits),
            "price_monthly": 4900,
            "price_yearly": 47000,
            "currency": "USD",
            "features": json.dumps(["100 analyses", "5 team members", "Requisitions & pipeline"]),
            "is_active": True,
            "sort_order": 2,
        },
        {
            "name": "agency",
            "display_name": "Agency",
            "description": "Agency tier",
            "limits": json.dumps(agency_limits),
            "price_monthly": 12900,
            "price_yearly": 131000,
            "currency": "USD",
            "features": json.dumps(["1,000 analyses", "API access", "Analytics"]),
            "is_active": True,
            "sort_order": 3,
        },
        {
            "name": "enterprise",
            "display_name": "Enterprise",
            "description": "Enterprise tier",
            "limits": json.dumps({
                "analyses_per_month": -1, "batch_size": 100, "team_members": 25, "storage_gb": 100,
                "api_access": True, "custom_weights": True, "dedicated_support": True,
                "requisitions": True, "pipeline": True, "compare": True, "analytics": True,
                "ai_interviews": True, "video_analysis": True, "export_excel": True,
                "transcript_analysis": True, "email_generation": True, "white_label": True, "hm_workflow": True,
                "sso": True, "custom_integrations": True,
            }),
            "price_monthly": 19900,
            "price_yearly": 191000,
            "currency": "USD",
            "features": json.dumps(["Unlimited analyses", "25 team members", "Dedicated support"]),
            "is_active": True,
            "sort_order": 4,
        },
    ]
    
    for plan_data in plans:
        plan = SubscriptionPlan(**plan_data)
        db.add(plan)
    db.commit()
    
    return plans


@pytest.fixture
def auth_client_with_free_plan(client, db, seed_subscription_plans):
    """Create an authenticated client with a tenant on the Free plan."""
    from app.backend.models.db_models import Tenant, SubscriptionPlan
    
    register_payload = {
        "company_name": "FreeCorp",
        "email": "free@freecorp.com",
        "password": "TestPass123!",
        "full_name": "Free User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    # Mark user as verified for testing
    _verify_user_via_api("free@freecorp.com")

    # Get the tenant and set it to starter plan
    starter_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("starter", "free"))).first()
    starter_plan_id = starter_plan.id
    tenant = db.query(Tenant).filter(Tenant.slug == "freecorp").first()
    tenant.plan_id = starter_plan_id
    db.commit()
    from app.backend.tests.test_helpers import allow_ad_hoc_screening
    allow_ad_hoc_screening(db, slug="freecorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "free@freecorp.com",
        "password": "TestPass123!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def auth_client_with_pro_plan(client, db, seed_subscription_plans):
    """Create an authenticated client with a tenant on the Pro plan."""
    from app.backend.models.db_models import Tenant, SubscriptionPlan
    from app.backend.tests.test_helpers import allow_ad_hoc_screening
    
    register_payload = {
        "company_name": "ProCorp",
        "email": "pro@procorp.com",
        "password": "TestPass123!",
        "full_name": "Pro User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    # Mark user as verified for testing
    _verify_user_via_api("pro@procorp.com")

    # Get the tenant and set it to pro plan
    growth_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("growth", "pro"))).first()
    growth_plan_id = growth_plan.id
    tenant = db.query(Tenant).filter(Tenant.slug == "procorp").first()
    tenant.plan_id = growth_plan_id
    tenant.subscription_status = "active"
    tenant.analyses_count_this_month = 0
    db.commit()
    allow_ad_hoc_screening(db, slug="procorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "pro@procorp.com",
        "password": "TestPass123!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def auth_client_with_agency_plan(client, db, seed_subscription_plans):
    """Create an authenticated client with a tenant on the Agency plan."""
    from app.backend.models.db_models import Tenant, SubscriptionPlan
    from app.backend.tests.test_helpers import allow_ad_hoc_screening

    register_payload = {
        "company_name": "AgencyCorp",
        "email": "agency@agencycorp.com",
        "password": "TestPass123!",
        "full_name": "Agency User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    _verify_user_via_api("agency@agencycorp.com")

    agency_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "agency").first()
    tenant = db.query(Tenant).filter(Tenant.slug == "agencycorp").first()
    tenant.plan_id = agency_plan.id
    tenant.subscription_status = "active"
    db.commit()
    allow_ad_hoc_screening(db, slug="agencycorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "agency@agencycorp.com",
        "password": "TestPass123!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def auth_client_with_enterprise_plan(client, db, seed_subscription_plans):
    """Create an authenticated client with a tenant on the Enterprise plan."""
    from app.backend.tests.test_helpers import assign_tenant_plan, allow_ad_hoc_screening

    register_payload = {
        "company_name": "EnterpriseCorp",
        "email": "enterprise@enterprisecorp.com",
        "password": "TestPass123!",
        "full_name": "Enterprise User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    _verify_user_via_api("enterprise@enterprisecorp.com")
    assign_tenant_plan(db, "enterprise", slug="enterprisecorp")
    from app.backend.models.db_models import Tenant
    tenant = db.query(Tenant).filter(Tenant.slug == "enterprisecorp").first()
    tenant.analyses_count_this_month = 0
    db.commit()
    allow_ad_hoc_screening(db, slug="enterprisecorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "enterprise@enterprisecorp.com",
        "password": "TestPass123!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def auth_client_at_usage_limit(client, db, seed_subscription_plans):
    """Create an authenticated client at their usage limit (Free plan = 5 analyses)."""
    from app.backend.models.db_models import Tenant, SubscriptionPlan
    from app.backend.tests.test_helpers import allow_ad_hoc_screening
    
    register_payload = {
        "company_name": "LimitedCorp",
        "email": "limited@limitedcorp.com",
        "password": "TestPass123!",
        "full_name": "Limited User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    # Mark user as verified for testing
    _verify_user_via_api("limited@limitedcorp.com")

    # Get the tenant and set it to starter plan at limit
    starter_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name.in_(("starter", "free"))).first()
    starter_plan_id = starter_plan.id
    tenant = db.query(Tenant).filter(Tenant.slug == "limitedcorp").first()
    tenant.plan_id = starter_plan_id
    tenant.analyses_count_this_month = 5  # At limit
    tenant.subscription_status = "active"
    db.commit()
    allow_ad_hoc_screening(db, slug="limitedcorp")

    login_resp = client.post("/api/auth/login", json={
        "email": "limited@limitedcorp.com",
        "password": "TestPass123!",
    })
    token = access_token_from(login_resp)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ─── AI Recruiter Fixtures ────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db_session(db):
    """Alias for the `db` fixture used by recruiter tests."""
    yield db


@pytest.fixture(scope="function")
def sample_user(client, db, seed_subscription_plans):
    """Create a test user + tenant for recruiter tests (role: recruiter)."""
    from app.backend.models.db_models import Tenant, SubscriptionPlan, User

    register_payload = {
        "company_name": "RecruiterTests",
        "email": "sampleuser@recruitertests.com",
        "password": "TestPass123!",
        "full_name": "Sample User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Register failed: {reg_resp.text}"

    _verify_user_via_api("sampleuser@recruitertests.com")

    enterprise_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").first()
    tenant = db.query(Tenant).filter(Tenant.slug == "recruitertests").first()
    tenant.plan_id = enterprise_plan.id
    tenant.subscription_status = "active"
    db.commit()

    user = db.query(User).filter(User.email == "sampleuser@recruitertests.com").first()
    assert user is not None
    # Registration creates the first user as admin; downgrade to recruiter so
    # admin-only endpoints can be tested separately.
    user.role = "recruiter"
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(scope="function")
def sample_candidate(db, sample_user):
    """Create a test candidate for recruiter tests."""
    from app.backend.models.db_models import Candidate

    candidate = Candidate(
        tenant_id=sample_user.tenant_id,
        name="Sample Candidate",
        email="candidate@example.com",
        phone="+14155551234",
        parsed_skills='["python", "java"]',
        parsed_work_exp='[{"title":"Software Engineer","company":"OldCo","start_date":"2020-01","end_date":"present"}]',
        gap_analysis_json='{}',
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


@pytest.fixture(scope="function")
def sample_jd(db, sample_user):
    """Create a test job description for recruiter tests."""
    from app.backend.models.db_models import RoleTemplate

    jd = RoleTemplate(
        tenant_id=sample_user.tenant_id,
        name="Sample Role",
        jd_text="We need Python, Kubernetes, and AWS experience.",
        required_skills_override='["python", "kubernetes", "aws"]',
    )
    db.add(jd)
    db.commit()
    db.refresh(jd)
    return jd


@pytest.fixture(scope="function")
def sample_screening_result(db, sample_user, sample_candidate, sample_jd):
    """Create a test screening result for recruiter tests."""
    from app.backend.models.db_models import ScreeningResult

    screening = ScreeningResult(
        tenant_id=sample_user.tenant_id,
        candidate_id=sample_candidate.id,
        role_template_id=sample_jd.id,
        resume_text="Sample resume text.",
        jd_text=sample_jd.jd_text,
        parsed_data='{}',
        analysis_result=json.dumps({
            "fit_score": 65,
            "risk_signals": [],
            "matched_skills": ["python"],
            "gap_skills": ["kubernetes", "aws"],
        }),
        deterministic_score=65,
        core_skill_score=70.0,
        status="shortlisted",
    )
    db.add(screening)
    db.commit()
    db.refresh(screening)
    return screening


@pytest.fixture(scope="function")
def auth_headers(client, sample_user):
    """Return authorization headers for the sample user."""
    login_resp = client.post("/api/auth/login", json={
        "email": sample_user.email,
        "password": "TestPass123!",
    })
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = access_token_from(login_resp)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def admin_auth_headers(client, db, sample_user):
    """Return authorization headers for an admin user in the sample tenant."""
    from app.backend.models.db_models import User

    register_payload = {
        "company_name": "RecruiterAdminTests",
        "email": "adminuser@recruitertests.com",
        "password": "AdminPass123!",
        "full_name": "Admin User",
    }
    reg_resp = client.post("/api/auth/register", json=register_payload)
    assert reg_resp.status_code in (200, 201), f"Admin register failed: {reg_resp.text}"

    _verify_user_via_api("adminuser@recruitertests.com")

    admin_user = db.query(User).filter(User.email == "adminuser@recruitertests.com").first()
    assert admin_user is not None
    admin_user.tenant_id = sample_user.tenant_id
    admin_user.role = "admin"
    db.commit()

    login_resp = client.post("/api/auth/login", json={
        "email": "adminuser@recruitertests.com",
        "password": "AdminPass123!",
    })
    assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
    token = access_token_from(login_resp)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def recruiter_session(db_session, sample_user, sample_candidate, sample_jd):
    """Create a test recruiter interview session."""
    from app.backend.models.db_models import RecruiterInterviewSession

    session = RecruiterInterviewSession(
        id=str(uuid.uuid4()),
        tenant_id=sample_user.tenant_id,
        candidate_id=sample_candidate.id,
        jd_id=sample_jd.id,
        trigger_type="manual",
        status="pending_strategy",
        created_by=sample_user.id,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture(scope="function")
def recruiter_session_with_questions(db_session, recruiter_session):
    """Create a recruiter session with a sample question."""
    from app.backend.models.db_models import RecruiterInterviewQuestion

    question = RecruiterInterviewQuestion(
        id=str(uuid.uuid4()),
        session_id=recruiter_session.id,
        sequence_number=1,
        category="technical",
        question_text="Describe a recent Python project.",
        question_context="Assess depth of Python experience.",
    )
    db_session.add(question)
    db_session.commit()
    return recruiter_session


@pytest.fixture(scope="function")
def recruiter_session_with_scorecard(db_session, recruiter_session):
    """Create a session with a completed scorecard."""
    from app.backend.models.db_models import RecruiterScorecard

    scorecard = RecruiterScorecard(
        id=str(uuid.uuid4()),
        session_id=recruiter_session.id,
        tenant_id=recruiter_session.tenant_id,
        candidate_id=recruiter_session.candidate_id,
        technical_score=75,
        behavioral_score=80,
        communication_score=85,
        cultural_fit_score=70,
        motivation_score=90,
        overall_score=80,
        confidence_level="high",
        recommendation="hire",
        recommendation_reasoning="Strong candidate",
        executive_summary="Recommended for hire.",
        original_fit_score=65,
        adjusted_fit_score=78,
        adjustment_reasoning="Interview validated technical skills above resume indication.",
    )
    recruiter_session.status = "completed"
    db_session.add(scorecard)
    db_session.commit()
    db_session.refresh(recruiter_session)
    return recruiter_session


@pytest.fixture(scope="function")
def completed_recruiter_session(recruiter_session, db_session):
    """Create a completed recruiter interview session."""
    recruiter_session.status = "completed"
    db_session.commit()
    db_session.refresh(recruiter_session)
    return recruiter_session


@pytest.fixture(scope="function")
def other_tenant_session(db_session):
    """Create a recruiter session under a different tenant."""
    from app.backend.models.db_models import (
        Candidate,
        RecruiterInterviewSession,
        RoleTemplate,
        Tenant,
    )

    tenant = Tenant(name="Other Tenant", slug="othertenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Other Candidate",
        email="othercandidate@example.com",
        phone="+14155559999",
    )
    db_session.add(candidate)
    db_session.commit()
    db_session.refresh(candidate)

    jd = RoleTemplate(
        tenant_id=tenant.id,
        name="Other Role",
        jd_text="Other JD",
    )
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)

    session = RecruiterInterviewSession(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        jd_id=jd.id,
        trigger_type="manual",
        status="pending_strategy",
    )
    db_session.add(session)
    db_session.commit()
    return session
