"""Tests for resilient LLM JSON invocation."""

import pytest

from app.backend.services.llm_json_service import invoke_llm_json_resilient


@pytest.mark.asyncio
async def test_json_path_uses_analysis_order(monkeypatch):
    seen: list[str] = []

    def _record(name: str):
        async def _call(*_args, **_kwargs):
            seen.append(name)
            return None

        return _call

    monkeypatch.setattr("app.backend.services.app_llm_client._try_ollama", _record("ollama"))
    monkeypatch.setattr("app.backend.services.app_llm_client._try_gemini", _record("gemini"))
    monkeypatch.setattr("app.backend.services.app_llm_client._try_openrouter", _record("openrouter"))

    result = await invoke_llm_json_resilient(["prompt"], log_label="test")
    assert result is None
    assert seen == ["ollama"]


@pytest.mark.asyncio
async def test_invoke_returns_none_when_all_tiers_empty(monkeypatch):
    async def _fake_gemini(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_gemini",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_ollama",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_openrouter",
        _fake_gemini,
    )
    result = await invoke_llm_json_resilient(["prompt a", "prompt b"], log_label="test")
    assert result is None


@pytest.mark.asyncio
async def test_invoke_parses_json_on_second_tier(monkeypatch):
    calls = {"n": 0}

    async def _fake_gemini(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return '{"fit_summary": "Strong match", "strengths": ["Python"]}'

    async def _fake_empty(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_ollama",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_gemini",
        _fake_empty,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_openrouter",
        _fake_empty,
    )
    result = await invoke_llm_json_resilient(["full", "compact"], log_label="test")
    assert result is not None
    assert result["fit_summary"] == "Strong match"


@pytest.mark.asyncio
async def test_invoke_retries_when_validator_rejects_first_tier(monkeypatch):
    calls = {"n": 0}

    async def _fake_gemini(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"interview_questions": {"threads": []}}'
        return '{"interview_questions": {"threads": [{"id": "t1", "steps": [{"text": "Q1?"}, {"text": "Q2?"}, {"text": "Q3?"}, {"text": "Q4?"}]}]}}'

    async def _fake_empty(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_ollama",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_gemini",
        _fake_empty,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_openrouter",
        _fake_empty,
    )

    from app.backend.services.background_enrichment import _kit_meets_minimum

    result = await invoke_llm_json_resilient(
        ["full", "compact"],
        log_label="interview_kit",
        validate_parsed=_kit_meets_minimum,
    )
    assert result is not None
    assert calls["n"] == 2
    assert len(result["interview_questions"]["threads"][0]["steps"]) == 4


@pytest.mark.asyncio
async def test_invoke_falls_back_to_ollama_when_gemini_non_json(monkeypatch):
    async def _fake_gemini(*args, **kwargs):
        return "not json at all"

    async def _fake_ollama(*args, **kwargs):
        return '{"fit_summary": "Ollama saved it", "strengths": []}'

    async def _fake_empty(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_gemini",
        _fake_gemini,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_ollama",
        _fake_ollama,
    )
    monkeypatch.setattr(
        "app.backend.services.app_llm_client._try_openrouter",
        _fake_empty,
    )

    result = await invoke_llm_json_resilient(["prompt"], log_label="test")
    assert result is not None
    assert result["fit_summary"] == "Ollama saved it"
