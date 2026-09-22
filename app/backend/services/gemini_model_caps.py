"""Thinking settings the Gemini Developer API accepts for a model id.

Stdlib only so the Portainer model bump can send the same config as the app.
"""
from __future__ import annotations

import re


def gemini_thinking_config(
    model: str,
    json_mode: bool,
    *,
    pro_budget: int = 128,
) -> dict[str, int | str] | None:
    lowered = model.lower()
    if "gemini-3" in lowered:
        if "pro" in lowered and "flash" not in lowered:
            return {"thinkingLevel": "low"}
        minor = _gemini3_minor(lowered)
        # 3.7+ Flash returns 400 for MINIMAL. low is accepted and still answers inside the read timeout.
        if minor is not None and minor >= 7:
            return {"thinkingLevel": "low"}
        if "flash" in lowered:
            return {"thinkingLevel": "minimal"}
        return {"thinkingLevel": "low"}
    if not json_mode:
        return None
    if "pro" in lowered:
        return {"thinkingBudget": pro_budget}
    return {"thinkingBudget": 0}


def _gemini3_minor(model: str) -> int | None:
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?", model)
    if not match or int(match.group(1)) != 3:
        return None
    return int(match.group(2) or 0)
