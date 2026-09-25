"""Voice planner LLM. Ollama only. LiveKit's own model slot is cloud_agent.py."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from app.backend.services.external_ai_boundary import (
    PreparedExternalPrompt,
    prepare_external_llm_prompt,
)

logger = logging.getLogger("voice_agent.voice_llm")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL_VOICE", os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"))
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

_http_client: httpx.AsyncClient | None = None


def use_gemini_for_voice() -> bool:
    """In-process voice planning stays on Ollama. LiveKit cloud keeps LIVEKIT_LLM_MODEL."""
    return False


def get_voice_llm_model() -> str:
    from app.backend.services.llm_service import get_gemini_voice_model, get_ollama_voice_model

    if use_gemini_for_voice():
        return get_gemini_voice_model()
    return get_ollama_voice_model()


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=45.0)
    return _http_client


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (r"```json\s*(\{.*?\})\s*```", r"(\{.*\})"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None


def _redact_key(text: str) -> str:
    return re.sub(r"key=[^&\s\"']+", "key=***", text or "")


async def generate_json(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int = 384,
    temperature: float = 0.3,
) -> dict[str, Any] | None:
    """Single LLM call returning a parsed JSON object. Falls back Gemini → Ollama."""
    prepared = prepare_external_llm_prompt(prompt, system=system)
    if use_gemini_for_voice():
        result = await _gemini_json(
            prepared,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        if result:
            return result
        logger.warning(
            "Gemini voice LLM unavailable — falling back to Ollama (%s)",
            get_voice_llm_model(),
        )

    return await _ollama_json(
        prepared,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )


async def _gemini_json(
    prepared: PreparedExternalPrompt,
    *,
    max_output_tokens: int,
    temperature: float,
) -> dict[str, Any] | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = get_voice_llm_model()
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"

    for use_json_mime in (True, False):
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prepared.prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if use_json_mime:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if prepared.system:
            body["systemInstruction"] = {"parts": [{"text": prepared.system}]}

        client = await _get_client()
        try:
            resp = await client.post(
                url,
                params={"key": api_key},
                json=body,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Gemini voice HTTP %s (model=%s, json_mime=%s): %s",
                    resp.status_code,
                    model,
                    use_json_mime,
                    _redact_key(resp.text[:400]),
                )
                continue
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                block = (data.get("promptFeedback") or {}).get("blockReason")
                logger.warning("Gemini voice returned no candidates (blockReason=%s)", block)
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            text = parts[0].get("text", "") if parts else ""
            parsed = _parse_json(text)
            if parsed:
                return parsed
            logger.warning("Gemini voice response was not valid JSON: %s", text[:200])
        except Exception as exc:
            logger.warning("Gemini voice LLM failed (json_mime=%s): %s", use_json_mime, exc)

    return None


async def _ollama_json(
    prepared: PreparedExternalPrompt,
    *,
    max_output_tokens: int,
    temperature: float,
) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    if OLLAMA_API_KEY.strip():
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY.strip()}"

    full_prompt = prepared.prompt
    if prepared.system:
        full_prompt = f"{prepared.system}\n\n{prepared.prompt}"

    ollama_model = get_voice_llm_model()
    client = await _get_client()
    try:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": ollama_model,
                "prompt": full_prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": temperature,
                    "num_predict": max_output_tokens,
                },
            },
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Ollama voice HTTP %s (model=%s): %s",
                resp.status_code,
                ollama_model,
                resp.text[:300],
            )
            return None
        text = resp.json().get("response", "")
        return _parse_json(text)
    except Exception as exc:
        logger.warning("Ollama voice LLM failed: %s", exc)
        return None
