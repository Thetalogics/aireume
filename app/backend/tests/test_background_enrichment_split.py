"""Narrative and interview kit run as independent durable jobs."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.backend.models.db_models import AnalysisJob, ScreeningResult, Tenant
from app.backend.services.background_enrichment import (
    complete_enrichment_job,
    enqueue_enrichment_job,
    schedule_post_narrative_enrichment,
)
from app.backend.services.hybrid_pipeline import _build_fallback_narrative


class TestNarrativeKitSplit:
    def test_fallback_narrative_has_no_interview_kit(self):
        python_result = {
            "fit_score": 72,
            "final_recommendation": "Consider",
            "candidate_profile": {"name": "Alex"},
            "jd_analysis": {"role_title": "Engineer", "required_skills": ["Python"]},
            "score_breakdown": {"experience_match": 80},
        }
        skill_analysis = {
            "matched_required": ["Python"],
            "missing_required": [],
            "required_count": 1,
        }
        narrative = _build_fallback_narrative(python_result, skill_analysis)
        assert "interview_questions" not in narrative

    def test_schedule_kit_even_when_narrative_fallback(self, db, seed_subscription_plans):
        tenant = Tenant(name="enrichment-jobs", slug=f"enrich-{uuid.uuid4().hex[:10]}")
        db.add(tenant)
        db.flush()
        result = ScreeningResult(
            tenant_id=tenant.id,
            resume_text="resume",
            jd_text="job",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 72}),
            narrative_status="fallback",
            analysis_generation=1,
        )
        db.add(result)
        db.commit()

        schedule_post_narrative_enrichment(
            screening_result_id=result.id,
            tenant_id=tenant.id,
            llm_context={"scores": {"final_recommendation": "Consider"}},
            python_result={"final_recommendation": "Consider", "skill_analysis": {}},
            expected_generation=1,
            narrative_status="fallback",
            narrative_payload={"fit_summary": "template"},
        )

        jobs = db.query(AnalysisJob).filter(AnalysisJob.tenant_id == tenant.id).all()
        assert len(jobs) == 1
        assert jobs[0].job_type == "interview_kit"
        assert jobs[0].status == "queued"
        assert jobs[0].job_config["screening_result_id"] == result.id

    @pytest.mark.asyncio
    async def test_complete_enrichment_job_marks_worker_owned_job_done(
        self, db, seed_subscription_plans, monkeypatch
    ):
        tenant = Tenant(name="enrichment-worker", slug=f"enrich-w-{uuid.uuid4().hex[:10]}")
        db.add(tenant)
        db.flush()
        result = ScreeningResult(
            tenant_id=tenant.id,
            resume_text="resume",
            jd_text="job",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 72}),
            narrative_status="ready",
            analysis_generation=1,
        )
        db.add(result)
        db.flush()

        called = {}

        async def _fake_background_interview_kit(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        monkeypatch.setattr(
            "app.backend.services.background_enrichment.background_interview_kit",
            _fake_background_interview_kit,
        )

        job = AnalysisJob(
            tenant_id=tenant.id,
            job_type="interview_kit",
            resume_hash="r" * 64,
            jd_hash="j" * 64,
            input_hash=f"ih-{uuid.uuid4().hex}",
            status="processing",
            worker_id="worker-1",
            leased_until=datetime.now(timezone.utc) + timedelta(minutes=5),
            job_config={
                "screening_result_id": result.id,
                "expected_generation": 1,
                "llm_context": {"scores": {"final_recommendation": "Consider"}},
                "python_result": {"final_recommendation": "Consider"},
            },
        )
        db.add(job)
        db.commit()

        assert await complete_enrichment_job(job, db, expected_worker_id="worker-1") is True
        db.refresh(job)
        assert job.status == "completed"
        assert job.progress_percent == 100
        assert called["kwargs"]["expected_generation"] == 1

    @pytest.mark.asyncio
    async def test_llm_narrative_is_durable_and_worker_dispatched(
        self, db, seed_subscription_plans, monkeypatch
    ):
        tenant = Tenant(name="narrative-worker", slug=f"narr-w-{uuid.uuid4().hex[:10]}")
        db.add(tenant)
        db.flush()
        result = ScreeningResult(
            tenant_id=tenant.id,
            resume_text="resume",
            jd_text="job",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 72}),
            narrative_status="pending",
            analysis_generation=3,
        )
        db.add(result)
        db.commit()

        assert enqueue_enrichment_job(
            db,
            job_type="llm_narrative",
            screening_result_id=result.id,
            tenant_id=tenant.id,
            expected_generation=3,
            llm_context={"scores": {"final_recommendation": "Consider"}},
            python_result={"final_recommendation": "Consider"},
            screening_decision_id=123,
        ) is True
        assert enqueue_enrichment_job(
            db,
            job_type="llm_narrative",
            screening_result_id=result.id,
            tenant_id=tenant.id,
            expected_generation=3,
        ) is False

        job = db.query(AnalysisJob).filter_by(tenant_id=tenant.id, job_type="llm_narrative").one()
        job.status = "processing"
        job.worker_id = "worker-n"
        job.leased_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.commit()

        called = {}

        async def _fake_background_llm_narrative(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        monkeypatch.setattr(
            "app.backend.services.hybrid_pipeline._background_llm_narrative",
            _fake_background_llm_narrative,
        )

        assert await complete_enrichment_job(job, db, expected_worker_id="worker-n") is True
        db.refresh(job)
        assert job.status == "completed"
        assert called["kwargs"]["expected_analysis_generation"] == 3
        assert called["kwargs"]["screening_decision_id"] == 123

    def test_generation_mode_and_narrative_metadata_are_consistent(self, db, seed_subscription_plans):
        from app.backend.services.reliability.stale import apply_narrative_if_generation

        tenant = Tenant(name="mode-consistency", slug=f"mode-{uuid.uuid4().hex[:10]}")
        db.add(tenant)
        db.flush()
        result = ScreeningResult(
            tenant_id=tenant.id,
            resume_text="resume",
            jd_text="job",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 72}),
            narrative_status="pending",
            generation_mode="pending",
            analysis_generation=7,
        )
        db.add(result)
        db.commit()

        outcome = apply_narrative_if_generation(
            db,
            screening_result_id=result.id,
            tenant_id=tenant.id,
            expected_generation=7,
            narrative={"summary": "fallback", "ai_enhanced": True},
            status="ready",
            generation_mode="deterministic_fallback",
        )
        assert outcome.stale is False
        db.commit()
        db.refresh(result)
        narrative = json.loads(result.narrative_json)
        assert result.generation_mode == "deterministic_fallback"
        assert narrative["ai_enhanced"] is False
