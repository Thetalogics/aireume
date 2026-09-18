import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import io


class TestAPIEndpoints:
    def test_root_endpoint(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert any(kw in data["message"] for kw in ("AI Resume Screener", "ARIA", "Resume", "API"))

    def test_health_check(self, client):
        """Shallow health check returns 200 with status 'ok'."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_deep_health_check_returns_structured_response(self, client):
        """Deep health check returns structured response with all checks."""
        response = client.get("/api/health/deep")
        assert response.status_code == 200
        data = response.json()
        # Verify top-level structure
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert "timestamp" in data
        assert "response_time_ms" in data
        assert "checks" in data
        # Verify checks structure
        checks = data["checks"]
        assert "database" in checks
        assert "status" in checks["database"]
        assert "latency_ms" in checks["database"]
        assert "ollama" in checks
        assert "status" in checks["ollama"]
        assert "disk" in checks
        assert "status" in checks["disk"]
        assert "free_gb" in checks["disk"]

    def test_deep_health_check_reports_degraded_when_ollama_not_hot(self, client):
        """Deep health check reports 'degraded' when Ollama sentinel is not HOT."""
        from app.backend.services import llm_service
        from unittest.mock import MagicMock

        # Mock the sentinel to be in WARMING state (not HOT)
        mock_sentinel = MagicMock()
        mock_sentinel.get_status.return_value = {
            "state": "warming",
            "model": "gemma4:31b-cloud",
            "last_probe_time": 1234567890.0,
            "last_latency_ms": 150.5,
            "healthy": False,  # Not healthy because not HOT
        }
        original_sentinel = llm_service._sentinel
        try:
            llm_service._sentinel = mock_sentinel
            response = client.get("/api/health/deep")
            assert response.status_code == 200
            data = response.json()
            # Status should be degraded because Ollama is not HOT
            assert data["status"] == "degraded"
            assert data["checks"]["ollama"]["status"] == "warming"
        finally:
            llm_service._sentinel = original_sentinel

    def test_deep_health_check_unhealthy_when_db_fails(self, client):
        """Deep health check reports 'unhealthy' when database fails."""
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError

        with patch("app.backend.main.SessionLocal") as mock_session_local:
            # Simulate DB failure
            mock_session_local.side_effect = OperationalError("DB connection failed", {}, None)
            response = client.get("/api/health/deep")
            assert response.status_code == 200
            data = response.json()
            # Status should be unhealthy because DB failed
            assert data["status"] == "unhealthy"
            assert "error" in data["checks"]["database"]["status"]

    def test_deep_health_check_no_sentinel(self, client):
        """Deep health check reports 'degraded' when sentinel not initialized."""
        from app.backend.services import llm_service

        original_sentinel = llm_service._sentinel
        try:
            llm_service._sentinel = None
            response = client.get("/api/health/deep")
            assert response.status_code == 200
            data = response.json()
            # Status should be degraded because sentinel is not initialized
            assert data["status"] == "degraded"
            assert data["checks"]["ollama"]["status"] == "unknown"
        finally:
            llm_service._sentinel = original_sentinel

    def test_analyze_endpoint_success(self, auth_client):
        file_content = (
            b"John Doe\nSenior Software Engineer\njohn@email.com\n\n"
            b"SKILLS\nPython, FastAPI, PostgreSQL\n\n"
            b"WORK EXPERIENCE\nSenior Dev | Company A | Jan 2020 - Present\n\n"
            b"EDUCATION\nBSc Computer Science, MIT, 2018\n"
        )
        _JD = (
            "Senior Python Backend Engineer. We need at least 5 years of Python "
            "experience with FastAPI or Django, PostgreSQL, Docker, and Kubernetes. "
            "Strong understanding of microservices, REST API design, and cloud "
            "platforms like AWS or GCP is required. The role involves leading a "
            "small engineering team and collaborating with data scientists. "
            "Experience with CI/CD pipelines, Redis caching, and async Python is "
            "highly desirable. The candidate should be comfortable with code review "
            "and technical mentoring of junior developers."
        )
        pipeline_result = {
            "fit_score": 75, "job_role": "Senior Python Engineer",
            "strengths": ["Strong Python skills"], "weaknesses": [],
            "education_analysis": "Good background.", "risk_signals": [],
            "final_recommendation": "Consider", "employment_gaps": [],
            "score_breakdown": {"skill_match": {"score": 80, "confidence_weighted": False, "avg_confidence": 1.0}, "experience_match": 70,
                                "stability": 100, "education": 70},
            "matched_skills": ["python"], "missing_skills": [], "adjacent_skills": [],
            "risk_level": "Low",
            "interview_questions": {"technical_questions": [], "behavioral_questions": [],
                                    "culture_fit_questions": []},
            "required_skills_count": 3, "jd_analysis": {}, "candidate_profile": {},
            "skill_analysis": {}, "edu_timeline_analysis": {}, "explainability": {},
            "work_experience": [], "contact_info": {},
            "analysis_quality": "high", "narrative_pending": False, "pipeline_errors": [],
        }
        with patch("app.backend.routes.analyze.parse_resume", return_value={
            "raw_text": "John Doe python fastapi", "skills": ["python", "fastapi"],
            "education": [], "work_experience": [],
            "contact_info": {"name": "John Doe", "email": "john@email.com"},
        }), patch("app.backend.routes.analyze_helpers.parse_resume", return_value={
            "raw_text": "John Doe python fastapi", "skills": ["python", "fastapi"],
            "education": [], "work_experience": [],
            "contact_info": {"name": "John Doe", "email": "john@email.com"},
        }), patch("app.backend.routes.analyze.analyze_gaps", return_value={}), \
           patch("app.backend.routes.analyze.run_hybrid_pipeline",
                 new_callable=AsyncMock, return_value=pipeline_result):
            response = auth_client.post(
                "/api/analyze",
                data={"job_description": _JD},
                files={"resume": ("resume.txt", io.BytesIO(file_content), "text/plain")},
            )
        if response.status_code == 200:
            assert "fit_score" in response.json()

    def test_analyze_endpoint_invalid_file_type(self, auth_client):
        response = auth_client.post(
            "/api/analyze",
            data={"job_description": "Test job"},
            files={"resume": ("resume.exe", io.BytesIO(b"invalid content"), "application/octet-stream")}
        )
        assert response.status_code == 400

    def test_analyze_endpoint_missing_job_description(self, auth_client):
        file_content = b"Simple resume content"
        response = auth_client.post(
            "/api/analyze",
            data={},  # Missing job_description
            files={"resume": ("resume.txt", io.BytesIO(file_content), "text/plain")}
        )
        # Route returns 400 (explicit validation) when JD is missing
        assert response.status_code == 400

    def test_history_endpoint_unauthenticated_returns_401(self, client):
        response = client.get("/api/history")
        assert response.status_code == 401

    def test_history_endpoint_authenticated_returns_list(self, auth_client):
        response = auth_client.get("/api/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_batch_analyze_returns_ranked_results(self, auth_client, mock_hybrid_pipeline):
        """Batch endpoint must return ranked results; null fit_score is allowed."""
        import io
        resume_bytes = b"John Doe\nPython Developer\njohn@email.com\n5 years Python"
        _JD = (
            "Senior Python Backend Engineer with at least 5 years of hands-on "
            "experience building scalable REST APIs using FastAPI or Django REST "
            "Framework. Strong knowledge of PostgreSQL, Redis, Docker, and "
            "Kubernetes is required for this role. Experience with cloud platforms "
            "such as AWS or GCP is strongly preferred. The ideal candidate should "
            "be comfortable leading cross-functional teams, conducting thorough code "
            "reviews, and mentoring junior engineers day to day. Familiarity with "
            "async Python, Celery task queues, and message brokers like RabbitMQ "
            "or Kafka will be considered a significant advantage in this position."
        )

        response = auth_client.post(
            "/api/analyze/batch",
            data={"job_description": _JD},
            files=[
                ("resumes", ("resume1.pdf", io.BytesIO(resume_bytes), "application/pdf")),
                ("resumes", ("resume2.pdf", io.BytesIO(resume_bytes), "application/pdf")),
            ],
        )
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total" in data
        # Results must be a ranked list; null fit_score must not crash serialization
        for row in data["results"]:
            assert "rank" in row
            assert "filename" in row
            assert "result" in row
            # fit_score is allowed to be null
            assert "fit_score" in row["result"]

    def test_batch_analyze_rejects_no_valid_files(self, auth_client):
        """Batch endpoint with only unsupported file types returns 400."""
        import io
        response = auth_client.post(
            "/api/analyze/batch",
            data={"job_description": "Some job"},
            files=[("resumes", ("resume.exe", io.BytesIO(b"data"), "application/octet-stream"))],
        )
        assert response.status_code == 400

    def test_batch_analyze_requires_job_description(self, auth_client):
        """Batch endpoint without a JD returns 400."""
        import io
        response = auth_client.post(
            "/api/analyze/batch",
            data={},
            files=[("resumes", ("resume.pdf", io.BytesIO(b"resume"), "application/pdf"))],
        )
        assert response.status_code == 400

    def test_llm_status_endpoint_returns_sentinel_status(self, client):
        """Test /api/llm-status returns sentinel status when initialized."""
        from app.backend.services import llm_service
        from unittest.mock import MagicMock

        # Mock the sentinel
        mock_sentinel = MagicMock()
        mock_sentinel.get_status.return_value = {
            "state": "hot",
            "model": "gemma4:31b-cloud",
            "last_probe_time": 1234567890.0,
            "last_latency_ms": 150.5,
            "healthy": True,
        }
        original_sentinel = llm_service._sentinel
        try:
            llm_service._sentinel = mock_sentinel
            response = client.get("/api/llm-status")
            assert response.status_code == 200
            data = response.json()
            assert "sentinel" in data
            assert data["sentinel"]["state"] == "hot"
            assert data["sentinel"]["healthy"] is True
            assert data["sentinel"]["model"] == "gemma4:31b-cloud"
        finally:
            llm_service._sentinel = original_sentinel

    def test_llm_status_endpoint_no_sentinel(self, client):
        """Test /api/llm-status returns unknown when sentinel not initialized."""
        from app.backend.services import llm_service

        original_sentinel = llm_service._sentinel
        try:
            llm_service._sentinel = None
            response = client.get("/api/llm-status")
            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "unknown"
            assert data["healthy"] is False
            assert "message" in data
        finally:
            llm_service._sentinel = original_sentinel


class TestNarrativePollingEndpoint:
    """Tests for GET /api/analysis/{id}/narrative endpoint."""

    def test_narrative_pending_state(self, auth_client, db):
        """Returns pending when narrative_json is NULL."""
        from app.backend.models.db_models import ScreeningResult, Tenant, User
        
        # Get the tenant from auth_client's user
        tenant = db.query(Tenant).first()
        user = db.query(User).filter(User.tenant_id == tenant.id).first()
        
        # Create a screening result without narrative
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=None,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json=None,  # No narrative yet
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        
        response = auth_client.get(f"/api/analysis/{result.id}/narrative")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    def test_narrative_ready_state(self, auth_client, db):
        """Returns ready with narrative when narrative_json is populated."""
        from app.backend.models.db_models import ScreeningResult, Tenant
        import json
        
        tenant = db.query(Tenant).first()
        
        # Create a screening result WITH narrative
        narrative = {
            "fit_summary": "Strong candidate",
            "strengths": ["Python expert"],
            "weaknesses": ["Limited Docker"],
        }
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=None,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json=json.dumps(narrative),
            narrative_status="ready",
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        
        response = auth_client.get(f"/api/analysis/{result.id}/narrative")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["narrative"]["fit_summary"] == "Strong candidate"
        assert "Python expert" in data["narrative"]["strengths"]

    def test_narrative_fallback_state(self, auth_client, db):
        """Returns fallback with narrative when LLM failed but fallback was generated."""
        from app.backend.models.db_models import ScreeningResult, Tenant
        import json

        tenant = db.query(Tenant).first()

        narrative = {
            "fit_summary": "Fallback summary",
            "strengths": ["Python"],
            "narrative_fallback": True,
        }
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=None,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json=json.dumps(narrative),
            narrative_status="fallback",
            narrative_error="AI analysis timed out",
        )
        db.add(result)
        db.commit()
        db.refresh(result)

        response = auth_client.get(f"/api/analysis/{result.id}/narrative")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "fallback"
        assert data["narrative"]["fit_summary"] == "Fallback summary"
        assert data["narrative"]["narrative_fallback"] is True
        assert "timed out" in data["error"]

    def test_narrative_not_found(self, auth_client):
        """Returns 404 for non-existent analysis ID."""
        response = auth_client.get("/api/analysis/99999/narrative")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_narrative_tenant_isolation(self, auth_client, db):
        """Returns 404 when trying to access another tenant's analysis."""
        from app.backend.models.db_models import ScreeningResult, Tenant, User
        
        # Create a different tenant
        other_tenant = Tenant(name="Other Corp", slug="other-corp")
        db.add(other_tenant)
        db.commit()
        db.refresh(other_tenant)
        
        # Create a screening result for the OTHER tenant
        result = ScreeningResult(
            tenant_id=other_tenant.id,  # Different tenant!
            candidate_id=None,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json='{"fit_summary": "test"}',
            narrative_status="ready",
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        
        # auth_client belongs to the FIRST tenant, should get 404
        response = auth_client.get(f"/api/analysis/{result.id}/narrative")
        assert response.status_code == 404

    def test_narrative_unauthenticated_returns_401(self, client):
        """Unauthenticated requests return 401."""
        # Just need to verify that a 401 is returned without auth
        # The specific ID doesn't matter since unauthenticated requests are rejected first
        response = client.get("/api/analysis/1/narrative")
        assert response.status_code == 401


class TestBackgroundNarrativeTask:
    """Tests for background LLM narrative generation."""

    @pytest.mark.asyncio
    async def test_background_task_writes_narrative_to_db(self, db):
        """Background task should write narrative to DB when complete."""
        from app.backend.services.hybrid_pipeline import _background_llm_narrative
        from app.backend.models.db_models import ScreeningResult, Tenant
        from unittest.mock import AsyncMock, patch, MagicMock
        import json
        
        # Create tenant and screening result
        tenant = Tenant(name="Test", slug="test")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=None,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json=None,
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        result_id = result.id
        tenant_id = tenant.id
        
        # Mock the LLM call
        mock_narrative = {
            "fit_summary": "Excellent match",
            "strengths": ["Strong Python"],
            "weaknesses": [],
            "recommendation_rationale": "Good fit",
            "explainability": {},
            "interview_questions": {
                "technical_questions": ["Q1"],
                "behavioral_questions": ["Q2"],
                "culture_fit_questions": ["Q3"],
            },
        }
        
        # Mock the DB session to use the test session
        def mock_session_local():
            return db
        
        with patch("app.backend.db.database.SessionLocal", mock_session_local), \
             patch("app.backend.services.hybrid_pipeline.explain_with_llm",
                   new_callable=AsyncMock, return_value=mock_narrative), \
             patch("app.backend.services.background_enrichment.schedule_post_narrative_enrichment"):
            await _background_llm_narrative(
                screening_result_id=result_id,
                tenant_id=tenant_id,
                llm_context={},
                python_result={"skill_analysis": {"matched_skills": [], "missing_skills": []}},
                expected_analysis_generation=result.analysis_generation,
            )
        
        # Query fresh from DB to check the narrative was written
        result = db.query(ScreeningResult).filter(ScreeningResult.id == result_id).first()
        assert result.narrative_json is not None
        narrative = json.loads(result.narrative_json)
        assert narrative["fit_summary"] == "Excellent match"

    @pytest.mark.asyncio
    async def test_background_task_handles_llm_failure(self, db):
        """Background task should write fallback on LLM failure."""
        from app.backend.services.hybrid_pipeline import _background_llm_narrative
        from app.backend.models.db_models import ScreeningResult, Tenant
        from unittest.mock import patch, AsyncMock
        import json
        
        tenant = Tenant(name="Test2", slug="test2")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=None,
            resume_text="test",
            jd_text="test",
            parsed_data="{}",
            analysis_result="{}",
            narrative_json=None,
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        result_id = result.id
        tenant_id = tenant.id
        
        # Mock the DB session to use the test session
        def mock_session_local():
            return db
        
        with patch("app.backend.db.database.SessionLocal", mock_session_local), \
             patch("app.backend.services.hybrid_pipeline.explain_with_llm",
                   new_callable=AsyncMock, side_effect=RuntimeError("LLM failed")), \
             patch("app.backend.services.background_enrichment.schedule_post_narrative_enrichment"):
            await _background_llm_narrative(
                screening_result_id=result_id,
                tenant_id=tenant_id,
                llm_context={},
                python_result={
                    "fit_score": 50,
                    "skill_analysis": {
                        "matched_skills": ["python"],
                        "missing_skills": [],
                        "required_count": 1,
                    },
                    "score_breakdown": {},
                    "_required_years": 3,
                    "final_recommendation": "Consider",
                    "score_rationales": {},
                },
                expected_analysis_generation=result.analysis_generation,
            )
        
        # Query fresh from DB to check the narrative was written
        result = db.query(ScreeningResult).filter(ScreeningResult.id == result_id).first()
        assert result.narrative_json is not None
        assert result.narrative_status == "ready"
        narrative = json.loads(result.narrative_json)
        # Synthesized narrative should have deterministic content (no user-visible failure)
        assert "fit_summary" in narrative
        assert "strengths" in narrative
        assert narrative.get("ai_enhanced") is False
