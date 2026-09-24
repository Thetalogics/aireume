"""Tests for recruiter LLM routing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_generate_recruiter_json_uses_gemini_when_key_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    from app.backend.services.recruiter.llm_client import generate_recruiter_json

    with patch(
        "app.backend.services.app_llm_client._try_ollama",
        new_callable=AsyncMock,
        return_value='{"score": 82, "evidence": ["strong answer"]}',
    ) as mock_ollama:
        result = await generate_recruiter_json("Evaluate this answer")

        assert result == {"score": 82, "evidence": ["strong answer"]}
        mock_ollama.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_recruiter_llm_falls_back_to_ollama_without_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import asyncio

    from app.backend.services.llm_service import generate_recruiter_llm

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": '{"score": 70}'}

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
        text = await generate_recruiter_llm("prompt", max_output_tokens=512)

    assert text == '{"score": 70}'
    mock_client.post.assert_awaited_once()
    assert mock_client.post.await_args.args[0].endswith("/api/generate")
