"""AUD-008 / AUD-009: HM interview/comment scope and generic requisition write."""
from app.backend.models.db_models import (
    Candidate,
    Comment,
    RequisitionCandidate,
    ScreeningResult,
    User,
    VoiceScreeningSession,
)
from app.backend.routes.auth import _hash_password
from app.backend.services.requisition_service import create_requisition
from app.backend.tests.test_helpers import assign_tenant_plan

_JD = (
    "We need a senior Python engineer with FastAPI, PostgreSQL, and Redis experience. "
    "You will own screening pipelines, write tests, and work with recruiters on hiring quality."
)


def _hm_setup(auth_client, db):
    admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
    assign_tenant_plan(db, "enterprise", email=admin.email)
    assigned = Candidate(tenant_id=admin.tenant_id, name="Assigned", email="hm-assigned@testcorp.com")
    other = Candidate(tenant_id=admin.tenant_id, name="Other", email="hm-other@testcorp.com")
    hm = User(
        tenant_id=admin.tenant_id,
        email="hm-phase-a@testcorp.com",
        hashed_password=_hash_password("TestPass123!"),
        role="hiring_manager",
        is_active=True,
        email_verified=True,
    )
    db.add_all([assigned, other, hm])
    db.commit()
    db.refresh(assigned)
    db.refresh(other)
    db.refresh(hm)
    req = create_requisition(
        db, tenant_id=admin.tenant_id, created_by=admin.id, title="HM Phase A", jd_text=_JD,
    )
    req.primary_hiring_manager_id = hm.id
    db.add(RequisitionCandidate(requisition_id=req.id, candidate_id=assigned.id, added_by=admin.id))
    db.commit()
    login = auth_client.post("/api/auth/login", json={"email": hm.email, "password": "TestPass123!"})
    assert login.status_code == 200, login.text
    token = login.cookies.get("access_token") or login.json().get("access_token")
    auth_client.headers.update({"Authorization": f"Bearer {token}"})
    return admin, assigned, other, hm, req


def test_hm_cannot_read_unassigned_interview_session(auth_client, db, seed_subscription_plans):
    admin, assigned, other, hm, req = _hm_setup(auth_client, db)
    session = VoiceScreeningSession(
        tenant_id=admin.tenant_id,
        candidate_id=other.id,
        phone_number="+14155550100",
        direction="outbound",
        status="completed",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    resp = auth_client.get(f"/api/interviews/sessions/{session.id}")
    assert resp.status_code in (403, 404)
    resp = auth_client.get(f"/api/interviews/sessions/{session.id}/transcript")
    assert resp.status_code in (403, 404)
    resp = auth_client.get(f"/api/interviews/sessions/{session.id}/scorecard")
    assert resp.status_code in (403, 404)
    listed = auth_client.get("/api/interviews/sessions")
    if listed.status_code == 200:
        ids = [row.get("id") for row in listed.json().get("sessions", [])]
        assert session.id not in ids
    else:
        assert listed.status_code in (403, 404)


def test_hm_cannot_read_unassigned_result_comments(auth_client, db, seed_subscription_plans):
    admin, assigned, other, hm, req = _hm_setup(auth_client, db)
    result = ScreeningResult(
        tenant_id=admin.tenant_id,
        candidate_id=other.id,
        resume_text="resume",
        jd_text=_JD,
        parsed_data="{}",
        analysis_result="{}",
    )
    db.add(result)
    db.flush()
    db.add(Comment(result_id=result.id, user_id=admin.id, text="secret note"))
    db.commit()
    db.refresh(result)
    resp = auth_client.get(f"/api/results/{result.id}/comments")
    assert resp.status_code in (403, 404)


def test_hm_cannot_generic_update_requisition_scoring(auth_client, db, seed_subscription_plans):
    admin, assigned, other, hm, req = _hm_setup(auth_client, db)
    resp = auth_client.put(
        f"/api/requisitions/{req.id}",
        json={"title": "Hacked requisition title"},
    )
    assert resp.status_code == 403


def test_hm_history_hides_unassigned_candidate_results(auth_client, db, seed_subscription_plans):
    admin, assigned, other, hm, req = _hm_setup(auth_client, db)
    visible = ScreeningResult(
        tenant_id=admin.tenant_id,
        candidate_id=assigned.id,
        resume_text="resume",
        jd_text=_JD,
        parsed_data="{}",
        analysis_result="{}",
    )
    hidden = ScreeningResult(
        tenant_id=admin.tenant_id,
        candidate_id=other.id,
        resume_text="resume",
        jd_text=_JD,
        parsed_data="{}",
        analysis_result="{}",
    )
    db.add_all([visible, hidden])
    db.commit()
    db.refresh(visible)
    db.refresh(hidden)
    resp = auth_client.get("/api/history")
    assert resp.status_code == 200
    ids = [row.get("id") for row in resp.json()]
    assert visible.id in ids
    assert hidden.id not in ids
