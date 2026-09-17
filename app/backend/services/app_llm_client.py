"""Shared application LLM client — Gemini primary, Ollama + OpenRouter fallbacks."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def parse_json_from_llm(text: str) -> dict[str, Any] | None:
    """Parse JSON from an LLM response, tolerating markdown fences."""
    if not text or not str(text).strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for pattern in (
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{.*\})",
    ):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None


async def generate_app_llm(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
    json_mode: bool = False,
    log_label: str = "app",
    allow_provider_fallback: bool = True,
) -> str | None:
    """Generate text via Gemini when configured, else Ollama/OpenRouter fallbacks."""
    from app.backend.services.circuit_breaker import get_circuit_breaker, CircuitBreakerOpenError
    from app.backend.services.external_ai_boundary import prepare_external_prompt
    from app.backend.services.llm_concurrency import LLMConcurrencySaturated, llm_slot

    prompt = prepare_external_prompt(prompt)
    system = prepare_external_prompt(system) if system else None

    breaker = get_circuit_breaker("llm")

    async def _inner() -> str | None:
        return await _generate_app_llm_uncached(
            prompt,
            system=system,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
            log_label=log_label,
            allow_provider_fallback=allow_provider_fallback,
        )

    import time
    from app.backend.services.metrics import LLM_DURATION_SECONDS, LLM_REQUEST_TOTAL

    started = time.perf_counter()
    try:
        with llm_slot("app"):
            result = await breaker.call(_inner)
        LLM_REQUEST_TOTAL.labels(provider="app", outcome="success" if result else "failure").inc()
        return result
    except LLMConcurrencySaturated:
        LLM_REQUEST_TOTAL.labels(provider="app", outcome="failure").inc()
        logger.error("%s LLM concurrency saturated", log_label)
        return None
    except CircuitBreakerOpenError:
        LLM_REQUEST_TOTAL.labels(provider="app", outcome="failure").inc()
        logger.error("%s LLM circuit breaker open", log_label)
        return None
    finally:
        LLM_DURATION_SECONDS.labels(provider="app").observe(time.perf_counter() - started)


async def _try_gemini(
    prompt: str,
    *,
    system: str | None,
    max_output_tokens: int,
    temperature: float,
    json_mode: bool,
    log_label: str,
) -> str | None:
    from app.backend.services.llm_service import (
        GeminiTruncatedError,
        gemini_generate_content,
        resolve_gemini_model_for_label,
        use_gemini_for_analysis,
    )

    if not use_gemini_for_analysis():
        return None
    try:
        gemini_model = resolve_gemini_model_for_label(log_label)
    except RuntimeError as exc:
        logger.warning("%s Gemini skipped: %s", log_label, exc)
        return None
    try:
        result = await gemini_generate_content(
            prompt,
            system=system,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
            model=gemini_model,
        )
        text = result.text
        if text:
            logger.info(
                "%s LLM via Google Gemini (model=%s)",
                log_label,
                gemini_model,
            )
            return text
    except GeminiTruncatedError as exc:
        logger.warning(
            "%s Gemini truncated (%s, %d chars) — trying fallback providers",
            log_label,
            exc.result.finish_reason,
            len(exc.result.text),
        )
    except Exception as exc:
        logger.warning("%s Gemini call failed: %s", log_label, exc)
    return None


async def _try_ollama(
    prompt: str,
    *,
    system: str | None,
    max_output_tokens: int,
    temperature: float,
    timeout: float,
    json_mode: bool,
    log_label: str,
) -> str | None:
    from app.backend.services.llm_service import get_ollama_headers, get_ollama_model, get_ollama_semaphore

    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    ollama_model = get_ollama_model()
    try:
        semaphore = get_ollama_semaphore()
        async with semaphore:
            async with httpx.AsyncClient(timeout=timeout) as client:
                headers = get_ollama_headers(ollama_base)
                if system:
                    resp = await client.post(
                        f"{ollama_base}/api/chat",
                        headers=headers,
                        json={
                            "model": ollama_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            "stream": False,
                            "options": {
                                "temperature": temperature,
                                "num_predict": max_output_tokens,
                            },
                        },
                    )
                    resp.raise_for_status()
                    text = resp.json().get("message", {}).get("content", "") or None
                else:
                    payload: dict[str, Any] = {
                        "model": ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_output_tokens,
                        },
                    }
                    if json_mode:
                        payload["format"] = "json"
                    resp = await client.post(
                        f"{ollama_base}/api/generate",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    text = resp.json().get("response", "") or None
                if text:
                    logger.info("%s LLM via Ollama (model=%s)", log_label, ollama_model)
                return text
    except Exception as exc:
        logger.warning("%s Ollama call failed: %s", log_label, exc)
    return None


async def _try_openrouter(
    prompt: str,
    *,
    system: str | None,
    max_output_tokens: int,
    temperature: float,
    timeout: float,
    json_mode: bool,
    log_label: str,
) -> str | None:
    from app.backend.services.llm_service import get_openrouter_model

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        model = get_openrouter_model()
    except RuntimeError as exc:
        logger.warning("%s OpenRouter skipped: %s", log_label, exc)
        return None
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    title = os.getenv("OPENROUTER_APP_TITLE", "ARIA Resume Screener").strip()
    if title:
        headers["X-Title"] = title

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            text = (choices[0].get("message") or {}).get("content", "") or None
            if text:
                logger.info("%s LLM via OpenRouter (model=%s)", log_label, model)
            return text
    except Exception as exc:
        logger.warning("%s OpenRouter call failed: %s", log_label, exc)
    return None


async def _generate_app_llm_uncached(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
    json_mode: bool = False,
    log_label: str = "app",
    allow_provider_fallback: bool = True,
) -> str | None:
    from app.backend.services.llm_service import compute_max_output_tokens

    effective_max = compute_max_output_tokens(
        prompt,
        system=system,
        requested=max_output_tokens,
        json_mode=json_mode,
    )
    llm_kwargs = {
        "system": system,
        "max_output_tokens": effective_max,
        "temperature": temperature,
        "json_mode": json_mode,
        "log_label": log_label,
    }
    network_kwargs = {**llm_kwargs, "timeout": timeout}

    text = await _try_gemini(prompt, **llm_kwargs)
    if text:
        return text
    if not allow_provider_fallback:
        return None

    text = await _try_ollama(prompt, **network_kwargs)
    if text:
        return text

    return await _try_openrouter(prompt, **network_kwargs)


async def generate_app_llm_providers(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
    json_mode: bool = False,
    log_label: str = "app",
    allow_provider_fallback: bool = True,
) -> tuple[str | None, str | None]:
    """Try each LLM provider in order; returns (text, provider_name)."""
    from app.backend.services.llm_service import compute_max_output_tokens

    effective_max = compute_max_output_tokens(
        prompt,
        system=system,
        requested=max_output_tokens,
        json_mode=json_mode,
    )
    llm_kwargs = {
        "system": system,
        "max_output_tokens": effective_max,
        "temperature": temperature,
        "json_mode": json_mode,
        "log_label": log_label,
    }
    network_kwargs = {**llm_kwargs, "timeout": timeout}

    providers: list[tuple[str, Any]] = [("gemini", _try_gemini)]
    if allow_provider_fallback:
        providers.extend([("ollama", _try_ollama), ("openrouter", _try_openrouter)])

    for name, fn in providers:
        kwargs = network_kwargs if name != "gemini" else llm_kwargs
        try:
            text = await fn(prompt, **kwargs)
        except Exception as exc:
            logger.warning("%s %s call failed: %s", log_label, name, exc)
            continue
        if text and len(str(text).strip()) >= 10:
            return str(text), name
    return None, None


async def generate_app_json(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 120.0,
    log_label: str = "app",
    allow_provider_fallback: bool = True,
) -> dict[str, Any] | None:
    """Generate and parse a JSON object from the application LLM with retries."""
    from app.backend.services.hybrid_pipeline import _parse_llm_json_response
    from app.backend.services.llm_json_service import invoke_llm_json_resilient

    compact = prompt + "\n\nReturn ONLY valid JSON. No markdown."
    parsed = await invoke_llm_json_resilient(
        [prompt, compact],
        max_output_tokens=max_output_tokens,
        log_label=log_label,
        temperature=temperature,
        allow_provider_fallback=allow_provider_fallback,
    )
    if parsed is not None:
        return parsed

    text = await generate_app_llm(
        prompt,
        system=system,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=True,
        log_label=log_label,
        allow_provider_fallback=allow_provider_fallback,
    )
    if not text:
        return None
    return parse_json_from_llm(text) or _parse_llm_json_response(text)
