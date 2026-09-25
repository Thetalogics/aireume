"""Canonical immutable screening-decision writer."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session
from sqlalchemy import event, inspect as sa_inspect

from app.backend.models.db_models import (
    AIDecisionLog,
    DecisionNarrative,
    ScreeningDecision,
    ScreeningResult,
)
from app.backend.services import ai_decision_log_service
from app.backend.services.decision_reproducibility import (
    CURRENT_ALGORITHM_VERSION,
    build_formula_trace,
)
from app.backend.services.metrics import (
    DECISION_CREATED_TOTAL,
    DECISION_WITHOUT_PROVENANCE_TOTAL,
    HUMAN_OVERRIDE_TOTAL,
    NARRATIVE_GENERATION_FAILURE_TOTAL,
)

log = logging.getLogger(__name__)

DECISION_INITIAL = "INITIAL_ANALYSIS"
DECISION_REANALYSIS = "REANALYSIS"
DECISION_RESCORE = "RESCORE"
DECISION_INTERVIEW_ADJUSTMENT = "INTERVIEW_ADJUSTMENT"
DECISION_HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
DECISION_LEGACY_IMPORTED = "LEGACY_IMPORTED"

OVERRIDE_REASON_CODES = frozenset(
    {
        "ADDITIONAL_CONTEXT",
        "INTERVIEW_EVIDENCE",
        "DATA_ERROR",
        "POLICY_EXCEPTION",
        "RECRUITER_JUDGMENT",
        "OTHER",
    }
)
AUTHORITATIVE_KEYS = frozenset(
    {
        "fit_score",
        "deterministic_score",
        "component_fit_score",
        "final_recommendation",
        "eligibility",
        "eligibility_status",
        "status",
    }
)


class ImmutableDecisionError(Exception):
    pass


class StaleAnalysisError(Exception):
    pass


class StaleDecisionConflict(Exception):
    status_code = 409


class DecisionNotFoundError(Exception):
    pass


class OperationScopeError(Exception):
    """source_operation_id is tenant-wide and must not map to another result."""


PUBLIC_AI_PROVENANCE_FIELDS = (
    "provider",
    "model",
    "requested_provider",
    "requested_model",
    "actual_provider",
    "actual_model",
    "fallback_used",
    "prompt_template_id",
    "prompt_version",
    "prompt_template_hash",
    "structured_schema_version",
    "temperature",
    "generated_at",
)


def serialize_public_ai_provenance(*contexts: dict[str, Any] | None) -> dict[str, Any]:
    """Allowlisted public model/prompt metadata. Missing keys stay absent."""
    public: dict[str, Any] = {}
    for context in contexts:
        if not isinstance(context, dict):
            continue
        for key in PUBLIC_AI_PROVENANCE_FIELDS:
            if key in context and context[key] is not None and key not in public:
                public[key] = context[key]
    return public


def canonical_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc)


@event.listens_for(ScreeningDecision, "before_update")
def _reject_decision_mutation(mapper, connection, target):
    state = sa_inspect(target)
    if state.persistent and state.committed_state:
        raise ImmutableDecisionError("ScreeningDecision is immutable")


def _lock_result(db: Session, screening_result_id: int) -> ScreeningResult:
    result = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.id == screening_result_id)
        .with_for_update()
        .one_or_none()
    )
    if result is None:
        raise DecisionNotFoundError("screening result not found")
    return result


def _existing_operation(
    db: Session, tenant_id: int, operation_id: str, decision_type: str
) -> ScreeningDecision | None:
    return (
        db.query(ScreeningDecision)
        .filter_by(
            tenant_id=tenant_id,
            source_operation_id=operation_id,
            decision_type=decision_type,
        )
        .one_or_none()
    )


def apply_decision_projection(screening_result: ScreeningResult, decision: ScreeningDecision) -> None:
    screening_result.current_decision_id = decision.id
    if decision.deterministic_score is not None:
        screening_result.deterministic_score = decision.deterministic_score
    if decision.eligibility_status is not None:
        screening_result.eligibility_status = decision.eligibility_status
    screening_result.eligibility_reason = decision.eligibility_reason
    try:
        payload = json.loads(screening_result.analysis_result or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["fit_score"] = decision.final_fit_score
    payload["component_fit_score"] = decision.component_fit_score
    payload["deterministic_score"] = decision.deterministic_score
    payload["final_recommendation"] = decision.effective_recommendation
    payload["current_decision_id"] = decision.id
    payload["decision_version"] = decision.decision_version
    payload["algorithm_version"] = decision.algorithm_version
    screening_result.analysis_result = json.dumps(payload, default=str)


def serialize_screening_decision(decision: ScreeningDecision, *, current_id: int | None = None) -> dict[str, Any]:
    return {
        "id": decision.id,
        "decision_id": decision.id,
        "decision_version": decision.decision_version,
        "decision_type": decision.decision_type,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
        "algorithm_version": decision.algorithm_version,
        "deterministic_score": decision.deterministic_score,
        "component_fit_score": decision.component_fit_score,
        "final_fit_score": decision.final_fit_score,
        "eligibility_status": decision.eligibility_status,
        "eligibility_reason": decision.eligibility_reason,
        "ai_recommendation": decision.ai_recommendation,
        "effective_recommendation": decision.effective_recommendation,
        "human_override_recommendation": decision.human_override_recommendation,
        "actor_id": decision.actor_id,
        "current": current_id == decision.id if current_id is not None else False,
        "provenance_complete": decision.provenance_complete,
        "formula_trace": decision.formula_trace,
        "explanation": decision.explanation_payload,
        "policy_context": decision.policy_context,
        "ai_provenance": serialize_public_ai_provenance(
            decision.model_context, decision.prompt_context
        ),
        "input_snapshot": {
            key: value
            for key, value in (decision.input_snapshot or {}).items()
            if key not in {"untrusted_resume_text", "untrusted_jd_text", "resume_text", "jd_text"}
        },
        "override_reason": decision.override_reason,
        "schema_version": decision.schema_version,
    }


def compare_decisions(old: ScreeningDecision, new: ScreeningDecision) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    for field in (
        "final_fit_score",
        "deterministic_score",
        "component_fit_score",
        "effective_recommendation",
        "algorithm_version",
        "policy_version",
    ):
        left = getattr(old, field)
        right = getattr(new, field)
        if left != right:
            diffs[field] = {"old": left, "new": right}
    old_weights = old.effective_weights or {}
    new_weights = new.effective_weights or {}
    if old_weights != new_weights:
        diffs["weights"] = True
    old_snap = old.input_snapshot or {}
    new_snap = new.input_snapshot or {}
    for key in ("resume_hash", "jd_hash"):
        if old_snap.get(key) != new_snap.get(key):
            diffs[key] = {"old": old_snap.get(key), "new": new_snap.get(key)}
    return diffs


def create_screening_decision(
    db: Session,
    *,
    screening_result_id: int,
    decision_type: str,
    scores: dict[str, Any],
    eligibility: dict[str, Any] | None,
    recommendation: str | None,
    scoring_weights: dict[str, Any] | None,
    algorithm_version: str | None,
    input_snapshot: dict[str, Any] | None,
    policy_context: dict[str, Any] | None,
    actor_id: int | None,
    operation_id: str,
    analysis_generation: int | None = None,
    expected_current_decision_id: int | None = None,
    model_context: dict[str, Any] | None = None,
    prompt_context: dict[str, Any] | None = None,
    provenance_complete: bool = True,
    supersedes_decision_id: int | None = None,
    override_reason: dict[str, Any] | None = None,
    human_override_recommendation: str | None = None,
    update_current: bool = True,
) -> ScreeningDecision:
    """Insert one immutable decision and flush. Does not commit.

    The caller owns the transaction: decision, projection, and the required
    AIDecisionLog are flushed together so the caller can commit or roll back.
    """
    if not operation_id:
        raise ValueError("source_operation_id is required")
    result = _lock_result(db, screening_result_id)
    existing = _existing_operation(db, result.tenant_id, operation_id, decision_type)
    if existing is not None:
        if existing.screening_result_id != result.id:
            raise OperationScopeError(
                "source_operation_id already belongs to another screening result in this tenant"
            )
        return existing
    if (
        analysis_generation is not None
        and (result.analysis_generation or 1) != analysis_generation
    ):
        raise StaleAnalysisError("analysis generation is no longer current")

    components = dict((scores or {}).get("components") or {})
    risk_penalty = (scores or {}).get("risk_penalty", 0)
    weights = dict(scoring_weights or {})
    trace = build_formula_trace(components, weights, risk_penalty)
    if scores.get("final_fit_score") is not None:
        trace["final_fit_score"] = int(scores["final_fit_score"])
        trace["post_clamp_total"] = int(scores["final_fit_score"])
    if scores.get("deterministic_score") is not None:
        trace["deterministic_score"] = int(scores["deterministic_score"])
    if scores.get("component_fit_score") is not None:
        trace["component_fit_score"] = int(scores["component_fit_score"])
    eligibility = eligibility or {}
    eligibility_status = eligibility.get("status")
    if eligibility_status is None:
        eligibility_status = eligibility.get("eligible")
    decision = ScreeningDecision(
        tenant_id=result.tenant_id,
        candidate_id=result.candidate_id,
        original_candidate_id=result.candidate_id,
        screening_result_id=result.id,
        role_template_id=result.role_template_id,
        requisition_id=result.requisition_id,
        decision_version=(
            db.query(ScreeningDecision.decision_version)
            .filter_by(screening_result_id=result.id)
            .order_by(ScreeningDecision.decision_version.desc())
            .limit(1)
            .scalar()
            or 0
        )
        + 1,
        analysis_generation=analysis_generation or result.analysis_generation,
        decision_type=decision_type,
        algorithm_version=algorithm_version,
        deterministic_score=trace["deterministic_score"],
        component_fit_score=trace["component_fit_score"],
        final_fit_score=trace["final_fit_score"],
        eligibility_status=eligibility_status,
        eligibility_reason=eligibility.get("reason"),
        ai_recommendation=recommendation,
        human_override_recommendation=human_override_recommendation,
        effective_recommendation=human_override_recommendation or recommendation,
        risk_score=int(risk_penalty or 0),
        actor_id=actor_id,
        source_operation_id=operation_id,
        supersedes_decision_id=supersedes_decision_id or expected_current_decision_id,
        provenance_complete=bool(provenance_complete),
        schema_version="decision_payload_v1",
        policy_version=(policy_context or {}).get("policy_version"),
        component_scores=components,
        scoring_weights=weights,
        effective_weights=weights,
        input_snapshot=input_snapshot,
        model_context=model_context,
        prompt_context=prompt_context,
        policy_context=policy_context,
        eligibility_gate_trace=eligibility.get("gate_trace") or [],
        formula_trace=trace,
        explanation_payload={
            "positive_factors": components,
            "risk_factors": {"risk_penalty": risk_penalty},
            "eligibility_gates": eligibility.get("gate_trace") or [],
            "effective_weights": weights,
            "formula_summary": trace,
        },
        override_reason=override_reason,
    )
    try:
        with db.begin_nested():
            db.add(decision)
            db.flush()
    except IntegrityError:
        existing = _existing_operation(db, result.tenant_id, operation_id, decision_type)
        if existing is not None:
            if existing.screening_result_id != result.id:
                raise OperationScopeError(
                    "source_operation_id already belongs to another screening result in this tenant"
                )
            return existing
        raise

    if update_current:
        apply_decision_projection(result, decision)
    ai_decision_log_service.write_ai_decision_log(
        db,
        tenant_id=result.tenant_id,
        screening_result_id=result.id,
        candidate_id=result.candidate_id,
        decision_type=decision_type,
        deterministic_score=decision.deterministic_score,
        final_score=decision.final_fit_score,
        recommendation=decision.effective_recommendation,
        scoring_weights=weights or None,
        algorithm_version=algorithm_version,
        actor_id=actor_id,
        screening_decision_id=decision.id,
        required=True,
    )
    DECISION_CREATED_TOTAL.labels(decision_type=decision_type).inc()
    if not provenance_complete:
        DECISION_WITHOUT_PROVENANCE_TOTAL.inc()
    log.info(
        "screening_decision_created",
        extra={
            "tenant_id": result.tenant_id,
            "decision_id": decision.id,
            "screening_result_id": result.id,
            "decision_version": decision.decision_version,
            "decision_type": decision_type,
            "algorithm_version": algorithm_version,
            "operation_id": operation_id,
            "actor_id": actor_id,
        },
    )
    return decision


def persist_from_pipeline(
    db: Session,
    result: ScreeningResult,
    pipeline_result: dict | None,
    *,
    decision_type: str,
    actor_id: int | None = None,
    operation_id: str | None = None,
) -> ScreeningDecision | None:
    if not isinstance(pipeline_result, dict) or not pipeline_result:
        return None
    if not any(
        pipeline_result.get(key) is not None
        for key in ("fit_score", "deterministic_score", "final_recommendation", "overall_score")
    ):
        return None
    meta = pipeline_result.get("_meta") if isinstance(pipeline_result.get("_meta"), dict) else {}
    components = (
        (pipeline_result.get("score_breakdown") or {}).get("components")
        if isinstance(pipeline_result.get("score_breakdown"), dict)
        else None
    )
    if not components:
        components = pipeline_result.get("components") or {
            "skills": pipeline_result.get("skill_score") or 0,
            "experience": pipeline_result.get("exp_score") or 0,
            "architecture": pipeline_result.get("arch_score") or 0,
            "education": pipeline_result.get("edu_score") or 0,
            "timeline": pipeline_result.get("timeline_score") or 0,
            "domain": pipeline_result.get("domain_score") or 0,
        }
    weights = pipeline_result.get("scoring_weights") or meta.get("scoring_weights") or {}
    eligibility = pipeline_result.get("eligibility") or {}
    snapshot = {
        "schema_version": "decision_input_v1",
        "resume_hash": canonical_hash((result.resume_text or "")[:2000]),
        "jd_hash": canonical_hash((result.jd_text or "")[:2000]),
        "components": components,
        "risk_penalty": pipeline_result.get("risk_penalty") or 0,
    }
    return create_screening_decision(
        db,
        screening_result_id=result.id,
        decision_type=decision_type,
        scores={
            "components": components,
            "risk_penalty": snapshot["risk_penalty"],
            "final_fit_score": pipeline_result.get("fit_score"),
            "deterministic_score": pipeline_result.get("deterministic_score"),
            "component_fit_score": pipeline_result.get("component_fit_score"),
        },
        eligibility={
            "status": eligibility.get("eligible"),
            "reason": eligibility.get("reason"),
            "gate_trace": eligibility.get("details") or eligibility.get("gate_trace") or [],
        },
        recommendation=pipeline_result.get("final_recommendation"),
        scoring_weights=weights,
        algorithm_version=meta.get("algorithm_version") or CURRENT_ALGORITHM_VERSION,
        input_snapshot=snapshot,
        policy_context={"policy_version": "candidate-processing-v1", "processing_purpose": "candidate screening"},
        actor_id=actor_id,
        operation_id=operation_id or meta.get("operation_id") or f"{decision_type}:{result.id}:{result.analysis_generation}",
        analysis_generation=result.analysis_generation,
        model_context=meta.get("model_context"),
        provenance_complete=True,
    )


def create_human_override(
    db: Session,
    *,
    screening_result_id: int,
    expected_current_decision_id: int,
    actor_id: int,
    operation_id: str,
    recommendation: str,
    reason_code: str,
    reason_text: str | None = None,
) -> ScreeningDecision:
    if reason_code not in OVERRIDE_REASON_CODES:
        raise ValueError(f"unsupported override reason {reason_code}")
    result = _lock_result(db, screening_result_id)
    if result.current_decision_id != expected_current_decision_id:
        raise StaleDecisionConflict("current decision changed")
    current = db.get(ScreeningDecision, expected_current_decision_id)
    if current is None:
        raise DecisionNotFoundError("current decision not found")
    override = create_screening_decision(
        db,
        screening_result_id=screening_result_id,
        decision_type=DECISION_HUMAN_OVERRIDE,
        scores={
            "components": current.component_scores or {},
            "risk_penalty": current.risk_score or 0,
        },
        eligibility={
            "status": current.eligibility_status,
            "reason": current.eligibility_reason,
            "gate_trace": current.eligibility_gate_trace or [],
        },
        recommendation=current.ai_recommendation,
        scoring_weights=current.effective_weights or {},
        algorithm_version=current.algorithm_version,
        input_snapshot=current.input_snapshot,
        policy_context=current.policy_context,
        actor_id=actor_id,
        operation_id=operation_id,
        analysis_generation=result.analysis_generation,
        expected_current_decision_id=expected_current_decision_id,
        provenance_complete=current.provenance_complete,
        human_override_recommendation=recommendation,
        override_reason={"reason_code": reason_code, "reason_text": reason_text},
    )
    HUMAN_OVERRIDE_TOTAL.labels(reason_code=reason_code).inc()
    return override


def _narrative_operation_id(decision_id: int, narrative_type: str, operation_id: str | None) -> str:
    return operation_id or f"narrative:{decision_id}:{narrative_type}"


def _existing_narrative(
    db: Session, *, decision_id: int, narrative_type: str, operation_id: str
) -> DecisionNarrative | None:
    return (
        db.query(DecisionNarrative)
        .filter_by(
            screening_decision_id=decision_id,
            narrative_type=narrative_type,
            source_operation_id=operation_id,
        )
        .one_or_none()
    )


def generate_decision_narrative(
    db: Session,
    *,
    decision_id: int,
    generator: Callable[..., Any],
    provider_context: dict[str, Any] | None = None,
    prompt_context: dict[str, Any] | None = None,
    operation_id: str | None = None,
    narrative_type: str = "candidate_narrative",
) -> DecisionNarrative:
    decision = db.get(ScreeningDecision, decision_id)
    if decision is None:
        raise DecisionNotFoundError("decision not found")
    op_id = _narrative_operation_id(decision.id, narrative_type, operation_id)
    existing = _existing_narrative(
        db, decision_id=decision.id, narrative_type=narrative_type, operation_id=op_id
    )
    if existing is not None:
        return existing
    row = DecisionNarrative(
        tenant_id=decision.tenant_id,
        screening_decision_id=decision.id,
        narrative_type=narrative_type,
        source_operation_id=op_id,
        status="pending",
        provider_context=provider_context,
        prompt_context=prompt_context,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = _existing_narrative(
            db, decision_id=decision.id, narrative_type=narrative_type, operation_id=op_id
        )
        if existing is not None:
            return existing
        raise
    try:
        output = generator(decision)
        if isinstance(output, dict):
            for key in AUTHORITATIVE_KEYS:
                output.pop(key, None)
        row.structured_output = output
        row.status = "ready"
        row.generated_at = _now()
        _mirror_narrative(db, decision, output, "ready")
        return row
    except Exception as exc:
        row.status = "failed"
        row.error = str(exc)
        NARRATIVE_GENERATION_FAILURE_TOTAL.inc()
        _mirror_narrative(db, decision, None, "failed")
        return row


def store_decision_narrative(
    db: Session,
    *,
    decision_id: int,
    structured_output: dict[str, Any],
    provider_context: dict[str, Any] | None = None,
    prompt_context: dict[str, Any] | None = None,
    operation_id: str | None = None,
    narrative_type: str = "candidate_narrative",
) -> DecisionNarrative:
    cleaned = dict(structured_output or {})
    for key in AUTHORITATIVE_KEYS:
        cleaned.pop(key, None)
    decision = db.get(ScreeningDecision, decision_id)
    if decision is None:
        raise DecisionNotFoundError("decision not found")
    op_id = _narrative_operation_id(decision.id, narrative_type, operation_id)
    existing = _existing_narrative(
        db, decision_id=decision.id, narrative_type=narrative_type, operation_id=op_id
    )
    if existing is not None:
        return existing
    row = DecisionNarrative(
        tenant_id=decision.tenant_id,
        screening_decision_id=decision.id,
        narrative_type=narrative_type,
        source_operation_id=op_id,
        status="ready",
        structured_output=cleaned,
        provider_context=provider_context,
        prompt_context=prompt_context,
        generated_at=_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = _existing_narrative(
            db, decision_id=decision.id, narrative_type=narrative_type, operation_id=op_id
        )
        if existing is not None:
            return existing
        raise
    _mirror_narrative(db, decision, cleaned, "ready")
    return row


def _mirror_narrative(
    db: Session,
    decision: ScreeningDecision,
    output: dict[str, Any] | None,
    status: str,
) -> None:
    result = db.get(ScreeningResult, decision.screening_result_id)
    if result is None or result.current_decision_id not in (None, decision.id):
        return
    if output is not None:
        result.narrative_json = json.dumps(output, default=str)
    result.narrative_status = status
    result.generation_mode = (
        "ai"
        if output and output.get("ai_enhanced") is True and status == "ready"
        else "deterministic_fallback"
        if status in ("ready", "fallback")
        else "failed"
        if status == "failed"
        else "pending"
    )


def list_screening_decisions(
    db: Session,
    *,
    tenant_id: int,
    screening_result_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[ScreeningDecision]:
    result = db.get(ScreeningResult, screening_result_id)
    if result is None or result.tenant_id != tenant_id:
        raise DecisionNotFoundError("decision history not found")
    return (
        db.query(ScreeningDecision)
        .filter_by(tenant_id=tenant_id, screening_result_id=screening_result_id)
        .order_by(ScreeningDecision.decision_version.desc(), ScreeningDecision.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_screening_decision(
    db: Session, *, tenant_id: int, screening_result_id: int, decision_id: int
) -> ScreeningDecision:
    decision = (
        db.query(ScreeningDecision)
        .filter_by(id=decision_id, tenant_id=tenant_id, screening_result_id=screening_result_id)
        .one_or_none()
    )
    if decision is None:
        raise DecisionNotFoundError("decision not found")
    return decision


def find_projection_mismatches(db: Session, *, tenant_id: int | None = None) -> list[dict[str, Any]]:
    query = db.query(ScreeningResult).filter(ScreeningResult.current_decision_id.isnot(None))
    if tenant_id is not None:
        query = query.filter(ScreeningResult.tenant_id == tenant_id)
    mismatches = []
    for result in query.all():
        decision = db.get(ScreeningDecision, result.current_decision_id)
        if decision is None:
            mismatches.append({"screening_result_id": result.id, "reason": "missing_decision"})
            continue
        try:
            payload = json.loads(result.analysis_result or "{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("fit_score") != decision.final_fit_score:
            mismatches.append(
                {
                    "screening_result_id": result.id,
                    "reason": "fit_score",
                    "projected": payload.get("fit_score"),
                    "canonical": decision.final_fit_score,
                }
            )
    return mismatches


def repair_projection(db: Session, *, screening_result_id: int, dry_run: bool = True) -> dict[str, Any]:
    result = db.get(ScreeningResult, screening_result_id)
    if result is None or result.current_decision_id is None:
        return {"repaired": False}
    decision = db.get(ScreeningDecision, result.current_decision_id)
    if decision is None:
        return {"repaired": False}
    if dry_run:
        return {"repaired": False, "would_repair": True, "decision_id": decision.id}
    apply_decision_projection(result, decision)
    return {"repaired": True, "decision_id": decision.id}
