import json
import os
import httpx
import enum
import asyncio
import time
import logging
import random
import re
from dataclasses import dataclass
from typing import Dict, Any, Optional
from app.backend.services.reliability.timeouts import (
    GEMINI_CONNECT,
    GEMINI_READ,
    OLLAMA_CONNECT,
    OLLAMA_READ,
    httpx_timeout,
)

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_TRUNCATED_FINISH_REASONS = frozenset({"MAX_TOKENS", "LENGTH"})


@dataclass(frozen=True)
class GeminiGenerateResult:
    text: str
    finish_reason: str = "STOP"

    @property
    def truncated(self) -> bool:
        return self.finish_reason in _TRUNCATED_FINISH_REASONS


class GeminiTruncatedError(RuntimeError):
    """Raised when Gemini stops due to output token limit (JSON tasks should failover)."""

    def __init__(self, result: GeminiGenerateResult):
        self.result = result
        super().__init__(
            f"Gemini output truncated ({result.finish_reason}, {len(result.text)} chars)"
        )


def fallback_on_gemini_max_tokens() -> bool:
    return os.getenv("LLM_FALLBACK_ON_MAX_TOKENS", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _redact_secrets(text: str) -> str:
    """Strip API keys from error messages before logging."""
    if not text:
        return text
    redacted = re.sub(r"key=[^&\s\"']+", "key=***REDACTED***", text, flags=re.IGNORECASE)
    redacted = re.sub(r"(Authorization:\s*Bearer\s+)\S+", r"\1***REDACTED***", redacted, flags=re.IGNORECASE)
    return redacted


def use_local_jd_profile() -> bool:
    """Local CPU Ollama for JD profile — off by default, especially when Gemini is primary."""
    return os.getenv("OLLAMA_USE_LOCAL_JD_PROFILE", "").strip().lower() in ("1", "true", "yes")


# ─── Google Gemini (direct API) ─────────────────────────────────────────────

def use_gemini_for_analysis() -> bool:
    """When GEMINI_API_KEY is set, analysis LLM calls use Google Gemini instead of Ollama."""
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def should_run_ollama_sentinel() -> bool:
    """Skip local Ollama warmup probes when analysis runs entirely on Gemini."""
    if not use_gemini_for_analysis():
        return True
    if use_local_jd_profile():
        return True
    return False


def require_env(name: str, *, purpose: str = "") -> str:
    """Return a required env var; model names must be configured outside application code."""
    val = os.getenv(name, "").strip()
    if not val:
        suffix = f" ({purpose})" if purpose else ""
        raise RuntimeError(
            f"{name} is not set{suffix}. Configure it in .env or docker-compose."
        )
    return val


def normalize_gemini_model_id(raw: str) -> str:
    """Turn a console display name into a generateContent model id.

    Portainer and AI Studio show "Gemini 3.8 Flash". The API id is
    gemini-3.8-flash; the display name is rejected as an unexpected format.
    """
    value = raw.strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.lower().replace("_", "-").replace(" ", "-")
    return re.sub(r"-{2,}", "-", value).strip("-")


def get_gemini_model() -> str:
    return normalize_gemini_model_id(require_env("GEMINI_MODEL", purpose="Google Gemini LLM"))


def get_gemini_kit_model() -> str:
    kit = os.getenv("GEMINI_KIT_MODEL", "").strip()
    return normalize_gemini_model_id(kit) if kit else get_gemini_model()


def get_gemini_voice_model() -> str:
    voice = os.getenv("GEMINI_MODEL_VOICE", "").strip()
    return normalize_gemini_model_id(voice) if voice else get_gemini_model()


def get_openrouter_model() -> str:
    return require_env("OPENROUTER_MODEL", purpose="OpenRouter LLM fallback")


def get_ollama_model() -> str:
    model = (
        os.getenv("OLLAMA_MODEL_BACKEND", "").strip()
        or os.getenv("OLLAMA_MODEL", "").strip()
    )
    if not model:
        raise RuntimeError(
            "OLLAMA_MODEL or OLLAMA_MODEL_BACKEND must be set. "
            "Configure in .env or docker-compose."
        )
    lowered = model.lower()
    if "gemini" in lowered or lowered.startswith("gemma"):
        logger.warning(
            "OLLAMA_MODEL=%s is Google/Gemma family; prefer DeepSeek or Qwen for "
            "fallback diversity from direct Gemini API",
            model,
        )
    return model


def get_ollama_voice_model() -> str:
    model = (
        os.getenv("OLLAMA_MODEL_VOICE", "").strip()
        or os.getenv("OLLAMA_MODEL", "").strip()
        or os.getenv("OLLAMA_MODEL_BACKEND", "").strip()
    )
    if not model:
        raise RuntimeError(
            "OLLAMA_MODEL_VOICE, OLLAMA_MODEL, or OLLAMA_MODEL_BACKEND must be set."
        )
    return model


def resolve_gemini_model_for_label(log_label: str) -> str:
    label = (log_label or "").lower()
    if "interview_kit" in label:
        return get_gemini_kit_model()
    narrative = os.getenv("GEMINI_NARRATIVE_MODEL", "").strip()
    if narrative and "narrative" in label:
        return normalize_gemini_model_id(narrative)
    return get_gemini_model()


def compute_max_output_tokens(
    prompt: str,
    *,
    system: str | None = None,
    requested: int | None = None,
    json_mode: bool = False,
) -> int:
    """Derive a reasonable max output token budget from prompt size and task type."""
    combined = f"{system or ''}\n{prompt}".strip()
    chars = len(combined)

    floor = int(os.getenv("LLM_OUTPUT_TOKENS_FLOOR", "2048"))
    ceiling = int(os.getenv("LLM_OUTPUT_TOKENS_CEILING", "8192"))
    base = int(os.getenv("LLM_OUTPUT_TOKENS_BASE", "3000"))
    # Scale output headroom with input size (~1 token per 40 input chars).
    scaled = base + chars // 40

    if json_mode:
        scaled = max(scaled, int(os.getenv("LLM_JSON_OUTPUT_TOKENS_MIN", "4096")))

    if requested is not None:
        scaled = max(scaled, requested)

    return max(floor, min(ceiling, scaled))


def _gemini_thinking_config(model: str, json_mode: bool) -> dict[str, int | str] | None:
    """Ask Gemini to answer without a long hidden think.

    generateContent sends one body when generation finishes. Gemini 3 defaults to
    thinkingLevel medium and does not honor thinkingBudget, so the client sits on
    an open socket until GEMINI_READ with no bytes. Gemini 3.7+ Flash rejects
    thinkingLevel minimal. Gemini 2.5 still uses thinkingBudget.
    """
    from app.backend.services.gemini_model_caps import gemini_thinking_config

    return gemini_thinking_config(
        model,
        json_mode,
        pro_budget=int(os.getenv("GEMINI_PRO_THINKING_BUDGET", "128")),
    )


async def probe_gemini_config() -> None:
    """One cheap generateContent using the production model and thinking config.

    Raises when the key, model id, or thinking level is rejected. Startup uses
    this so a present API key is not reported as READY.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = get_gemini_model()
    generation_config: dict = {
        "temperature": 0,
        "maxOutputTokens": 16,
        "responseMimeType": "application/json",
    }
    thinking = _gemini_thinking_config(model, True)
    if thinking is not None:
        generation_config["thinkingConfig"] = thinking
    async with httpx.AsyncClient(timeout=httpx_timeout(GEMINI_CONNECT, 8)) as client:
        response = await client.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": 'Reply with JSON: {"ok": true}'}]}],
                "generationConfig": generation_config,
            },
            headers={"Content-Type": "application/json"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini API HTTP {response.status_code}: {response.text[:180]}")


async def gemini_generate_content(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 1500,
    temperature: float = 0.1,
    response_mime_type: str | None = None,
    model: str | None = None,
) -> GeminiGenerateResult:
    """Call Google Gemini generateContent API (AI Studio / API key auth)."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    resolved_model = model or get_gemini_model()
    json_mode = bool(response_mime_type)
    effective_max = compute_max_output_tokens(
        prompt,
        system=system,
        requested=max_output_tokens,
        json_mode=json_mode,
    )
    url = f"{GEMINI_API_BASE}/models/{resolved_model}:generateContent"
    generation_config: dict = {
        "temperature": temperature,
        "maxOutputTokens": effective_max,
    }
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    thinking = _gemini_thinking_config(resolved_model, json_mode)
    if thinking is not None:
        generation_config["thinkingConfig"] = thinking
    if effective_max != max_output_tokens:
        logger.debug(
            "Gemini maxOutputTokens scaled %s -> %s (prompt_chars=%d, json=%s)",
            max_output_tokens,
            effective_max,
            len(f"{system or ''}\n{prompt}"),
            json_mode,
        )
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    # Durable queue execution owns the total retry budget; provider calls are single-shot.
    max_retries = int(os.getenv("GEMINI_MAX_RETRIES", "0"))
    last_error: Exception | None = None
    data: dict | None = None

    sem = get_gemini_semaphore()
    async with sem:
        await _gemini_throttle_wait()
        async with httpx.AsyncClient(
            timeout=httpx_timeout(GEMINI_CONNECT, GEMINI_READ),
        ) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(
                        url,
                        params={"key": api_key},
                        json=body,
                        headers={"Content-Type": "application/json"},
                    )
                    if response.status_code in (429, 503) and attempt < max_retries:
                        delay = (2 ** attempt) + random.uniform(0.5, 1.5)
                        logger.warning(
                            "Gemini API %s (model=%s), retry %d/%d after %.1fs",
                            response.status_code,
                            resolved_model,
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    if response.status_code >= 400:
                        logger.warning(
                            "Gemini API error %s: %s",
                            response.status_code,
                            response.text[:500],
                        )
                    response.raise_for_status()
                    data = response.json()
                    break
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status = exc.response.status_code if exc.response else 0
                    if status in (429, 503) and attempt < max_retries:
                        delay = (2 ** attempt) + random.uniform(0.5, 1.5)
                        logger.warning(
                            "Gemini HTTP %s, retry %d/%d after %.1fs",
                            status,
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(
                        f"Gemini API HTTP {status}: {_redact_secrets(str(exc))[:200]}"
                    ) from exc
                except Exception as exc:
                    last_error = exc
                    raise
            else:
                raise RuntimeError(
                    f"Gemini API unavailable after {max_retries + 1} attempts: "
                    f"{_redact_secrets(str(last_error))[:200] if last_error else 'unknown error'}"
                )

    if data is None:
        raise RuntimeError("Gemini API returned no data")

    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError(f"Gemini returned no candidates (blockReason={block})")

    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    finish_reason = candidates[0].get("finishReason") or ""
    usage = data.get("usageMetadata") or {}
    if finish_reason and finish_reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        logger.warning(
            "Gemini finishReason=%s (model=%s, chars=%d, maxOutputTokens=%d, "
            "thoughts=%s, output=%s)",
            finish_reason,
            resolved_model,
            len(text.strip()),
            effective_max,
            usage.get("thoughtsTokenCount"),
            usage.get("candidatesTokenCount"),
        )

    result = GeminiGenerateResult(text=text.strip(), finish_reason=finish_reason or "STOP")
    if (
        result.truncated
        and json_mode
        and fallback_on_gemini_max_tokens()
    ):
        raise GeminiTruncatedError(result)
    return result


def create_gemini_chat_llm(
    *,
    max_output_tokens: int = 4000,
    response_mime_type: str | None = None,
):
    """LangChain chat model for Gemini (used by hybrid pipeline ainvoke)."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    kwargs: dict = {
        "model": get_gemini_model(),
        "google_api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "temperature": 0.1,
        "max_output_tokens": max_output_tokens,
    }
    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    return ChatGoogleGenerativeAI(**kwargs)


def use_gemini_for_recruiter() -> bool:
    """Recruiter agents use Gemini when GEMINI_API_KEY is set."""
    return use_gemini_for_analysis()


async def generate_recruiter_llm(
    prompt: str,
    *,
    max_output_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> str | None:
    """LLM text generation for recruiter agents (Gemini primary, Ollama fallback)."""
    from app.backend.services.app_llm_client import generate_app_llm

    return await generate_app_llm(
        prompt,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=True,
        log_label="recruiter",
    )


# ─── Ollama Cloud Detection & Headers ────────────────────────────────────────

def is_ollama_cloud(base_url: str) -> bool:
    """Check if the base URL points to Ollama Cloud (ollama.com)."""
    return "ollama.com" in base_url.lower()


def get_ollama_headers(base_url: str) -> Dict[str, str]:
    """
    Build headers for Ollama API requests.
    Adds Authorization header when using Ollama Cloud with API key.
    """
    headers = {}
    if is_ollama_cloud(base_url):
        api_key = os.getenv("OLLAMA_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            logger.debug("Ollama Cloud detected: using API key authentication")
        else:
            logger.warning("Ollama Cloud detected but OLLAMA_API_KEY is not set!")
    return headers

# ─── Shared Gemini throttle ───────────────────────────────────────────────────
# Serializes Gemini calls and spaces them out to avoid 429 bursts during
# narrative → interview kit → personalizer chains on a single screening.
_gemini_semaphore: asyncio.Semaphore | None = None
_gemini_throttle_lock: asyncio.Lock | None = None
_gemini_last_call_at: float = 0.0


def get_gemini_semaphore(max_concurrent: int | None = None) -> asyncio.Semaphore:
    """Shared Gemini concurrency limit (default 1 — safest for free/low quotas)."""
    global _gemini_semaphore
    if _gemini_semaphore is None:
        if max_concurrent is None:
            env_val = os.getenv("GEMINI_MAX_CONCURRENT")
            max_concurrent = int(env_val) if env_val is not None else 1
        _gemini_semaphore = asyncio.Semaphore(max(1, max_concurrent))
    return _gemini_semaphore


async def _gemini_throttle_wait() -> None:
    """Enforce a minimum gap between Gemini API calls."""
    global _gemini_last_call_at, _gemini_throttle_lock
    if _gemini_throttle_lock is None:
        _gemini_throttle_lock = asyncio.Lock()

    min_interval = float(os.getenv("GEMINI_MIN_INTERVAL_S", "1.25"))
    async with _gemini_throttle_lock:
        elapsed = time.monotonic() - _gemini_last_call_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        _gemini_last_call_at = time.monotonic()


# ─── Shared Ollama Semaphore ─────────────────────────────────────────────────
# Prevents LLM contention across resume narrative, video analysis, and transcript analysis.
# Local Ollama may support limited parallelism; cloud supports higher concurrency.
_ollama_semaphore: asyncio.Semaphore | None = None


def get_ollama_semaphore(max_concurrent: int | None = None) -> asyncio.Semaphore:
    """Get the shared Ollama semaphore. Lazily creates it if needed.

    If max_concurrent is not provided, auto-detects based on OLLAMA_BASE_URL:
      - Ollama Cloud (https:// or ollama.com) → default 8
      - Local Ollama → default 1
    Can be overridden with OLLAMA_MAX_CONCURRENT env var.
    """
    global _ollama_semaphore
    if _ollama_semaphore is None:
        if max_concurrent is None:
            # Check env var first
            env_val = os.getenv("OLLAMA_MAX_CONCURRENT")
            if env_val is not None:
                max_concurrent = int(env_val)
            else:
                # Auto-detect: cloud vs local
                base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
                if base_url.startswith("https://") or "ollama.com" in base_url.lower():
                    max_concurrent = 4  # Cloud: conservative to avoid 429 rate limits
                else:
                    max_concurrent = 1  # Local Ollama is single-threaded
        _ollama_semaphore = asyncio.Semaphore(max_concurrent)
    return _ollama_semaphore


class OllamaState(str, enum.Enum):
    COLD = "cold"        # Model not loaded in RAM
    WARMING = "warming"  # Warmup in progress
    HOT = "hot"          # Model loaded and responsive
    ERROR = "error"      # Ollama unreachable or failing


class OllamaHealthSentinel:
    def __init__(self, ollama_base_url: str = None, model_name: str = None, probe_interval: int = 60):
        self.base_url = ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        self.model_name = model_name or get_ollama_model()
        self.probe_interval = probe_interval
        self.state = OllamaState.COLD
        self.last_probe_time: float = 0
        self.last_latency_ms: float = 0
        self._task: asyncio.Task | None = None
        self._running = False
        self._is_cloud = is_ollama_cloud(self.base_url)

    async def start(self):
        """Start the sentinel background loop."""
        self._running = True
        self._task = asyncio.create_task(self._probe_loop())
        logger.info("Ollama health sentinel started (interval=%ds)", self.probe_interval)

    async def stop(self):
        """Stop the sentinel gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Ollama health sentinel stopped")

    async def _probe_loop(self):
        """Main probe loop — runs every probe_interval seconds."""
        while self._running:
            await self._probe_once()
            await asyncio.sleep(self.probe_interval)

    async def _probe_once(self):
        """Single health probe: lightweight generate with num_predict=1."""
        # Skip local health checks for Ollama Cloud — cloud doesn't need warmup
        if self._is_cloud:
            self.state = OllamaState.HOT
            self.last_probe_time = time.time()
            self.last_latency_ms = 0
            return

        start = time.monotonic()
        headers = get_ollama_headers(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Check if model is loaded via /api/ps
                resp = await client.get(f"{self.base_url}/api/ps", headers=headers)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_hot = any(self.model_name in m.get("name", "") for m in models)
                else:
                    model_hot = False

                if not model_hot:
                    # Model not in RAM — trigger warmup
                    self.state = OllamaState.WARMING
                    logger.info("Ollama model %s not hot, triggering warmup", self.model_name)
                    await client.post(
                        f"{self.base_url}/api/generate",
                        headers=headers,
                        json={"model": self.model_name, "prompt": "warmup", "stream": False, "options": {"num_predict": 1}},
                        timeout=120.0
                    )
                    self.state = OllamaState.HOT
                    logger.info("Ollama model %s warmed up successfully", self.model_name)
                else:
                    # Model is hot — /api/ps confirmed it's loaded, no need for generate probe
                    self.state = OllamaState.HOT

                self.last_latency_ms = (time.monotonic() - start) * 1000
                self.last_probe_time = time.time()

        except Exception as e:
            self.state = OllamaState.ERROR
            self.last_latency_ms = (time.monotonic() - start) * 1000
            self.last_probe_time = time.time()
            logger.warning("Ollama health probe failed: %s: %s", type(e).__name__, e)

    def get_status(self) -> dict:
        status = {
            "state": self.state.value,
            "model": self.model_name,
            "last_probe_time": self.last_probe_time,
            "last_latency_ms": round(self.last_latency_ms, 1),
            "healthy": self.state == OllamaState.HOT,
        }
        # Add cloud indicator for better visibility
        if self._is_cloud:
            status["mode"] = "cloud"
            status["message"] = f"Using Ollama Cloud with model: {self.model_name}"
        else:
            status["mode"] = "local"
        return status


# Module-level singleton
_sentinel: OllamaHealthSentinel | None = None


def get_sentinel() -> OllamaHealthSentinel | None:
    return _sentinel


class LLMService:
    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = os.getenv("OLLAMA_MODEL_BACKEND", "").strip() or get_ollama_model()
        self.max_retries = 0
        # Local model for fast skill extraction (JD profile + resume skills)
        self._local_base_url = os.getenv("OLLAMA_LOCAL_URL", "http://ollama:11434")
        self._local_model = os.getenv("OLLAMA_MODEL_SKILLS", "qwen3.5:2b")

    async def analyze_resume(
        self,
        resume_text: str,
        job_description: str,
        skill_match_percent: float,
        total_years: float,
        gaps: list,
        risks: list
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(
            resume_text=resume_text,
            job_description=job_description,
            skill_match_percent=skill_match_percent,
            total_years=total_years,
            gaps=gaps,
            risks=risks
        )

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._call_ollama(prompt)
                parsed = self._parse_json_response(response)
                if parsed:
                    return self._validate_and_normalize(parsed)
            except Exception as e:
                if attempt == self.max_retries:
                    return self._fallback_response(str(e))

        return self._fallback_response("Max retries exceeded")

    async def generate_text(self, prompt: str) -> str:
        """Public interface for text generation (Gemini primary, Ollama fallback)."""
        from app.backend.services.app_llm_client import generate_app_llm

        text = await generate_app_llm(
            prompt,
            max_output_tokens=2048,
            temperature=0.3,
            log_label="generate_text",
        )
        return text or ""

    async def extract_jd_profile(self, job_description: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Extract a structured, domain-agnostic profile from a job description.

        The profile drives the rest of the pipeline: domain detection, architecture
        scoring, education relevance, and experience alignment.  A clean JSON schema
        is requested from the model.
        """
        if not job_description:
            return self._fallback_jd_profile()

        prompt = self._build_jd_profile_prompt(job_description)
        _timeout = (timeout or 60) + 10

        if use_gemini_for_analysis():
            for attempt in range(self.max_retries + 1):
                try:
                    response = await gemini_generate_content(
                        prompt,
                        system="Return ONLY a valid JSON object. No markdown, no code fences.",
                        max_output_tokens=2000,
                        temperature=0.1,
                    )
                    parsed = self._parse_json_response(response.text)
                    if parsed:
                        logger.info("JD profile extracted via Google Gemini (model=%s)", get_gemini_model())
                        return self._validate_jd_profile(parsed)
                except Exception as e:
                    logger.warning(
                        "JD profile Gemini extraction failed (attempt %d): %s",
                        attempt + 1,
                        _redact_secrets(str(e)[:200]),
                    )
            logger.info("Gemini JD profile failed — trying Ollama cloud fallback")

        # Cloud Ollama before local CPU (fast, works without local ollama container)
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._call_ollama(prompt, timeout=_timeout)
                parsed = self._parse_json_response(response)
                if parsed:
                    logger.info("JD profile extracted via Ollama cloud")
                    return self._validate_jd_profile(parsed)
            except Exception as e:
                logger.warning(
                    "JD profile cloud extraction failed (attempt %d): %s",
                    attempt + 1,
                    _redact_secrets(str(e)[:200]),
                )

        # Local CPU Ollama — opt-in only (OLLAMA_USE_LOCAL_JD_PROFILE=1)
        if use_local_jd_profile():
            for attempt in range(self.max_retries + 1):
                try:
                    response = await self._call_ollama_local(prompt, timeout=_timeout)
                    parsed = self._parse_json_response(response)
                    if parsed:
                        return self._validate_jd_profile(parsed)
                except Exception as e:
                    logger.warning(
                        "JD profile local extraction failed (attempt %d): %s",
                        attempt + 1,
                        _redact_secrets(str(e)[:200]),
                    )

        logger.warning("JD profile LLM extraction exhausted — using rules fallback")
        return self._fallback_jd_profile()

    # ─── Layer 2: LLM Resume Skill Extraction ───────────────────────────────

    async def extract_resume_skills(
        self,
        resume_text: str,
        timeout: Optional[float] = None,
        jd_domain: Optional[str] = None,
        target_skills: Optional[list] = None,
    ) -> list:
        """Extract domain-specific skills from resume text using LLM.

        Args:
            resume_text: The resume content to extract skills from.
            timeout: Optional timeout for the LLM call.
            jd_domain: Optional domain context (e.g., 'sap', 'salesforce') to guide extraction.
            target_skills: Optional list of target skills from the JD to confirm in the resume.

        Returns a list of canonical skill strings.  Falls back to an empty
        list on any error so the caller can proceed with rule-based skills only.
        """
        if not resume_text or not resume_text.strip():
            return []

        prompt = self._build_resume_skills_prompt(
            resume_text,
            jd_domain=jd_domain,
            target_skills=target_skills,
        )
        _timeout = (timeout or 60) + 10

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._call_ollama_local(prompt, timeout=_timeout)
                skills = self._parse_resume_skills_response(response)
                if skills:
                    return skills
            except Exception as e:
                logger.warning("Resume skill extraction failed (attempt %d): %s", attempt + 1, e)
                if attempt == self.max_retries:
                    return []

        return []

    def _build_resume_skills_prompt(
        self,
        resume_text: str,
        jd_domain: Optional[str] = None,
        target_skills: Optional[list] = None,
    ) -> str:
        """Build a strict JSON prompt for LLM resume skill extraction.

        If target_skills is provided, the prompt will ask the LLM to confirm
        which of those skills are present in the resume, along with any additional
        skills found. This improves extraction accuracy by using the JD as a guide.
        """
        resume_summary = resume_text[:6000] if len(resume_text) > 6000 else resume_text

        base_instructions = """You are a precise resume parser. Extract ALL technical and domain-specific skills from the resume below.

Return ONLY a JSON array of skill strings. No explanations, no markdown, no trailing text.

Rules:
- Extract specific technologies, tools, methodologies, and domain concepts
- Include both explicitly stated skills and skills clearly implied by work descriptions
- Use canonical industry names (e.g., "SAP MM" not "SAP Materials Management")
- Include module/ecosystem names (e.g., "SAP S/4HANA", "SAP SD", "SAP FI")
- Include methodologies (e.g., "Procure-to-Pay", "Data Migration", "Cutover")
- Include tools (e.g., "LSMW", "BDC", "Solution Manager", "ServiceNow")
- Include certifications (e.g., "SAP Certified Application Associate")
- Do NOT include soft skills (e.g., "communication", "leadership")
- Do NOT include generic terms (e.g., "management", "training")"""

        if jd_domain or target_skills:
            domain_context = f"""

CONTEXT: This candidate is being evaluated for a {jd_domain or 'general'} role.""" if jd_domain else ""

            target_context = ""
            if target_skills:
                skills_list = ", ".join(f'"{s}"' for s in target_skills[:30])
                target_context = f"""

TARGET SKILLS TO CONFIRM: Check if the resume contains evidence of these skills: [{skills_list}].
For each target skill, only include it in the output if you find clear evidence in the resume text (explicit mention or clear implication).
Also look for any additional relevant skills not in the target list."""

            return f"""{base_instructions}{domain_context}{target_context}

RESUME:
{resume_summary}

JSON:"""

        return f"""{base_instructions}

RESUME:
{resume_summary}

JSON:"""

    def _parse_resume_skills_response(self, response: str) -> list:
        """Parse the LLM response into a list of skill strings."""
        # Try direct JSON parse first
        try:
            skills = json.loads(response)
            if isinstance(skills, list):
                return [str(s).strip() for s in skills if s and isinstance(s, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to extract a JSON array from the response
        import re
        array_match = re.search(r'\[.*?\]', response, re.DOTALL)
        if array_match:
            try:
                skills = json.loads(array_match.group(0))
                if isinstance(skills, list):
                    return [str(s).strip() for s in skills if s and isinstance(s, (str, int, float))]
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: extract quoted strings
        matches = re.findall(r'"([^"]+)"', response)
        return matches if matches else []

    async def _call_ollama(self, prompt: str, timeout: Optional[float] = None) -> str:
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        headers = get_ollama_headers(self.base_url)
        _timeout = timeout or OLLAMA_READ
        async with httpx.AsyncClient(
            timeout=httpx_timeout(OLLAMA_CONNECT, _timeout),
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    async def _call_ollama_local(self, prompt: str, timeout: Optional[float] = None) -> str:
        """Use local model for fast skill extraction (JD profile + resume skills)."""
        url = f"{self._local_base_url}/api/generate"
        logger.info(f"[LLM] Calling local Ollama: {url} with model {self._local_model}")

        payload = {
            "model": self._local_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        # Local model doesn't need auth headers
        _timeout = timeout or OLLAMA_READ
        try:
            async with httpx.AsyncClient(
                timeout=httpx_timeout(OLLAMA_CONNECT, _timeout),
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
        except Exception as e:
            logger.error(f"[LLM] Local Ollama failed: {type(e).__name__}: {e}")
            raise

    def _build_jd_profile_prompt(self, job_description: str) -> str:
        """Build a strict JSON prompt for domain-agnostic JD profile extraction."""
        jd_summary = job_description[:4000] if len(job_description) > 4000 else job_description
        return f"""You are a precise talent-acquisition parser. Analyze the job description below and return a single JSON object with no markdown, no explanations, and no trailing text.

Extract the following fields:
- "role_title": the exact job title or the most concise professional title for this role.
- "domain": a free-text domain label (e.g., "SAP/ERP", "Healthcare", "Finance", "Backend Engineering", "Data Science", "DevOps", "Sales", "Legal", "Manufacturing"). Use the domain name most commonly used in industry.
- "domain_keywords": 10-20 specific terms, tools, technologies, certifications, or concepts that indicate this domain. Include abbreviations and synonyms. Do not include generic soft skills.
- "architecture_signals": 8-12 role-excellence signals that show a senior candidate has designed, led, scaled, or delivered outcomes in this domain. Examples: "designed", "architected", "led implementation", "configured", "optimized", "cutover", "compliance", "workflow design", "process blueprint", "solution design", "mentored", "stakeholder management".
- "relevant_education_fields": 5-10 relevant degree fields, majors, or certifications (e.g., ["Computer Science", "MBA", "Supply Chain", "Industrial Engineering", "SAP Certification"]).
- "required_skills": the hard must-have skills/qualifications for the role.
- "nice_to_have_skills": preferred but not mandatory skills.
- "min_required_years": minimum years of experience required (0 if not specified).
- "max_required_years": maximum years of experience requested (0 if not specified; use the upper bound of a range like "5-8").
- "seniority": "junior", "mid", "senior", or "lead".

Return JSON:
{{
  "role_title": "...",
  "domain": "...",
  "domain_keywords": ["..."],
  "architecture_signals": ["..."],
  "relevant_education_fields": ["..."],
  "required_skills": ["..."],
  "nice_to_have_skills": ["..."],
  "min_required_years": 0,
  "max_required_years": 0,
  "seniority": "..."
}}

JOB DESCRIPTION:
{jd_summary}

JSON:"""

    def _validate_jd_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize and sanitize the LLM-extracted JD profile."""
        def _as_list(value, default=None):
            if default is None:
                default = []
            if isinstance(value, list):
                return [str(v).strip() for v in value if v]
            return default

        min_years = int(data.get("min_required_years", 0) or 0)
        max_years = int(data.get("max_required_years", 0) or 0)
        if max_years and max_years < min_years:
            max_years = min_years

        seniority = str(data.get("seniority", "mid")).lower()
        if seniority not in ("junior", "mid", "senior", "lead"):
            seniority = "mid"

        return {
            "role_title": str(data.get("role_title", "Not specified")).strip() or "Not specified",
            "domain": str(data.get("domain", "General")).strip() or "General",
            "domain_keywords": _as_list(data.get("domain_keywords")),
            "architecture_signals": _as_list(data.get("architecture_signals")),
            "relevant_education_fields": _as_list(data.get("relevant_education_fields")),
            "required_skills": _as_list(data.get("required_skills")),
            "nice_to_have_skills": _as_list(data.get("nice_to_have_skills")),
            "min_required_years": min_years,
            "max_required_years": max_years,
            "seniority": seniority,
            "_source": "llm",
        }

    def _fallback_jd_profile(self) -> Dict[str, Any]:
        """Safe fallback when LLM extraction fails."""
        return {
            "role_title": "Not specified",
            "domain": "General",
            "domain_keywords": [],
            "architecture_signals": [],
            "relevant_education_fields": [],
            "required_skills": [],
            "nice_to_have_skills": [],
            "min_required_years": 0,
            "max_required_years": 0,
            "seniority": "mid",
            "_source": "fallback",
        }

    async def _call_ollama(self, prompt: str, timeout: Optional[float] = None) -> str:
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        headers = get_ollama_headers(self.base_url)
        _timeout = timeout or OLLAMA_READ
        async with httpx.AsyncClient(
            timeout=httpx_timeout(OLLAMA_CONNECT, _timeout),
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    def _build_prompt(
        self,
        resume_text: str,
        job_description: str,
        skill_match_percent: float,
        total_years: float,
        gaps: list,
        risks: list
    ) -> str:
        # Truncate text for faster processing
        jd_summary = job_description[:500] if len(job_description) > 500 else job_description
        resume_summary = resume_text[:1000] if len(resume_text) > 1000 else resume_text
        
        return f"""Analyze this candidate for the job. Be concise.

JOB: {jd_summary}

RESUME: {resume_summary}

METRICS: Match {skill_match_percent:.0f}%, Exp {total_years:.1f}y, Gaps {len(gaps)}, Risks {len(risks)}

Return JSON: {{"fit_score": 0-100, "strengths": ["3-5 items"], "weaknesses": ["3-5 items"], "education_analysis": "brief", "risk_signals": ["list"], "final_recommendation": "Shortlist|Consider|Reject"}}

JSON:"""

    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        # Try to find JSON in the response
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))

            json_match = re.search(r'```\s*(\{.*?\})\s*```', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))

            json_match = re.search(r'(\{.*\})', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))

        return None

    def _validate_and_normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure fit_score is within bounds
        fit_score = max(0, min(100, int(data.get("fit_score", 0))))

        # Ensure arrays have max 5 items
        strengths = data.get("strengths", [])[:5]
        weaknesses = data.get("weaknesses", [])[:5]

        # Validate recommendation
        valid_recommendations = ["Shortlist", "Consider", "Reject"]
        recommendation = data.get("final_recommendation", "Consider")
        if recommendation not in valid_recommendations:
            recommendation = "Consider"

        return {
            "fit_score": fit_score,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "education_analysis": data.get("education_analysis", ""),
            "risk_signals": data.get("risk_signals", []),
            "final_recommendation": recommendation
        }

    def _fallback_response(self, error_message: str) -> Dict[str, Any]:
        return {
            "fit_score": 50,
            "strengths": ["Analysis temporarily unavailable"],
            "weaknesses": ["Unable to complete detailed analysis"],
            "education_analysis": "Education analysis could not be completed.",
            "risk_signals": [{"type": "analysis_error", "description": error_message}],
            "final_recommendation": "Consider"
        }


async def analyze_with_llm(
    resume_text: str,
    job_description: str,
    skill_match_percent: float,
    total_years: float,
    gaps: list,
    risks: list
) -> Dict[str, Any]:
    service = LLMService()
    return await service.analyze_resume(
        resume_text=resume_text,
        job_description=job_description,
        skill_match_percent=skill_match_percent,
        total_years=total_years,
        gaps=gaps,
        risks=risks
    )


async def extract_jd_profile_with_llm(
    job_description: str,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Extract a domain-agnostic structured profile from a job description.

    Returns a dict with domain, skills, experience range, education fields,
    and role-excellence signals.  Falls back to a safe default if the LLM fails.
    """
    service = LLMService()
    return await service.extract_jd_profile(job_description, timeout=timeout)


async def extract_resume_skills_with_llm(
    resume_text: str,
    timeout: Optional[float] = None,
    jd_domain: Optional[str] = None,
    target_skills: Optional[list] = None,
) -> list:
    """Extract domain-specific skills from resume text using LLM.

    Args:
        resume_text: The resume content to extract skills from.
        timeout: Optional timeout for the LLM call.
        jd_domain: Optional domain context to guide extraction.
        target_skills: Optional list of target skills from the JD to confirm.

    Returns a list of canonical skill strings.  Falls back to an empty
    list on any error so the caller can proceed with rule-based skills only.
    """
    service = LLMService()
    return await service.extract_resume_skills(
        resume_text,
        timeout=timeout,
        jd_domain=jd_domain,
        target_skills=target_skills,
    )
