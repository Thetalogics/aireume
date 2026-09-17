"""Canonical scoring-weight schema used by sync, queue, and persistence."""
from __future__ import annotations

from typing import Any

from app.backend.services.constants import DEFAULT_WEIGHTS

CANONICAL_KEYS = ("skills", "experience", "architecture", "education", "domain", "risk")
_POSITIVE_KEYS = ("skills", "experience", "architecture", "education", "domain")
_ALIAS = {
    "core_competencies": "skills",
    "skill_match": "skills",
    "core_skill_match": "skills",
    "stability": "education",  # legacy 4-weight: do not map to timeline/penalty
    "career_trajectory": "education",
    "role_excellence": "architecture",
    "domain_fit": "domain",
    "timeline": "education",
}
_KNOWN = set(CANONICAL_KEYS) | set(_ALIAS) | {"timeline", "secondary_skill_match", "relevant_experience"}


class ScoringWeightError(ValueError):
    pass


def canonicalize_scoring_weights(weights: dict | None, *, strict: bool = True) -> dict[str, float]:
    """Return canonical weights. Unknown keys fail closed when strict."""
    if not weights:
        out = {k: float(DEFAULT_WEIGHTS[k]) for k in CANONICAL_KEYS}
        return out
    unknown = [k for k in weights if k not in _KNOWN]
    if unknown and strict:
        raise ScoringWeightError(f"Unknown scoring weight keys: {sorted(unknown)}")
    mapped: dict[str, float] = {k: 0.0 for k in CANONICAL_KEYS}
    for key, raw in weights.items():
        if key not in _KNOWN:
            continue
        dest = _ALIAS.get(key, key if key in CANONICAL_KEYS else None)
        if dest is None:
            continue
        try:
            mapped[dest] = mapped.get(dest, 0.0) + float(raw)
        except (TypeError, ValueError) as exc:
            raise ScoringWeightError(f"Weight {key} must be numeric") from exc
    positive = sum(mapped[k] for k in _POSITIVE_KEYS)
    if positive <= 0:
        raise ScoringWeightError("Scoring weights must include a positive qualification total")
    if strict and abs(positive - 1.0) > 0.05:
        raise ScoringWeightError("Scoring weights must sum to 1.0 (±0.05) excluding risk")
    if abs(positive - 1.0) > 1e-9:
        for k in _POSITIVE_KEYS:
            mapped[k] = mapped[k] / positive
    mapped["risk"] = min(max(float(mapped.get("risk") or DEFAULT_WEIGHTS["risk"]), 0.0), 0.3)
    return mapped


def scoring_weights_for_command(weights: Any) -> dict | None:
    if not weights:
        return None
    if not isinstance(weights, dict):
        raise ScoringWeightError("scoring_weights must be an object")
    if not any(key in _KNOWN for key in weights):
        return dict(weights)
    return canonicalize_scoring_weights(weights, strict=False)
