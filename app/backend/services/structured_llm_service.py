"""
Outlines-backed structured JSON generation for critical LLM outputs.

Uses schema-constrained generation on Gemini and Ollama before falling back to
the legacy parse-and-repair path in llm_json_service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.backend.services.external_ai_boundary import (
    PreparedExternalPrompt,
    prepare_external_llm_prompt,
    require_prepared_external_prompt,
)

log = logging.getLogger("aria.structured_llm")

DEFAULT_TIER_DELAY_S = float(os.getenv("LLM_JSON_TIER_DELAY", "1.5"))
DEFAULT_MAX_TIERS = max(1, int(os.getenv("LLM_JSON_MAX_TIERS", "4")))

T = TypeVar("T", bound=BaseModel)


def is_structured_llm_enabled() -> bool:
    return os.getenv("OUTLINES_STRUCTURED_JSON", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _outlines_available() -> bool:
    # find_spec avoids importing torch/transformers during availability checks.
    # Runtime imports in _try_outlines_* still catch ImportError if the package
    # is present but unloadable.
    import importlib.util
    return importlib.util.find_spec("outlines") is not None


def schema_for_developer_api(output_type: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schemas set additionalProperties; the Developer API rejects that key."""
    schema = output_type.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            node.pop("additional_properties", None)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema


def parse_outlines_json_text(raw: str, output_type: type[T]) -> dict[str, Any] | None:
    if not raw or len(str(raw).strip()) < 2:
        return None
    try:
        data = json.loads(str(raw).strip())
    except json.JSONDecodeError:
        return None
    try:
        validated = output_type.model_validate(data)
    except ValidationError:
        return None
    return validated.model_dump(mode="json")


async def _try_outlines_gemini(
    prepared: PreparedExternalPrompt,
    *,
    output_type: type[BaseModel],
    max_output_tokens: int,
    temperature: float,
    log_label: str,
) -> str | None:
    prepared = require_prepared_external_prompt(prepared)
    from app.backend.services.llm_service import (
        _gemini_thinking_config,
        compute_max_output_tokens,
        resolve_gemini_model_for_label,
        use_gemini_for_analysis,
    )

    if not use_gemini_for_analysis():
        return None
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from google import genai
        from google.genai import types as genai_types
        from app.backend.services.reliability.timeouts import GEMINI_READ
    except ImportError:
        log.debug("%s Outlines Gemini dependencies unavailable", log_label)
        return None

    try:
        gemini_model = resolve_gemini_model_for_label(log_label)
    except RuntimeError as exc:
        log.warning("%s Outlines Gemini skipped: %s", log_label, exc)
        return None

    effective_max = compute_max_output_tokens(
        prepared.prompt,
        requested=max_output_tokens,
        json_mode=True,
    )

    def _generate() -> str:
        client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(GEMINI_READ * 1000)),
        )
        config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": effective_max,
            "response_mime_type": "application/json",
            "response_schema": schema_for_developer_api(output_type),
        }
        thinking = _gemini_thinking_config(gemini_model, True)
        if thinking:
            if "thinkingLevel" in thinking:
                config["thinking_config"] = {"thinking_level": thinking["thinkingLevel"]}
            else:
                config["thinking_config"] = {"thinking_budget": thinking["thinkingBudget"]}
        response = client.models.generate_content(
            model=gemini_model,
            contents=prepared.prompt,
            config=config,
        )
        return response.text or ""

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=GEMINI_READ,
        )
        if text and len(str(text).strip()) >= 2:
            log.info("%s Outlines Gemini structured OK (model=%s)", log_label, gemini_model)
            return str(text)
    except Exception as exc:
        log.warning(
            "%s Outlines Gemini failed: %s: %s",
            log_label,
            type(exc).__name__,
            str(exc)[:160],
        )
    return None


async def _try_outlines_ollama(
    prepared: PreparedExternalPrompt,
    *,
    output_type: type[BaseModel],
    max_output_tokens: int,
    temperature: float,
    log_label: str,
) -> str | None:
    prepared = require_prepared_external_prompt(prepared)
    from app.backend.services.llm_service import get_ollama_headers, get_ollama_model, get_ollama_semaphore

    try:
        from ollama import AsyncClient
        from outlines.models import from_ollama
        from app.backend.services.reliability.timeouts import (
            OLLAMA_CONNECT,
            OLLAMA_READ,
            httpx_timeout,
        )
    except ImportError:
        log.debug("%s Outlines Ollama dependencies unavailable", log_label)
        return None

    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    ollama_model = get_ollama_model()

    async def _generate() -> str:
        client = AsyncClient(
            host=ollama_base,
            headers=get_ollama_headers(ollama_base),
            timeout=httpx_timeout(OLLAMA_CONNECT, OLLAMA_READ),
        )
        model = from_ollama(client, ollama_model)
        return await model.generate(
            prepared.prompt,
            output_type,
            options={
                "temperature": temperature,
                "num_predict": max_output_tokens,
            },
        )

    try:
        semaphore = get_ollama_semaphore()
        async with semaphore:
            text = await _generate()
        if text and len(str(text).strip()) >= 2:
            log.info("%s Outlines Ollama structured OK (model=%s)", log_label, ollama_model)
            return str(text)
    except Exception as exc:
        log.warning(
            "%s Outlines Ollama failed: %s: %s",
            log_label,
            type(exc).__name__,
            str(exc)[:160],
        )
    return None


async def invoke_outlines_json_resilient(
    prompts: list[str],
    *,
    output_type: type[T],
    max_output_tokens: int = 2200,
    log_label: str = "structured_llm",
    temperature: float = 0.2,
    validate_parsed: Callable[[dict[str, Any]], bool] | None = None,
    on_rejected: Callable[[dict[str, Any], int, str], None] | None = None,
) -> dict[str, Any] | None:
    """Schema-bound generation via Outlines (Ollama, then Gemini)."""
    if not is_structured_llm_enabled() or not _outlines_available():
        return None

    tiers = [p for p in prompts if p and str(p).strip()][:DEFAULT_MAX_TIERS]
    if not tiers:
        return None

    from app.backend.services.llm_service import compute_max_output_tokens

    for attempt, prompt in enumerate(tiers):
        if attempt > 0:
            await asyncio.sleep(DEFAULT_TIER_DELAY_S * attempt)

        prepared = prepare_external_llm_prompt(prompt)
        tier_tokens = compute_max_output_tokens(
            prepared.prompt,
            requested=max_output_tokens,
            json_mode=True,
        )
        tier_temp = temperature if attempt == 0 else min(temperature, 0.15)
        tier_label = f"{log_label}_outlines_tier{attempt + 1}"

        from app.backend.services.app_llm_client import ANALYSIS_LLM_ORDER

        outlines_fns = {
            "ollama": _try_outlines_ollama,
            "gemini": _try_outlines_gemini,
        }
        provider_chain = [
            (name, outlines_fns[name]) for name in ANALYSIS_LLM_ORDER if name in outlines_fns
        ]

        for provider_name, provider_fn in provider_chain:
            raw = await provider_fn(
                prepared,
                output_type=output_type,
                max_output_tokens=tier_tokens,
                temperature=tier_temp,
                log_label=tier_label,
            )
            if not raw:
                continue

            parsed = parse_outlines_json_text(raw, output_type)
            if parsed is None:
                log.warning(
                    "%s %s returned non-conforming JSON (%d chars)",
                    tier_label,
                    provider_name,
                    len(raw),
                )
                continue

            if validate_parsed is not None and not validate_parsed(parsed):
                log.warning(
                    "%s %s parsed JSON rejected by validator",
                    tier_label,
                    provider_name,
                )
                if on_rejected:
                    try:
                        on_rejected(parsed, attempt + 1, raw)
                    except Exception as cb_err:
                        log.debug("%s on_rejected failed: %s", tier_label, cb_err)
                continue

            log.info(
                "%s succeeded on tier %s via Outlines %s",
                log_label,
                attempt + 1,
                provider_name,
            )
            return parsed

    return None
