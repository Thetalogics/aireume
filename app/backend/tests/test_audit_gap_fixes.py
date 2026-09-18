"""Tests for audit gap fixes: denormalized columns and voice screening nice-to-have skills."""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestPopulateDenormalizedColumns:
    """GAP 1: Test that _populate_denormalized_columns correctly sets DB columns from pipeline result."""

    def test_populates_all_columns(self):
        from app.backend.routes.analyze import _populate_denormalized_columns

        sr = MagicMock()
        result = {
            "deterministic_score": 72,
            "fit_score": 68,
            "skill_analysis": {"core_match_ratio": 0.85},
            "candidate_domain": {"confidence": 0.9},
            "eligibility": {"eligible": True, "reason": None},
        }

        _populate_denormalized_columns(sr, result)

        assert sr.deterministic_score == 72
        assert sr.core_skill_score == 0.85
        assert sr.domain_match_score == 0.9
        assert sr.eligibility_status is True
        assert sr.eligibility_reason is None

    def test_does_not_copy_fit_score_into_deterministic_score(self):
        from app.backend.routes.analyze import _populate_denormalized_columns

        sr = MagicMock()
        sr.deterministic_score = None
        result = {
            "deterministic_score": None,
            "fit_score": 55,
            "skill_analysis": {},
            "candidate_domain": {},
            "eligibility": {},
        }

        _populate_denormalized_columns(sr, result)

        assert sr.deterministic_score is None

    def test_handles_missing_keys_gracefully(self):
        from app.backend.routes.analyze import _populate_denormalized_columns

        sr = MagicMock()
        sr.deterministic_score = None
        sr.core_skill_score = None
        sr.domain_match_score = None
        result = {"fit_score": 42}

        _populate_denormalized_columns(sr, result)

        assert sr.deterministic_score is None
        assert sr.core_skill_score is None
        assert sr.domain_match_score is None

    def test_handles_non_dict_result(self):
        from app.backend.routes.analyze import _populate_denormalized_columns

        sr = MagicMock()
        # Should return without error and without setting any attributes
        _populate_denormalized_columns(sr, None)
        # Verify the function didn't raise — deterministic_score should not have been set
        # (MagicMock auto-creates attributes, so we just verify no exception was raised)

    def test_populates_eligibility_false(self):
        from app.backend.routes.analyze import _populate_denormalized_columns

        sr = MagicMock()
        result = {
            "deterministic_score": 30,
            "eligibility": {"eligible": False, "reason": "Domain mismatch"},
            "skill_analysis": {"core_match_ratio": 0.2},
            "candidate_domain": {"confidence": 0.1},
        }

        _populate_denormalized_columns(sr, result)

        assert sr.eligibility_status is False
        assert sr.eligibility_reason == "Domain mismatch"


class TestUpsertScreeningResultWithColumns:
    """GAP 1: Test that _upsert_screening_result populates denormalized columns when pipeline_result is passed."""

    def test_new_result_gets_denormalized_columns(self, db_session, sample_user, sample_candidate, sample_jd):
        from app.backend.routes.analyze import _upsert_screening_result

        pipeline_result = {
            "deterministic_score": 78,
            "fit_score": 75,
            "skill_analysis": {"core_match_ratio": 0.9},
            "candidate_domain": {"confidence": 0.8},
            "eligibility": {"eligible": True, "reason": None},
        }

        result = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data='{}',
            analysis_result='{"fit_score": 75}',
            pipeline_result=pipeline_result,
        )

        assert result.deterministic_score == 78
        assert result.core_skill_score == 0.9
        assert result.domain_match_score == 0.8
        assert result.eligibility_status is True

    def test_existing_result_updated_with_denormalized_columns(self, db_session, sample_user, sample_candidate, sample_jd):
        from app.backend.routes.analyze import _upsert_screening_result

        # First insert
        result = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data='{}',
            analysis_result='{"fit_score": 50}',
        )
        assert result.deterministic_score is None

        # Update with pipeline_result
        pipeline_result = {
            "deterministic_score": 82,
            "skill_analysis": {"core_match_ratio": 0.95},
            "candidate_domain": {"confidence": 0.7},
            "eligibility": {"eligible": True, "reason": None},
        }

        updated = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="test resume",
            jd_text="test jd",
            parsed_data='{}',
            analysis_result='{"fit_score": 82}',
            pipeline_result=pipeline_result,
        )

        assert updated.deterministic_score == 82
        assert updated.core_skill_score == 0.95
        assert updated.domain_match_score == 0.7


class TestVoiceScreeningNiceToHave:
    """GAP 5: Test that nice_to_have_skills are extracted and passed to post-call assessment."""

    def test_build_conversation_context_includes_nice_to_have(self, db_session, sample_user, sample_candidate, sample_jd):
        from app.backend.services.voice_screening_service import build_conversation_context
        from app.backend.models.db_models import VoiceScreeningSession, VoiceTenantConfig

        # Add nice_to_have_skills_override to the JD
        sample_jd.nice_to_have_skills_override = '["docker", "terraform"]'
        db_session.commit()

        config = VoiceTenantConfig(
            tenant_id=sample_user.tenant_id,
            bot_name="ARIA",
            greeting_style="professional",
            call_duration_max=420,
        )
        db_session.add(config)
        db_session.commit()

        voice_session = VoiceScreeningSession(
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            jd_id=sample_jd.id,
            phone_number="+1234567890",
            direction="outbound",
            status="scheduled",
            interview_depth="deep",
        )
        db_session.add(voice_session)
        db_session.commit()
        db_session.refresh(voice_session)

        ctx = build_conversation_context(db_session, voice_session.id)

        assert "nice_to_have_skills" in ctx
        assert ctx["nice_to_have_skills"] == ["docker", "terraform"]
        assert ctx["must_have_skills"] == ["python", "kubernetes", "aws"]

    def test_build_conversation_context_nice_to_have_empty_when_no_override(self, db_session, sample_user, sample_candidate, sample_jd):
        from app.backend.services.voice_screening_service import build_conversation_context
        from app.backend.models.db_models import VoiceScreeningSession, VoiceTenantConfig

        config = VoiceTenantConfig(
            tenant_id=sample_user.tenant_id,
            bot_name="ARIA",
            greeting_style="professional",
            call_duration_max=420,
        )
        db_session.add(config)
        db_session.commit()

        voice_session = VoiceScreeningSession(
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            jd_id=sample_jd.id,
            phone_number="+1234567890",
            direction="outbound",
            status="scheduled",
            interview_depth="deep",
        )
        db_session.add(voice_session)
        db_session.commit()
        db_session.refresh(voice_session)

        ctx = build_conversation_context(db_session, voice_session.id)

        assert ctx["nice_to_have_skills"] == []
        assert ctx["must_have_skills"] == ["python", "kubernetes", "aws"]
