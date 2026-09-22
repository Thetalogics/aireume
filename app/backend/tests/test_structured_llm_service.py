"""Tests for Outlines structured LLM generation."""

import json

import pytest

from app.backend.schemas.llm_structured import InterviewKitLLMResponse, NarrativeLLMResponse, narrative_meets_minimum
from app.backend.services.structured_llm_service import (
    invoke_outlines_json_resilient,
    is_structured_llm_enabled,
    parse_outlines_json_text,
    schema_for_developer_api,
)


def _valid_kit_payload() -> dict:
    steps = [{"text": f"Question {i}?", "spoken_text": f"Question {i}?"} for i in range(1, 5)]
    return {
        "interview_questions": {
            "kit_version": 3,
            "screen_objective": "Validate fit",
            "threads": [{"id": "t1", "title": "Focus", "kind": "technical", "steps": steps}],
            "technical_questions": steps,
            "behavioral_questions": [],
            "experience_deep_dive_questions": [],
        }
    }


def test_parse_outlines_json_text_validates_schema():
    raw = json.dumps(_valid_kit_payload())
    parsed = parse_outlines_json_text(raw, InterviewKitLLMResponse)
    assert parsed is not None
    assert parsed["interview_questions"]["kit_version"] == 3


def test_parse_outlines_json_text_rejects_invalid():
    assert parse_outlines_json_text('{"wrong": true}', InterviewKitLLMResponse) is None


def test_developer_api_schema_omits_additional_properties():
    raw = json.dumps(NarrativeLLMResponse.model_json_schema())
    assert "additionalProperties" in raw
    schema = schema_for_developer_api(NarrativeLLMResponse)

    def walk(node):
        if isinstance(node, dict):
            assert "additionalProperties" not in node
            assert "additional_properties" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    assert schema["type"] == "object"


@pytest.mark.asyncio
async def test_invoke_outlines_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "0")
    result = await invoke_outlines_json_resilient(
        ["prompt"],
        output_type=InterviewKitLLMResponse,
        log_label="test",
    )
    assert result is None


@pytest.mark.asyncio
async def test_outlines_order_is_ollama_then_gemini(monkeypatch):
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "1")
    seen: list[str] = []

    async def _fake_ollama(*_args, **_kwargs):
        seen.append("ollama")
        return None

    async def _fake_gemini(*_args, **_kwargs):
        seen.append("gemini")
        return None

    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_ollama",
        _fake_ollama,
    )
    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_gemini",
        _fake_gemini,
    )

    result = await invoke_outlines_json_resilient(
        ["prompt"],
        output_type=InterviewKitLLMResponse,
        log_label="interview_kit",
    )
    assert result is None
    assert seen == ["ollama", "gemini"]


@pytest.mark.asyncio
async def test_invoke_outlines_gemini_success(monkeypatch):
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async def _fake_gemini(prompt, **kwargs):
        return json.dumps(_valid_kit_payload())

    async def _fake_ollama(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_gemini",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.structured_llm_service._try_outlines_ollama",
        _fake_ollama,
    )

    from app.backend.services.background_enrichment import _kit_meets_minimum

    result = await invoke_outlines_json_resilient(
        ["prompt"],
        output_type=InterviewKitLLMResponse,
        log_label="interview_kit",
        validate_parsed=_kit_meets_minimum,
    )
    assert result is not None
    assert result["interview_questions"]["threads"]


@pytest.mark.asyncio
async def test_invoke_llm_json_prefers_outlines_when_schema_set(monkeypatch):
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "1")

    async def _fake_outlines(*args, **kwargs):
        return _valid_kit_payload()

    async def _fake_gemini(*args, **kwargs):
        raise AssertionError("legacy gemini should not run when outlines succeeds")

    monkeypatch.setattr(
        "app.backend.services.structured_llm_service.invoke_outlines_json_resilient",
        _fake_outlines,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_gemini",
        _fake_gemini,
    )

    from app.backend.services.llm_json_service import invoke_llm_json_resilient

    result = await invoke_llm_json_resilient(
        ["prompt"],
        output_type=InterviewKitLLMResponse,
        log_label="interview_kit",
    )
    assert result is not None
    assert "interview_questions" in result


def test_is_structured_llm_enabled_default_true(monkeypatch):
    monkeypatch.delenv("OUTLINES_STRUCTURED_JSON", raising=False)
    assert is_structured_llm_enabled() is True


def _valid_narrative_payload() -> dict:
    return {
        "candidate_profile_summary": "Senior engineer with 8 years in backend systems.",
        "fit_summary": "Strong Python and API experience; minor gap on Kubernetes. Recommend interview.",
        "strengths": ["Python", "FastAPI"],
        "concerns": ["Limited K8s"],
        "dealbreakers": [],
        "differentiators": ["Led platform migration"],
        "recommendation_rationale": "Scores and skills align with must-haves.",
        "hiring_decision": {
            "verdict": "Shortlist",
            "confidence": 0.82,
            "key_factors": ["Python", "API design"],
            "action_items": ["Phone screen"],
        },
        "explainability": {
            "skill_rationale": "Matched 4/5 must-haves.",
            "experience_rationale": "8y exceeds 5y bar.",
            "overall_rationale": "Fit score supports interview.",
        },
    }


def test_parse_narrative_schema():
    raw = json.dumps(_valid_narrative_payload())
    parsed = parse_outlines_json_text(raw, NarrativeLLMResponse)
    assert parsed is not None
    assert "Strong Python" in parsed["fit_summary"]


def test_narrative_meets_minimum_rejects_empty():
    assert narrative_meets_minimum({"fit_summary": "", "candidate_profile_summary": ""}) is False


def test_narrative_meets_minimum_accepts_fit_summary():
    assert narrative_meets_minimum({"fit_summary": "Strong match for backend role with clear gaps noted."}) is True


@pytest.mark.asyncio
async def test_explain_with_llm_uses_outlines_schema(monkeypatch):
    monkeypatch.setenv("OUTLINES_STRUCTURED_JSON", "1")

    async def _fake_invoke(*args, **kwargs):
        assert kwargs.get("output_type") is NarrativeLLMResponse
        return _valid_narrative_payload()

    monkeypatch.setattr(
        "app.backend.services.llm_json_service.invoke_llm_json_resilient",
        _fake_invoke,
    )

    from app.backend.services.hybrid_pipeline import explain_with_llm

    result = await explain_with_llm({
        "jd_analysis": {"role_title": "Backend Engineer", "title": "Backend Engineer", "domain": "tech", "seniority": "senior"},
        "candidate_profile": {"name": "Alex", "current_role": "Engineer", "current_company": "Acme", "years_experience": 8},
        "scores": {"fit_score": 75, "skill_score": 80, "exp_score": 70, "edu_score": 60, "timeline_score": 90, "final_recommendation": "Interview"},
        "skill_analysis": {"matched_must_haves": ["Python"], "missing_must_haves": [], "matched_nice_to_haves": [], "missing_nice_to_haves": []},
        "score_rationales": {},
        "risk_summary": {"risk_flags": []},
    })
    assert result["ai_enhanced"] is True
    assert "Strong Python" in result["fit_summary"]
