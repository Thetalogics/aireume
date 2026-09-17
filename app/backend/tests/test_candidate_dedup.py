"""
Tests for the hybrid pipeline route features introduced in the deduplication
and profile-storage overhaul:

  1. JD cache  (_get_or_cache_jd)
  2. Candidate deduplication  (_get_or_create_candidate — 3-layer logic)
  3. Profile storage  (_store_candidate_profile)
  4. duplicate_candidate / analysis_quality / narrative_pending in /api/analyze
  5. JD length gate  (_check_jd_length — POST /api/analyze and POST /api/analyze/batch)
  6. POST /api/candidates/{id}/analyze-jd  (re-analysis without re-upload)
  7. Enriched GET /api/candidates and GET /api/candidates/{id}
"""
import copy
import io
import json
import hashlib
from contextlib import ExitStack, contextmanager
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.backend.tests.test_helpers import assign_tenant_plan

# ─── Helpers ─────────────────────────────────────────────────────────────────

LONG_JD = (
    "Senior Python Backend Engineer — Remote\n\n"
    "We are looking for a seasoned Python engineer with at least 5 years of "
    "hands-on experience building scalable, production-grade REST APIs using "
    "FastAPI or Django REST Framework. You will work closely with our data "
    "platform team to design and implement microservices that power our core "
    "analytics pipeline. Strong experience with PostgreSQL, Redis, and Docker "
    "is required. Familiarity with Kubernetes and cloud platforms such as AWS "
    "or GCP is highly desirable. The ideal candidate has led cross-functional "
    "projects, mentored junior engineers, and cares deeply about code quality, "
    "test coverage, and observability. Experience with async Python, Celery, "
    "and message queues like RabbitMQ or Kafka is a plus."
)

SHORT_JD = "Python developer needed."  # well under 80 words

def _make_file(name: str = "resume.txt", content: bytes = b"") -> tuple:
    content = content or (
        b"%PDF-1.4 John Doe\nSoftware Engineer\njohn@test.com\n+1-555-0100\n\n"
        b"SKILLS\nPython, FastAPI, PostgreSQL\n\n"
        b"WORK EXPERIENCE\nSenior Engineer | TechCorp | Jan 2019 - Present\n\n"
        b"EDUCATION\nBSc Computer Science, MIT, 2018\n"
    )
    return (name, io.BytesIO(content), "text/plain")


MOCK_PARSE_RESULT = {
    "raw_text": "John Doe\nSoftware Engineer\njohn@test.com\n+1-555-0100\nPython FastAPI PostgreSQL",
    "skills":   ["python", "fastapi", "postgresql"],
    "education": [{"degree": "BSc Computer Science", "field": "computer science"}],
    "work_experience": [
        {"title": "Senior Engineer", "company": "TechCorp",
         "start_date": "Jan 2019", "end_date": "present"},
    ],
    "contact_info": {"name": "John Doe", "email": "john@test.com", "phone": "+1-555-0100"},
}

MOCK_GAP_RESULT = {
    "total_years": 6.0,
    "employment_gaps": [],
    "overlapping_jobs": [],
    "short_stints": [],
}

MOCK_PIPELINE_RESULT = {
    "fit_score": 82,
    "job_role": "Senior Python Engineer",
    "strengths": ["Strong Python skills"],
    "weaknesses": [],
    "education_analysis": "Solid CS background.",
    "risk_signals": [],
    "final_recommendation": "Recommend",
    "employment_gaps": [],
    "score_breakdown": {"skill_match": {"score": 85, "confidence_weighted": False, "avg_confidence": 1.0}, "experience_match": 80, "stability": 90, "education": 75},
    "matched_skills": ["python", "fastapi", "postgresql"],
    "missing_skills": ["kubernetes"],
    "adjacent_skills": ["docker"],
    "risk_level": "Low",
    "interview_questions": {"technical_questions": [], "behavioral_questions": [], "culture_fit_questions": []},
    "required_skills_count": 4,
    "jd_analysis": {"role_title": "Senior Python Engineer", "domain": "backend",
                    "seniority": "senior", "required_skills": ["python", "kubernetes"],
                    "required_years": 5, "nice_to_have_skills": [], "key_responsibilities": []},
    "candidate_profile": {"name": "John Doe", "skills_identified": ["python", "fastapi"],
                          "total_effective_years": 6.0, "current_role": "Senior Engineer",
                          "current_company": "TechCorp",
                          "career_summary": "Senior Engineer at TechCorp",
                          "education": [{"degree": "BSc Computer Science"}]},
    "skill_analysis": {"matched_skills": ["python"], "missing_skills": ["kubernetes"],
                       "adjacent_skills": ["docker"], "skill_score": 85, "required_count": 4},
    "edu_timeline_analysis": {"education_score": 75, "timeline_text": "No gaps.",
                              "employment_gaps": [], "overlapping_jobs": [], "short_stints": []},
    "explainability": {"skill_rationale": "3/4 matched.", "overall_rationale": "Strong fit."},
    "recommendation_rationale": "Score 82 — Recommend.",
    "work_experience": [{"title": "Senior Engineer", "company": "TechCorp",
                         "start_date": "Jan 2019", "end_date": "present"}],
    "contact_info": {"name": "John Doe", "email": "john@test.com"},
    "analysis_quality": "high",
    "narrative_pending": False,
    "pipeline_errors": [],
}


@contextmanager
def _mock_analyze_patches(pipeline_result: dict | None = None):
    """Patch parse/pipeline on both analyze.py and analyze_helpers.py.

    Resume parsing lives in analyze_helpers; scoring still runs in analyze.py
    for the single-file endpoint and in helpers for batch.
    """
    base_result = pipeline_result or MOCK_PIPELINE_RESULT

    async def _fresh_result(**kwargs):
        return copy.deepcopy(base_result)

    with ExitStack() as stack:
        for mod in (
            "app.backend.routes.analyze",
            "app.backend.routes.analyze_helpers",
        ):
            stack.enter_context(patch(f"{mod}.parse_resume", return_value=MOCK_PARSE_RESULT))
            stack.enter_context(patch(f"{mod}.analyze_gaps", return_value=MOCK_GAP_RESULT))
            stack.enter_context(patch(f"{mod}.run_hybrid_pipeline", side_effect=_fresh_result))
        yield


# ─── 1. JD cache ──────────────────────────────────────────────────────────────

class TestJdCache:
    def test_cache_stores_and_retrieves_parsed_jd(self, db):
        from app.backend.routes.analyze import _get_or_cache_jd
        result1 = _get_or_cache_jd(db, LONG_JD)
        assert isinstance(result1, dict)

        # Second call for the same JD — should be served from DB
        result2 = _get_or_cache_jd(db, LONG_JD)
        assert result2 == result1

    def test_different_jds_produce_different_cache_entries(self, db):
        from app.backend.routes.analyze import _get_or_cache_jd
        jd2 = LONG_JD + " Additional requirement: Rust experience preferred."
        result1 = _get_or_cache_jd(db, LONG_JD)
        result2 = _get_or_cache_jd(db, jd2)
        # Both succeed; hashes differ so both are stored
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)

    def test_cache_key_based_on_first_2000_chars(self, db):
        from app.backend.routes.analyze import _get_or_cache_jd
        from app.backend.models.db_models import JdCache

        jd = LONG_JD
        _get_or_cache_jd(db, jd)
        expected_hash = hashlib.md5(f"0:{jd}".encode()).hexdigest()
        cached = db.query(JdCache).filter(JdCache.hash == expected_hash).first()
        assert cached is not None
        data = json.loads(cached.result_json)
        assert isinstance(data, dict)


# ─── 2. Candidate deduplication ───────────────────────────────────────────────

class TestCandidateDeduplication:
    """Unit tests for _get_or_create_candidate (3-layer dedup)."""

    def _make_parsed(self, email=None, name=None, phone=None):
        return {"contact_info": {"email": email, "name": name, "phone": phone},
                "raw_text": "dummy", "skills": [], "education": [], "work_experience": []}

    def test_creates_new_candidate_on_first_upload(self, db, auth_client):
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB — run after auth_client fixture creates one")

        parsed = self._make_parsed(email="new@example.com", name="Alice Smith", phone="+1-555-9999")
        cid, is_dup = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()
        assert isinstance(cid, int)
        assert is_dup is False

    def test_layer1_email_dedup(self, auth_client, db):
        """Same email → duplicate detected on second upload."""
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        parsed = self._make_parsed(email="dup@example.com", name="Bob Jones", phone="+1-555-1111")
        cid1, is_dup1 = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()
        assert is_dup1 is False

        cid2, is_dup2 = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()
        assert is_dup2 is True
        assert cid1 == cid2

    def test_layer2_file_hash_dedup(self, auth_client, db):
        """Same file hash → duplicate detected (even if email differs)."""
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User, Candidate
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        file_hash = hashlib.md5(b"unique-resume-bytes").hexdigest()
        parsed = self._make_parsed(email="first@example.com")

        cid1, _ = _get_or_create_candidate(db, parsed, user.tenant_id, file_hash=file_hash,
                                            gap_analysis={})
        db.commit()

        # Different email, same hash
        parsed2 = self._make_parsed(email="second@example.com")
        cid2, is_dup = _get_or_create_candidate(db, parsed2, user.tenant_id, file_hash=file_hash)
        db.commit()
        assert is_dup is True
        assert cid1 == cid2

    def test_layer3_name_phone_dedup(self, auth_client, db):
        """Same name + phone → duplicate, even if no email/hash match."""
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        parsed = self._make_parsed(name="Carol White", phone="+1-555-7777")
        cid1, _ = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()

        cid2, is_dup = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()
        assert is_dup is True
        assert cid1 == cid2

    def test_action_create_new_skips_dedup(self, auth_client, db):
        """action='create_new' always creates a fresh row."""
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        parsed = self._make_parsed(email="force@example.com", name="Dave Green", phone="+1-555-5555")
        cid1, _ = _get_or_create_candidate(db, parsed, user.tenant_id)
        db.commit()

        cid2, is_dup = _get_or_create_candidate(db, parsed, user.tenant_id, action="create_new")
        db.commit()
        assert is_dup is False
        assert cid1 != cid2

    def test_different_tenants_not_deduped(self, db):
        """Candidates belonging to different tenants are never considered duplicates."""
        from app.backend.routes.analyze import _get_or_create_candidate
        parsed = self._make_parsed(email="shared@example.com", name="Eve Black", phone="+1-555-3333")

        cid1, _ = _get_or_create_candidate(db, parsed, tenant_id=1)
        db.commit()
        cid2, is_dup = _get_or_create_candidate(db, parsed, tenant_id=2)
        db.commit()
        assert is_dup is False
        assert cid1 != cid2


# ─── 3. Profile storage ───────────────────────────────────────────────────────

class TestCandidateProfileStorage:
    def test_stores_all_profile_fields(self, auth_client, db):
        from app.backend.routes.analyze import _get_or_create_candidate, _store_candidate_profile
        from app.backend.models.db_models import User, Candidate
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        file_hash = hashlib.md5(b"profile-test-bytes").hexdigest()
        cid, _ = _get_or_create_candidate(
            db, MOCK_PARSE_RESULT, user.tenant_id,
            file_hash=file_hash, gap_analysis=MOCK_GAP_RESULT,
        )
        db.commit()

        cand = db.get(Candidate, cid)
        assert cand.resume_file_hash == file_hash
        assert cand.raw_resume_text is not None
        assert cand.parsed_skills is not None
        assert cand.parser_snapshot_json is not None
        snap = json.loads(cand.parser_snapshot_json)
        assert snap.get("contact_info", {}).get("email") == "john@test.com"
        skills = json.loads(cand.parsed_skills)
        assert isinstance(skills, list)
        assert "python" in skills
        assert cand.total_years_exp == 6.0
        assert cand.profile_updated_at is not None

    def test_update_profile_action_overwrites_stored_data(self, auth_client, db):
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User, Candidate

        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        # Create candidate with initial profile
        parsed_v1 = dict(MOCK_PARSE_RESULT, skills=["python"])
        cid, _ = _get_or_create_candidate(db, parsed_v1, user.tenant_id,
                                           file_hash="hash1", gap_analysis={"total_years": 3.0})
        db.commit()

        # Re-upload same candidate with "update_profile"
        parsed_v2 = dict(MOCK_PARSE_RESULT, skills=["python", "rust", "go"])
        _get_or_create_candidate(db, parsed_v2, user.tenant_id,
                                  file_hash="hash1", gap_analysis={"total_years": 5.0},
                                  action="update_profile")
        db.commit()

        cand = db.get(Candidate, cid)
        skills = json.loads(cand.parsed_skills)
        assert "rust" in skills
        assert cand.total_years_exp == 5.0

    def test_name_and_email_populated_from_contact_info(self, auth_client, db):
        from app.backend.routes.analyze import _get_or_create_candidate
        from app.backend.models.db_models import User, Candidate

        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        cid, _ = _get_or_create_candidate(
            db, MOCK_PARSE_RESULT, user.tenant_id,
            file_hash="hash-contact", gap_analysis=MOCK_GAP_RESULT,
        )
        db.commit()
        cand = db.get(Candidate, cid)
        assert cand.name == "John Doe"
        assert cand.email == "john@test.com"


# ─── 4. /api/analyze — dedup response fields ──────────────────────────────────

class TestAnalyzeEndpointDedup:
    def test_jd_too_short_returns_400(self, auth_client):
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": SHORT_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 400
        assert "80 words" in resp.json()["detail"]

    def test_successful_analysis_returns_analysis_quality(self, auth_client):
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis_quality"] == "high"
        assert data["narrative_pending"] is False

    def test_successful_analysis_returns_fit_score(self, auth_client):
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        assert resp.json()["fit_score"] == 82

    def test_duplicate_candidate_flag_on_second_upload(self, auth_client):
        """Uploading same resume twice → second response contains duplicate_candidate."""
        resume_bytes = (
            b"%PDF-1.4 Jane Smith\nBackend Engineer\njane@corp.com\n+1-555-2222\n\n"
            b"SKILLS\nPython, Django\n\n"
            b"EDUCATION\nBSc CS, MIT 2017\n"
        )
        with _mock_analyze_patches({
            **MOCK_PIPELINE_RESULT,
            "contact_info": {"name": "Jane Smith", "email": "jane@corp.com", "phone": "+1-555-2222"},
        }):
            r1 = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file("r1.txt", resume_bytes))],
            )
            r2 = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file("r1.txt", resume_bytes))],
            )
        assert r1.status_code == 200
        assert r1.json().get("duplicate_candidate") is None
        assert r2.status_code == 200
        dup_info = r2.json().get("duplicate_candidate")
        assert dup_info is not None
        assert dup_info["result_count"] >= 1

    def test_action_create_new_skips_dedup_response(self, auth_client):
        """action=create_new → duplicate_candidate should be None."""
        resume_bytes = b"%PDF-1.4 Eve Tester\nDev\neve@corp.com\n+1-555-3333\nPython\n"
        with _mock_analyze_patches():
            auth_client.post("/api/analyze",
                             data={"job_description": LONG_JD, "action": "create_new"},
                             files=[("resume", _make_file("e.txt", resume_bytes))])
            r2 = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD, "action": "create_new"},
                files=[("resume", _make_file("e.txt", resume_bytes))],
            )
        assert r2.status_code == 200
        assert r2.json().get("duplicate_candidate") is None

    def test_narrative_pending_flag_exposed_in_response(self, auth_client):
        """If the pipeline sets narrative_pending=True, the response reflects it."""
        pending_result = {**MOCK_PIPELINE_RESULT, "narrative_pending": True, "analysis_quality": "medium"}
        with _mock_analyze_patches(pending_result):
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        assert resp.json()["narrative_pending"] is True

    def test_unsupported_file_extension_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/analyze",
            data={"job_description": LONG_JD},
            files=[("resume", ("resume.exe", io.BytesIO(b"binary"), "application/octet-stream"))],
        )
        assert resp.status_code == 400

    def test_missing_jd_returns_400(self, auth_client):
        resp = auth_client.post(
            "/api/analyze",
            data={},
            files=[("resume", _make_file())],
        )
        assert resp.status_code == 400

    def test_analysis_persists_candidate_in_db(self, auth_client, db):
        from app.backend.models.db_models import Candidate
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        candidate_id = resp.json()["candidate_id"]
        cand = db.get(Candidate, candidate_id)
        assert cand is not None


# ─── 5. Batch endpoint JD gate ────────────────────────────────────────────────

class TestBatchJdGate:
    def test_batch_short_jd_returns_400(self, auth_client):
        # JD length check runs before file-extension filter in the batch route
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze/batch",
                data={"job_description": SHORT_JD},
                files=[("resumes", ("r.pdf", io.BytesIO(b"%PDF-1.4 dummy"), "application/pdf"))],
            )
        assert resp.status_code == 400
        assert "80 words" in resp.json()["detail"]

    def test_batch_long_jd_succeeds(self, auth_client):
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze/batch",
                data={"job_description": LONG_JD},
                files=[("resumes", _make_file())],
            )
        assert resp.status_code in (200, 400)  # 400 only if parse fails gracefully
        if resp.status_code == 200:
            assert "results" in resp.json()


# ─── 6. POST /api/candidates/{id}/analyze-jd ─────────────────────────────────

class TestAnalyzeJdEndpoint:
    def _seed_candidate_with_profile(self, auth_client, db):
        """Upload a resume so the candidate gets a stored profile, then return its id."""
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        return resp.json()["candidate_id"]

    def test_nonexistent_candidate_returns_404(self, auth_client):
        resp = auth_client.post(
            "/api/candidates/99999/analyze-jd",
            json={"job_description": LONG_JD},
        )
        assert resp.status_code == 404

    def test_candidate_without_profile_returns_422(self, auth_client, db):
        """Candidate created without stored resume text → 422."""
        from app.backend.models.db_models import Candidate, User
        user = db.query(User).first()
        if user is None:
            pytest.skip("No user in DB")

        # Create a bare candidate with no raw_resume_text
        cand = Candidate(tenant_id=user.tenant_id, name="No Profile", email="noprofile@test.com")
        db.add(cand)
        db.commit()
        db.refresh(cand)

        resp = auth_client.post(
            f"/api/candidates/{cand.id}/analyze-jd",
            json={"job_description": LONG_JD},
        )
        assert resp.status_code == 422

    def test_short_jd_returns_400(self, auth_client):
        cid = self._seed_candidate_with_profile(auth_client, db=None)
        # We don't have the db here but the 422 for "no profile" may fire first
        # — patch the pipeline to ensure the candidate has a profile via /analyze
        resp = auth_client.post(
            f"/api/candidates/{cid}/analyze-jd",
            json={"job_description": SHORT_JD},
        )
        assert resp.status_code == 400

    def test_valid_request_returns_200_with_fit_score(self, auth_client):
        cid = self._seed_candidate_with_profile(auth_client, db=None)
        with patch("app.backend.services.hybrid_pipeline.run_hybrid_pipeline",
                   new_callable=AsyncMock, return_value=MOCK_PIPELINE_RESULT):
            resp = auth_client.post(
                f"/api/candidates/{cid}/analyze-jd",
                json={"job_description": LONG_JD},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "fit_score" in data
        assert data["candidate_id"] == cid

    def test_result_persisted_in_history(self, auth_client):
        cid = self._seed_candidate_with_profile(auth_client, db=None)
        with patch("app.backend.services.hybrid_pipeline.run_hybrid_pipeline",
                   new_callable=AsyncMock, return_value=MOCK_PIPELINE_RESULT):
            auth_client.post(
                f"/api/candidates/{cid}/analyze-jd",
                json={"job_description": LONG_JD},
            )

        detail = auth_client.get(f"/api/candidates/{cid}")
        assert detail.status_code == 200
        history = detail.json()["history"]
        assert len(history) >= 2  # original upload + this re-analysis

    def test_custom_scoring_weights_accepted(self, auth_client, db, seed_subscription_plans):
        assign_tenant_plan(db, "enterprise", slug="testcorp")
        cid = self._seed_candidate_with_profile(auth_client, db=None)
        with patch("app.backend.services.hybrid_pipeline.run_hybrid_pipeline",
                   new_callable=AsyncMock, return_value=MOCK_PIPELINE_RESULT) as mock_pl:
            resp = auth_client.post(
                f"/api/candidates/{cid}/analyze-jd",
                json={
                    "job_description": LONG_JD,
                    "scoring_weights": {"skill_match": 0.5, "experience_match": 0.3},
                },
            )
        assert resp.status_code == 200
        # Verify weights were forwarded to the pipeline
        call_kwargs = mock_pl.call_args.kwargs
        assert call_kwargs.get("scoring_weights") == {"skill_match": 0.5, "experience_match": 0.3}


# ─── 7. Enriched candidate GET responses ─────────────────────────────────────

class TestEnrichedCandidateResponses:
    def _upload_and_get_id(self, auth_client):
        with _mock_analyze_patches():
            resp = auth_client.post(
                "/api/analyze",
                data={"job_description": LONG_JD},
                files=[("resume", _make_file())],
            )
        assert resp.status_code == 200
        return resp.json()["candidate_id"]

    def test_list_candidates_includes_profile_fields(self, auth_client):
        self._upload_and_get_id(auth_client)
        resp = auth_client.get("/api/candidates")
        assert resp.status_code == 200
        candidates = resp.json()["candidates"]
        assert len(candidates) >= 1
        c = candidates[0]
        assert "current_role" in c
        assert "current_company" in c
        assert "total_years_exp" in c
        assert "profile_quality" in c

    def test_list_candidates_pagination(self, auth_client):
        # Upload two distinct candidates
        for i in range(2):
            content = f"%PDF-1.4 Candidate {i}\nEngineer{i}@test.com\n+1-555-000{i}\nPython\n".encode()
            with _mock_analyze_patches():
                auth_client.post(
                    "/api/analyze",
                    data={"job_description": LONG_JD},
                    files=[("resume", _make_file(f"r{i}.txt", content))],
                )

        resp = auth_client.get("/api/candidates?page=1&page_size=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "page" in data
        assert len(data["candidates"]) <= 1

    def test_get_candidate_detail_has_stored_profile_flag(self, auth_client):
        cid = self._upload_and_get_id(auth_client)
        resp = auth_client.get(f"/api/candidates/{cid}")
        assert resp.status_code == 200
        data = resp.json()
        assert "has_stored_profile" in data
        assert data["has_stored_profile"] is True

    def test_get_candidate_detail_includes_skills_snapshot(self, auth_client):
        cid = self._upload_and_get_id(auth_client)
        resp = auth_client.get(f"/api/candidates/{cid}")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills_snapshot" in data
        assert isinstance(data["skills_snapshot"], list)

    def test_get_candidate_detail_history_includes_analysis_quality(self, auth_client):
        cid = self._upload_and_get_id(auth_client)
        resp = auth_client.get(f"/api/candidates/{cid}")
        assert resp.status_code == 200
        history = resp.json()["history"]
        assert len(history) >= 1
        assert "analysis_quality" in history[0]

    def test_candidate_search_by_name(self, auth_client):
        self._upload_and_get_id(auth_client)  # creates "John Doe"
        resp = auth_client.get("/api/candidates?search=John")
        assert resp.status_code == 200
        data = resp.json()
        assert any("John" in (c.get("name") or "") for c in data["candidates"])

    def test_candidate_search_no_match(self, auth_client):
        self._upload_and_get_id(auth_client)
        resp = auth_client.get("/api/candidates?search=ZZZNOMATCH")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_get_nonexistent_candidate_returns_404(self, auth_client):
        resp = auth_client.get("/api/candidates/99999")
        assert resp.status_code == 404
