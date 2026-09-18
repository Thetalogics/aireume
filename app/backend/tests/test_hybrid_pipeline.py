"""
Tests for hybrid_pipeline.py — all pure Python (no LLM calls for Components 1-7).
Component 8 (explain_with_llm) is tested with a mocked LLM.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import json


@pytest.fixture(autouse=True)
def _disable_outlines_import_in_hybrid_tests(monkeypatch):
    """Avoid importing outlines/transformers/torch during unit tests.

    Those imports hang on this Windows environment while scanning package
    metadata. Hybrid unit tests mock the chat LLM and do not need Outlines.
    """
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "0")


# ─── Import the module under test ─────────────────────────────────────────────
from app.backend.services.hybrid_pipeline import (
    parse_jd_rules,
    parse_resume_rules,
    _infer_total_years_from_resume_text,
    score_education_rules,
    score_experience_rules,
    domain_architecture_rules,
    compute_fit_score,
    _build_fallback_narrative,
    _merge_llm_into_result,
    _run_python_phase,
    run_hybrid_pipeline,
    _assess_quality,
    _compute_domain_similarity,
)
from app.backend.services.skill_matcher import (
    match_skills,
    _normalize_skill,
    _expand_skill,
)


# ==============================================================================
# Domain Similarity
# ==============================================================================

class TestComputeDomainSimilarity:
    """Tests for _compute_domain_similarity in hybrid_pipeline.py."""

    def test_same_domain_high_similarity(self):
        """Identical score vectors should yield similarity > 0.8."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {"embedded": 0.6, "hardware": 0.3, "backend": 0.1}}
        cand = {"domain": "embedded", "confidence": 0.6, "scores": {"embedded": 0.6, "hardware": 0.3, "backend": 0.1}}
        sim = _compute_domain_similarity(jd, cand)
        assert sim > 0.8

    def test_related_domains_medium_similarity(self):
        """Embedded + hardware share overlap -- medium similarity."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {"embedded": 0.6, "hardware": 0.3, "backend": 0.1}}
        cand = {"domain": "hardware", "confidence": 0.5, "scores": {"embedded": 0.3, "hardware": 0.5, "backend": 0.2}}
        sim = _compute_domain_similarity(jd, cand)
        assert 0.3 <= sim <= 0.9

    def test_unrelated_domains_low_similarity(self):
        """Embedded vs management -- low similarity."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {"embedded": 0.6, "hardware": 0.3, "backend": 0.1}}
        cand = {"domain": "management", "confidence": 0.5, "scores": {"management": 0.7, "backend": 0.2, "devops": 0.1}}
        sim = _compute_domain_similarity(jd, cand)
        assert sim < 0.2

    def test_unknown_domain_fallback_binary_match(self):
        """When scores are empty, fallback to binary name comparison."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {}}
        cand = {"domain": "embedded", "confidence": 0.6, "scores": {}}
        sim = _compute_domain_similarity(jd, cand)
        assert sim == 0.6

    def test_unknown_domain_fallback_mismatch(self):
        """When scores are empty and names differ, returns 0."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {}}
        cand = {"domain": "backend", "confidence": 0.5, "scores": {}}
        sim = _compute_domain_similarity(jd, cand)
        assert sim == 0.0

    def test_zero_magnitude_returns_zero(self):
        """All-zero score vectors should return 0."""
        jd = {"domain": "unknown", "confidence": 0.0, "scores": {"embedded": 0.0}}
        cand = {"domain": "unknown", "confidence": 0.0, "scores": {"embedded": 0.0}}
        sim = _compute_domain_similarity(jd, cand)
        assert sim == 0.0

    def test_symmetry(self):
        """Similarity should be symmetric."""
        jd = {"domain": "embedded", "confidence": 0.6, "scores": {"embedded": 0.6, "hardware": 0.3}}
        cand = {"domain": "backend", "confidence": 0.5, "scores": {"embedded": 0.2, "backend": 0.6, "hardware": 0.1}}
        sim_ab = _compute_domain_similarity(jd, cand)
        sim_ba = _compute_domain_similarity(cand, jd)
        assert sim_ab == sim_ba


# ═══════════════════════════════════════════════════════════════════════════════
# Component 1: parse_jd_rules
# ═══════════════════════════════════════════════════════════════════════════════

class TestInferResumeMetadata:
    def test_infer_years_from_prose(self):
        t = "Embedded engineer with 7+ years of experience in automotive systems."
        assert _infer_total_years_from_resume_text(t) == 7.0

    def test_parse_resume_rules_uses_inferred_years_when_gap_zero(self):
        gap = {
            "total_years": 0.0,
            "employment_gaps": [],
            "overlapping_jobs": [],
            "short_stints": [],
        }
        parsed = {
            "contact_info": {},
            "work_experience": [],
            "raw_text": "I bring over 6 years of professional software development experience.",
            "skills": ["python"],
            "education": [],
        }
        prof = parse_resume_rules(parsed, gap)
        assert prof["total_effective_years"] == 6.0


class TestParseJdRules:

    def test_years_pattern_plus(self):
        jd = "We need 5+ years of Python experience."
        result = parse_jd_rules(jd * 10)  # multiply to pass word count
        assert result["required_years"] == 5

    def test_years_pattern_minimum(self):
        jd = "Minimum of 3 years working with React. " * 10
        result = parse_jd_rules(jd)
        assert result["required_years"] == 3

    def test_years_pattern_range_takes_lower(self):
        jd = "3-7 years of experience required. " * 10
        result = parse_jd_rules(jd)
        assert result["required_years"] == 3

    def test_years_zero_when_not_mentioned(self):
        jd = "We are looking for a talented engineer to join our team and build great products. " * 5
        result = parse_jd_rules(jd)
        assert result["required_years"] == 0

    def test_domain_backend(self):
        jd = ("Senior Backend Engineer. We use FastAPI, PostgreSQL, Redis, microservices, "
              "REST API, docker, kubernetes. " * 5)
        result = parse_jd_rules(jd)
        assert result["domain"] == "backend"

    def test_domain_ml_ai(self):
        jd = ("Machine learning engineer role. Must know PyTorch, TensorFlow, deep learning, "
              "NLP, neural network, transformers, LLM. " * 5)
        result = parse_jd_rules(jd)
        assert result["domain"] == "ml_ai"

    def test_domain_devops(self):
        jd = ("DevOps engineer. Kubernetes, docker, terraform, ansible, jenkins, ci/cd, "
              "prometheus, grafana, infrastructure as code. " * 5)
        result = parse_jd_rules(jd)
        assert result["domain"] == "devops"

    def test_seniority_from_title(self):
        jd = "Senior Python Developer\n\nWe need 5 years experience in backend development. " * 5
        result = parse_jd_rules(jd)
        assert result["seniority"] == "senior"

    def test_seniority_junior_from_title(self):
        jd = "Junior Software Engineer\nEntry-level position. 0-1 years experience. " * 5
        result = parse_jd_rules(jd)
        assert result["seniority"] in ("junior", "mid")

    def test_seniority_lead_from_years(self):
        jd = "Software Engineer with 10+ years experience managing large teams. " * 5
        result = parse_jd_rules(jd)
        assert result["seniority"] in ("lead", "senior")

    def test_required_vs_nice_to_have_split(self):
        jd = (
            "Required: Python, FastAPI, PostgreSQL. "
            "Nice to have: GraphQL, Redis, Kubernetes. "
        ) * 10
        result = parse_jd_rules(jd)
        # required should contain python/fastapi
        req_lower = [s.lower() for s in result["required_skills"]]
        nice_lower = [s.lower() for s in result["nice_to_have_skills"]]
        assert any("python" in s for s in req_lower)
        # nice-to-have should be separate (no duplication)
        assert set(result["required_skills"]).isdisjoint(set(result["nice_to_have_skills"]))

    def test_role_title_extraction(self):
        jd = "Senior Backend Engineer\n\nWe are looking for a skilled engineer " * 5
        result = parse_jd_rules(jd)
        assert "engineer" in result["role_title"].lower()

    def test_default_domain_other(self):
        jd = "Creative director needed with strong leadership skills. " * 10
        result = parse_jd_rules(jd)
        # Should not crash and should return a domain
        assert result["domain"] in (
            "backend", "frontend", "fullstack", "data_science", "ml_ai",
            "devops", "embedded", "mobile", "management", "other"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Component 3: match_skills
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchSkills:

    def test_exact_match(self):
        result = match_skills(["python"], ["python"])
        assert "python" in result["matched_skills"]
        assert result["skill_score"] == 100

    def test_alias_match_postgres(self):
        """'postgres' in candidate should match 'postgresql' in JD."""
        result = match_skills(["postgres"], ["postgresql"])
        assert result["matched_skills"]
        assert result["missing_skills"] == [] or "postgresql" not in result["missing_skills"]

    def test_alias_match_js(self):
        """'js' in candidate should match 'javascript' in JD."""
        result = match_skills(["js"], ["javascript"])
        assert result["matched_skills"]

    def test_substring_match(self):
        """'react' in candidate should match 'react native' in JD."""
        result = match_skills(["react"], ["react native"])
        assert result["matched_skills"]

    def test_missing_skill_tracked(self):
        result = match_skills(["python"], ["python", "kubernetes"])
        assert "kubernetes" in result["missing_skills"]

    def test_empty_required_defaults_50(self):
        """If JD has no required skills, skill match is unevaluable (0, not 50)."""
        result = match_skills(["python"], [])
        assert result["skill_score"] == 0

    def test_adjacent_skills(self):
        result = match_skills(
            ["python", "redis"],
            ["python"],
            jd_nice_to_have=["redis", "graphql"],
        )
        assert "redis" in result["adjacent_skills"]
        assert "graphql" not in result["adjacent_skills"]

    def test_skill_score_partial(self):
        result = match_skills(
            ["python"],
            ["python", "java", "c++"],
        )
        # 1 of 3 matched = 33%
        assert result["skill_score"] == pytest.approx(33, abs=2)

    def test_rawtext_fallback_scan(self):
        """Text-scanned skills are promoted when they have domain context."""
        result = match_skills(
            ["docker"],                          # structured candidate skills (provides context)
            ["kubernetes"],
            text_scanned_skills=["kubernetes"],   # pre-extracted from text
            structured_skills=["docker"],         # context: devops domain
        )
        assert result["matched_skills"]


# ═══════════════════════════════════════════════════════════════════════════════
# Component 4: score_education_rules
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreEducationRules:

    def _profile(self, edu):
        return {"education": edu, "skills_identified": []}

    def test_phd_score(self):
        profile = self._profile([{"degree": "PhD Computer Science", "field": "computer science"}])
        score = score_education_rules(profile, "backend")
        assert score >= 90

    def test_masters_relevant_field(self):
        profile = self._profile([{"degree": "Master of Computer Science", "field": "computer science"}])
        score = score_education_rules(profile, "backend")
        assert score >= 80

    def test_bachelors_relevant(self):
        profile = self._profile([{"degree": "Bachelor of Science in Computer Engineering", "field": "computer engineering"}])
        score = score_education_rules(profile, "backend")
        assert 60 <= score <= 80

    def test_irrelevant_degree_penalised(self):
        profile = self._profile([{"degree": "Bachelor of Arts in History", "field": "history"}])
        score_rel = score_education_rules(
            self._profile([{"degree": "Bachelor Computer Science", "field": "computer science"}]),
            "backend"
        )
        score_irrel = score_education_rules(profile, "backend")
        assert score_rel > score_irrel

    def test_no_education_returns_neutral(self):
        """Missing education data should not penalise — return 60."""
        score = score_education_rules(self._profile([]), "backend")
        assert score == 60

    def test_mba_management(self):
        profile = self._profile([{"degree": "MBA", "field": "business administration"}])
        score = score_education_rules(profile, "management")
        assert score >= 75

    def test_embedded_domain_electronics(self):
        profile = self._profile([{"degree": "BE Electronics Engineering", "field": "electronics"}])
        score = score_education_rules(profile, "embedded")
        assert score >= 60


# ═══════════════════════════════════════════════════════════════════════════════
# Component 5: score_experience_rules
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreExperienceRules:

    def _profile(self, years):
        return {"total_effective_years": years, "education": [], "skills_identified": []}

    def _jd(self, required_years):
        return {"required_years": required_years, "domain": "backend", "role_title": "Eng"}

    def _gap(self, gaps=None, stints=None, overlaps=None):
        return {
            "employment_gaps":  gaps or [],
            "short_stints":     stints or [],
            "overlapping_jobs": overlaps or [],
            "total_years":      0,
        }

    def test_exact_years_match(self):
        result = score_experience_rules(self._profile(5), self._jd(5), self._gap())
        assert result["exp_score"] == 95  # within requested range = 95

    def test_over_years_bonus(self):
        result = score_experience_rules(self._profile(8), self._jd(5), self._gap())
        assert result["exp_score"] >= 95  # above min, treated as within range

    def test_under_years_penalty(self):
        result = score_experience_rules(self._profile(2), self._jd(5), self._gap())
        assert result["exp_score"] < 70

    def test_zero_required_years_scales_with_actual(self):
        r1 = score_experience_rules(self._profile(5), self._jd(0), self._gap())
        r2 = score_experience_rules(self._profile(10), self._jd(0), self._gap())
        assert r2["exp_score"] >= r1["exp_score"]

    def test_critical_gap_deducts_timeline(self):
        gaps = [{"severity": "critical", "duration_months": 15, "start_date": "2020-01", "end_date": "2021-04"}]
        result = score_experience_rules(self._profile(5), self._jd(5), self._gap(gaps=gaps))
        assert result["timeline_score"] <= 76   # 90 - 14

    def test_minor_gaps_moderate_deduction(self):
        gaps = [
            {"severity": "minor", "duration_months": 4},
            {"severity": "moderate", "duration_months": 8},
        ]
        result = score_experience_rules(self._profile(5), self._jd(5), self._gap(gaps=gaps))
        assert result["timeline_score"] <= 80   # 90 - 3 - 7

    def test_short_stints_deduction(self):
        stints = [{"duration_months": 2}, {"duration_months": 3}]
        result = score_experience_rules(self._profile(5), self._jd(5), self._gap(stints=stints))
        assert result["timeline_score"] <= 84   # 90 - 6

    def test_no_gaps_full_timeline(self):
        result = score_experience_rules(self._profile(7), self._jd(5), self._gap())
        assert result["timeline_score"] == 90
        assert "Continuous" in result["timeline_text"]

    def test_timeline_text_mentions_gaps(self):
        gaps = [{"severity": "critical", "duration_months": 14}]
        result = score_experience_rules(self._profile(5), self._jd(5), self._gap(gaps=gaps))
        assert "gap" in result["timeline_text"].lower()

    def test_timeline_score_floor_10(self):
        many_gaps = [{"severity": "critical"}] * 10
        result = score_experience_rules(self._profile(0), self._jd(5), self._gap(gaps=many_gaps))
        assert result["timeline_score"] >= 10


# ═══════════════════════════════════════════════════════════════════════════════
# Component 6: domain_architecture_rules
# ═══════════════════════════════════════════════════════════════════════════════

class TestDomainArchitectureRules:

    def test_high_domain_match(self):
        text = ("Worked with FastAPI, PostgreSQL, Redis, microservices, REST API, Docker, "
                "Kubernetes. Backend developer with 7 years experience. ")
        result = domain_architecture_rules(text * 3, "backend", "Senior Backend Engineer")
        assert result["domain_score"] >= 60

    def test_wrong_domain_low_score(self):
        text = ("Mobile app developer building iOS and Android apps with Flutter and Swift. "
                "App Store deployments, push notifications, Xcode. ")
        result = domain_architecture_rules(text * 3, "backend", "iOS Developer")
        assert result["domain_score"] < 60  # no backend keywords

    def test_architecture_signals_increase_score(self):
        text_basic = "Developed features using Python and FastAPI. "
        text_arch  = ("Architected microservices, led design reviews, scalable distributed "
                      "system, tech lead, mentored team, system design. ")
        r_basic = domain_architecture_rules(text_basic * 5, "backend", None)
        r_arch  = domain_architecture_rules(text_arch  * 5, "backend", None)
        assert r_arch["arch_score"] > r_basic["arch_score"]

    def test_current_role_bonus(self):
        text = "FastAPI REST API microservices PostgreSQL. " * 5
        r_match    = domain_architecture_rules(text, "backend", "Backend Engineer")
        r_mismatch = domain_architecture_rules(text, "backend", "Marketing Manager")
        assert r_match["domain_score"] >= r_mismatch["domain_score"]


# ═══════════════════════════════════════════════════════════════════════════════
# Component 7: compute_fit_score
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeFitScore:

    def _scores(self, **kwargs):
        defaults = {
            "skill_score": 80, "exp_score": 75, "arch_score": 70,
            "edu_score": 70, "timeline_score": 85, "domain_score": 70,
            "actual_years": 6, "required_years": 5,
            "matched_skills": ["python"], "missing_skills": [],
            "required_count": 1,
            "employment_gaps": [], "short_stints": [],
        }
        defaults.update(kwargs)
        return defaults

    def test_high_scores_shortlist(self):
        result = compute_fit_score(self._scores())
        assert result["final_recommendation"] == "Shortlist"
        assert result["fit_score"] >= 72

    def test_low_scores_reject(self):
        result = compute_fit_score(self._scores(
            skill_score=10, exp_score=10, arch_score=20,
            edu_score=30, timeline_score=30, domain_score=10,
            missing_skills=["python", "java", "kubernetes", "docker"],
            required_count=4,
        ))
        assert result["final_recommendation"] == "Reject"
        assert result["fit_score"] < 45

    def test_medium_scores_consider(self):
        result = compute_fit_score(self._scores(
            skill_score=50, exp_score=45, arch_score=45,
            edu_score=50, timeline_score=50, domain_score=50,
        ))
        assert result["final_recommendation"] in ("Consider", "Reject")

    def test_fit_score_clamped_0_100(self):
        result = compute_fit_score(self._scores(
            skill_score=200, exp_score=200,
        ))
        assert 0 <= result["fit_score"] <= 100

    def test_critical_gap_risk_signal(self):
        gaps = [{"severity": "critical", "duration_months": 14}]
        result = compute_fit_score(self._scores(employment_gaps=gaps))
        types = [r["type"] for r in result["risk_signals"]]
        assert "gap" in types

    def test_skill_gap_50pct_risk_high(self):
        result = compute_fit_score(self._scores(
            matched_skills=["python"],
            missing_skills=["java", "docker", "kubernetes"],
            required_count=4,
        ))
        gap_signals = [r for r in result["risk_signals"] if r["type"] == "skill_gap"]
        assert gap_signals
        assert gap_signals[0]["severity"] in ("high", "medium")

    def test_domain_mismatch_risk_signal(self):
        result = compute_fit_score(self._scores(domain_score=30))
        types = [r["type"] for r in result["risk_signals"]]
        assert "domain_mismatch" in types

    def test_overqualified_risk_signal(self):
        result = compute_fit_score(self._scores(actual_years=15, required_years=3))
        types = [r["type"] for r in result["risk_signals"]]
        assert "overqualified" in types

    def test_score_breakdown_contains_all_dimensions(self):
        result = compute_fit_score(self._scores())
        bd = result["score_breakdown"]
        assert "skill_match" in bd
        assert "experience_match" in bd
        assert "education" in bd
        assert "architecture" in bd
        assert "timeline" in bd
        assert "domain_fit" in bd

    def test_custom_weights_change_score(self):
        """Custom weights should produce a different score than default weights."""
        scores = self._scores(skill_score=100, exp_score=0)
        r_default = compute_fit_score(scores)
        r_custom = compute_fit_score(scores, {"skills": 0.10, "experience": 0.50,
                                               "architecture": 0.10, "education": 0.05,
                                               "timeline": 0.10, "domain": 0.05, "risk": 0.10})
        # Custom weights should shift the score toward experience (0) and away from skills (100)
        assert r_custom["fit_score"] != r_default["fit_score"]
        # With skills weighted low and experience weighted high (but score=0),
        # the custom result should be lower
        assert r_custom["fit_score"] < r_default["fit_score"]


# ═══════════════════════════════════════════════════════════════════════════════
# Quality assessment
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssessQuality:

    def test_high_quality(self):
        profile = {
            "skills_identified": ["python", "fastapi", "postgresql", "docker"],
            "education": [{"degree": "BSc"}],
            "total_effective_years": 5.0,
        }
        assert _assess_quality(profile) == "high"

    def test_low_quality_no_data(self):
        profile = {"skills_identified": [], "total_effective_years": 0, "education": []}
        assert _assess_quality(profile) == "low"

    def test_medium_quality_few_skills(self):
        profile = {
            "skills_identified": ["python"],
            "total_effective_years": 0,
            "education": [],
        }
        assert _assess_quality(profile) == "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# Fallback narrative
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildFallbackNarrative:

    def test_strengths_include_matched_skills(self):
        python_result = {"fit_score": 65, "score_breakdown": {}, "_required_years": 5}
        skill_analysis = {"matched_skills": ["python", "fastapi"], "missing_skills": [], "required_count": 2}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert result["strengths"]
        assert any("python" in s.lower() or "fastapi" in s.lower() for s in result["strengths"])

    def test_weaknesses_include_missing_skills(self):
        python_result = {"fit_score": 40, "score_breakdown": {}, "_required_years": 5}
        skill_analysis = {"matched_skills": [], "missing_skills": ["kubernetes", "terraform"], "required_count": 2}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert result["weaknesses"]
        assert any("kubernetes" in w.lower() or "terraform" in w.lower() for w in result["weaknesses"])

    def test_interview_questions_generated(self):
        from app.backend.services.hybrid_pipeline import _placeholder_interview_kit

        python_result = {
            "fit_score": 60,
            "score_breakdown": {},
            "_required_years": 3,
            "candidate_profile": {"name": "Alex"},
            "jd_analysis": {"role_title": "Engineer", "required_skills": ["python", "java", "spring"]},
            "skill_analysis": {"matched_required": ["python"], "missing_required": ["java", "spring"]},
        }
        iq = _placeholder_interview_kit(python_result)
        assert len(iq["technical_questions"]) >= 1
        assert len(iq["experience_deep_dive_questions"]) >= 0
        assert iq["culture_fit_questions"] == []
        total = (
            len(iq["technical_questions"])
            + len(iq["behavioral_questions"])
            + len(iq["experience_deep_dive_questions"])
        )
        assert 3 <= total <= 10
        texts = [q["text"] for q in iq["technical_questions"] + iq["experience_deep_dive_questions"]]
        assert all(len(t) <= 140 for t in texts)

    def test_fallback_narrative_has_no_interview_kit(self):
        python_result = {"fit_score": 60, "score_breakdown": {}, "_required_years": 3}
        skill_analysis = {"matched_skills": ["python"], "missing_skills": ["java", "spring"], "required_count": 3}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert "interview_questions" not in result

    def test_rationale_mentions_score(self):
        python_result = {"fit_score": 72, "score_breakdown": {}, "_required_years": 5}
        skill_analysis = {"matched_skills": ["python"], "missing_skills": [], "required_count": 1}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert "72" in result["recommendation_rationale"]

    def test_fallback_has_ai_enhanced_false(self):
        """Fallback narrative should have ai_enhanced=False to distinguish from LLM."""
        python_result = {"fit_score": 65, "score_breakdown": {}, "_required_years": 5}
        skill_analysis = {"matched_skills": ["python"], "missing_skills": [], "required_count": 1}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert result.get("ai_enhanced") is False

    def test_fallback_has_narrative_fallback_true(self):
        """Fallback narrative should have narrative_fallback=True for frontend indicator."""
        python_result = {"fit_score": 65, "score_breakdown": {}, "_required_years": 5}
        skill_analysis = {"matched_skills": ["python"], "missing_skills": [], "required_count": 1}
        result = _build_fallback_narrative(python_result, skill_analysis)
        assert result.get("narrative_fallback") is True

    def test_fallback_briefing_avoids_jd_wall_of_text(self):
        """Placeholder kit briefing must not paste full JD paragraphs into probes or questions."""
        from app.backend.services.hybrid_pipeline import _placeholder_interview_kit

        long_jd = (
            "You are a detail-oriented finance professional who thrives in a dynamic environment. "
            "With a strong foundation in financial planning and analysis, you possess curiosity "
            "and initiative to dig deep into data and uncover insights that drive business decisions."
        )
        python_result = {
            "fit_score": 40,
            "score_breakdown": {},
            "_required_years": 5,
            "candidate_profile": {"name": "Test User", "current_role": "Analyst", "total_effective_years": 8},
            "jd_analysis": {
                "key_responsibilities": [long_jd],
                "required_skills": ["excel"],
                "role_title": "FP&A Analyst",
            },
            "skill_analysis": {
                "matched_required": ["excel"],
                "missing_required": ["financial planning", "forecasting"],
            },
        }
        iq = _placeholder_interview_kit(python_result)
        briefing = iq["candidate_briefing"]
        assert "Fallback analysis" not in briefing["profile_snapshot"]
        assert all(len(p) < 80 for p in briefing["areas_to_probe"])
        for q in iq["technical_questions"]:
            assert long_jd not in q["text"]
            assert len(q["text"]) < 200


class TestMergeLlmIntoResult:
    """Tests for _merge_llm_into_result function."""

    def test_merge_includes_ai_enhanced_from_llm_result(self):
        """Merged result should include ai_enhanced field from LLM result."""
        python_result = {"fit_score": 75, "score_breakdown": {}, "skill_analysis": {}}
        llm_result = {
            "ai_enhanced": True,
            "fit_summary": "Great candidate",
            "strengths": ["Strong skills"],
            "concerns": [],
            "weaknesses": [],
        }
        merged = _merge_llm_into_result(python_result, llm_result)
        assert merged.get("ai_enhanced") is True

    def test_merge_includes_ai_enhanced_false_for_fallback(self):
        """Merged result should include ai_enhanced=False for fallback."""
        python_result = {"fit_score": 75, "score_breakdown": {}, "skill_analysis": {}}
        fallback_result = {
            "ai_enhanced": False,
            "fit_summary": "Fallback summary",
            "strengths": ["Some strengths"],
            "concerns": ["Some concerns"],
            "weaknesses": ["Some concerns"],
        }
        merged = _merge_llm_into_result(python_result, fallback_result)
        assert merged.get("ai_enhanced") is False



# ═══════════════════════════════════════════════════════════════════════════════
# Normalization helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeSkill:

    def test_lowercase(self):
        assert _normalize_skill("Python") == "python"

    def test_dot_replaced(self):
        assert _normalize_skill("Node.js") == "node js"

    def test_dash_replaced(self):
        assert _normalize_skill("ci-cd") == "ci cd"

    def test_csharp_preserved(self):
        assert _normalize_skill("C#") == "c#"

    def test_cpp_preserved(self):
        assert _normalize_skill("C++") == "c++"

    def test_multiple_spaces_collapsed(self):
        # _normalize_skill strips surrounding whitespace and collapses internal
        # runs of spaces to a single space
        assert _normalize_skill("  react   native  ") == "react native"


class TestExpandSkill:

    def test_js_expands_to_javascript(self):
        expanded = _expand_skill("javascript")
        assert "js" in expanded

    def test_postgres_in_postgresql_aliases(self):
        expanded = _expand_skill("postgresql")
        assert "postgres" in expanded

    def test_no_duplicates(self):
        expanded = _expand_skill("python")
        assert len(expanded) == len(set(expanded))


# ═══════════════════════════════════════════════════════════════════════════════
# Component 8: explain_with_llm (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLlmResponseNeedsCompactRetry:

    def test_accepts_markdown_fenced_json(self):
        from app.backend.services.hybrid_pipeline import _llm_response_needs_compact_retry

        payload = json.dumps({"fit_summary": "Strong fit", "strengths": ["Python"]})
        wrapped = f"```json\n{payload}\n```"
        assert _llm_response_needs_compact_retry(wrapped) is False

    def test_needs_retry_when_json_incomplete(self):
        from app.backend.services.hybrid_pipeline import _llm_response_needs_compact_retry

        assert _llm_response_needs_compact_retry('{"fit_summary": "partial') is True

    def test_needs_retry_when_empty(self):
        from app.backend.services.hybrid_pipeline import _llm_response_needs_compact_retry

        assert _llm_response_needs_compact_retry("") is True


class TestBindNumPredict:

    def test_uses_options_not_top_level_kwarg(self):
        from app.backend.services.hybrid_pipeline import _bind_num_predict

        llm = MagicMock()
        llm.num_ctx = 8192
        llm.temperature = 0.1
        bound = MagicMock()
        llm.bind.return_value = bound
        result = _bind_num_predict(llm, 1500)
        llm.bind.assert_called_once()
        kwargs = llm.bind.call_args.kwargs
        assert "num_predict" not in kwargs
        assert kwargs["options"]["num_predict"] == 1500
        assert kwargs["options"]["num_ctx"] == 8192
        assert result is bound


class TestExplainWithLlm:

    @pytest.mark.asyncio
    async def test_llm_result_parsed(self):
        from app.backend.services.hybrid_pipeline import explain_with_llm

        mock_response_json = json.dumps({
            "strengths": ["Strong Python skills", "Good architecture background"],
            "weaknesses": ["Limited Kubernetes experience"],
            "recommendation_rationale": "Good fit for the role.",
            "explainability": {
                "skill_rationale": "Matches core skills.",
                "experience_rationale": "7 years is above the required 5.",
                "overall_rationale": "Solid candidate.",
            },
            "interview_questions": {
                "technical_questions":   ["Describe your Python async experience."],
                "behavioral_questions":  ["Tell me about a project you led."],
                "culture_fit_questions": ["What motivates you?"],
            },
        })

        mock_llm_response = MagicMock()
        mock_llm_response.content = mock_response_json

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_llm_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        with patch("app.backend.services.hybrid_pipeline._get_llm", return_value=mock_llm):
            context = {
                "jd_analysis": {"role_title": "Python Engineer", "domain": "backend",
                                  "seniority": "senior"},
                "candidate_profile": {"name": "Jane Doe", "total_effective_years": 7,
                                       "current_role": "Engineer", "current_company": "Tech",
                                       "career_summary": "Engineer at Tech — 7y"},
                "skill_analysis": {"matched_skills": ["python"], "missing_skills": ["kubernetes"]},
                "scores": {"skill_score": 80, "exp_score": 85, "edu_score": 70,
                           "timeline_score": 85, "fit_score": 79, "final_recommendation": "Shortlist"},
            }
            result = await explain_with_llm(context)

        assert "strengths" in result
        assert len(result["strengths"]) == 2
        assert "weaknesses" in result
        assert "interview_questions" not in result

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_synthesized_narrative(self):
        from app.backend.services.hybrid_pipeline import explain_with_llm

        with patch(
            "app.backend.services.llm_json_service.invoke_llm_json_resilient",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await explain_with_llm({
                "jd_analysis": {"role_title": "Engineer"},
                "candidate_profile": {"name": "Alex"},
                "skill_analysis": {"matched_required": [], "missing_required": []},
                "scores": {"fit_score": 55, "final_recommendation": "Consider"},
            })

        assert result.get("fit_summary")
        assert result.get("ai_enhanced") is False


import json


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end: run_hybrid_pipeline (mocked LLM)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunHybridPipeline:

    def _parsed_data(self):
        return {
            "raw_text": (
                "John Doe | john@example.com | +1-555-0100\n"
                "Senior Python Developer with 7 years experience.\n"
                "Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes\n"
                "Education: BSc Computer Science, MIT, 2014\n"
                "Experience:\n"
                "  Senior Engineer, TechCorp, Jan 2019 – Present\n"
                "  Software Developer, StartupInc, Jun 2016 – Dec 2018\n"
            ),
            "work_experience": [
                {"title": "Senior Engineer", "company": "TechCorp",
                 "start_date": "Jan 2019", "end_date": "present"},
                {"title": "Software Developer", "company": "StartupInc",
                 "start_date": "Jun 2016", "end_date": "Dec 2018"},
            ],
            "skills": ["python", "fastapi", "postgresql", "docker", "kubernetes"],
            "education": [{"degree": "BSc Computer Science", "field": "computer science",
                           "university": "MIT", "year": "2014"}],
            "contact_info": {"name": "John Doe", "email": "john@example.com"},
        }

    def _gap_analysis(self):
        return {
            "employment_timeline": [],
            "employment_gaps": [],
            "overlapping_jobs": [],
            "short_stints": [],
            "total_years": 7.0,
        }

    def _jd_text(self):
        return (
            "Senior Python Backend Engineer\n"
            "We are looking for a Senior Python Engineer with 5+ years experience.\n"
            "Required: Python, FastAPI, PostgreSQL, Docker, Kubernetes, REST API.\n"
            "Nice to have: Redis, Elasticsearch, Terraform.\n"
            "You will design and build scalable microservices and mentor junior engineers.\n"
            "Responsibilities include system design, code reviews, and technical leadership.\n"
            "Strong understanding of distributed systems and high availability is required.\n"
        )

    @pytest.mark.asyncio
    async def test_full_pipeline_returns_fit_score(self):
        mock_llm_json = json.dumps({
            "strengths": ["Strong Python"],
            "weaknesses": ["Limited Redis"],
            "recommendation_rationale": "Good match.",
            "explainability": {"skill_rationale": "", "experience_rationale": "", "overall_rationale": ""},
            "interview_questions": {
                "technical_questions": ["Question 1"],
                "behavioral_questions": ["Behavioral 1"],
                "culture_fit_questions": ["Culture 1"],
            },
        })
        mock_resp = MagicMock()
        mock_resp.content = mock_llm_json

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        with patch("app.backend.services.hybrid_pipeline._get_llm", return_value=mock_llm), patch(
            "app.backend.services.hybrid_pipeline.explain_with_llm",
            new=AsyncMock(return_value={
                "strengths": ["Strong Python"],
                "weaknesses": ["Limited Redis"],
                "recommendation_rationale": "Good match.",
                "explainability": {"skill_rationale": "", "experience_rationale": "", "overall_rationale": ""},
                "interview_questions": {
                    "technical_questions": ["Question 1"],
                    "behavioral_questions": ["Behavioral 1"],
                    "culture_fit_questions": ["Culture 1"],
                },
                "ai_enhanced": True,
            }),
        ):
            result = await run_hybrid_pipeline(
                resume_text=self._parsed_data()["raw_text"],
                job_description=self._jd_text(),
                parsed_data=self._parsed_data(),
                gap_analysis=self._gap_analysis(),
                jd_analysis={
                    "role_title": "Senior Python Backend Engineer",
                    "domain": "backend",
                    "required_skills": ["python", "fastapi"],
                    "_profile_source": "merged",
                },
            )

        assert isinstance(result["fit_score"], int)
        assert 0 <= result["fit_score"] <= 100
        assert result["final_recommendation"] in ("Shortlist", "Consider", "Reject")
        assert isinstance(result["matched_skills"], list)
        assert isinstance(result["missing_skills"], list)
        assert result["analysis_quality"] in ("high", "medium", "low")
        assert result["interview_questions"] is not None

    @pytest.mark.asyncio
    async def test_pipeline_fallback_on_llm_timeout(self):
        """Pipeline must return a valid result even when LLM times out."""
        async def _slow_explain(*_args, **_kwargs):
            await asyncio.sleep(100)

        with patch("app.backend.services.hybrid_pipeline.explain_with_llm",
                   side_effect=asyncio.TimeoutError()):
            result = await run_hybrid_pipeline(
                resume_text=self._parsed_data()["raw_text"],
                job_description=self._jd_text(),
                parsed_data=self._parsed_data(),
                gap_analysis=self._gap_analysis(),
                jd_analysis={
                    "role_title": "Senior Python Backend Engineer",
                    "domain": "backend",
                    "required_skills": ["python"],
                    "_profile_source": "merged",
                },
            )

        # Must always return scores — never raise
        assert isinstance(result["fit_score"], int)
        assert result["narrative_pending"] is True
        assert result["final_recommendation"] in ("Shortlist", "Consider", "Reject")

    @pytest.mark.asyncio
    async def test_precomputed_jd_analysis_skips_parse(self):
        """If jd_analysis is pre-provided, parse_jd_rules should not be called."""
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "strengths": [], "weaknesses": [], "recommendation_rationale": "",
            "explainability": {"skill_rationale": "", "experience_rationale": "", "overall_rationale": ""},
            "interview_questions": {"technical_questions": [], "behavioral_questions": [], "culture_fit_questions": []},
        })
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        prebuilt_jd = {
            "role_title": "Pre-built", "domain": "backend", "seniority": "senior",
            "required_skills": ["python"], "required_years": 5,
            "nice_to_have_skills": [], "key_responsibilities": [],
            "_profile_source": "merged",
        }

        with patch("app.backend.services.hybrid_pipeline._get_llm", return_value=mock_llm):
            with patch("app.backend.services.hybrid_pipeline.parse_jd_rules") as mock_parse:
                with patch("app.backend.services.hybrid_pipeline.explain_with_llm", new=AsyncMock(return_value={
                    "strengths": [], "weaknesses": [], "recommendation_rationale": "",
                    "explainability": {}, "interview_questions": {},
                })):
                    result = await run_hybrid_pipeline(
                        resume_text="python developer 5 years experience",
                        job_description="this text should not be parsed",
                        parsed_data=self._parsed_data(),
                        gap_analysis=self._gap_analysis(),
                        jd_analysis=prebuilt_jd,
                    )
                mock_parse.assert_not_called()

        assert result["job_role"] == "Pre-built"


# ═══════════════════════════════════════════════════════════════════════════════
# Domain-agnostic regression tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDomainAgnosticPipeline:
    """Ensure non-tech JDs are scored fairly using dynamic LLM-extracted profiles."""

    def _parsed_data(self, resume_text):
        return {
            "raw_text": resume_text,
            "contact_info": {"name": "Jane Doe", "email": "jane@example.com"},
            "skills": ["SAP", "SAP MM", "Inventory Management", "Procurement", "Vendor Management"],
            "education": [{"degree": "MBA", "field": "Supply Chain Management"}],
            "work_experience": [
                {
                    "title": "SAP MM Consultant",
                    "company": "Acme Logistics",
                    "start_date": "2018-01",
                    "end_date": "2024-01",
                    "description": "Implemented SAP MM, managed procurement and inventory.",
                }
            ],
            "career_summary": "SAP MM Consultant with 6 years of experience.",
        }

    def _gap_analysis(self):
        return {
            "employment_gaps": [],
            "short_stints": [],
            "overlapping_jobs": [],
            "total_years": 6.0,
        }

    @pytest.mark.asyncio
    async def test_non_tech_jd_uses_dynamic_domain_keywords(self):
        """A SAP/MM resume should not be rejected by static tech keyword lists."""
        jd_text = (
            "SAP MM Consultant\n"
            "We need an SAP MM Consultant with 5+ years of experience in SAP MM, "
            "procurement, inventory management, and vendor management."
        )
        resume_text = (
            "Jane Doe\n"
            "SAP MM Consultant with 6 years of experience.\n"
            "Expert in SAP MM, procurement, inventory management, vendor management.\n"
            "Implemented SAP MM modules at Acme Logistics."
        )

        mock_llm_profile = {
            "role_title": "SAP MM Consultant",
            "domain": "SAP/ERP",
            "domain_keywords": ["sap", "sap mm", "procurement", "inventory management", "vendor management"],
            "architecture_signals": ["implemented", "led", "managed", "configured"],
            "relevant_education_fields": ["supply chain", "business administration", "information systems"],
            "required_skills": ["sap mm", "procurement", "inventory management"],
            "nice_to_have_skills": ["vendor management"],
            "min_required_years": 5,
            "max_required_years": 8,
            "seniority": "senior",
        }

        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "strengths": ["Strong SAP MM experience"],
            "weaknesses": [],
            "recommendation_rationale": "Good fit.",
            "explainability": {"skill_rationale": "", "experience_rationale": "", "overall_rationale": ""},
            "interview_questions": {"technical_questions": [], "behavioral_questions": [], "culture_fit_questions": []},
        })
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

        with patch("app.backend.services.hybrid_pipeline._get_llm", return_value=mock_llm):
            with patch("app.backend.services.jd_profile_service.extract_jd_profile", new=AsyncMock(return_value=mock_llm_profile)):
                with patch("app.backend.services.hybrid_pipeline.explain_with_llm", new=AsyncMock(return_value={
                    "strengths": ["Strong SAP MM experience"],
                    "weaknesses": [],
                    "recommendation_rationale": "Good fit.",
                    "explainability": {},
                    "interview_questions": {},
                    "ai_enhanced": True,
                })):
                    result = await run_hybrid_pipeline(
                        resume_text=resume_text,
                        job_description=jd_text,
                        parsed_data=self._parsed_data(resume_text),
                        gap_analysis=self._gap_analysis(),
                    )

        # A qualified SAP/MM candidate should not be hard-rejected by a tech-only cap
        assert result["fit_score"] > 40
        assert result["final_recommendation"] in ("Shortlist", "Consider")
        assert result["job_role"] == "SAP MM Consultant"


# ==============================================================================
# Layer 1: JD Skills Injected into confirm_skills_in_text
# ==============================================================================

class TestLayer1JDSkillInjection:
    """Tests that JD required_skills are injected into gap_analysis so
    confirm_skills_in_text can find domain-specific skills in the resume text."""

    def test_confirm_skills_finds_jd_skills_in_resume_text(self):
        """Layer 1: JD skills like 'Material Master' and 'LSMW' should be
        confirmed from resume text even when not in the static FlashText registry."""
        parsed_data = {
            "raw_text": (
                "Kalpana P\n"
                "SAP MM Consultant with 9 years of experience.\n"
                "Configured Material Master, Vendor Master, and Source Determination.\n"
                "Used LSMW for data migration and BAPI for integration.\n"
                "Managed cutover activities during S/4HANA migration."
            ),
            "skills": ["SAP MM"],
            "work_experience": [
                {
                    "title": "SAP MM Consultant",
                    "company": "TechCorp",
                    "start_date": "2015-01",
                    "end_date": "2024-01",
                    "description": "SAP MM implementation and data migration.",
                }
            ],
            "contact_info": {"name": "Kalpana P", "email": "kalpana@example.com"},
            "education": [],
        }
        gap_analysis = {"total_years": 9.0, "employment_gaps": [], "short_stints": []}
        jd_analysis = {
            "required_skills": ["SAP MM", "Material Master", "Vendor Master", "LSMW", "ABAP"],
            "nice_to_have_skills": ["SAP Certification"],
            "min_required_years": 5,
            "role_title": "SAP MM Consultant",
            "domain": "SAP/ERP",
            "domain_keywords": ["sap mm", "material master", "lsmw"],
            "seniority": "senior",
        }

        result = _run_python_phase(
            resume_text=parsed_data["raw_text"],
            job_description="SAP MM Consultant with 5+ years experience",
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=None,
            jd_analysis=jd_analysis,
        )

        skills = result["candidate_profile"]["skills_identified"]
        skills_lower = [s.lower() for s in skills]
        # Material Master, Vendor Master, LSMW should be confirmed from text
        assert "material master" in skills_lower
        assert "vendor master" in skills_lower
        assert "lsmw" in skills_lower

    def test_confirm_skills_does_not_mutate_caller_gap_analysis(self):
        """Layer 1: The injection should not mutate the caller's gap_analysis dict."""
        parsed_data = {
            "raw_text": "Python developer with Django and Flask experience.",
            "skills": ["Python"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 3.0, "employment_gaps": []}
        original_keys = set(gap_analysis.keys())

        jd_analysis = {
            "required_skills": ["Python", "Django", "Flask"],
            "nice_to_have_skills": [],
            "role_title": "Python Developer",
            "domain": "backend",
            "min_required_years": 2,
        }

        _run_python_phase(
            resume_text=parsed_data["raw_text"],
            job_description="Python Developer",
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=None,
            jd_analysis=jd_analysis,
        )

        # The caller's dict should not have been mutated
        assert set(gap_analysis.keys()) == original_keys
        assert "required_skills" not in gap_analysis

    def test_confirm_skills_with_empty_jd_skills(self):
        """Layer 1: Empty JD skills should not cause errors."""
        parsed_data = {
            "raw_text": "Software engineer with Java experience.",
            "skills": ["Java"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 2.0, "employment_gaps": []}
        jd_analysis = {
            "required_skills": [],
            "nice_to_have_skills": [],
            "role_title": "Software Engineer",
            "domain": "backend",
            "min_required_years": 1,
        }

        result = _run_python_phase(
            resume_text=parsed_data["raw_text"],
            job_description="Software Engineer",
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=None,
            jd_analysis=jd_analysis,
        )

        # Should not crash, and Java should still be in skills
        skills = result["candidate_profile"]["skills_identified"]
        skills_lower = [s.lower() for s in skills]
        assert "java" in skills_lower


# ==============================================================================
# Layer 2: LLM Resume Skill Extraction and Merging
# ==============================================================================

class TestLayer2LLMResumeSkills:
    """Tests for LLM-extracted resume skill merging into parse_resume_rules."""

    def test_llm_resume_skills_merged_into_skills_identified(self):
        """Layer 2: LLM-extracted skills should be merged into skills_identified."""
        parsed_data = {
            "raw_text": (
                "SAP MM Consultant. Implemented P2P, configured BAPI, managed cutover."
            ),
            "skills": ["SAP MM"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 9.0}
        llm_skills = ["SAP MM", "Procure-to-Pay", "BAPI", "Cutover", "SAP S/4HANA"]

        profile = parse_resume_rules(parsed_data, gap_analysis, llm_resume_skills=llm_skills)

        skills_lower = [s.lower() for s in profile["skills_identified"]]
        assert "procure-to-pay" in skills_lower
        assert "bapi" in skills_lower
        assert "cutover" in skills_lower
        assert "sap s/4hana" in skills_lower

    def test_llm_resume_skills_deduplicated(self):
        """Layer 2: LLM skills should not create duplicates with existing skills."""
        parsed_data = {
            "raw_text": "SAP MM Consultant with procurement experience.",
            "skills": ["SAP MM", "Procurement"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 5.0}
        # LLM returns "SAP MM" and "Procurement" which already exist
        llm_skills = ["SAP MM", "Procurement", "Material Master", "LSMW"]

        profile = parse_resume_rules(parsed_data, gap_analysis, llm_resume_skills=llm_skills)

        skills = profile["skills_identified"]
        # Check no duplicates (case-insensitive)
        skills_lower = [s.lower() for s in skills]
        assert len(skills_lower) == len(set(skills_lower))
        # New skills should be added
        assert "material master" in skills_lower
        assert "lsmw" in skills_lower

    def test_llm_resume_skills_none_does_not_break(self):
        """Layer 2: Passing None for llm_resume_skills should work as before."""
        parsed_data = {
            "raw_text": "Python developer with Django experience.",
            "skills": ["Python", "Django"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 3.0}

        profile = parse_resume_rules(parsed_data, gap_analysis, llm_resume_skills=None)

        skills_lower = [s.lower() for s in profile["skills_identified"]]
        assert "python" in skills_lower
        assert "django" in skills_lower

    def test_llm_resume_skills_empty_list_does_not_break(self):
        """Layer 2: Empty list for llm_resume_skills should work as before."""
        parsed_data = {
            "raw_text": "Java developer with Spring experience.",
            "skills": ["Java", "Spring"],
            "work_experience": [],
            "contact_info": {},
            "education": [],
        }
        gap_analysis = {"total_years": 2.0}

        profile = parse_resume_rules(parsed_data, gap_analysis, llm_resume_skills=[])

        skills_lower = [s.lower() for s in profile["skills_identified"]]
        assert "java" in skills_lower
        assert "spring" in skills_lower

    def test_layer1_and_layer2_combined(self):
        """Both layers together: JD skills injected (Layer 1) + LLM skills merged (Layer 2)."""
        parsed_data = {
            "raw_text": (
                "Kalpana P\n"
                "SAP MM Consultant with 9 years of experience.\n"
                "Configured Material Master and Vendor Master.\n"
                "Used LSMW for data migration."
            ),
            "skills": ["SAP MM"],
            "work_experience": [
                {
                    "title": "SAP MM Consultant",
                    "company": "TechCorp",
                    "start_date": "2015-01",
                    "end_date": "2024-01",
                    "description": "SAP MM implementation.",
                }
            ],
            "contact_info": {"name": "Kalpana P"},
            "education": [],
        }
        gap_analysis = {"total_years": 9.0, "employment_gaps": [], "short_stints": []}
        jd_analysis = {
            "required_skills": ["SAP MM", "Material Master", "Vendor Master", "LSMW", "ABAP"],
            "nice_to_have_skills": ["SAP Certification"],
            "min_required_years": 5,
            "role_title": "SAP MM Consultant",
            "domain": "SAP/ERP",
            "domain_keywords": ["sap mm", "material master", "lsmw"],
            "seniority": "senior",
        }
        llm_skills = ["SAP MM", "Procure-to-Pay", "BAPI", "SAP S/4HANA", "Cutover"]

        result = _run_python_phase(
            resume_text=parsed_data["raw_text"],
            job_description="SAP MM Consultant with 5+ years experience",
            parsed_data=parsed_data,
            gap_analysis=gap_analysis,
            scoring_weights=None,
            jd_analysis=jd_analysis,
            llm_resume_skills=llm_skills,
        )

        skills = result["candidate_profile"]["skills_identified"]
        skills_lower = [s.lower() for s in skills]

        # Layer 1: confirmed from text
        assert "material master" in skills_lower
        assert "vendor master" in skills_lower
        assert "lsmw" in skills_lower

        # Layer 2: from LLM extraction
        assert "procure-to-pay" in skills_lower
        assert "bapi" in skills_lower
        assert "sap s/4hana" in skills_lower
        assert "cutover" in skills_lower

        # No duplicates
        assert len(skills_lower) == len(set(skills_lower))

        # Core skill match should be significantly improved
        skill_analysis = result["skill_analysis"]
        core_match = skill_analysis.get("core_match_ratio", 0)
        assert core_match > 0.5, f"Expected core_match_ratio > 0.5, got {core_match}"


# ==============================================================================
# Layer 2: LLM Service — extract_resume_skills
# ==============================================================================

class TestLLMResumeSkillExtraction:
    """Tests for the LLMService.extract_resume_skills method and parser."""

    def test_parse_resume_skills_response_valid_json(self):
        """_parse_resume_skills_response should parse a valid JSON array."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()
        response = '["SAP MM", "SAP S/4HANA", "Procure-to-Pay", "LSMW"]'
        skills = svc._parse_resume_skills_response(response)
        assert skills == ["SAP MM", "SAP S/4HANA", "Procure-to-Pay", "LSMW"]

    def test_parse_resume_skills_response_with_surrounding_text(self):
        """_parse_resume_skills_response should extract JSON array from surrounding text."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()
        response = 'Here are the skills:\n["Python", "Django", "PostgreSQL"]\nDone.'
        skills = svc._parse_resume_skills_response(response)
        assert "Python" in skills
        assert "Django" in skills
        assert "PostgreSQL" in skills

    def test_parse_resume_skills_response_empty(self):
        """_parse_resume_skills_response should return empty list for invalid input."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()
        skills = svc._parse_resume_skills_response("")
        assert skills == []

    def test_parse_resume_skills_response_fallback_quoted(self):
        """_parse_resume_skills_response should fall back to quoted string extraction."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()
        response = 'I found "SAP MM" and "Material Master" in the resume.'
        skills = svc._parse_resume_skills_response(response)
        assert "SAP MM" in skills
        assert "Material Master" in skills

    @pytest.mark.asyncio
    async def test_extract_resume_skills_empty_text(self):
        """extract_resume_skills should return empty list for empty resume text."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()
        skills = await svc.extract_resume_skills("")
        assert skills == []

    @pytest.mark.asyncio
    async def test_extract_resume_skills_with_llm_failure(self):
        """extract_resume_skills should return empty list on LLM failure."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()

        with patch.object(svc, "_call_ollama", side_effect=Exception("LLM unavailable")):
            skills = await svc.extract_resume_skills("Some resume text here")
            assert skills == []

    @pytest.mark.asyncio
    async def test_extract_resume_skills_with_mocked_llm(self):
        """extract_resume_skills should parse skills from LLM response."""
        from app.backend.services.llm_service import LLMService
        svc = LLMService()

        mock_response = '["SAP MM", "SAP S/4HANA", "Procure-to-Pay", "LSMW", "BAPI"]'
        with patch.object(svc, "_call_ollama_local", new=AsyncMock(return_value=mock_response)):
            skills = await svc.extract_resume_skills("SAP MM Consultant resume text")
            assert "SAP MM" in skills
            assert "SAP S/4HANA" in skills
            assert "Procure-to-Pay" in skills
            assert "LSMW" in skills
            assert "BAPI" in skills
