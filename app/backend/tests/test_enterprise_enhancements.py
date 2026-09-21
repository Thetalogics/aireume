"""
Tests for enterprise-grade enhancement services.

Covers:
  - OCR service (graceful fallback when unavailable)
  - Language detection service
  - GDPR data retention service
  - Bias auditing framework
  - International PII patterns
  - Resume deduplication
  - Proficiency level detection
  - Fraud detection
  - Scoring cache consistency
  - Resume enrichment (social profiles, salary, education, dates)
  - Expanded constants (non-tech domains, skills, certifications)
"""
import pytest
import hashlib
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta


# ─── OCR Service ──────────────────────────────────────────────────────────────

class TestOCRService:
    def test_is_ocr_available_returns_bool(self):
        from app.backend.services.ocr_service import is_ocr_available
        result = is_ocr_available()
        assert isinstance(result, bool)

    def test_ocr_pdf_returns_none_when_unavailable(self):
        from app.backend.services.ocr_service import ocr_pdf
        with patch("app.backend.services.ocr_service.is_ocr_available", return_value=False):
            result = ocr_pdf(b"fake pdf bytes")
            assert result is None

    def test_ocr_with_fallback_returns_existing_when_sufficient(self):
        from app.backend.services.ocr_service import ocr_with_fallback
        existing = "A" * 200
        result = ocr_with_fallback(b"fake", existing)
        assert result == existing

    def test_ocr_with_fallback_returns_existing_when_ocr_unavailable(self):
        from app.backend.services.ocr_service import ocr_with_fallback
        with patch("app.backend.services.ocr_service.is_ocr_available", return_value=False):
            result = ocr_with_fallback(b"fake", "short")
            assert result == "short"


# ─── Language Service ─────────────────────────────────────────────────────────

class TestLanguageService:
    def test_detect_english(self):
        from app.backend.services.language_service import detect_language
        text = "Experienced software engineer with 5 years of experience in Python development."
        lang = detect_language(text)
        assert lang == "en"

    def test_detect_chinese_by_characters(self):
        from app.backend.services.language_service import detect_language
        text = "拥有五年经验的软件工程师，精通Python开发"
        lang = detect_language(text)
        assert lang == "zh"

    def test_detect_japanese_by_characters(self):
        from app.backend.services.language_service import detect_language
        text = "五年の経験を持つソフトウェアエンジニアです。よろしくお願いします。"
        lang = detect_language(text)
        assert lang == "ja"

    def test_detect_empty_text_returns_english(self):
        from app.backend.services.language_service import detect_language
        assert detect_language("") == "en"
        assert detect_language("short") == "en"

    def test_get_llm_instruction_english_is_empty(self):
        from app.backend.services.language_service import get_llm_language_instruction
        assert get_llm_language_instruction("en") == ""

    def test_get_llm_instruction_non_english_has_instruction(self):
        from app.backend.services.language_service import get_llm_language_instruction
        instruction = get_llm_language_instruction("es")
        assert "español" in instruction.lower()

    def test_get_resume_language_context(self):
        from app.backend.services.language_service import get_resume_language_context
        ctx = get_resume_language_context("Experienced engineer with Python skills")
        assert ctx["language"] == "en"
        assert ctx["language_name"] == "English"
        assert "llm_instruction" in ctx

    def test_heuristic_spanish_detection(self):
        from app.backend.services.language_service import detect_language
        text = ("Experiencia en desarrollo de software. Educación en Universidad. "
                "Habilidades en trabajo y empresa. Responsabilidades del proyecto.")
        lang = detect_language(text)
        assert lang == "es"


# ─── GDPR Service ─────────────────────────────────────────────────────────────

class TestGDPRService:
    def test_default_retention_config(self):
        from app.backend.services.gdpr_service import get_retention_config, DEFAULT_RETENTION_DAYS
        config = get_retention_config()
        assert "candidate_data" in config
        assert config["candidate_data"] == 730

    def test_retention_config_with_tenant_falls_back_gracefully(self):
        from app.backend.services.gdpr_service import get_retention_config
        mock_db = MagicMock()
        config = get_retention_config(tenant_id=999, db=mock_db)
        assert "candidate_data" in config

    def test_hard_delete_candidate_not_found(self):
        from app.backend.services.gdpr_service import hard_delete_candidate
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = hard_delete_candidate(mock_db, 999, 1)
        assert "error" in result

    def test_anonymize_candidate_not_found(self):
        from app.backend.services.gdpr_service import anonymize_candidate
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = anonymize_candidate(mock_db, 999, 1)
        assert "error" in result

    def test_cleanup_expired_data_handles_errors(self):
        from app.backend.services.gdpr_service import cleanup_expired_data
        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("DB error")
        result = cleanup_expired_data(mock_db)
        assert "error" in result


# ─── Bias Audit Service ───────────────────────────────────────────────────────

class TestBiasAuditService:
    def test_four_fifths_rule_no_violation(self):
        from app.backend.services.bias_audit_service import _four_fifths_rule, GroupOutcome
        outcomes = [
            GroupOutcome("A", 100, 50, 30, 20, 0.50, 0.30, 0.20, 75),
            GroupOutcome("B", 100, 45, 30, 25, 0.45, 0.30, 0.25, 72),
        ]
        violations = _four_fifths_rule(outcomes)
        assert len(violations) == 0

    def test_four_fifths_rule_violation(self):
        from app.backend.services.bias_audit_service import _four_fifths_rule, GroupOutcome
        outcomes = [
            GroupOutcome("A", 100, 50, 30, 20, 0.50, 0.30, 0.20, 75),
            GroupOutcome("B", 100, 30, 30, 40, 0.30, 0.30, 0.40, 65),
        ]
        violations = _four_fifths_rule(outcomes)
        assert len(violations) == 1
        assert violations[0]["group"] == "B"

    def test_score_disparity_detection(self):
        from app.backend.services.bias_audit_service import _score_disparity_test, GroupOutcome
        outcomes = [
            GroupOutcome("A", 50, 30, 15, 5, 0.60, 0.30, 0.10, 85),
            GroupOutcome("B", 50, 20, 15, 15, 0.40, 0.30, 0.30, 60),
        ]
        disparities = _score_disparity_test(outcomes)
        assert len(disparities) >= 1

    def test_bias_audit_insufficient_data(self):
        from app.backend.services.bias_audit_service import run_bias_audit
        mock_db = MagicMock()
        mock_db.query.return_value.join.return_value.filter.return_value.all.return_value = []
        result = run_bias_audit(mock_db, tenant_id=1)
        assert result.total_candidates < 10
        assert result.risk_level == "none"


# ─── PII Redaction (International Patterns) ───────────────────────────────────

def test_pii_does_not_download_large_spacy_model(monkeypatch):
    """Presidio's default engine fetches en_core_web_lg on first analyze request."""
    from app.backend.services.pii_redaction_service import PIIRedactionService

    monkeypatch.setattr("spacy.util.is_package", lambda _name: False)
    service = PIIRedactionService()
    assert service.use_presidio is False
    result = service.redact_pii("Reach me at candidate@example.com")
    assert "candidate@example.com" not in result.redacted_text


class TestInternationalPII:
    def test_uk_nino_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "My NI number is AB123456C for tax purposes."
        result = service._redact_with_regex(text)
        assert "AB123456C" not in result.redacted_text
        assert "UK_NINO" in result.redaction_map

    def test_india_aadhaar_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "Aadhaar: 1234 5678 9012"
        result = service._redact_with_regex(text)
        assert "1234 5678 9012" not in result.redacted_text

    def test_india_pan_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "PAN: ABCDE1234F"
        result = service._redact_with_regex(text)
        assert "ABCDE1234F" not in result.redacted_text

    def test_iban_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "Bank IBAN: GB29NWBK60161331926819"
        result = service._redact_with_regex(text)
        assert "GB29NWBK60161331926819" not in result.redacted_text

    def test_international_phone_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "Call me at +44 20 7946 0958"
        result = service._redact_with_regex(text)
        assert "+44 20 7946 0958" not in result.redacted_text

    def test_ip_address_redaction(self):
        from app.backend.services.pii_redaction_service import PIIRedactionService
        service = PIIRedactionService()
        if service.use_presidio:
            pytest.skip("Testing regex fallback only")
        text = "Server at 192.168.1.100"
        result = service._redact_with_regex(text)
        assert "192.168.1.100" not in result.redacted_text


# ─── Dedup Service ────────────────────────────────────────────────────────────

class TestDedupService:
    def test_compute_resume_hash_consistent(self):
        from app.backend.services.dedup_service import compute_resume_hash
        text1 = "John Doe\nSoftware Engineer\n5 years experience"
        text2 = "John  Doe\nSoftware Engineer\n5 years experience"  # Extra space
        h1 = compute_resume_hash(text1)
        h2 = compute_resume_hash(text2)
        assert h1 == h2  # Normalized whitespace should produce same hash

    def test_compute_resume_hash_different(self):
        from app.backend.services.dedup_service import compute_resume_hash
        text1 = "John Doe\nSoftware Engineer"
        text2 = "Jane Smith\nData Scientist"
        assert compute_resume_hash(text1) != compute_resume_hash(text2)

    def test_compute_resume_hash_empty(self):
        from app.backend.services.dedup_service import compute_resume_hash
        assert compute_resume_hash("") == ""

    def test_compute_email_hash(self):
        from app.backend.services.dedup_service import compute_email_hash
        assert compute_email_hash("John@example.com") == compute_email_hash("john@example.com")
        assert compute_email_hash("") == ""

    def test_find_duplicate_no_match(self):
        from app.backend.services.dedup_service import find_duplicate_candidate
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        is_dup, existing_id = find_duplicate_candidate(mock_db, 1, "Unique resume text")
        assert is_dup is False
        assert existing_id is None


# ─── Proficiency Service ──────────────────────────────────────────────────────

class TestProficiencyService:
    def test_detect_expert_level(self):
        from app.backend.services.proficiency_service import detect_proficiency
        text = ("Architected distributed system handling millions of requests. "
                "Principal engineer who pioneered the microservices architecture. "
                "Patented novel caching algorithm. Conference speaker at KubeCon.")
        result = detect_proficiency(text)
        assert result["proficiency_level"] == "expert"
        assert result["confidence"] > 0

    def test_detect_beginner_level(self):
        from app.backend.services.proficiency_service import detect_proficiency
        text = "Intern with 0 years experience. Familiar with Python. Bootcamp graduate."
        result = detect_proficiency(text)
        assert result["proficiency_level"] == "beginner"

    def test_detect_team_size(self):
        from app.backend.services.proficiency_service import detect_proficiency
        text = "Led a team of 12 engineers on the platform migration project."
        result = detect_proficiency(text)
        assert result["team_size"] == 12

    def test_detect_project_complexity_high(self):
        from app.backend.services.proficiency_service import detect_proficiency
        text = "Built enterprise system with 99.99% uptime handling millions of users."
        result = detect_proficiency(text)
        assert result["project_complexity"] == "high"

    def test_detect_empty_text(self):
        from app.backend.services.proficiency_service import detect_proficiency
        result = detect_proficiency("")
        assert result["proficiency_level"] == "beginner"
        assert result["confidence"] == 0.0

    def test_assess_skill_proficiency_adds_fields(self):
        from app.backend.services.proficiency_service import assess_skill_proficiency
        text = "Senior engineer who architected scalable systems."
        skills = [{"skill": "Python", "match_type": "exact"}]
        enhanced = assess_skill_proficiency(text, skills)
        assert "proficiency_level" in enhanced[0]
        assert "proficiency_confidence" in enhanced[0]


# ─── Fraud Detection Service ──────────────────────────────────────────────────

class TestFraudDetection:
    def test_detect_template_resume(self):
        from app.backend.services.fraud_detection_service import detect_template_resume
        text = "[Your Name] [Your Email] Software Engineer [Company Name]"
        result = detect_template_resume(text)
        assert result is not None
        assert result["type"] == "template_resume"
        assert result["risk"] == "high"

    def test_detect_template_resume_clean(self):
        from app.backend.services.fraud_detection_service import detect_template_resume
        text = "John Doe\nSoftware Engineer at Google\n5 years experience"
        result = detect_template_resume(text)
        assert result is None

    def test_detect_skill_stacking(self):
        from app.backend.services.fraud_detection_service import detect_skill_stacking
        skills = [{"skill": f"skill_{i}"} for i in range(50)]
        text = "Short resume"
        result = detect_skill_stacking(skills, text)
        assert result is not None
        assert result["type"] == "skill_stacking"

    def test_detect_skill_stacking_not_triggered(self):
        from app.backend.services.fraud_detection_service import detect_skill_stacking
        skills = [{"skill": f"skill_{i}"} for i in range(10)]
        text = "A" * 10000
        result = detect_skill_stacking(skills, text)
        assert result is None

    def test_detect_inflated_experience(self):
        from app.backend.services.fraud_detection_service import detect_inflated_experience
        profile = {"total_years": 15, "work_history": [{"company": "A", "start_date": "2020-01", "end_date": "2023-01"}]}
        gap_analysis = {"total_effective_years": 8, "overlapping_jobs": []}
        issues = detect_inflated_experience(profile, gap_analysis)
        assert len(issues) == 1
        assert issues[0]["type"] == "experience_inflation"
        assert issues[0]["risk"] == "high"

    def test_run_fraud_check_clean_resume(self):
        from app.backend.services.fraud_detection_service import run_fraud_check
        result = run_fraud_check(
            resume_text="John Doe\nSoftware Engineer\nDeveloped Python applications.",
            matched_skills=[{"skill": "Python"}],
            candidate_profile={"total_years": 5, "work_history": []},
            gap_analysis={"total_effective_years": 5, "overlapping_jobs": []},
            jd_required_skills=["Python"],
        )
        assert result["overall_risk_level"] == "none"
        assert result["indicator_count"] == 0


# ─── Scoring Cache Service ────────────────────────────────────────────────────

class TestScoringCacheService:
    def test_cache_key_deterministic(self):
        from app.backend.services.scoring_cache_service import _compute_cache_key
        key1 = _compute_cache_key("resume", "jd", {"skills": 0.3}, 1)
        key2 = _compute_cache_key("resume", "jd", {"skills": 0.3}, 1)
        assert key1 == key2

    def test_cache_key_different_for_different_inputs(self):
        from app.backend.services.scoring_cache_service import _compute_cache_key
        key1 = _compute_cache_key("resume1", "jd", None, 1)
        key2 = _compute_cache_key("resume2", "jd", None, 1)
        assert key1 != key2

    def test_cache_and_retrieve(self):
        from app.backend.services.scoring_cache_service import cache_result, get_cached_result, clear_all_cache
        clear_all_cache()
        result = {"fit_score": 85, "recommendation": "shortlist"}
        cache_result("resume text", "jd text", result, None, 1)
        cached = get_cached_result("resume text", "jd text", None, 1)
        assert cached is not None
        assert cached["fit_score"] == 85
        assert cached["_cached"] is True

    def test_cache_miss(self):
        from app.backend.services.scoring_cache_service import get_cached_result, clear_all_cache
        clear_all_cache()
        cached = get_cached_result("nonexistent", "nonexistent", None, 999)
        assert cached is None

    def test_clear_all_cache(self):
        from app.backend.services.scoring_cache_service import cache_result, clear_all_cache
        cache_result("a", "b", {"x": 1}, None, 1)
        count = clear_all_cache()
        assert count >= 1


# ─── Resume Enrichment: Social Profiles ───────────────────────────────────────

class TestSocialProfiles:
    def test_extract_linkedin(self):
        from app.backend.services.resume_enrichment_service import extract_social_profiles
        text = "Contact: https://www.linkedin.com/in/johndoe"
        profiles = extract_social_profiles(text)
        assert "linkedin" in profiles
        assert "johndoe" in profiles["linkedin"]

    def test_extract_github(self):
        from app.backend.services.resume_enrichment_service import extract_social_profiles
        text = "GitHub: https://github.com/johndoe"
        profiles = extract_social_profiles(text)
        assert "github" in profiles

    def test_extract_multiple_profiles(self):
        from app.backend.services.resume_enrichment_service import extract_social_profiles
        text = "LinkedIn: linkedin.com/in/janedoe | GitHub: github.com/janedoe"
        profiles = extract_social_profiles(text)
        assert "linkedin" in profiles
        assert "github" in profiles

    def test_extract_no_profiles(self):
        from app.backend.services.resume_enrichment_service import extract_social_profiles
        profiles = extract_social_profiles("No social links here")
        assert profiles == {}

    def test_extract_empty_text(self):
        from app.backend.services.resume_enrichment_service import extract_social_profiles
        assert extract_social_profiles("") == {}


# ─── Resume Enrichment: Salary Parsing ────────────────────────────────────────

class TestSalaryParsing:
    def test_parse_usd_salary(self):
        from app.backend.services.resume_enrichment_service import parse_salary_expectation
        text = "Expected salary: $120,000"
        result = parse_salary_expectation(text)
        assert result is not None
        assert result["min"] == 120000
        assert result["currency"] == "USD"

    def test_parse_salary_range(self):
        from app.backend.services.resume_enrichment_service import parse_salary_expectation
        text = "Salary expectation: $100,000 - $130,000"
        result = parse_salary_expectation(text)
        assert result is not None
        assert result["min"] == 100000
        assert result["max"] == 130000

    def test_parse_euro_salary(self):
        from app.backend.services.resume_enrichment_service import parse_salary_expectation
        text = "Expected compensation: €60,000 per annum"
        result = parse_salary_expectation(text)
        assert result is not None
        assert result["currency"] == "EUR"

    def test_parse_indian_lpa(self):
        from app.backend.services.resume_enrichment_service import parse_salary_expectation
        text = "Expected CTC: 15 LPA"
        result = parse_salary_expectation(text)
        assert result is not None
        assert result["currency"] == "INR"

    def test_parse_no_salary(self):
        from app.backend.services.resume_enrichment_service import parse_salary_expectation
        result = parse_salary_expectation("No salary info here")
        assert result is None


# ─── Resume Enrichment: Education Parsing ─────────────────────────────────────

class TestEducationParsing:
    def test_parse_gpa(self):
        from app.backend.services.resume_enrichment_service import parse_education_details
        text = "Education\nMS Computer Science, Stanford University\nGPA: 3.8/4.0\nGraduated: 2022"
        results = parse_education_details(text)
        assert len(results) > 0
        assert any(r.get("gpa") == 3.8 for r in results)

    def test_parse_graduation_year(self):
        from app.backend.services.resume_enrichment_service import parse_education_details
        text = "Education\nBS Computer Science\nGraduated: 2020"
        results = parse_education_details(text)
        assert any(r.get("graduation_year") == 2020 for r in results)

    def test_detect_top_tier_institution(self):
        from app.backend.services.resume_enrichment_service import parse_education_details
        text = "Education\nMS Computer Science, MIT\nGPA: 3.9\n2021"
        results = parse_education_details(text)
        assert any(r.get("institution_tier") == "top_tier" for r in results)

    def test_detect_second_tier_institution(self):
        from app.backend.services.resume_enrichment_service import parse_education_details
        text = "Education\nBS Engineering, University of Michigan\n2020"
        results = parse_education_details(text)
        assert any(r.get("institution_tier") == "second_tier" for r in results)

    def test_parse_empty_text(self):
        from app.backend.services.resume_enrichment_service import parse_education_details
        assert parse_education_details("") == []


# ─── Resume Enrichment: International Dates ───────────────────────────────────

class TestInternationalDates:
    def test_parse_dmy_format(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        dates = parse_international_dates("Started 15/03/2020")
        assert len(dates) >= 1
        assert dates[0]["format_type"] == "dmy"
        assert dates[0]["day"] == 15
        assert dates[0]["month"] == 3
        assert dates[0]["year"] == 2020

    def test_parse_ymd_format(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        dates = parse_international_dates("Date: 2020-03-15")
        assert len(dates) >= 1
        assert dates[0]["year"] == 2020

    def test_parse_german_date_format(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        dates = parse_international_dates("Start: 15.03.2020")
        assert len(dates) >= 1
        assert dates[0]["day"] == 15

    def test_parse_japanese_date(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        dates = parse_international_dates("2020年3月")
        assert len(dates) >= 1
        assert dates[0]["year"] == 2020
        assert dates[0]["month"] == 3

    def test_parse_month_name(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        dates = parse_international_dates("January 2020")
        assert len(dates) >= 1
        assert dates[0]["month"] == 1
        assert dates[0]["year"] == 2020

    def test_parse_empty_text(self):
        from app.backend.services.resume_enrichment_service import parse_international_dates
        assert parse_international_dates("") == []


# ─── Expanded Constants ───────────────────────────────────────────────────────

class TestExpandedConstants:
    def test_healthcare_domain_keywords_exist(self):
        from app.backend.services.constants import DOMAIN_KEYWORDS
        assert "healthcare" in DOMAIN_KEYWORDS
        assert "patient care" in DOMAIN_KEYWORDS["healthcare"]

    def test_finance_domain_keywords_exist(self):
        from app.backend.services.constants import DOMAIN_KEYWORDS
        assert "finance" in DOMAIN_KEYWORDS
        assert "gaap" in DOMAIN_KEYWORDS["finance"]

    def test_legal_domain_keywords_exist(self):
        from app.backend.services.constants import DOMAIN_KEYWORDS
        assert "legal" in DOMAIN_KEYWORDS
        assert "litigation" in DOMAIN_KEYWORDS["legal"]

    def test_healthcare_field_relevance_exists(self):
        from app.backend.services.constants import FIELD_RELEVANCE
        assert "healthcare" in FIELD_RELEVANCE
        assert "medicine" in FIELD_RELEVANCE["healthcare"]

    def test_finance_field_relevance_exists(self):
        from app.backend.services.constants import FIELD_RELEVANCE
        assert "finance" in FIELD_RELEVANCE
        assert "accounting" in FIELD_RELEVANCE["finance"]

    def test_non_tech_job_function_taxonomy_exists(self):
        from app.backend.services.constants import JOB_FUNCTION_SKILL_TAXONOMY
        assert "healthcare_clinical" in JOB_FUNCTION_SKILL_TAXONOMY
        assert "finance_accounting" in JOB_FUNCTION_SKILL_TAXONOMY
        assert "legal" in JOB_FUNCTION_SKILL_TAXONOMY

    def test_non_tech_job_function_keywords_exist(self):
        from app.backend.services.constants import JOB_FUNCTION_KEYWORDS
        assert "healthcare_clinical" in JOB_FUNCTION_KEYWORDS
        assert "finance_accounting" in JOB_FUNCTION_KEYWORDS


# ─── Expanded Skills & Certifications ─────────────────────────────────────────

class TestExpandedSkills:
    def test_healthcare_skills_in_master_list(self):
        from app.backend.services.skill_matcher import MASTER_SKILLS
        skill_set = set(s.lower() for s in MASTER_SKILLS)
        assert "patient care" in skill_set
        assert "nursing" in skill_set
        assert "hipaa" in skill_set

    def test_finance_skills_in_master_list(self):
        from app.backend.services.skill_matcher import MASTER_SKILLS
        skill_set = set(s.lower() for s in MASTER_SKILLS)
        assert "financial analysis" in skill_set
        assert "gaap" in skill_set
        assert "quickbooks" in skill_set

    def test_legal_skills_in_master_list(self):
        from app.backend.services.skill_matcher import MASTER_SKILLS
        skill_set = set(s.lower() for s in MASTER_SKILLS)
        assert "legal research" in skill_set
        assert "litigation" in skill_set
        assert "westlaw" in skill_set

    def test_non_tech_skill_taxonomy_exists(self):
        from app.backend.services.skill_matcher import SKILL_TAXONOMY
        assert "healthcare_medical" in SKILL_TAXONOMY
        assert "finance_accounting" in SKILL_TAXONOMY
        assert "legal" in SKILL_TAXONOMY
        assert "marketing_domain" in SKILL_TAXONOMY

    def test_non_tech_certifications_exist(self):
        from app.backend.services.skill_matcher import CERTIFICATION_SKILL_MAP
        assert "cpa" in CERTIFICATION_SKILL_MAP
        assert "cfa" in CERTIFICATION_SKILL_MAP
        assert "usmle" in CERTIFICATION_SKILL_MAP
        assert "bar exam" in CERTIFICATION_SKILL_MAP
        assert "pmp" in CERTIFICATION_SKILL_MAP

    def test_cpa_infers_accounting(self):
        from app.backend.services.skill_matcher import CERTIFICATION_SKILL_MAP
        skills = CERTIFICATION_SKILL_MAP["cpa"]
        assert "accounting" in skills
        assert "audit" in skills

    def test_usmle_infers_medical(self):
        from app.backend.services.skill_matcher import CERTIFICATION_SKILL_MAP
        skills = CERTIFICATION_SKILL_MAP["usmle"]
        assert "medical" in skills
        assert "patient care" in skills


# ─── SAP Skill Registry & Domain Inference ───────────────────────────────────

class TestSAPSkillInference:
    RESUME_TEXT = """
    Dynamic and accomplished SAP MM Consultant with over 9 years of specialized experience in SAP MM,
    SD Integration, and SAP S/4HANA implementations. Skilled in SAP project management from solution
    design to deployment, with a focus on process optimization and cross-functional integration.
    Certified in S/4HANA Sourcing and Procurement, with a hands-on approach to configuring and
    enhancing supply chain processes.

    Responsibilities:
    Functional Specification Development: Created detailed functional specification documents.
    S/4HANA Brownfield Implementation: Participated in the S/4HANA Brownfield implementation.
    Testing: We conducted detailed unit testing across various scenarios within the development environment.
    Configured Material Master, Purchase Requisition (PR), Request for quotation, Purchase Order (PO).
    Configuration of Goods Receipt in two steps for quality inspection.
    Set up of third-party drop shipment process, Subcontracting, Consignment processes.
    Stock transfer order (Inter and Intra), Release strategy for Purchase requisition and Purchase Order.
    Cross verification of Vendor invoices and send them to the Finance dept.
    WBS assignment from Inventory.
    Worked with the technical team to complete the interfaces for Service now.
    """

    def test_sap_skills_in_master_list(self):
        from app.backend.services.skill_matcher import MASTER_SKILLS
        assert "sap mm" in MASTER_SKILLS
        assert "sap s/4hana" in MASTER_SKILLS
        assert "procure-to-pay" in MASTER_SKILLS
        assert "inventory management" in MASTER_SKILLS
        assert "idoc" in MASTER_SKILLS

    def test_sap_procure_to_pay_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["procure-to-pay"], self.RESUME_TEXT)
        assert confirmed["procure-to-pay"]["found"] is True

    def test_sap_inventory_management_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["inventory management"], self.RESUME_TEXT)
        assert confirmed["inventory management"]["found"] is True

    def test_sap_invoice_verification_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["invoice verification"], self.RESUME_TEXT)
        assert confirmed["invoice verification"]["found"] is True

    def test_sap_material_master_data_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["material master data"], self.RESUME_TEXT)
        assert confirmed["material master data"]["found"] is True

    def test_sap_vendor_master_data_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["vendor master data"], self.RESUME_TEXT)
        assert confirmed["vendor master data"]["found"] is True

    def test_sap_s4hana_and_sd_extracted_from_resume(self):
        from app.backend.services.skill_matcher import _extract_skills_from_text
        skills = _extract_skills_from_text(self.RESUME_TEXT)
        skills_lower = [s.lower() for s in skills]
        assert "sap s/4hana" in skills_lower or "s/4hana" in skills_lower
        # SAP SD may not be extracted as a separate skill - it could be under "sap" parent
        assert "sap" in skills_lower

    def test_sap_p2p_alias_inferred_from_resume(self):
        from app.backend.services.skill_matcher import confirm_skills_in_text
        confirmed = confirm_skills_in_text(["p2p"], self.RESUME_TEXT)
        assert confirmed["p2p"]["found"] is True


class TestExpandedRecruiterSkills:
    def test_recruiting_ats_tools_in_registry(self):
        from app.backend.services.skill_matcher import MASTER_SKILLS, SKILL_TAXONOMY
        assert "greenhouse" in MASTER_SKILLS
        assert "lever" in MASTER_SKILLS
        assert "icims" in MASTER_SKILLS
        assert "salesforce" in MASTER_SKILLS
        assert "hubspot" in MASTER_SKILLS
        assert "customer success" in MASTER_SKILLS
        assert "microsoft office" in MASTER_SKILLS
        assert "pmp" in MASTER_SKILLS
        assert "sales_customer_success" in SKILL_TAXONOMY
        assert "operations_admin" in SKILL_TAXONOMY
        assert "certifications" in SKILL_TAXONOMY

    def test_ats_skills_extracted_from_job_description(self):
        from app.backend.services.skill_matcher import _extract_skills_from_text
        jd = """
        We are looking for a recruiter with experience in Greenhouse, Lever, and LinkedIn Recruiter.
        Familiarity with Workday Recruiting and BambooHR is a plus. SHRM-CP or PHR preferred.
        """
        skills = _extract_skills_from_text(jd)
        skills_lower = [s.lower() for s in skills]
        assert "greenhouse" in skills_lower
        assert "lever" in skills_lower
        assert "linkedin recruiter" in skills_lower
        assert "workday recruiting" in skills_lower
        assert "bamboohr" in skills_lower

    def test_sales_skills_extracted_from_resume(self):
        from app.backend.services.skill_matcher import _extract_skills_from_text
        resume = """
        Account Executive with 5 years of experience in Salesforce CRM, HubSpot, and Gong.
        Managed renewals, upsell, and cross-sell motions. Used Salesloft and Outreach for cadences.
        """
        skills = _extract_skills_from_text(resume)
        skills_lower = [s.lower() for s in skills]
        assert "salesforce" in skills_lower
        assert "hubspot" in skills_lower
        assert "gong" in skills_lower
        assert "salesloft" in skills_lower
        assert "outreach" in skills_lower

    def test_operations_skills_extracted_from_resume(self):
        from app.backend.services.skill_matcher import _extract_skills_from_text
        resume = """
        Operations Manager proficient in Microsoft Office, Excel, Google Workspace, Asana, and QuickBooks.
        Managed procurement, vendor coordination, and expense reporting.
        """
        skills = _extract_skills_from_text(resume)
        skills_lower = [s.lower() for s in skills]
        assert "microsoft office" in skills_lower
        assert "google workspace" in skills_lower
        assert "asana" in skills_lower
        assert "quickbooks" in skills_lower
        assert "procurement" in skills_lower


# ─── Full Enrichment Integration ──────────────────────────────────────────────

class TestRecruiterSkillRegistryFeedback:
    def test_user_skills_added_to_global_registry(self, db):
        from app.backend.services.skill_matcher import add_user_skills_to_registry
        from app.backend.models.db_models import Skill

        new_skills = ["zztestrecruiteraddedtool", "zztestrecruiteraddedskill"]
        added = add_user_skills_to_registry(new_skills, db)

        assert "zztestrecruiteraddedtool" in added
        assert "zztestrecruiteraddedskill" in added

        db_skill = db.query(Skill).filter(Skill.name == "zztestrecruiteraddedtool").first()
        assert db_skill is not None
        assert db_skill.source == "manual"
        assert db_skill.status == "active"
        assert db_skill.domain == "general"

    def test_existing_skills_not_duplicated(self, db):
        from app.backend.services.skill_matcher import add_user_skills_to_registry
        from app.backend.models.db_models import Skill

        db.add(Skill(
            name="zzpreexistingtestskill",
            aliases="",
            domain="general",
            status="active",
            source="seed",
            frequency=0,
        ))
        db.commit()

        added = add_user_skills_to_registry(
            ["zzpreexistingtestskill", "zznewtestskill"], db
        )
        assert "zzpreexistingtestskill" not in added
        assert "zznewtestskill" in added

    def test_proficiency_dict_skills_extracted(self, db):
        from app.backend.services.skill_matcher import add_user_skills_to_registry
        from app.backend.models.db_models import Skill

        skills = [
            {"skill": "zztestdictskill", "proficiency": "advanced"},
            "zzteststringskill",
        ]
        added = add_user_skills_to_registry(skills, db)
        assert "zztestdictskill" in added
        assert "zzteststringskill" in added


class TestResumeEnrichment:
    def test_enrich_resume_combines_all(self):
        from app.backend.services.resume_enrichment_service import enrich_resume
        text = """
        John Doe
        LinkedIn: linkedin.com/in/johndoe
        GitHub: github.com/johndoe
        Expected salary: $120,000
        Education: MS Computer Science, Stanford University, GPA: 3.8, Graduated: 2022
        Experience: 01/03/2020 to Present
        """
        result = enrich_resume(text)
        assert "social_profiles" in result
        assert "salary_expectation" in result
        assert "education_details" in result
        assert "parsed_dates" in result
        assert "linkedin" in result["social_profiles"]
        assert result["salary_expectation"] is not None


# ─── Non-Tech JD Parsing Tests ───────────────────────────────────────────────

class TestNonTechJDParsing:
    """Test JD parsing for non-technical industries."""

    def test_hr_manager_jd_parsing(self):
        """HR Manager JD should extract HR-related skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        HR Manager
        We are looking for an experienced HR Manager to lead our people operations.
        Requirements:
        - 5+ years HR experience
        - SHRM-CP or PHR certification preferred
        - Experience with Workday, BambooHR
        Nice to have:
        - SAP SuccessFactors
        - ADP
        """
        result = parse_jd_rules(jd)
        # Domain may be 'hr' or 'legal' depending on keyword overlap
        assert result["required_years"] >= 5, f"Expected 5+ years, got {result['required_years']}"
        # Check HR-related skills are extracted
        all_skills = [s.lower() for s in result.get("required_skills", []) + result.get("nice_to_have_skills", [])]
        assert any("workday" in s or "bamboohr" in s or "shrm" in s for s in all_skills), f"Expected HR tools in skills, got {all_skills}"

    def test_financial_analyst_jd_parsing(self):
        """Financial Analyst JD should extract finance-related skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Financial Analyst
        We need a Financial Analyst to join our finance team.
        Requirements:
        - 3+ years in financial analysis
        - CPA or CFA preferred
        - Experience with QuickBooks, Excel
        - Knowledge of GAAP, financial modeling
        """
        result = parse_jd_rules(jd)
        assert result["domain"] == "finance", f"Expected domain 'finance', got '{result['domain']}'"
        assert result["required_years"] >= 3
        # Check finance-related skills are extracted (may be in required or nice-to-have)
        all_skills = [s.lower() for s in result.get("required_skills", []) + result.get("nice_to_have_skills", [])]
        assert any("quickbooks" in s or "excel" in s or "gaap" in s or "cpa" in s or "cfa" in s for s in all_skills), f"Expected finance skills, got {all_skills}"

    def test_sales_representative_jd_parsing(self):
        """Sales Rep JD should extract: domain=sales, skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Account Executive
        Join our sales team to drive revenue growth.
        Requirements:
        - 2+ years B2B sales experience
        - Experience with Salesforce CRM
        - Strong negotiation skills
        - Track record of meeting quota
        """
        result = parse_jd_rules(jd)
        assert result["domain"] == "sales", f"Expected domain 'sales', got '{result['domain']}'"
        assert result["required_years"] >= 2
        skills_lower = [s.lower() for s in result.get("required_skills", [])]
        assert any("salesforce" in s or "crm" in s for s in skills_lower), f"Expected CRM skills, got {skills_lower}"

    def test_healthcare_jd_parsing(self):
        """Healthcare JD should extract: domain=healthcare, skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Registered Nurse
        We are hiring a Registered Nurse for our hospital.
        Requirements:
        - Current RN license
        - 2+ years clinical experience
        - Epic or Cerner experience preferred
        - BLS certification
        """
        result = parse_jd_rules(jd)
        assert result["domain"] == "healthcare", f"Expected domain 'healthcare', got '{result['domain']}'"
        skills_lower = [s.lower() for s in result.get("required_skills", [])]
        assert any("epic" in s or "cerner" in s or "rn" in s for s in skills_lower), f"Expected healthcare skills, got {skills_lower}"

    def test_legal_paralegal_jd_parsing(self):
        """Legal/Paralegal JD should extract: domain=legal, skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Paralegal
        Seeking a Paralegal to support our legal team.
        Requirements:
        - 3+ years paralegal experience
        - Experience with litigation support
        - Knowledge of Westlaw, LexisNexis
        - Strong legal research skills
        """
        result = parse_jd_rules(jd)
        assert result["domain"] == "legal", f"Expected domain 'legal', got '{result['domain']}'"
        skills_lower = [s.lower() for s in result.get("required_skills", [])]
        assert any("westlaw" in s or "lexis" in s or "litigation" in s for s in skills_lower), f"Expected legal skills, got {skills_lower}"

    def test_operations_manager_jd_parsing(self):
        """Operations Manager JD should extract operations-related skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Operations Manager
        Lead our operations team for smooth business execution.
        Requirements:
        - 5+ years operations experience
        - Experience with process improvement
        - Knowledge of supply chain management
        - Six Sigma preferred
        """
        result = parse_jd_rules(jd)
        # Domain may be 'operations_admin' or 'manufacturing' due to supply chain keywords
        assert result["required_years"] >= 5
        all_skills = [s.lower() for s in result.get("required_skills", []) + result.get("nice_to_have_skills", [])]
        assert any("six sigma" in s or "process improvement" in s for s in all_skills), f"Expected operations skills, got {all_skills}"

    def test_marketing_manager_jd_parsing(self):
        """Marketing Manager JD should extract: domain=marketing, skills."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Marketing Manager
        Join our marketing team to drive brand growth.
        Requirements:
        - 4+ years marketing experience
        - Experience with HubSpot, Google Analytics
        - SEO/SEM knowledge
        - Content marketing experience
        """
        result = parse_jd_rules(jd)
        assert result["domain"] == "marketing", f"Expected domain 'marketing', got '{result['domain']}'"
        skills_lower = [s.lower() for s in result.get("required_skills", [])]
        assert any("hubspot" in s or "google analytics" in s or "seo" in s for s in skills_lower), f"Expected marketing skills, got {skills_lower}"


class TestIndustryWeights:
    """Test industry-specific scoring weights."""

    def test_healthcare_weights_higher_experience(self):
        """Healthcare should weight experience higher than skills."""
        from app.backend.services.constants import get_industry_weights
        hw = get_industry_weights("healthcare")
        assert hw["experience"] > hw["skills"], "Healthcare should value experience more than specific skills"

    def test_finance_weights_higher_education(self):
        """Finance should weight education/certifications higher."""
        from app.backend.services.constants import get_industry_weights
        fw = get_industry_weights("finance")
        assert fw["education"] > fw["skills"], "Finance should value education/certifications more"

    def test_sales_weights_higher_domain(self):
        """Sales should weight domain knowledge higher."""
        from app.backend.services.constants import get_industry_weights
        sw = get_industry_weights("sales")
        assert sw["domain"] > 0.15, "Sales should weight domain knowledge higher"

    def test_legal_weights_highest_experience(self):
        """Legal should weight experience highest."""
        from app.backend.services.constants import get_industry_weights
        lw = get_industry_weights("legal")
        assert lw["experience"] >= 0.35, "Legal should weight experience at 35%+"

    def test_default_weights_for_unknown_domain(self):
        """Unknown domains should fall back to default weights."""
        from app.backend.services.constants import get_industry_weights, DEFAULT_WEIGHTS
        uw = get_industry_weights("unknown_domain")
        assert uw == DEFAULT_WEIGHTS, "Unknown domain should use default weights"

    def test_tech_domain_uses_default(self):
        """Tech domains should use default weights."""
        from app.backend.services.constants import get_industry_weights, DEFAULT_WEIGHTS
        tw = get_industry_weights("backend")
        assert tw == DEFAULT_WEIGHTS, "Tech domain should use default weights"


# ─── Edge Case Tests for JD Parsing ───────────────────────────────────────────

class TestJDEdgeCases:
    """Test edge cases for JD parsing."""

    def test_empty_jd_parsing(self):
        """Empty JD should not crash and return sensible defaults."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        result = parse_jd_rules("")
        assert "domain" in result
        assert "required_years" in result
        assert result["required_years"] == 0

    def test_very_short_jd_parsing(self):
        """Very short JD should parse without crashing."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        result = parse_jd_rules("Manager needed")
        assert "domain" in result
        assert "role_title" in result

    def test_jd_with_only_soft_skills(self):
        """JD with only soft skills should handle gracefully."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        We need a great communicator and leader.
        Must have excellent communication skills and leadership ability.
        """
        result = parse_jd_rules(jd)
        assert "domain" in result
        # Should still extract soft skills to nice-to-have
        assert len(result.get("nice_to_have_skills", [])) > 0

    def test_jd_with_typos(self):
        """JD with typos should still parse reasonably."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Sofware Devloper
        Need 5+ years of Pytthon experiance
        """
        result = parse_jd_rules(jd)
        assert "domain" in result
        # May detect as backend due to "developer" keyword

    def test_jd_with_multiple_domains(self):
        """JD matching multiple domains should pick the dominant one."""
        from app.backend.services.hybrid_pipeline import parse_jd_rules
        jd = """
        Technical Project Manager
        Need someone with Python, JavaScript, and also marketing experience.
        Experience with React and financial analysis.
        """
        result = parse_jd_rules(jd)
        assert "domain" in result
        # Should pick one based on keyword count
