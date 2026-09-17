"""AUD-040 explicit candidate merge."""
import json

import pytest

from app.backend.models.db_models import (
    AuditLog,
    Candidate,
    CandidateMergeEvent,
    CandidateNote,
    ScreeningResult,
    Tenant,
    User,
)
from app.backend.routes.auth import _hash_password
from app.backend.services.candidate_merge_service import (
    MergeError,
    classify_duplicate,
    merge_candidates,
)


def _tenant_user(db, slug: str):
    t = Tenant(name=slug, slug=slug)
    db.add(t)
    db.commit()
    db.refresh(t)
    u = User(
        tenant_id=t.id,
        email=f"admin@{slug}.test",
        hashed_password=_hash_password("TestPass123!"),
        role="admin",
        is_active=True,
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return t, u


def _candidate(db, tenant_id, **kwargs):
    c = Candidate(tenant_id=tenant_id, name=kwargs.get("name", "Pat Lee"), email=kwargs.get("email"), phone=kwargs.get("phone"), resume_file_hash=kwargs.get("resume_file_hash"))
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _screening(db, tenant_id, candidate_id, text="resume"):
    sr = ScreeningResult(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        resume_text=text,
        jd_text="jd",
        parsed_data="{}",
        analysis_result=json.dumps({"fit_score": 70}),
        status="pending",
    )
    db.add(sr)
    db.commit()
    db.refresh(sr)
    return sr


class TestClassifyDuplicate:
    def test_name_similarity_alone_is_ambiguous(self, db):
        t, _ = _tenant_user(db, "dup-name")
        a = _candidate(db, t.id, name="Pat Lee", email="a@x.test")
        b = _candidate(db, t.id, name="Pat Lee", email="b@x.test")
        assert classify_duplicate(a, b) == "ambiguous"

    def test_exact_email_is_not_auto_merged(self, db):
        t, _ = _tenant_user(db, "dup-email")
        a = _candidate(db, t.id, name="A", email="same@x.test")
        b = _candidate(db, t.id, name="B", email="same@x.test")
        assert classify_duplicate(a, b) == "exact_email"
        assert a.merged_into_id is None
        assert b.merged_into_id is None


class TestMergeCandidates:
    def test_same_tenant_merge_moves_results_and_audit(self, db):
        t, actor = _tenant_user(db, "merge-ok")
        source = _candidate(db, t.id, name="Source", email="source@x.test", phone="111")
        target = _candidate(db, t.id, name="Target", email="target@x.test", phone="222")
        sr = _screening(db, t.id, source.id)
        db.add(CandidateNote(candidate_id=source.id, user_id=actor.id, tenant_id=t.id, text="keep me"))
        db.commit()

        out = merge_candidates(db, t.id, source.id, target.id, actor.id, reason="duplicate email review")
        db.commit()

        assert out["canonical_id"] == target.id
        assert out["already_merged"] is False
        assert "phone" in out["conflicts"]
        assert "email" in out["conflicts"]
        db.refresh(source)
        db.refresh(target)
        db.refresh(sr)
        assert source.merged_into_id == target.id
        assert target.merged_into_id is None
        assert sr.candidate_id == target.id
        assert db.query(ScreeningResult).filter(ScreeningResult.candidate_id == source.id).count() == 0
        assert db.query(ScreeningResult).filter(ScreeningResult.candidate_id == target.id).count() == 1
        assert db.query(CandidateNote).filter(CandidateNote.candidate_id == target.id).count() == 1
        event = db.query(CandidateMergeEvent).filter(CandidateMergeEvent.id == out["merge_event_id"]).one()
        assert event.source_candidate_id == source.id
        assert event.target_candidate_id == target.id
        assert event.actor_user_id == actor.id
        audit = db.query(AuditLog).filter(AuditLog.action == "candidate.merge").all()
        assert audit

    def test_cross_tenant_denied(self, db):
        a, actor = _tenant_user(db, "merge-a")
        b, _ = _tenant_user(db, "merge-b")
        source = _candidate(db, a.id, email="s@x.test")
        target = _candidate(db, b.id, email="t@x.test")
        with pytest.raises(MergeError) as exc:
            merge_candidates(db, a.id, source.id, target.id, actor.id)
        assert exc.value.code == "cross_tenant"

    def test_repeat_merge_is_idempotent(self, db):
        t, actor = _tenant_user(db, "merge-idemp")
        source = _candidate(db, t.id, email="s@idemp.test")
        target = _candidate(db, t.id, email="t@idemp.test")
        merge_candidates(db, t.id, source.id, target.id, actor.id)
        db.commit()
        again = merge_candidates(db, t.id, source.id, target.id, actor.id, reason="retry")
        db.commit()
        assert again["already_merged"] is True
        assert db.query(CandidateMergeEvent).filter(CandidateMergeEvent.source_candidate_id == source.id).count() >= 2

    def test_ambiguous_does_not_auto_merge(self, db):
        t, _ = _tenant_user(db, "merge-amb")
        a = _candidate(db, t.id, name="Pat Lee", email="a@amb.test")
        b = _candidate(db, t.id, name="Pat Lee", email="b@amb.test")
        assert classify_duplicate(a, b) == "ambiguous"
        assert a.merged_into_id is None
        assert db.query(Candidate).filter(Candidate.tenant_id == t.id, Candidate.merged_into_id.is_(None)).count() == 2


class TestMergeEndpoint:
    def test_recruiter_can_merge(self, auth_client, db):
        from app.backend.models.db_models import User
        actor = db.query(User).filter(User.email == "admin@testcorp.com").one()
        source = _candidate(db, actor.tenant_id, email="src@testcorp.com")
        target = _candidate(db, actor.tenant_id, email="tgt@testcorp.com")
        _screening(db, actor.tenant_id, source.id)
        resp = auth_client.post(
            "/api/candidates/merge",
            json={"source_candidate_id": source.id, "target_candidate_id": target.id, "reason": "manual"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["canonical_id"] == target.id
        listed = auth_client.get("/api/candidates")
        ids = [c["id"] for c in listed.json()["candidates"]]
        assert target.id in ids
        assert source.id not in ids

    def test_viewer_cannot_merge(self, viewer_client, db):
        from app.backend.models.db_models import User
        actor = db.query(User).filter(User.email == "viewer@viewercorp.com").one()
        source = _candidate(db, actor.tenant_id, email="src@viewercorp.com")
        target = _candidate(db, actor.tenant_id, email="tgt@viewercorp.com")
        resp = viewer_client.post(
            "/api/candidates/merge",
            json={"source_candidate_id": source.id, "target_candidate_id": target.id},
        )
        assert resp.status_code in (401, 403)
