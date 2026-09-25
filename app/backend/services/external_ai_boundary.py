"""Single outbound LLM boundary: redact PII, then invoke the provider (AUD-035)."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable

from app.backend.services.pii_redaction_service import PIIRedactionService

logger = logging.getLogger(__name__)
_redactor = PIIRedactionService()


@dataclass(frozen=True)
class PreparedExternalPrompt:
    """Prompt payload that has passed the outbound AI redaction boundary."""

    prompt: str
    system: str | None = None


def prepare_external_prompt(text: str | None) -> str:
    if not text:
        return ""
    result = _redactor.redact_pii(text)
    logger.info("external_ai_boundary redacted_count=%s", result.redaction_count)
    return result.redacted_text


def prepare_external_llm_prompt(
    prompt: str | None,
    *,
    system: str | None = None,
) -> PreparedExternalPrompt:
    return PreparedExternalPrompt(
        prompt=prepare_external_prompt(prompt),
        system=prepare_external_prompt(system) if system else None,
    )


def require_prepared_external_prompt(value: PreparedExternalPrompt) -> PreparedExternalPrompt:
    if not isinstance(value, PreparedExternalPrompt):
        raise TypeError("Outbound LLM providers require PreparedExternalPrompt")
    return value


async def invoke_external_llm(
    prompt: str,
    *,
    system: str | None = None,
    sender: Callable[..., Awaitable[Any]],
    **kwargs: Any,
) -> Any:
    prepared = prepare_external_llm_prompt(prompt, system=system)
    return await sender(prepared.prompt, system=prepared.system, **kwargs)
