"""Narrative and interview kit run as independent background LLM tasks."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.backend.services.background_enrichment import schedule_post_narrative_enrichment
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

    def test_schedule_kit_even_when_narrative_fallback(self, monkeypatch):
        created = []

        def _capture_task(coro):
            created.append(coro)
            coro.close()
            return MagicMock()

        monkeypatch.setattr(asyncio, "create_task", _capture_task)
        monkeypatch.setattr(
            "app.backend.services.background_enrichment._update_screening_fields",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            "app.backend.services.hybrid_pipeline.register_background_task",
            lambda _task: None,
        )

        schedule_post_narrative_enrichment(
            screening_result_id=42,
            tenant_id=1,
            llm_context={"scores": {"final_recommendation": "Consider"}},
            python_result={"final_recommendation": "Consider", "skill_analysis": {}},
            expected_generation=1,
            narrative_status="fallback",
            narrative_payload={"fit_summary": "template"},
        )

        assert len(created) == 1
        assert asyncio.iscoroutine(created[0])
