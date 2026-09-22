"""Tests for llm_service.py including OllamaHealthSentinel."""

import httpx
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.backend.services.llm_service import (
    OllamaHealthSentinel,
    OllamaState,
    get_sentinel,
    LLMService,
    analyze_with_llm,
)


class TestOllamaHealthSentinel:
    """Tests for OllamaHealthSentinel class."""

    def test_init_default_values(self):
        """Test sentinel initializes with correct default values."""
        sentinel = OllamaHealthSentinel()
        assert sentinel.base_url == "http://ollama:11434"
        assert sentinel.model_name == "qwen2.5:7b"
        assert sentinel.probe_interval == 60
        assert sentinel.state == OllamaState.COLD
        assert sentinel.last_probe_time == 0
        assert sentinel.last_latency_ms == 0
        assert sentinel._task is None
        assert sentinel._running is False

    def test_init_custom_values(self):
        """Test sentinel initializes with custom values."""
        sentinel = OllamaHealthSentinel(
            ollama_base_url="http://custom:11434",
            model_name="llama2:7b",
            probe_interval=30
        )
        assert sentinel.base_url == "http://custom:11434"
        assert sentinel.model_name == "llama2:7b"
        assert sentinel.probe_interval == 30

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        """Test start() creates and runs the probe loop task."""
        sentinel = OllamaHealthSentinel()
        
        with patch.object(sentinel, '_probe_loop', new_callable=AsyncMock) as mock_loop:
            await sentinel.start()
            assert sentinel._running is True
            assert sentinel._task is not None
            # Cancel the task to clean up
            sentinel._task.cancel()
            try:
                await sentinel._task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """Test stop() cancels the probe loop task gracefully."""
        sentinel = OllamaHealthSentinel()
        sentinel._running = True
        sentinel._task = asyncio.create_task(asyncio.sleep(10))
        
        await sentinel.stop()
        
        assert sentinel._running is False
        assert sentinel._task is None

    @pytest.mark.asyncio
    async def test_probe_once_model_not_hot_triggers_warmup(self):
        """Test _probe_once triggers warmup when model is not in RAM."""
        sentinel = OllamaHealthSentinel()
        
        mock_response_ps = MagicMock()
        mock_response_ps.status_code = 200
        mock_response_ps.json.return_value = {"models": []}  # No models loaded
        
        mock_response_generate = MagicMock()
        mock_response_generate.status_code = 200
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response_ps
            mock_client.post.return_value = mock_response_generate
            
            await sentinel._probe_once()
            
            assert sentinel.state == OllamaState.HOT
            assert sentinel.last_probe_time > 0
            assert sentinel.last_latency_ms >= 0
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://ollama:11434/api/generate"
            assert call_args[1]["json"]["model"] == "qwen2.5:7b"
            assert call_args[1]["json"]["prompt"] == "warmup"

    @pytest.mark.asyncio
    async def test_probe_once_model_hot_no_generate_probe(self):
        """Test _probe_once skips generate probe when model is already hot."""
        sentinel = OllamaHealthSentinel()
        
        mock_response_ps = MagicMock()
        mock_response_ps.status_code = 200
        mock_response_ps.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response_ps
            
            await sentinel._probe_once()
            
            assert sentinel.state == OllamaState.HOT
            # No POST should be made when model is hot - /api/ps already confirmed it's loaded
            mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_once_error_state_on_exception(self):
        """Test _probe_once sets ERROR state when Ollama is unreachable."""
        sentinel = OllamaHealthSentinel()
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.get.side_effect = Exception("Connection refused")
            
            await sentinel._probe_once()
            
            assert sentinel.state == OllamaState.ERROR
            assert sentinel.last_probe_time > 0

    @pytest.mark.asyncio
    async def test_probe_once_error_state_on_warmup_failure(self):
        """Test _probe_once sets ERROR state when warmup POST fails."""
        sentinel = OllamaHealthSentinel()
        
        mock_response_ps = MagicMock()
        mock_response_ps.status_code = 200
        mock_response_ps.json.return_value = {"models": []}  # Model not loaded
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response_ps
            mock_client.post.side_effect = Exception("Connection refused")
            
            await sentinel._probe_once()
            
            assert sentinel.state == OllamaState.ERROR

    def test_get_status_returns_correct_dict(self):
        """Test get_status() returns correctly structured dict."""
        sentinel = OllamaHealthSentinel()
        sentinel.state = OllamaState.HOT
        sentinel.last_probe_time = 1234567890.0
        sentinel.last_latency_ms = 150.5
        
        status = sentinel.get_status()
        
        assert status["state"] == "hot"
        assert status["model"] == "qwen2.5:7b"
        assert status["last_probe_time"] == 1234567890.0
        assert status["last_latency_ms"] == 150.5
        assert status["healthy"] is True

    def test_get_status_healthy_only_when_hot(self):
        """Test healthy is only True when state is HOT."""
        sentinel = OllamaHealthSentinel()
        
        for state, expected_healthy in [
            (OllamaState.COLD, False),
            (OllamaState.WARMING, False),
            (OllamaState.HOT, True),
            (OllamaState.ERROR, False),
        ]:
            sentinel.state = state
            status = sentinel.get_status()
            assert status["healthy"] == expected_healthy, f"Failed for state {state.value}"

    @pytest.mark.asyncio
    async def test_probe_loop_runs_periodically(self):
        """Test _probe_loop calls _probe_once at intervals."""
        sentinel = OllamaHealthSentinel(probe_interval=0.1)
        sentinel._running = True
        
        with patch.object(sentinel, '_probe_once', new_callable=AsyncMock) as mock_probe:
            # Run for a short time then cancel
            task = asyncio.create_task(sentinel._probe_loop())
            await asyncio.sleep(0.25)  # Allow 2 probe cycles
            sentinel._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            assert mock_probe.call_count >= 2


class TestGetSentinel:
    """Tests for get_sentinel() function."""

    def test_get_sentinel_returns_none_by_default(self):
        """Test get_sentinel() returns None when no sentinel initialized."""
        # Note: This test assumes _sentinel is None at module level
        # We need to patch it to ensure clean state
        from app.backend.services import llm_service
        original_sentinel = llm_service._sentinel
        try:
            llm_service._sentinel = None
            assert get_sentinel() is None
        finally:
            llm_service._sentinel = original_sentinel

    def test_get_sentinel_returns_sentinel_when_set(self):
        """Test get_sentinel() returns the sentinel when initialized."""
        from app.backend.services import llm_service
        original_sentinel = llm_service._sentinel
        try:
            mock_sentinel = MagicMock()
            llm_service._sentinel = mock_sentinel
            assert get_sentinel() is mock_sentinel
        finally:
            llm_service._sentinel = original_sentinel


class TestLLMService:
    """Tests for LLMService class."""

    def test_init_default_values(self):
        """Test LLMService initializes with correct default values."""
        service = LLMService()
        assert service.base_url == "http://localhost:11434"
        assert service.model == "qwen2.5:7b"
        assert service.max_retries == 0

    @pytest.mark.asyncio
    async def test_call_ollama_success(self):
        """Test _call_ollama returns response on success."""
        service = LLMService()
        
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": '{"fit_score": 85}'}
        mock_response.raise_for_status = MagicMock()
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response
            
            result = await service._call_ollama("test prompt")
            
            assert result == '{"fit_score": 85}'

    def test_parse_json_response_plain_json(self):
        """Test _parse_json_response handles plain JSON."""
        service = LLMService()
        result = service._parse_json_response('{"fit_score": 85}')
        assert result == {"fit_score": 85}

    def test_parse_json_response_markdown_code_block(self):
        """Test _parse_json_response extracts JSON from markdown code block."""
        service = LLMService()
        response = '```json\n{"fit_score": 85}\n```'
        result = service._parse_json_response(response)
        assert result == {"fit_score": 85}

    def test_parse_json_response_invalid_json(self):
        """Test _parse_json_response returns None for invalid JSON."""
        service = LLMService()
        result = service._parse_json_response('not valid json')
        assert result is None

    def test_validate_and_normalize_clamps_fit_score(self):
        """Test _validate_and_normalize clamps fit_score to 0-100 range."""
        service = LLMService()
        
        # Test upper bound
        result = service._validate_and_normalize({"fit_score": 150})
        assert result["fit_score"] == 100
        
        # Test lower bound
        result = service._validate_and_normalize({"fit_score": -10})
        assert result["fit_score"] == 0
        
        # Test within range
        result = service._validate_and_normalize({"fit_score": 75})
        assert result["fit_score"] == 75

    def test_validate_and_normalize_limits_array_lengths(self):
        """Test _validate_and_normalize limits arrays to max 5 items."""
        service = LLMService()
        data = {
            "fit_score": 50,
            "strengths": ["a", "b", "c", "d", "e", "f", "g"],
            "weaknesses": ["x", "y", "z"],
        }
        result = service._validate_and_normalize(data)
        assert len(result["strengths"]) == 5
        assert len(result["weaknesses"]) == 3

    def test_validate_and_normalize_default_recommendation(self):
        """Test _validate_and_normalize defaults invalid recommendation to 'Consider'."""
        service = LLMService()
        
        # Invalid recommendation
        result = service._validate_and_normalize({
            "fit_score": 50,
            "final_recommendation": "Invalid"
        })
        assert result["final_recommendation"] == "Consider"
        
        # Valid recommendations
        for rec in ["Shortlist", "Consider", "Reject"]:
            result = service._validate_and_normalize({
                "fit_score": 50,
                "final_recommendation": rec
            })
            assert result["final_recommendation"] == rec

    def test_fallback_response_structure(self):
        """Test _fallback_response returns expected structure."""
        service = LLMService()
        result = service._fallback_response("test error")
        
        assert result["fit_score"] == 50
        assert "Analysis temporarily unavailable" in result["strengths"]
        assert result["final_recommendation"] == "Consider"
        assert any("test error" in str(r) for r in result["risk_signals"])


class TestAnalyzeWithLLM:
    """Tests for analyze_with_llm function."""

    @pytest.mark.asyncio
    async def test_analyze_with_llm_returns_result(self):
        """Test analyze_with_llm returns analysis result."""
        with patch.object(LLMService, 'analyze_resume', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {"fit_score": 85}
            
            result = await analyze_with_llm(
                resume_text="test resume",
                job_description="test job",
                skill_match_percent=80.0,
                total_years=5.0,
                gaps=[],
                risks=[]
            )
            
            assert result["fit_score"] == 85


class TestGeminiAnalysisHelpers:
    def test_should_run_ollama_sentinel_false_when_gemini_only(self, monkeypatch):
        from app.backend.services.llm_service import should_run_ollama_sentinel

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OLLAMA_USE_LOCAL_JD_PROFILE", "0")
        assert should_run_ollama_sentinel() is False

    def test_should_run_ollama_sentinel_true_when_local_jd_enabled(self, monkeypatch):
        from app.backend.services.llm_service import should_run_ollama_sentinel

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OLLAMA_USE_LOCAL_JD_PROFILE", "1")
        assert should_run_ollama_sentinel() is True

    def test_get_gemini_model_requires_env(self, monkeypatch):
        from app.backend.services.llm_service import get_gemini_model

        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_MODEL"):
            get_gemini_model()

    def test_get_gemini_model_maps_display_name_to_api_id(self, monkeypatch):
        from app.backend.services.llm_service import get_gemini_model, resolve_gemini_model_for_label

        monkeypatch.setenv("GEMINI_MODEL", "Gemini 3.8 Flash")
        assert get_gemini_model() == "gemini-3.8-flash"

        monkeypatch.setenv("GEMINI_NARRATIVE_MODEL", "models/Gemini 3.8 Flash")
        assert resolve_gemini_model_for_label("narrative_outlines_tier1") == "gemini-3.8-flash"

    def test_get_gemini_kit_model_falls_back_to_gemini_model(self, monkeypatch):
        from app.backend.services.llm_service import get_gemini_kit_model

        monkeypatch.setenv("GEMINI_MODEL", "gemini-primary")
        monkeypatch.delenv("GEMINI_KIT_MODEL", raising=False)
        assert get_gemini_kit_model() == "gemini-primary"

        monkeypatch.setenv("GEMINI_KIT_MODEL", "gemini-kit")
        assert get_gemini_kit_model() == "gemini-kit"

    def test_compute_max_output_tokens_scales_with_prompt(self, monkeypatch):
        from app.backend.services.llm_service import compute_max_output_tokens

        monkeypatch.delenv("LLM_JSON_OUTPUT_TOKENS_MIN", raising=False)
        small = compute_max_output_tokens("x" * 500, requested=1024)
        large = compute_max_output_tokens("x" * 20000, requested=1024)
        assert large > small
        assert compute_max_output_tokens("hi", json_mode=True) >= 4096

    def test_gemini_3_json_requests_thinking_level(self):
        from app.backend.services.llm_service import _gemini_thinking_config

        assert _gemini_thinking_config("gemini-3.5-flash", True) == {"thinkingLevel": "minimal"}
        assert _gemini_thinking_config("gemini-3.7-flash", True) == {"thinkingLevel": "low"}
        assert _gemini_thinking_config("gemini-3.8-flash", True) == {"thinkingLevel": "low"}
        assert _gemini_thinking_config("gemini-3.1-pro", True) == {"thinkingLevel": "low"}
        assert _gemini_thinking_config("gemini-2.5-flash", True) == {"thinkingBudget": 0}

    @pytest.mark.asyncio
    async def test_startup_probe_uses_thinking_config_and_reports_400(self, monkeypatch):
        from app.backend.services import llm_service

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        posted: dict = {}

        class FakeResponse:
            status_code = 400
            text = "Thinking level MINIMAL is not supported"

        async def fake_post(*_args, **kwargs):
            posted["body"] = kwargs["json"]
            return FakeResponse()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = fake_post
            with pytest.raises(RuntimeError, match="400"):
                await llm_service.probe_gemini_config()

        assert posted["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}

    @pytest.mark.asyncio
    async def test_startup_probe_uses_analysis_read_budget(self, monkeypatch):
        from app.backend.services import llm_service
        from app.backend.services.reliability.timeouts import GEMINI_READ

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")

        class Ok:
            status_code = 200
            text = '{"candidates":[]}'

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=Ok())
            await llm_service.probe_gemini_config()

        timeout = mock_client.call_args.kwargs["timeout"]
        assert timeout.read == GEMINI_READ

    @pytest.mark.asyncio
    async def test_startup_probe_names_timeout_and_capacity(self, monkeypatch):
        from app.backend.services import llm_service

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("")
            )
            with pytest.raises(RuntimeError, match="timed out") as timed_out:
                await llm_service.probe_gemini_config()
        assert "incompatible" not in str(timed_out.value).lower()

        class Busy:
            status_code = 503
            text = "This model is currently experiencing high demand"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=Busy())
            with pytest.raises(RuntimeError, match="503") as busy:
                await llm_service.probe_gemini_config()
        assert "busy" in str(busy.value).lower()
        assert "incompatible" not in str(busy.value).lower()

    @pytest.mark.asyncio
    async def test_gemini_raises_truncated_on_max_tokens_json(self, monkeypatch):
        from app.backend.services import llm_service
        from app.backend.services.llm_service import GeminiTruncatedError

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

        ok = MagicMock()
        ok.status_code = 200
        ok.raise_for_status = MagicMock()
        ok.json = MagicMock(return_value={
            "candidates": [{
                "content": {"parts": [{"text": '{"partial": true'}]},
                "finishReason": "MAX_TOKENS",
            }],
        })

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=ok)
            with pytest.raises(GeminiTruncatedError):
                await llm_service.gemini_generate_content(
                    "hello",
                    response_mime_type="application/json",
                )

    @pytest.mark.asyncio
    async def test_extract_jd_profile_uses_gemini_when_configured(self, monkeypatch):
        from app.backend.services.llm_service import LLMService

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

        svc = LLMService()
        mock_json = '{"role_title": "Engineer", "domain": "Backend Engineering", "domain_keywords": ["python"], "architecture_signals": ["designed"], "education_fields": [], "min_required_years": 3, "max_required_years": 8, "seniority": "mid", "required_skills": ["Python"], "nice_to_have_skills": []}'

        with patch.object(
            svc,
            "_call_ollama",
            new=AsyncMock(side_effect=RuntimeError("ollama down")),
        ) as mock_ollama, patch(
            "app.backend.services.llm_service.gemini_generate_content",
            new=AsyncMock(
                return_value=__import__(
                    "app.backend.services.llm_service",
                    fromlist=["GeminiGenerateResult"],
                ).GeminiGenerateResult(mock_json, "STOP"),
            ),
        ) as mock_gemini:
            result = await svc.extract_jd_profile("Senior Python Engineer role")

        mock_ollama.assert_awaited_once()
        mock_gemini.assert_awaited_once()
    @pytest.mark.asyncio
    async def test_gemini_retries_on_503(self, monkeypatch):
        from app.backend.services import llm_service

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
        monkeypatch.setenv("GEMINI_MAX_RETRIES", "2")

        call_count = 0

        class FakeResponse:
            status_code = 503
            text = '{"error":{"code":503}}'

            def raise_for_status(self):
                import httpx
                raise httpx.HTTPStatusError("503", request=MagicMock(), response=self)

        async def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return FakeResponse()
            ok = MagicMock()
            ok.status_code = 200
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={
                "candidates": [{
                    "content": {"parts": [{"text": '{"ok": true}'}]},
                    "finishReason": "STOP",
                }],
            })
            return ok

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = fake_post
            with patch("asyncio.sleep", new=AsyncMock()):
                result = await llm_service.gemini_generate_content("hello")

        assert call_count == 3
        assert "ok" in result.text
