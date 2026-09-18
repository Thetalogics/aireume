"""Phase 0 core scoring invariants — must fail against the original defects."""
from __future__ import annotations

import pytest

from app.backend.services.constants import DEFAULT_WEIGHTS, NEW_DEFAULT_WEIGHTS
from app.backend.services.fit_scorer import compute_fit_score
from app.backend.services.scoring_weights import (
    canonicalize_scoring_weights,
    effective_scoring_weights,
    normalize_risk_weight,
)
from app.backend.services.weight_mapper import normalize_weights

_BASE_SCORES = {
    "skill_score": 80,
    "exp_score": 70,
    "arch_score": 70,
    "edu_score": 60,
    "timeline_score": 90,
    "domain_score": 70,
    "actual_years": 5,
    "required_years": 5,
    "matched_skills": ["python"],
    "missing_skills": [],
    "required_count": 1,
    "employment_gaps": [],
    "short_stints": [],
}

_BALANCED = {
    "skills": 0.40,
    "experience": 0.20,
    "architecture": 0.15,
    "education": 0.15,
    "domain": 0.10,
}


def _score(*, risk_penalty: float, risk_weight: float, **score_overrides):
    weights = {**_BALANCED, "risk": risk_weight}
    scores = {**_BASE_SCORES, **score_overrides}
    return compute_fit_score(scores, weights, risk_signals=[], risk_penalty=risk_penalty)


class TestRiskWeightSign:
    def test_positive_risk_weight_reduces_score_when_penalty_exists(self):
        low = _score(risk_penalty=0, risk_weight=0.10)
        high = _score(risk_penalty=20, risk_weight=0.10)
        assert high["fit_score"] < low["fit_score"]

    def test_legacy_negative_risk_weight_matches_positive_magnitude(self):
        pos = _score(risk_penalty=20, risk_weight=0.10)
        neg = _score(risk_penalty=20, risk_weight=-0.10)
        assert pos["fit_score"] == neg["fit_score"]
        assert pos["fit_score"] < _score(risk_penalty=0, risk_weight=0.10)["fit_score"]

    def test_zero_risk_penalty_leaves_score_unchanged(self):
        a = _score(risk_penalty=0, risk_weight=0.10)
        b = _score(risk_penalty=0, risk_weight=0.20)
        assert a["fit_score"] == b["fit_score"]

    def test_increasing_risk_penalty_cannot_increase_score(self):
        scores = [_score(risk_penalty=p, risk_weight=0.10)["fit_score"] for p in (0, 10, 20, 40)]
        assert scores == sorted(scores, reverse=True)

    def test_increasing_risk_weight_cannot_increase_score(self):
        scores = [_score(risk_penalty=20, risk_weight=w)["fit_score"] for w in (0.05, 0.10, 0.20)]
        assert scores == sorted(scores, reverse=True)

    def test_risk_contribution_cannot_be_a_bonus(self):
        baseline = _score(risk_penalty=0, risk_weight=0.10)["fit_score"]
        penalized = _score(risk_penalty=25, risk_weight=-0.10)["fit_score"]
        assert penalized <= baseline

    def test_score_bounded_0_100(self):
        for penalty, weight in ((0, 0.10), (100, 0.30), (50, -0.10)):
            s = _score(risk_penalty=penalty, risk_weight=weight)["fit_score"]
            assert 0 <= s <= 100

    def test_risk_clamped_to_unit_interval_not_arbitrary_030(self):
        assert normalize_risk_weight(0.30) == pytest.approx(0.30)
        assert normalize_risk_weight(0.50) == pytest.approx(0.50)
        assert normalize_risk_weight(1.0) == pytest.approx(1.0)
        assert normalize_risk_weight(1.5) == pytest.approx(1.0)
        assert normalize_risk_weight(-0.4) == pytest.approx(0.4)
        assert normalize_risk_weight(DEFAULT_WEIGHTS["risk"]) > 0
        assert normalize_risk_weight(NEW_DEFAULT_WEIGHTS["risk"]) > 0
        canon = canonicalize_scoring_weights(None)
        assert canon["risk"] > 0
        mapped = normalize_weights(NEW_DEFAULT_WEIGHTS.copy())
        assert mapped["risk"] > 0

    def test_regression_negative_risk_must_not_increase_score(self):
        """Original defect: fit = base - (penalty * negative_weight) became a bonus."""
        low_risk = _score(risk_penalty=5, risk_weight=-0.10)
        high_risk = _score(risk_penalty=20, risk_weight=-0.10)
        assert high_risk["fit_score"] <= low_risk["fit_score"]


class TestTeamGapMonotonicity:
    @pytest.mark.parametrize("skills_w", [0.03, 0.05, 0.40])
    def test_effective_positive_coefficients_non_negative(self, skills_w):
        rest = 1.0 - skills_w
        weights = {
            "skills": skills_w,
            "experience": rest * 0.4,
            "architecture": rest * 0.2,
            "education": rest * 0.2,
            "domain": rest * 0.2,
            "risk": 0.10,
        }
        for active in (False, True):
            eff = effective_scoring_weights(weights, team_gap_active=active)
            for key in ("skills", "experience", "architecture", "education", "domain"):
                assert eff[key] >= 0
            if active:
                assert eff["team_gap"] == pytest.approx(0.05)
            else:
                assert eff.get("team_gap", 0) == 0

    def test_better_skills_never_lower_score_with_team_gap(self):
        weights = {
            "skills": 0.03,
            "experience": 0.40,
            "architecture": 0.20,
            "education": 0.20,
            "domain": 0.17,
            "risk": 0.10,
        }
        ctx = {"team_gaps": ["python"]}
        low_skills = compute_fit_score(
            {**_BASE_SCORES, "skill_score": 20, "matched_skills": ["python"]},
            weights,
            risk_signals=[],
            risk_penalty=0,
            phase3_context=ctx,
        )
        high_skills = compute_fit_score(
            {**_BASE_SCORES, "skill_score": 90, "matched_skills": ["python"]},
            weights,
            risk_signals=[],
            risk_penalty=0,
            phase3_context=ctx,
        )
        assert high_skills["fit_score"] >= low_skills["fit_score"]

    def test_team_gap_absent_does_not_shrink_skills_weight(self):
        weights = {**_BALANCED, "risk": 0.10}
        without = effective_scoring_weights(weights, team_gap_active=False)
        assert without["skills"] == pytest.approx(0.40)

    def test_positive_weights_sum_to_one_without_team_gap(self):
        weights = {**_BALANCED, "risk": 0.10}
        eff = effective_scoring_weights(weights, team_gap_active=False)
        positive = sum(eff[k] for k in ("skills", "experience", "architecture", "education", "domain"))
        assert positive == pytest.approx(1.0)
        assert eff.get("team_gap", 0.0) == 0.0
        assert eff["risk"] == pytest.approx(0.10)

    def test_positive_weights_plus_team_gap_sum_to_one(self):
        weights = {**_BALANCED, "risk": 0.10}
        eff = effective_scoring_weights(weights, team_gap_active=True)
        base = sum(eff[k] for k in ("skills", "experience", "architecture", "education", "domain"))
        assert base == pytest.approx(0.95)
        assert eff["team_gap"] == pytest.approx(0.05)
        assert base + eff["team_gap"] == pytest.approx(1.0)
        assert eff["risk"] == pytest.approx(0.10)

    def test_default_mix_all_positive_no_risk_is_100(self):
        scores = {
            "skill_score": 100,
            "exp_score": 100,
            "arch_score": 100,
            "edu_score": 100,
            "timeline_score": 100,
            "domain_score": 100,
        }
        perfect = compute_fit_score(scores, risk_signals=[], risk_penalty=0)
        assert perfect["fit_score"] == 100
        penalized = compute_fit_score(scores, risk_signals=[], risk_penalty=20)
        assert penalized["fit_score"] < 100
        assert penalized["fit_score"] < perfect["fit_score"]

    def test_better_experience_never_lowers_score(self):
        weights = {**_BALANCED, "risk": 0.10}
        low = compute_fit_score({**_BASE_SCORES, "exp_score": 10}, weights, risk_signals=[], risk_penalty=0)
        high = compute_fit_score({**_BASE_SCORES, "exp_score": 90}, weights, risk_signals=[], risk_penalty=0)
        assert high["fit_score"] >= low["fit_score"]
