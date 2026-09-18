"""Stale-version write protection (unit via SQLite session)."""
from __future__ import annotations

from app.backend.models.db_models import ScreeningResult, Tenant
from app.backend.services.reliability.stale import apply_narrative_if_generation, discard_stale


def test_stale_narrative_does_not_overwrite(db, seed_subscription_plans):
    tenant = Tenant(name="rel-stale", slug="rel-stale")
    db.add(tenant)
    db.flush()
    row = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result='{"fit_score": 70}',
        analysis_generation=2,
        narrative_json='{"gen": 2}',
        narrative_status="ready",
    )
    db.add(row)
    db.flush()
    outcome = apply_narrative_if_generation(
        db,
        screening_result_id=row.id,
        tenant_id=tenant.id,
        expected_generation=1,
        narrative={"fit_summary": "old"},
        status="ready",
    )
    db.refresh(row)
    assert outcome.stale is True
    assert row.narrative_json == '{"gen": 2}'
    assert row.analysis_generation == 2


def test_matching_generation_writes(db, seed_subscription_plans):
    tenant = Tenant(name="rel-ok", slug="rel-ok")
    db.add(tenant)
    db.flush()
    row = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result="{}",
        analysis_generation=1,
    )
    db.add(row)
    db.flush()
    outcome = apply_narrative_if_generation(
        db,
        screening_result_id=row.id,
        tenant_id=tenant.id,
        expected_generation=1,
        narrative={"fit_summary": "new"},
        status="ready",
    )
    db.refresh(row)
    assert outcome.stale is False
    assert "new" in (row.narrative_json or "")


def test_discard_stale_payload_has_required_fields():
    payload = discard_stale(
        job_type="llm_narrative",
        job_id="j1",
        target_id=9,
        expected_version=1,
        current_version=2,
        tenant_id=3,
    )
    assert payload["stale"] is True
    assert payload["job_type"] == "llm_narrative"
    assert "resume" not in str(payload).lower()
