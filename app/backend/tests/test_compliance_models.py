"""
Tests for the compliance/audit ORM models added for GDPR / EU AI Act:
CandidateConsent, AIDecisionLog, IdempotencyKey, BreachLog, DataRetentionPolicy.

Verifies they persist, enforce their key constraints, and round-trip.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.backend.models.db_models import (
    Tenant, Candidate,
    CandidateConsent, AIDecisionLog, IdempotencyKey, BreachLog, DataRetentionPolicy,
)


@pytest.fixture
def tenant(db):
    t = Tenant(name="ComplianceCo", slug=f"compliance-{uuid.uuid4().hex[:6]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture
def candidate(db, tenant):
    c = Candidate(tenant_id=tenant.id, name="Jane Candidate", email="jane@example.com")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


class TestCandidateConsent:
    def test_create_and_query(self, db, tenant, candidate):
        consent = CandidateConsent(
            tenant_id=tenant.id, candidate_id=candidate.id,
            consent_type="ai_screening", consented=True,
            consented_at=datetime.now(timezone.utc), consent_version="1.0",
            consent_ip="203.0.113.5",
        )
        db.add(consent)
        db.commit()
        got = db.query(CandidateConsent).filter_by(candidate_id=candidate.id).first()
        assert got.consented is True
        assert got.consent_type == "ai_screening"

    def test_unique_candidate_consent_type(self, db, tenant, candidate):
        db.add(CandidateConsent(tenant_id=tenant.id, candidate_id=candidate.id,
                                consent_type="ai_screening", consented=True))
        db.commit()
        db.add(CandidateConsent(tenant_id=tenant.id, candidate_id=candidate.id,
                                consent_type="ai_screening", consented=False))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


class TestAIDecisionLog:
    def test_create_captures_decision_chain(self, db, tenant, candidate):
        log = AIDecisionLog(
            id=1,  # BigInteger PK doesn't autoincrement on SQLite (Postgres BIGSERIAL is fine)
            tenant_id=tenant.id, candidate_id=candidate.id,
            model_name="gemma4:31b", model_version="cloud",
            prompt_template_version="2.0", prompt_hash="abc123",
            raw_llm_output='{"fit_score": 75}',
            guardrails_triggered=["prompt_injection_check"],
            fallback_used=False,
            deterministic_score=70.0, llm_score=78.0, final_score=75.0,
        )
        db.add(log)
        db.commit()
        got = db.query(AIDecisionLog).filter_by(candidate_id=candidate.id).first()
        assert got.final_score == 75.0
        assert got.guardrails_triggered == ["prompt_injection_check"]
        assert got.fallback_used is False


class TestIdempotencyKey:
    def test_primary_key_dedupes(self, db, tenant):
        expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        db.add(IdempotencyKey(key="idem-1", tenant_id=tenant.id,
                              endpoint="/api/analyze", request_fingerprint="a" * 64,
                              response_status=200,
                              response_body={"ok": True}, expires_at=expiry))
        db.commit()
        db.add(IdempotencyKey(key="idem-1", tenant_id=tenant.id,
                              endpoint="/api/analyze", request_fingerprint="b" * 64,
                              expires_at=expiry))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


class TestBreachLog:
    def test_create_incident(self, db, tenant):
        breach = BreachLog(
            id=1,  # BigInteger PK doesn't autoincrement on SQLite (Postgres BIGSERIAL is fine)
            tenant_id=tenant.id, breach_type="unauthorized_access",
            affected_records_count=42,
            affected_data_categories=["email", "resume"],
            description="Test incident",
        )
        db.add(breach)
        db.commit()
        got = db.query(BreachLog).filter_by(tenant_id=tenant.id).first()
        assert got.affected_records_count == 42
        assert "email" in got.affected_data_categories


class TestDataRetentionPolicy:
    def test_defaults_and_uniqueness(self, db, tenant):
        policy = DataRetentionPolicy(tenant_id=tenant.id)
        db.add(policy)
        db.commit()
        got = db.query(DataRetentionPolicy).filter_by(tenant_id=tenant.id).first()
        assert got.candidate_retention_days == 730
        assert got.screening_result_retention_days == 1095

        # One policy per tenant
        db.add(DataRetentionPolicy(tenant_id=tenant.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
