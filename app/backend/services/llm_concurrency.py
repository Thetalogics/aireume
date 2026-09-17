"""Cross-process LLM concurrency caps (AUD-045)."""
from __future__ import annotations

import os
from contextlib import contextmanager

from app.backend.services.shared_cache import cache_release_slot, cache_try_acquire_slot


class LLMConcurrencySaturated(RuntimeError):
    pass


def _max_slots(provider: str) -> int:
    env = os.getenv(f"{provider.upper()}_MAX_CONCURRENT")
    if env:
        return max(1, int(env))
    return 2 if provider == "gemini" else 4


@contextmanager
def llm_slot(provider: str, tenant_id: int | None = None, ttl_seconds: int = 180):
    key = f"llm:slots:{provider}:{tenant_id or 'global'}"
    if not cache_try_acquire_slot(key, _max_slots(provider), ttl_seconds=ttl_seconds):
        raise LLMConcurrencySaturated(f"{provider} concurrency saturated")
    try:
        yield
    finally:
        cache_release_slot(key)
