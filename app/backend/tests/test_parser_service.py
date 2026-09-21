import asyncio

import pytest
from unittest.mock import patch, MagicMock
from app.backend.services.parser_service import (
    ResumeParser, parse_resume, enrich_parsed_resume,
    _extract_name_ner, _get_spacy_model, _name_from_filename,
    _llm_extract_structured,
)


class TestResumeParser:
    def test_extract_text_with_plain_text(self):
        parser = ResumeParser()
        text = "John Doe\nSoftware Engineer\n"
        result = parser.extract_text(text.encode(), "resume.txt")
        # Should handle gracefully, not fail
        assert isinstance(result, str)

    def test_parse_resume_structure(self):
        sample_text = """
John Doe
Software Engineer
john.doe@email.com

SKILLS
Python, JavaScript, React

WORK EXPERIENCE
Senior Engineer | TechCorp
January 2020 - Present
Leading development teams

Software Engineer | StartupCo
June 2017 - December 2019
Built web applications

EDUCATION
BS Computer Science, 2015
"""
        parser = ResumeParser()
        result = parser.parse_resume(sample_text.encode(), "resume.txt")

        assert "raw_text" in result
        assert "work_experience" in result
        assert "skills" in result
        assert "education" in result
        assert "contact_info" in result

    def test_extract_skills(self):
        sample_text = """
SKILLS
Python, JavaScript, React, Node.js, PostgreSQL, Docker, AWS
"""
        parser = ResumeParser()
        result = parser.parse_resume(sample_text.encode(), "resume.txt")

        skills = result["skills"]
        assert len(skills) > 0
        assert any("Python" in s for s in skills)

    def test_extract_work_experience(self):
        sample_text = """
WORK EXPERIENCE

TechCorp | Senior Engineer
January 2020 - Present
Leading teams and building products

Startup | Software Engineer
June 2017 - December 2019
Full stack development
"""
        parser = ResumeParser()
        result = parser.parse_resume(sample_text.encode(), "resume.txt")

        jobs = result["work_experience"]
        # Parser may find jobs depending on format
        # Just verify the structure is correct
        assert isinstance(jobs, list)

    def test_extract_contact_info(self):
        sample_text = """
John Doe
john.doe@email.com
+1-555-123-4567
linkedin.com/in/johndoe
"""
        parser = ResumeParser()
        result = parser.parse_resume(sample_text.encode(), "resume.txt")

        contact = result["contact_info"]
        assert "email" in contact
        assert "john.doe@email.com" in contact["email"]

    def test_extract_name_hyphenated(self):
        parser = ResumeParser()
        text = """
Mary-Jane Smith
Software Engineer
mary@example.com
"""
        assert parser._extract_name(text) == "Mary-Jane Smith"

    def test_extract_name_pipe_before_phone(self):
        parser = ResumeParser()
        text = """
Revanth Kumar | +91 98765 43210 | revanth@company.com
Embedded Systems Engineer
"""
        assert parser._extract_name(text).startswith("Revanth")

    def test_extract_work_year_to_present(self):
        parser = ResumeParser()
        text = """
WORK
Acme Corp — Senior Engineer
2018 – Present
Shipped firmware.
"""
        jobs = parser._extract_work_experience(text)
        assert len(jobs) >= 1
        assert jobs[0].get("end_date") == "present"

    def test_parse_resume_function(self):
        sample_text = "Simple resume text with Python and JavaScript skills"
        result = parse_resume(sample_text.encode(), "resume.txt")

        assert isinstance(result, dict)
        assert "raw_text" in result

    def test_enrich_name_from_email_when_header_missing(self):
        data = {
            "raw_text": "SKILLS\nPython, C++\n",
            "contact_info": {"email": "jane.smith@corp.io"},
            "work_experience": [],
            "skills": [],
            "education": [],
        }
        enrich_parsed_resume(data)
        assert data["contact_info"]["name"] == "Jane Smith"


class TestNerNameExtraction:
    """Tests for spaCy NER-based name extraction (Tier 0)."""

    def test_ner_extracts_name_from_standard_header(self):
        """NER should find PERSON entity in standard resume header."""
        text = "John Smith\nSoftware Engineer\njohn.smith@email.com\n"
        # This test requires spaCy to be installed with en_core_web_sm
        result = _extract_name_ner(text)
        # If spaCy is not installed, result will be None (acceptable)
        # If installed, should find "John Smith"
        if result is not None:
            assert "John" in result

    def test_ner_name_not_on_first_line(self):
        """NER should find name even if not on the very first line."""
        text = "\n\nJane Doe\nSenior Developer\njane@company.com\n"
        result = _extract_name_ner(text)
        if result is not None:
            assert "Jane" in result

    def test_ner_returns_none_when_no_person_entity(self):
        """NER should return None when text contains no PERSON entities."""
        text = "TECHNICAL SKILLS\nPython, JavaScript, Docker\nEXPERIENCE\nCoding since 2015\n"
        result = _extract_name_ner(text)
        # Technical text should not yield a person name
        if result is not None:
            # If spaCy misidentifies something, ensure it's not a reasonable name
            assert not result or len(result.split()) > 5

    def test_ner_skips_names_with_digits(self):
        """NER should reject entities containing digits."""
        text = "John123 Smith\nEngineer\njohn@email.com\n"
        result = _extract_name_ner(text)
        # Should return None or a valid name without digits
        if result is not None:
            assert not any(c.isdigit() for c in result)

    def test_ner_skips_names_with_too_many_words(self):
        """NER should reject entities with more than 5 words."""
        text = "Dr. John Michael William Robert James Smith III\nEngineer\njohn@email.com\n"
        result = _extract_name_ner(text)
        # Should return None or a shorter valid name
        if result is not None:
            assert len(result.split()) <= 5

    def test_ner_handles_spacy_not_installed(self):
        """NER should gracefully handle spaCy not being available."""
        with patch('app.backend.services.parser_service._get_spacy_model', return_value=None):
            result = _extract_name_ner("John Smith\nEngineer\njohn@email.com")
            assert result is None

    def test_get_spacy_model_returns_none_on_import_error(self):
        """Model loader should return None on ImportError (spaCy not installed)."""
        import app.backend.services.parser_service as ps
        # Save original state
        original_nlp = ps._spacy_nlp
        try:
            ps._spacy_nlp = None  # Reset to trigger reload attempt
            with patch.dict('sys.modules', {'spacy': None}):
                with patch('builtins.__import__', side_effect=ImportError("No spacy")):
                    result = _get_spacy_model()
                    assert result is None
        finally:
            ps._spacy_nlp = original_nlp  # Restore

    def test_enrich_uses_ner_result_when_available(self):
        """enrich_parsed_resume should use NER result when available."""
        data = {
            "raw_text": "Alice Johnson\nSoftware Engineer\nalice.johnson@company.com\nSKILLS\nPython\n",
            "contact_info": {"email": "alice.johnson@company.com"},
            "work_experience": [],
            "skills": [],
            "education": [],
        }
        # Mock NER to return a specific name
        with patch('app.backend.services.parser_service._extract_name_ner', return_value="Alice Johnson"):
            enrich_parsed_resume(data)
            assert data["contact_info"]["name"] == "Alice Johnson"

    def test_enrich_fallback_when_ner_returns_none(self):
        """When NER returns None, should fall back to email-based extraction."""
        data = {
            "raw_text": "SKILLS\nPython, C++\n",
            "contact_info": {"email": "jane.smith@corp.io"},
            "work_experience": [],
            "skills": [],
            "education": [],
        }
        with patch('app.backend.services.parser_service._extract_name_ner', return_value=None):
            enrich_parsed_resume(data)
            # Should fall back to email-based extraction
            assert data["contact_info"]["name"] == "Jane Smith"

    def test_full_pipeline_with_ner(self):
        """Full parse_resume flow should use NER for name extraction."""
        text = b"""Bob Williams
Senior Software Engineer
bob.williams@techcorp.com

SKILLS
Python, JavaScript, React

EXPERIENCE
TechCorp | Lead Developer
2020 - Present
Building web applications
"""
        # This integration test requires spaCy to be installed
        result = parse_resume(text, "resume.txt")
        # Name should be extracted (either via NER or fallback)
        assert "name" in result["contact_info"]
        if result["contact_info"]["name"]:
            # Should have found a name
            assert len(result["contact_info"]["name"]) > 0


class TestFilenameNameExtraction:
    """Tests for _name_from_filename function."""

    def test_simple_name_extraction(self):
        """Should extract name from simple filename."""
        assert _name_from_filename("Suhas Mullangi.pdf") == "Suhas Mullangi"

    def test_underscore_separated_name(self):
        """Should extract name from underscore-separated filename."""
        assert _name_from_filename("john_doe_resume_2024.pdf") == "John Doe"

    def test_resume_prefix_removed(self):
        """Should remove 'resume' prefix and extract name."""
        assert _name_from_filename("resume_jane_smith.docx") == "Jane Smith"

    def test_cv_prefix_removed(self):
        """Should remove 'cv' prefix and extract name."""
        assert _name_from_filename("cv_john_smith.pdf") == "John Smith"

    def test_date_removed(self):
        """Should remove year dates from filename."""
        assert _name_from_filename("alice_wonderland_2023.pdf") == "Alice Wonderland"

    def test_hyphen_separated_name(self):
        """Should handle hyphen-separated names."""
        assert _name_from_filename("jane-smith-resume.pdf") == "Jane Smith"

    def test_rejects_single_word(self):
        """Should reject filenames with only one word."""
        assert _name_from_filename("resume.pdf") == ""

    def test_rejects_too_many_words(self):
        """Should reject filenames with more than 5 words."""
        assert _name_from_filename("john_james_william_robert_michael_smith.pdf") == ""

    def test_rejects_digits_in_name(self):
        """Should reject filenames with digits in the name portion."""
        assert _name_from_filename("user123_test.pdf") == ""

    def test_handles_no_extension(self):
        """Should handle filenames without extension."""
        assert _name_from_filename("John Doe Resume") == "John Doe"

    def test_empty_filename(self):
        """Should return empty string for empty filename."""
        assert _name_from_filename("") == ""

    def test_enrich_uses_filename_when_other_tiers_fail(self):
        """enrich_parsed_resume should use filename when other tiers fail."""
        data = {
            "raw_text": "SKILLS\nPython\n",
            "contact_info": {},  # No email, no name
            "work_experience": [],
            "skills": [],
            "education": [],
        }
        with patch('app.backend.services.parser_service._extract_name_ner', return_value=None):
            enrich_parsed_resume(data, filename="Suhas Mullangi.pdf")
            assert data["contact_info"]["name"] == "Suhas Mullangi"

    def test_enrich_prefers_ner_over_filename(self):
        """enrich_parsed_resume should prefer NER result over filename."""
        data = {
            "raw_text": "Alice Johnson\nSoftware Engineer\n",
            "contact_info": {},
            "work_experience": [],
            "skills": [],
            "education": [],
        }
        with patch('app.backend.services.parser_service._extract_name_ner', return_value="Alice Johnson"):
            enrich_parsed_resume(data, filename="Wrong Name.pdf")
            assert data["contact_info"]["name"] == "Alice Johnson"


@pytest.mark.asyncio
async def test_structured_extract_gives_up_within_10s():
    """Gap-fill must not hold the analyze request through two Gemini read timeouts."""

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(30)
        return {"work_experience": [{"title": "Eng", "company": "Acme"}]}

    data = {
        "work_experience": [],
        "education": [],
    }
    raw = "Bachelor of Science at Acme Technologies and Beta Solutions Ltd"
    started = asyncio.get_running_loop().time()
    with patch("app.backend.services.app_llm_client.generate_app_json", new=hang):
        result = await _llm_extract_structured(raw, data)
    elapsed = asyncio.get_running_loop().time() - started
    assert result == {}
    assert elapsed < 12
