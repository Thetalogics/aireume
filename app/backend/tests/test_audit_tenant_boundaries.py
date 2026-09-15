"""AUD-004 ATS tenant bind, AUD-006 use_existing, AUD-007 HM search."""
from unittest.mock import patch

import pytest

from app.backend.models.db_models import (
    ATSConnection,
    Candidate,
    RequisitionCandidate,
    Tenant,
    User,
)
from app.backend.routes.auth import _hash_password
from app.backend.services.ats_connector import ATSConnector
from app.backend.services.requisition_service import create_requisition
from app.backend.tests.test_helpers import allow_ad_hoc_screening

_JD = (
    "We need a senior Python engineer with FastAPI, PostgreSQL, and Redis experience. "
    "You will own screening pipelines, write tests, and work with recruiters on hiring quality."
)


@pytest.mark.asyncio
async def test_tenant_a_ats_connection_cannot_push_tenant_b_candidate(db, seed_subscription_plans):
    a = Tenant(name="A", slug="ats-a")
    b = Tenant(name="B", slug="ats-b")
    db.add_all([a, b])
    db.flush()
    cand_b = Candidate(tenant_id=b.id, name="Secret", email="b@x.com")
    db.add(cand_b)
    db.flush()
    conn = ATSConnection(
        tenant_id=a.id,
        provider="generic",
        label="A ATS",
        base_url="https://example.test",
        is_active=True,
    )
    db.add(conn)
    db.commit()

    with patch("httpx.AsyncClient") as client_cls:
        connector = ATSConnector(db)
        result = await connector.push_candidate_status(conn, cand_b.id)
        assert result["success"] is False
        assert result.get("http_status") == 404
        client_cls.assert_not_called()


def test_use_existing_requires_explicit_duplicate_candidate_when_hash_not_resolved(
    auth_client, db, seed_subscription_plans
):
    allow_ad_hoc_screening(db, email="admin@testcorp.com")
    files = {"resume": ("r.txt", b"hello resume python fastapi", "text/plain")}
    data = {"job_description": _JD, "action": "use_existing"}
    resp = auth_client.post("/api/analyze", files=files, data=data)
    assert resp.status_code == 409


def test_use_existing_candidate_must_belong_to_current_tenant(auth_client, db, seed_subscription_plans):
    other = Tenant(name="Other", slug="other-use-existing")
    db.add(other)
    db.flush()
    foreign = Candidate(tenant_id=other.id, name="Foreign", email="f@x.com", raw_resume_text="x")
    db.add(foreign)
    db.commit()
    allow_ad_hoc_screening(db, email="admin@testcorp.com")
    files = {"resume": ("r.txt", b"hello resume python fastapi", "text/plain")}
    data = {
        "job_description": _JD,
        "action": "use_existing",
        "candidate_id": str(foreign.id),
    }
    resp = auth_client.post("/api/analyze", files=files, data=data)
    assert resp.status_code == 409


def test_hm_search_cannot_find_unassigned_candidate_by_exact_email(auth_client, db, seed_subscription_plans):
    admin = db.query(User).filter(User.email == "admin@testcorp.com").first()
    assigned = Candidate(tenant_id=admin.tenant_id, name="Assigned", email="assigned-hm-search@testcorp.com")
    other = Candidate(tenant_id=admin.tenant_id, name="Hidden", email="hidden-hm-search@testcorp.com")
    hm = User(
        tenant_id=admin.tenant_id,
        email="hm-search@testcorp.com",
        hashed_password=_hash_password("pass"),
        role="hiring_manager",
        is_active=True,
        email_verified=True,
    )
    db.add_all([assigned, other, hm])
    db.commit()
    db.refresh(assigned)
    db.refresh(hm)
    req = create_requisition(
        db, tenant_id=admin.tenant_id, created_by=admin.id, title="HM Search Opening", jd_text="Need python",
    )
    req.primary_hiring_manager_id = hm.id
    db.add(RequisitionCandidate(requisition_id=req.id, candidate_id=assigned.id, added_by=admin.id))
    db.commit()

    login = auth_client.post("/api/auth/login", json={"email": hm.email, "password": "pass"})
    token = login.cookies.get("access_token") or login.json().get("access_token")
    auth_client.headers.update({"Authorization": f"Bearer {token}"})
    resp = auth_client.get("/api/candidates/search", params={"q": "hidden-hm-search@testcorp.com"})
    assert resp.status_code == 200
    emails = [c.get("email") for c in resp.json().get("candidates", [])]
    assert "hidden-hm-search@testcorp.com" not in emails


def test_use_existing_on_stream_requires_explicit_candidate(auth_client, db, seed_subscription_plans):
    allow_ad_hoc_screening(db, email="admin@testcorp.com")
    files = {"resume": ("r.txt", b"hello resume python fastapi", "text/plain")}
    data = {"job_description": _JD, "action": "use_existing"}
    resp = auth_client.post("/api/analyze/stream", files=files, data=data)
    assert resp.status_code == 409
