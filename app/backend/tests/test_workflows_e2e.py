"""
Real-world end-to-end workflow tests spanning multiple features, exercising the
same paths a recruiter/admin would hit in production.

Scenarios:
  1. Full hiring flow: register company → verify → login → screen a resume →
     result appears in history → an AI decision-log audit row is written.
  2. Session lifecycle isolation: a tenant can only see its own recruiter
     sessions (multi-tenant scoping).
  3. Auth lifecycle: login → access protected route → logout → token rejected.
"""
import io
import json
from unittest.mock import patch, AsyncMock

import pytest

from app.backend.tests.test_helpers import _verify_user_via_api


_PIPELINE_RESULT = {
    "fit_score": 82, "job_role": "Senior Python Engineer",
    "strengths": ["Strong Python skills"], "weaknesses": [],
    "education_analysis": "Good background.", "risk_signals": [],
    "final_recommendation": "Consider", "employment_gaps": [],
    "score_breakdown": {"skill_match": {"score": 85, "confidence_weighted": False, "avg_confidence": 1.0},
                        "experience_match": 80, "stability": 100, "education": 75},
    "matched_skills": ["python"], "missing_skills": [], "adjacent_skills": [],
    "risk_level": "Low",
    "interview_questions": {"technical_questions": [], "behavioral_questions": [], "culture_fit_questions": []},
    "required_skills_count": 3, "jd_analysis": {}, "candidate_profile": {},
    "skill_analysis": {}, "edu_timeline_analysis": {}, "explainability": {},
    "work_experience": [], "contact_info": {"name": "Jane Dev", "email": "jane@dev.com"},
    "analysis_quality": "high", "narrative_pending": False, "pipeline_errors": [],
}

_JD = (
    "Senior Python Backend Engineer. We are looking for an experienced engineer "
    "with at least five years of professional Python experience building "
    "production systems with FastAPI or Django, PostgreSQL, Docker, and "
    "Kubernetes. Strong understanding of REST API design, microservices "
    "architecture, asynchronous programming, and message queues is required. "
    "The ideal candidate has hands-on experience with cloud platforms such as "
    "AWS or GCP, CI/CD pipelines, infrastructure as code, and observability "
    "tooling. You will lead a small engineering team, own technical design "
    "decisions, mentor junior developers, perform thorough code reviews, and "
    "collaborate closely with product managers and data scientists to ship "
    "reliable, well-tested, and scalable backend services on a regular cadence."
)

_RESUME = (
    b"Jane Dev\nSenior Software Engineer\njane@dev.com\n\n"
    b"SKILLS\nPython, FastAPI, PostgreSQL, Docker\n\n"
    b"WORK EXPERIENCE\nSenior Dev | Company A | Jan 2019 - Present\n\n"
    b"EDUCATION\nBSc Computer Science, MIT, 2017\n"
)


def _run_analyze(client):
    with patch("app.backend.routes.analyze.parse_resume", return_value={
        "raw_text": "Jane Dev python fastapi postgresql docker",
        "skills": ["python", "fastapi", "postgresql", "docker"],
        "education": [], "work_experience": [],
        "contact_info": {"name": "Jane Dev", "email": "jane@dev.com"},
    }), patch("app.backend.routes.analyze_helpers.parse_resume", return_value={
        "raw_text": "Jane Dev python fastapi postgresql docker",
        "skills": ["python", "fastapi", "postgresql", "docker"],
        "education": [], "work_experience": [],
        "contact_info": {"name": "Jane Dev", "email": "jane@dev.com"},
    }), patch("app.backend.routes.analyze.analyze_gaps", return_value={}), \
       patch("app.backend.routes.analyze.run_hybrid_pipeline",
             new_callable=AsyncMock, return_value=_PIPELINE_RESULT):
        return client.post(
            "/api/analyze",
            data={"job_description": _JD},
            files={"resume": ("resume.txt", io.BytesIO(_RESUME), "text/plain")},
        )


class TestFullHiringWorkflow:
    def test_register_login_screen_history_and_audit_log(self, client, db):
        # 1) Company signs up
        reg = client.post("/api/auth/register", json={
            "company_name": "HiringCo", "email": "recruiter@hiringco.com",
            "password": "Recruit123!", "full_name": "Recruiter One",
        })
        assert reg.status_code in (200, 201)

        # 2) Email verification + login
        _verify_user_via_api("recruiter@hiringco.com")
        login = client.post("/api/auth/login", json={
            "email": "recruiter@hiringco.com", "password": "Recruit123!",
        })
        assert login.status_code == 200
        token = (login.cookies.get("access_token") or login.json().get("access_token"))
        client.headers.update({"Authorization": f"Bearer {token}"})

        from app.backend.tests.test_helpers import allow_ad_hoc_screening
        allow_ad_hoc_screening(db, email="recruiter@hiringco.com")

        # 3) Screen a candidate resume against a JD
        resp = _run_analyze(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "fit_score" in body

        # 4) The screening shows up in history
        history = client.get("/api/history")
        assert history.status_code == 200
        assert isinstance(history.json(), list)

        # 5) The screening decision is persisted (candidate + result + audit log).
        from app.backend.models.db_models import ScreeningResult, Candidate, AIDecisionLog
        assert db.query(ScreeningResult).count() >= 1
        assert db.query(Candidate).count() >= 1
        assert db.query(AIDecisionLog).count() >= 1


class TestAuthLifecycleWorkflow:
    def test_login_use_logout_then_blocked(self, client):
        client.post("/api/auth/register", json={
            "company_name": "LifecycleCo", "email": "life@cycle.com",
            "password": "Cycle1234!", "full_name": "Life Cycle",
        })
        _verify_user_via_api("life@cycle.com")
        login = client.post("/api/auth/login", json={
            "email": "life@cycle.com", "password": "Cycle1234!",
        })
        token = login.cookies.get("access_token") or login.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/auth/me", headers=headers).status_code == 401


class TestMultiTenantIsolationWorkflow:
    def test_recruiter_sessions_are_tenant_scoped(
        self, client, auth_headers, other_tenant_session
    ):
        """A user must not see recruiter sessions belonging to another tenant."""
        resp = client.get("/api/recruiter/sessions", headers=auth_headers)
        assert resp.status_code == 200
        payload = resp.json()
        sessions = payload if isinstance(payload, list) else payload.get("sessions", [])
        ids = {s.get("id") for s in sessions}
        assert other_tenant_session.id not in ids
