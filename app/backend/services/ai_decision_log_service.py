"""Immutable AI screening decision records (GDPR Art. 22 / EU AI Act)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.backend.models.db_models import AIDecisionLog

log = logging.getLogger(__name__)


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None

DECISION_INITIAL = "INITIAL_ANALYSIS"
DECISION_REANALYSIS = "REANALYSIS"
DECISION_RESCORE = "RESCORE"

ALGORITHM_VERSION = "phase0-1"


def write_ai_decision_log(
    db: Session,
    *,
    tenant_id: int,
    screening_result_id: Optional[int],
    candidate_id: Optional[int],
    decision_type: str,
    deterministic_score: Any = None,
    final_score: Any = None,
    recommendation: Any = None,
    scoring_weights: Any = None,
    algorithm_version: str = ALGORITHM_VERSION,
    model_name: Optional[str] = None,
    model_version: Optional[str] = None,
    prompt_template_version: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    actor_id: Optional[int] = None,
    screening_decision_id: Optional[int] = None,
    required: bool = True,
) -> AIDecisionLog:
    """Persist one immutable decision row. Does not commit.

    When ``required`` is True, failures propagate so the caller can roll back
    the screening mutation. Secrets, resume text, and JD text are never stored.
    """
    row = AIDecisionLog(
        tenant_id=tenant_id,
        screening_result_id=screening_result_id,
        candidate_id=candidate_id,
        model_name=model_name,
        model_version=model_version,
        prompt_template_version=prompt_template_version or decision_type,
        prompt_hash=prompt_hash,
        guardrails_triggered=[],
        fallback_used=False,
        deterministic_score=_as_float(deterministic_score),
        llm_score=None,
        final_score=_as_float(final_score),
        decision_type=decision_type,
        scoring_weights=scoring_weights if isinstance(scoring_weights, (dict, list)) else None,
        algorithm_version=algorithm_version,
        actor_id=actor_id,
        recommendation=str(recommendation) if recommendation is not None else None,
        screening_decision_id=screening_decision_id,
    )
    try:
        db.add(row)
        db.flush()
        return row
    except Exception:
        if required:
            raise
        log.warning("AIDecisionLog write failed (non-required)", exc_info=True)
        return row


def write_log_from_pipeline(
    db: Session,
    result,
    pipeline_result: dict | None,
    *,
    decision_type: str,
    actor_id: Optional[int] = None,
    required: bool = True,
) -> Optional[AIDecisionLog]:
    if pipeline_result is None:
        if required:
            raise ValueError("pipeline_result is required for an auditable screening decision")
        return None
    meta = pipeline_result.get("_meta", {}) if isinstance(pipeline_result, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return write_ai_decision_log(
        db,
        tenant_id=result.tenant_id,
        screening_result_id=result.id,
        candidate_id=result.candidate_id,
        decision_type=decision_type,
        deterministic_score=first_not_none(
            pipeline_result.get("deterministic_score"),
            meta.get("deterministic_score"),
        ),
        final_score=first_not_none(
            pipeline_result.get("fit_score"),
            pipeline_result.get("overall_score"),
        ),
        recommendation=pipeline_result.get("final_recommendation"),
        scoring_weights=first_not_none(
            pipeline_result.get("scoring_weights"),
            meta.get("scoring_weights"),
        ),
        algorithm_version=meta.get("algorithm_version") or ALGORITHM_VERSION,
        model_name=meta.get("model_name") or pipeline_result.get("model_used"),
        model_version=meta.get("model_version"),
        prompt_template_version=meta.get("prompt_template_version"),
        prompt_hash=meta.get("prompt_hash"),
        actor_id=actor_id,
        required=required,
    )


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
