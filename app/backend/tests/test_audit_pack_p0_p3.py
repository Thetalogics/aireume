"""P0–P3 audit pack: score/badge alignment, RBAC, billing, outcomes, gates."""
from types import SimpleNamespace

import pytest

from app.backend.middleware.rbac import get_tenant_role, TENANT_ROLE_VIEWER
from app.backend.services.constants import recommendation_from_score
from app.backend.services.fit_scorer import align_decision_to_score
from app.backend.services.jd_profile_service import merge_jd_profile
from app.backend.services.skill_matcher import match_skills
from app.backend.services.hybrid_pipeline import _merge_llm_into_result


class TestRecommendationFromBlendedScore:
    def test_thresholds(self):
        assert recommendation_from_score(72) == "Shortlist"
        assert recommendation_from_score(71) == "Consider"
        assert recommendation_from_score(45) == "Consider"
        assert recommendation_from_score(44) == "Reject"

    def test_aligns_explanation_decision_to_displayed_score(self):
        explanation = {"decision": "Reject", "reasons": ["low core skills"]}
        rec, aligned = align_decision_to_score(80, explanation)
        assert rec == "Shortlist"
        assert aligned["decision"] == "Shortlist"
        assert aligned["reasons"] == ["low core skills"]

    def test_blended_score_not_deterministic_badge(self):
        blended = int(0.6 * 90 + 0.4 * 40)  # 70
        assert recommendation_from_score(blended) == "Consider"
        assert recommendation_from_score(40) == "Reject"


class TestUnknownRoleFailClosed:
    def test_unknown_role_is_viewer(self):
        assert get_tenant_role(SimpleNamespace(role="hm")) == TENANT_ROLE_VIEWER
        assert get_tenant_role(SimpleNamespace(role="superuser")) == TENANT_ROLE_VIEWER
        assert get_tenant_role(SimpleNamespace(role=None)) == TENANT_ROLE_VIEWER
        assert get_tenant_role(SimpleNamespace(role="")) == TENANT_ROLE_VIEWER

    def test_known_roles_unchanged(self):
        assert get_tenant_role(SimpleNamespace(role="admin")) == "admin"
        assert get_tenant_role(SimpleNamespace(role="recruiter")) == "recruiter"
        assert get_tenant_role(SimpleNamespace(role="viewer")) == "viewer"


class TestEmptyJdSkillsUnevaluable:
    def test_empty_required_skills_score_zero(self):
        result = match_skills(["python"], [])
        assert result["skill_score"] == 0
        assert result.get("unevaluable_skills") is True


class TestSubstringDoesNotMatchAffixes:
    def test_java_does_not_match_javascript(self):
        result = match_skills(["javascript"], ["Java"])
        assert "Java" not in result["matched_skills"]

    def test_react_still_matches_react_native(self):
        result = match_skills(["react native"], ["React"])
        assert result["matched_skills"]


class TestMergeJdProfileValidatesSkills:
    def test_drops_skills_absent_from_jd_text(self):
        rules = {"required_skills": ["Python"], "nice_to_have_skills": []}
        llm = {
            "required_skills": ["Python", "Quantum Telepathy"],
            "nice_to_have_skills": ["Mind Reading"],
            "domain": "backend",
            "role_title": "Engineer",
        }
        merged = merge_jd_profile(
            rules,
            llm,
            jd_text="We need a Python engineer with Django experience.",
        )
        assert "Quantum Telepathy" not in merged["required_skills"]
        assert "Mind Reading" not in merged["nice_to_have_skills"]
        assert any(s.lower() == "python" for s in merged["required_skills"])


class TestResumeNarrativeEvidence:
    def test_strips_unsupported_strength_strings(self):
        merged = _merge_llm_into_result(
            {"fit_score": 70},
            {"strengths": ["World expert in COBOL mainframes"], "concerns": []},
            source_text="Senior Python developer with Django and AWS.",
        )
        strengths = merged.get("strengths") or []
        joined = " ".join(str(s) for s in strengths).lower()
        assert "cobol" not in joined


class TestBillingAdminOnly:
    def _recruiter_headers(self, client, db):
        import uuid
        from app.backend.models.db_models import User
        from app.backend.routes.auth import _hash_password
        from app.backend.tests.test_helpers import access_token_from

        admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
        rec = User(
            email=f"recruiter.billing.{uuid.uuid4().hex[:8]}@testcorp.com",
            hashed_password=_hash_password("TestPass123!"),
            tenant_id=admin.tenant_id,
            role="recruiter",
            email_verified=True,
        )
        db.add(rec)
        db.commit()
        login = client.post("/api/auth/login", json={
            "email": rec.email,
            "password": "TestPass123!",
        })
        assert login.status_code == 200, login.text
        token = access_token_from(login)
        return admin.tenant_id, {"Authorization": f"Bearer {token}"}

    def test_recruiter_cannot_cancel(self, auth_client, db, client):
        tenant_id, headers = self._recruiter_headers(client, db)
        resp = client.post(f"/api/billing/cancel/{tenant_id}", headers=headers)
        assert resp.status_code == 403

    def test_recruiter_cannot_checkout(self, auth_client, db, client):
        _tenant_id, headers = self._recruiter_headers(client, db)
        resp = client.post(
            "/api/billing/checkout",
            json={"plan": "growth", "success_url": "https://example.com", "cancel_url": "https://example.com"},
            headers=headers,
        )
        assert resp.status_code == 403

    def test_recruiter_cannot_list_invoices(self, auth_client, db, client):
        _tenant_id, headers = self._recruiter_headers(client, db)
        resp = client.get("/api/billing/invoices", headers=headers)
        assert resp.status_code == 403

    def test_admin_can_list_invoices(self, auth_client):
        resp = auth_client.get("/api/billing/invoices")
        assert resp.status_code == 200


class TestTranscriptVideoQueueGates:
    def test_transcript_locked_on_growth_plan(self, auth_client):
        resp = auth_client.post(
            "/api/transcript/analyze",
            data={"transcript_text": "Interviewer: hello\nCandidate: hi", "role_template_id": 1},
        )
        assert resp.status_code == 403
        detail = resp.json().get("detail") or {}
        if isinstance(detail, dict):
            assert detail.get("error_code") == "PLAN_FEATURE_LOCKED"

    def test_video_locked_on_growth_plan(self, auth_client):
        resp = auth_client.post(
            "/api/analyze/video",
            files={"video": ("clip.mp4", b"not-a-real-video", "video/mp4")},
        )
        assert resp.status_code == 403

    def test_queue_submit_requires_requisition(self, auth_client_with_enterprise_plan, db):
        from app.backend.models.db_models import User
        from app.backend.services.requisition_service import get_or_create_tenant_settings

        user = db.query(User).filter(User.email == "enterprise@enterprisecorp.com").first()
        settings = get_or_create_tenant_settings(db, user.tenant_id)
        settings.screening_mode = "requisition_required"
        db.commit()

        resp = auth_client_with_enterprise_plan.post(
            "/queue/submit",
            params={
                "resume_text": "Python developer with 5 years experience in Django.",
                "resume_filename": "cv.txt",
                "job_description": "Looking for a Python engineer with Django and AWS.",
            },
        )
        assert resp.status_code == 400
        detail = resp.json().get("detail") or {}
        if isinstance(detail, dict):
            assert detail.get("error_code") == "REQUISITION_REQUIRED"


class TestHmOutcomeWritesHiringOutcome:
    def test_hire_records_learning_outcome(self, auth_client, db):
        import json
        from datetime import datetime, timezone
        from app.backend.models.db_models import (
            Candidate, HiringOutcome, RequisitionCandidate, ScreeningResult, User,
        )
        from app.backend.services.requisition_service import create_requisition

        admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
        req = create_requisition(
            db, tenant_id=admin.tenant_id, created_by=admin.id,
            title="Outcome Learning", jd_text="Python AWS",
        )
        cand = Candidate(tenant_id=admin.tenant_id, name="Hire Me", email="hireme@example.com")
        db.add(cand)
        db.flush()
        sr = ScreeningResult(
            tenant_id=admin.tenant_id,
            candidate_id=cand.id,
            resume_text="Python developer",
            jd_text="Python AWS",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 80, "skill_analysis": {"matched_skills": ["python"]}}),
            timestamp=datetime.now(timezone.utc),
        )
        db.add(sr)
        db.flush()
        db.add(RequisitionCandidate(
            requisition_id=req.id,
            candidate_id=cand.id,
            screening_result_id=sr.id,
            pipeline_status="shortlisted",
            submission_status="submitted",
        ))
        db.commit()

        resp = auth_client.put(
            f"/api/requisitions/{req.id}/candidates/{cand.id}/outcome",
            json={"hm_outcome": "hire"},
        )
        assert resp.status_code == 200, resp.text
        outcome = db.query(HiringOutcome).filter(HiringOutcome.screening_result_id == sr.id).first()
        assert outcome is not None
        assert outcome.decision == "hired"

    def test_hold_sets_in_review(self, auth_client, db):
        from app.backend.models.db_models import Candidate, RequisitionCandidate, User
        from app.backend.services.requisition_service import create_requisition

        admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
        req = create_requisition(
            db, tenant_id=admin.tenant_id, created_by=admin.id,
            title="Hold Test", jd_text="Python",
        )
        cand = Candidate(tenant_id=admin.tenant_id, name="Hold Me", email="holdme@example.com")
        db.add(cand)
        db.flush()
        db.add(RequisitionCandidate(
            requisition_id=req.id,
            candidate_id=cand.id,
            pipeline_status="pending",
            submission_status="submitted",
        ))
        db.commit()

        resp = auth_client.put(
            f"/api/requisitions/{req.id}/candidates/{cand.id}/outcome",
            json={"hm_outcome": "hold"},
        )
        assert resp.status_code == 200, resp.text
        rc = db.query(RequisitionCandidate).filter(
            RequisitionCandidate.requisition_id == req.id,
            RequisitionCandidate.candidate_id == cand.id,
        ).first()
        assert rc.pipeline_status == "in-review"


class TestReqShareLinkPasscode:
    def test_stores_passcode_hash(self, auth_client, db):
        from app.backend.models.db_models import HandoffShareLink, User
        from app.backend.services.requisition_service import create_requisition

        admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
        req = create_requisition(
            db, tenant_id=admin.tenant_id, created_by=admin.id,
            title="Share Passcode", jd_text="Python",
        )
        db.commit()
        resp = auth_client.post(
            f"/api/requisitions/{req.id}/share-links",
            json={"label": "HM pack", "expires_in_days": 7, "passcode": "s3cret"},
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        from app.backend.services.share_crypto import hash_share_token
        link = db.query(HandoffShareLink).filter(
            HandoffShareLink.token_hash == hash_share_token(token)
        ).first()
        assert link.token is None
        assert link.passcode_hash
        assert link.passcode_hash != "s3cret"
        assert link.passcode_hash.startswith("scrypt$")


class TestSkillFilterQuotedMatch:
    def test_go_does_not_match_mongodb_blob(self, auth_client, db):
        import json
        from datetime import datetime, timezone
        from app.backend.models.db_models import Candidate, ScreeningResult, User

        admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
        cand = Candidate(tenant_id=admin.tenant_id, name="Mongo Dev", email="mongo@example.com")
        db.add(cand)
        db.flush()
        db.add(ScreeningResult(
            tenant_id=admin.tenant_id,
            candidate_id=cand.id,
            resume_text="MongoDB Python engineer",
            jd_text="Backend role",
            parsed_data="{}",
            analysis_result=json.dumps({"matched_skills": ["MongoDB", "Python"]}),
            timestamp=datetime.now(timezone.utc),
        ))
        db.commit()
        resp = auth_client.get("/api/candidates", params={"skill": "go"})
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json().get("candidates", resp.json() if isinstance(resp.json(), list) else [])]
        if not names:
            payload = resp.json()
            items = payload.get("items") or payload.get("results") or payload.get("data") or []
            names = [c.get("name") for c in items]
        assert "Mongo Dev" not in names
