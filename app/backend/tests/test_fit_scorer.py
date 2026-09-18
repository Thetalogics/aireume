"""Tests for fit_scorer.py — deterministic scoring and explanation."""

import pytest

from app.backend.services.fit_scorer import compute_deterministic_score, explain_decision, compute_fit_score
from app.backend.services.eligibility_service import EligibilityResult


class TestComputeDeterministicScore:
    """Tests for compute_deterministic_score."""

    def test_perfect_features_eligible_returns_100(self):
        features = {
            "core_skill_match": 1.0,
            "secondary_skill_match": 1.0,
            "domain_match": 1.0,
            "relevant_experience": 1.0,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        assert score == 100

    def test_all_zero_features_returns_0(self):
        features = {
            "core_skill_match": 0.0,
            "secondary_skill_match": 0.0,
            "domain_match": 0.0,
            "relevant_experience": 0.0,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        assert score == 0

    def test_ineligible_penalty_applied(self):
        features = {
            "core_skill_match": 1.0,
            "secondary_skill_match": 1.0,
            "domain_match": 1.0,
            "relevant_experience": 1.0,
        }
        eligibility = EligibilityResult(eligible=False, reason="domain_mismatch")
        score = compute_deterministic_score(features, eligibility)
        assert score == 80  # 100 - 20 ineligible penalty

    def test_low_domain_match_penalty_applied(self):
        features = {
            "core_skill_match": 0.8,
            "secondary_skill_match": 0.8,
            "domain_match": 0.2,
            "relevant_experience": 0.8,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        # (0.8*0.4 + 0.8*0.15 + 0.2*0.25 + 0.8*0.2) * 100 - 10 = 65 - 10
        assert score == 55

    def test_low_core_skill_match_penalty_applied(self):
        features = {
            "core_skill_match": 0.2,
            "secondary_skill_match": 0.8,
            "domain_match": 0.8,
            "relevant_experience": 0.8,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        # (0.2*0.4 + 0.8*0.15 + 0.8*0.25 + 0.8*0.2) * 100 - 10 = 56 - 10
        assert score == 46

    def test_multiple_penalties_stack(self):
        """Ineligible, low core skills, and low domain match all subtract."""
        features = {
            "core_skill_match": 0.2,
            "secondary_skill_match": 0.8,
            "domain_match": 0.2,
            "relevant_experience": 0.8,
        }
        eligibility = EligibilityResult(eligible=False, reason="low_core_skills")
        score = compute_deterministic_score(features, eligibility)
        # (0.2*0.4 + 0.8*0.15 + 0.2*0.25 + 0.8*0.2) * 100 - 20 - 10 - 10 = 41 - 40
        assert score == 1

    def test_score_is_integer(self):
        features = {
            "core_skill_match": 0.5,
            "secondary_skill_match": 0.5,
            "domain_match": 0.5,
            "relevant_experience": 0.5,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        assert isinstance(score, int)

    def test_score_in_range_0_to_100(self):
        features = {
            "core_skill_match": 0.75,
            "secondary_skill_match": 0.6,
            "domain_match": 0.5,
            "relevant_experience": 0.4,
        }
        eligibility = EligibilityResult(eligible=True)
        score = compute_deterministic_score(features, eligibility)
        assert 0 <= score <= 100


class TestExplainDecision:
    """Tests for explain_decision."""

    def test_shortlist_candidate_no_caps(self):
        features = {
            "core_skill_match": 1.0,
            "secondary_skill_match": 1.0,
            "domain_match": 1.0,
            "relevant_experience": 1.0,
        }
        eligibility = EligibilityResult(eligible=True)
        result = explain_decision(features, eligibility)
        assert result["decision"] == "Shortlist"
        assert result["score"] == 100
        assert result["caps_applied"] == []
        assert "Strong match across all criteria" in result["reasons"]

    def test_reject_candidate_caps_listed(self):
        features = {
            "core_skill_match": 0.2,
            "secondary_skill_match": 0.2,
            "domain_match": 0.2,
            "relevant_experience": 0.0,
        }
        eligibility = EligibilityResult(eligible=False, reason="no_relevant_experience")
        result = explain_decision(features, eligibility)
        assert result["decision"] == "Reject"
        assert len(result["caps_applied"]) > 0
        assert "ineligible_penalty" in result["caps_applied"]

    def test_domain_mismatch_reason_mentions_both_domains(self):
        features = {
            "core_skill_match": 0.5,
            "secondary_skill_match": 0.5,
            "domain_match": 0.5,
            "relevant_experience": 0.5,
        }
        eligibility = EligibilityResult(
            eligible=False,
            reason="domain_mismatch",
            details={"jd_domain": "backend", "candidate_domain": "frontend"},
        )
        result = explain_decision(features, eligibility)
        assert any("backend" in r and "frontend" in r for r in result["reasons"])

    def test_low_core_skills_reason_mentions_percentage(self):
        features = {
            "core_skill_match": 0.2,
            "secondary_skill_match": 0.5,
            "domain_match": 0.5,
            "relevant_experience": 0.5,
        }
        eligibility = EligibilityResult(
            eligible=False,
            reason="low_core_skills",
            details={"core_skill_match": 0.2},
        )
        result = explain_decision(features, eligibility)
        assert any("20%" in r for r in result["reasons"])

    def test_returns_all_required_keys(self):
        features = {
            "core_skill_match": 0.5,
            "secondary_skill_match": 0.5,
            "domain_match": 0.5,
            "relevant_experience": 0.5,
        }
        eligibility = EligibilityResult(eligible=True)
        result = explain_decision(features, eligibility)
        assert "decision" in result
        assert "score" in result
        assert "reasons" in result
        assert "feature_summary" in result
        assert "caps_applied" in result


class TestComputeFitScore:
    """Tests for compute_fit_score (existing function)."""

    def test_basic_weighted_calculation(self):
        scores = {
            "skill_score": 100,
            "exp_score": 100,
            "arch_score": 100,
            "edu_score": 100,
            "timeline_score": 100,
            "domain_score": 100,
        }
        result = compute_fit_score(scores)
        assert result["fit_score"] == 100

    def test_risk_signals_affect_penalty(self):
        scores = {
            "skill_score": 100,
            "exp_score": 100,
            "arch_score": 100,
            "edu_score": 100,
            "timeline_score": 100,
            "domain_score": 100,
        }
        risk_signals = [{"severity": "high"}]  # penalty = 20
        result = compute_fit_score(scores, risk_signals=risk_signals)
        assert result["fit_score"] == 97
        assert result["risk_penalty"] == 20

    def test_score_clamped_to_100_max(self):
        scores = {
            "skill_score": 100,
            "exp_score": 100,
            "arch_score": 100,
            "edu_score": 100,
            "timeline_score": 100,
            "domain_score": 100,
        }
        result = compute_fit_score(scores, risk_penalty=-100)  # negative penalty adds to score
        assert result["fit_score"] <= 100

    def test_score_clamped_to_0_min(self):
        scores = {
            "skill_score": 0,
            "exp_score": 0,
            "arch_score": 0,
            "edu_score": 0,
            "timeline_score": 0,
            "domain_score": 0,
        }
        result = compute_fit_score(scores, risk_penalty=1000)
        assert result["fit_score"] >= 0

    def test_empty_risk_signals_no_penalty(self):
        scores = {
            "skill_score": 100,
            "exp_score": 100,
            "arch_score": 100,
            "edu_score": 100,
            "timeline_score": 100,
            "domain_score": 100,
        }
        result = compute_fit_score(scores, risk_signals=[])
        assert result["risk_penalty"] == 0
        assert result["fit_score"] == 100

    def test_dict_breakdown_scores_accepted(self):
        """score_breakdown dict format (experience_match/skill_match) must not crash fit scoring."""
        scores = {
            "skill_score": {"score": 80, "confidence_weighted": True, "avg_confidence": 0.9},
            "exp_score": {"score": 75, "actual_years": 5.0, "required_years": 3.0},
            "arch_score": 70,
            "edu_score": 60,
            "timeline_score": 85,
            "domain_score": 65,
        }
        result = compute_fit_score(scores)
        assert isinstance(result["fit_score"], int)
        assert 0 <= result["fit_score"] <= 100
        assert result["score_breakdown"]["experience_match"]["score"] == 75
