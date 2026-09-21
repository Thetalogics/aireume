"""Deterministic scoring provenance and reproduction for Phase 2 decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

CURRENT_ALGORITHM_VERSION = "aria-fit-v1"
POSITIVE_COMPONENTS = (
    "skills",
    "experience",
    "architecture",
    "education",
    "timeline",
    "domain",
)


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _clamp_int(value: Decimal) -> int:
    clamped = max(Decimal("0"), min(Decimal("100"), value))
    return int(clamped.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def recommendation_for_score(score: int) -> str:
    # Golden fixture treats 72 as Consider; Shortlist requires a strictly higher score.
    if score > 72:
        return "Shortlist"
    if score >= 45:
        return "Consider"
    return "Reject"


def build_formula_trace(
    components: dict[str, Any] | None,
    weights: dict[str, Any] | None,
    risk_penalty: Any = 0,
) -> dict[str, Any]:
    components = components or {}
    weights = weights or {}
    positive = Decimal("0")
    breakdown: dict[str, Any] = {}
    for key in POSITIVE_COMPONENTS:
        score = _as_decimal(components.get(key, 0))
        weight = _as_decimal(weights.get(key, 0))
        contribution = _quantize(score * weight)
        breakdown[key] = {
            "score": int(score) if score == score.to_integral_value() else float(score),
            "weight": float(weight),
            "contribution": float(contribution),
        }
        positive += score * weight
    risk_weight = _as_decimal(weights.get("risk", 0))
    risk = _as_decimal(risk_penalty)
    risk_contribution = _quantize(risk * risk_weight)
    pre_clamp = _quantize(positive - (risk * risk_weight))
    post_clamp = _clamp_int(pre_clamp)
    return {
        "schema_version": "formula_trace_v1",
        "positive_components": breakdown,
        "risk_penalty": int(risk) if risk == risk.to_integral_value() else float(risk),
        "risk_weight": float(risk_weight),
        "risk_penalty_contribution": float(risk_contribution),
        "pre_clamp_total": float(pre_clamp),
        "post_clamp_total": post_clamp,
        "component_fit_score": _clamp_int(positive),
        "deterministic_score": post_clamp,
        "final_fit_score": post_clamp,
    }


def recompute_formula_trace(formula_trace: dict[str, Any] | None) -> dict[str, Any]:
    trace = dict(formula_trace or {})
    components = {
        key: values.get("score", 0)
        for key, values in (trace.get("positive_components") or {}).items()
    }
    weights = {
        key: values.get("weight", 0)
        for key, values in (trace.get("positive_components") or {}).items()
    }
    weights["risk"] = trace.get("risk_weight", 0)
    return build_formula_trace(components, weights, trace.get("risk_penalty", 0))


def score_for_algorithm(
    *,
    algorithm_version: str,
    input_snapshot: dict[str, Any] | None,
    effective_weights: dict[str, Any] | None,
) -> dict[str, Any]:
    if algorithm_version != CURRENT_ALGORITHM_VERSION:
        raise UnsupportedAlgorithmVersion(algorithm_version)
    snapshot = dict(input_snapshot or {})
    weights = dict(effective_weights or snapshot.get("effective_weights") or {})
    components = snapshot.get("components") or {}
    risk_penalty = snapshot.get("risk_penalty", 0)
    trace = build_formula_trace(components, weights, risk_penalty)
    eligibility = snapshot.get("eligibility_status", True)
    return {
        "algorithm_version": CURRENT_ALGORITHM_VERSION,
        "component_fit_score": trace["component_fit_score"],
        "deterministic_score": trace["deterministic_score"],
        "final_fit_score": trace["final_fit_score"],
        "eligibility_status": bool(eligibility),
        "risk_penalty": int(_as_decimal(risk_penalty)),
        "recommendation": recommendation_for_score(trace["final_fit_score"]),
    }


@dataclass
class ReproductionResult:
    status: str
    differences: dict[str, Any] = field(default_factory=dict)
    algorithm_version: str | None = None


class UnsupportedAlgorithmVersion(ValueError):
    pass


def verify_decision_reproducibility(db, *, decision_id: int) -> ReproductionResult:
    from app.backend.models.db_models import ScreeningDecision

    decision = db.get(ScreeningDecision, decision_id)
    if decision is None:
        return ReproductionResult(status="INPUT_UNAVAILABLE")
    if not decision.input_snapshot:
        return ReproductionResult(
            status="INPUT_UNAVAILABLE",
            algorithm_version=decision.algorithm_version,
        )
    if decision.algorithm_version != CURRENT_ALGORITHM_VERSION:
        return ReproductionResult(
            status="UNSUPPORTED_ALGORITHM_VERSION",
            algorithm_version=decision.algorithm_version,
        )
    reproduced = score_for_algorithm(
        algorithm_version=decision.algorithm_version,
        input_snapshot=decision.input_snapshot,
        effective_weights=decision.effective_weights,
    )
    differences = {}
    stored = {
        "final_fit_score": decision.final_fit_score,
        "deterministic_score": decision.deterministic_score,
        "component_fit_score": decision.component_fit_score,
    }
    for key, stored_value in stored.items():
        if reproduced.get(key) != stored_value:
            differences[key] = {"stored": stored_value, "reproduced": reproduced.get(key)}
    if differences:
        from app.backend.services.metrics import DECISION_REPRODUCTION_MISMATCH_TOTAL

        DECISION_REPRODUCTION_MISMATCH_TOTAL.labels(
            algorithm_version=decision.algorithm_version or "unknown"
        ).inc()
        return ReproductionResult(
            status="MISMATCH",
            differences=differences,
            algorithm_version=decision.algorithm_version,
        )
    return ReproductionResult(
        status="MATCH",
        differences={},
        algorithm_version=decision.algorithm_version,
    )
