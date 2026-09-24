"""Tests for shared application LLM client."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_analysis_order_is_ollama_only(monkeypatch):
    """Analysis calls Ollama and does not continue to Gemini or OpenRouter."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3.8-27b:free")
    monkeypatch.setenv("OLLAMA_MODEL_BACKEND", "deepseek-v4.1-flash:cloud")

    from app.backend.services.app_llm_client import generate_app_llm

    seen: list[str] = []

    def _record(name: str):
        async def _call(*_args, **_kwargs):
            seen.append(name)
            return None

        return _call

    with patch(
        "app.backend.services.app_llm_client._try_ollama",
        new=_record("ollama"),
    ), patch(
        "app.backend.services.app_llm_client._try_gemini",
        new=_record("gemini"),
    ), patch(
        "app.backend.services.app_llm_client._try_openrouter",
        new=_record("openrouter"),
    ):
        text = await generate_app_llm("prompt", max_output_tokens=128)

    assert text is None
    assert seen == ["ollama"]


@pytest.mark.asyncio
async def test_generate_app_json_uses_gemini_when_key_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    from app.backend.services.app_llm_client import generate_app_json

    with patch(
        "app.backend.services.app_llm_client._try_ollama",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.backend.services.llm_service.gemini_generate_content",
        new_callable=AsyncMock,
    ) as mock_gemini:
        from app.backend.services.llm_service import GeminiGenerateResult

        mock_gemini.return_value = GeminiGenerateResult(
            '{"role_category": "technical", "confidence": 0.9}',
            "STOP",
        )

        result = await generate_app_json("Analyze this JD")

        assert result is None
        mock_gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_app_llm_falls_back_to_ollama_without_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from app.backend.services.app_llm_client import generate_app_llm

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "hello from ollama"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.backend.services.llm_service.get_ollama_semaphore",
        return_value=asyncio.Semaphore(1),
    ), patch(
        "app.backend.services.app_llm_client.httpx.AsyncClient",
        return_value=mock_client,
    ):
        text = await generate_app_llm("prompt", max_output_tokens=128)

    assert text == "hello from ollama"
    mock_client.post.assert_awaited_once()
    assert mock_client.post.await_args.args[0].endswith("/api/generate")


@pytest.mark.asyncio
async def test_generate_app_llm_falls_back_to_openrouter_when_gemini_and_ollama_fail(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324")

    from app.backend.services.app_llm_client import generate_app_llm

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hello from openrouter"}}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.backend.services.llm_service.gemini_generate_content",
        new_callable=AsyncMock,
        side_effect=RuntimeError("429"),
    ), patch(
        "app.backend.services.llm_service.get_ollama_semaphore",
        return_value=asyncio.Semaphore(1),
    ), patch(
        "app.backend.services.app_llm_client.httpx.AsyncClient",
        return_value=mock_client,
    ) as mock_async_client:
        text = await generate_app_llm("prompt", max_output_tokens=128)

    assert text is None
    assert mock_client.post.await_count == 1
    assert mock_client.post.await_args.args[0].endswith("/api/generate")


@pytest.mark.asyncio
async def test_openrouter_skip_is_logged_when_key_missing(monkeypatch, caplog):
    from app.backend.services.app_llm_client import _try_openrouter

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    caplog.set_level("WARNING")
    text = await _try_openrouter(
        "prompt",
        system=None,
        max_output_tokens=32,
        temperature=0,
        timeout=5,
        json_mode=True,
        log_label="interview_kit_tier1",
    )
    assert text is None
    assert "provider=openrouter outcome=skipped reason=missing_api_key" in caplog.text


@pytest.mark.asyncio
async def test_generate_app_llm_falls_back_when_gemini_truncated(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    from app.backend.services.app_llm_client import generate_app_llm
    from app.backend.services.llm_service import GeminiGenerateResult, GeminiTruncatedError

    async def _truncated(*args, **kwargs):
        raise GeminiTruncatedError(
            GeminiGenerateResult('{"partial": true', "MAX_TOKENS")
        )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "hello from ollama"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.backend.services.llm_service.gemini_generate_content",
        new_callable=AsyncMock,
        side_effect=_truncated,
    ), patch(
        "app.backend.services.llm_service.get_ollama_semaphore",
        return_value=asyncio.Semaphore(1),
    ), patch(
        "app.backend.services.app_llm_client.httpx.AsyncClient",
        return_value=mock_client,
    ):
        text = await generate_app_llm("prompt", max_output_tokens=128, json_mode=True)

    assert text == "hello from ollama"


@pytest.mark.asyncio
async def test_generate_app_llm_skips_fallbacks_when_disabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    from app.backend.services.app_llm_client import generate_app_llm

    with patch(
        "app.backend.services.llm_service.gemini_generate_content",
        new_callable=AsyncMock,
        side_effect=RuntimeError("429"),
    ) as mock_gemini, patch(
        "app.backend.services.app_llm_client._try_ollama",
        new_callable=AsyncMock,
        return_value=None,
    ) as mock_ollama, patch(
        "app.backend.services.app_llm_client._try_openrouter",
        new_callable=AsyncMock,
    ) as mock_openrouter:
        text = await generate_app_llm(
            "prompt",
            max_output_tokens=128,
            allow_provider_fallback=False,
        )

    assert text is None
    mock_ollama.assert_awaited_once()
    mock_gemini.assert_not_awaited()
    mock_openrouter.assert_not_awaited()
