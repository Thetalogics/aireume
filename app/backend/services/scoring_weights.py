"""Canonical scoring-weight schema used by sync, queue, and persistence."""
from __future__ import annotations

from typing import Any

from app.backend.services.constants import DEFAULT_WEIGHTS

CANONICAL_KEYS = ("skills", "experience", "architecture", "education", "domain", "risk")
_POSITIVE_KEYS = ("skills", "experience", "architecture", "education", "domain")
TEAM_GAP_SHARE = 0.05
# Risk is a unit-interval penalty magnitude (0..1). 0.30 was not a product rule.
MAX_RISK_WEIGHT = 1.0


def normalize_risk_weight(value: float | int | None) -> float:
    """Risk weight is a positive penalty magnitude.

    Legacy configs stored ``risk: -0.10`` as a signed penalty. Those values are
    converted with ``abs()`` so ``fit - (penalty * weight)`` never becomes a bonus.
    Magnitude is clamped to ``[0, 1]`` (canonical weight interval). The suggester
    range 0.05–0.15 is guidance, not a hard cap.
    """
    if value is None:
        return float(DEFAULT_WEIGHTS["risk"])
    try:
        magnitude = abs(float(value))
    except (TypeError, ValueError) as exc:
        raise ScoringWeightError("risk weight must be numeric") from exc
    return min(max(magnitude, 0.0), MAX_RISK_WEIGHT)


def effective_scoring_weights(weights: dict | None, *, team_gap_active: bool = False) -> dict[str, float]:
    """Canonical positive weights plus a separate positive risk magnitude.

    When team-gap contributes, every positive dimension is scaled by 0.95 and
    ``team_gap`` receives 0.05. Risk is not part of that 100% bucket.
    """
    base = canonicalize_scoring_weights(weights, strict=False) if weights else {
        k: float(DEFAULT_WEIGHTS[k]) for k in CANONICAL_KEYS
    }
    extra_positive = {k: float(weights.get(k) or 0.0) for k in ("timeline",) if weights and k in weights}
    out = dict(base)
    out.update(extra_positive)
    out["risk"] = normalize_risk_weight(out.get("risk"))
    if not team_gap_active:
        out["team_gap"] = 0.0
        return out
    scale = 1.0 - TEAM_GAP_SHARE
    for key in _POSITIVE_KEYS:
        out[key] = float(out.get(key) or 0.0) * scale
    if "timeline" in out:
        out["timeline"] = float(out.get("timeline") or 0.0) * scale
    out["team_gap"] = TEAM_GAP_SHARE
    return out
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
    mapped["risk"] = normalize_risk_weight(mapped.get("risk") if mapped.get("risk") else DEFAULT_WEIGHTS["risk"])
    return mapped


def scoring_weights_for_command(weights: Any) -> dict | None:
    if not weights:
        return None
    if not isinstance(weights, dict):
        raise ScoringWeightError("scoring_weights must be an object")
    if not any(key in _KNOWN for key in weights):
        return dict(weights)
    return canonicalize_scoring_weights(weights, strict=False)
