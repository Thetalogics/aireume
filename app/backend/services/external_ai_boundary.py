"""Single outbound LLM boundary: redact PII, then invoke the provider (AUD-035)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.backend.services.pii_redaction_service import PIIRedactionService

logger = logging.getLogger(__name__)
_redactor = PIIRedactionService()


def prepare_external_prompt(text: str | None) -> str:
    if not text:
        return ""
    result = _redactor.redact_pii(text)
    logger.info("external_ai_boundary redacted_count=%s", result.redaction_count)
    return result.redacted_text


async def invoke_external_llm(
    prompt: str,
    *,
    system: str | None = None,
    sender: Callable[..., Awaitable[Any]],
    **kwargs: Any,
) -> Any:
    safe_prompt = prepare_external_prompt(prompt)
    safe_system = prepare_external_prompt(system) if system else None
    return await sender(safe_prompt, system=safe_system, **kwargs)
