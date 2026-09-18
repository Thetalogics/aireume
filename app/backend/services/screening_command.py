from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

ALGORITHM_VERSION_DEFAULT = "1.0"

class CrossTenantRequisitionError(ValueError):
    pass

class CandidateNotFoundError(ValueError):
    pass

class ArtifactUnavailable(ValueError):
    pass

@dataclass
class ScreeningCommand:
    schema_version: str
    tenant_id: int
    user_id: int
    resume_hash: str
    jd_hash: str
    algorithm_version: str
    candidate_id: Optional[int] = None
    artifact_id: Optional[str] = None
    requisition_id: Optional[int] = None
    role_template_id: Optional[int] = None
    criteria_version: Optional[int] = None
    scoring_weights: Optional[dict] = None
    skill_overrides: Optional[dict] = None

def normalize_config(value: dict | None) -> dict | None:
    if not value:
        return None
    def _norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _norm(v[k]) for k in sorted(v, key=lambda x: str(x))}
        if isinstance(v, list):
            return [_norm(i) for i in v]
        if isinstance(v, bool):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, float):
            return format(v, ".10g")
        return v
    return _norm(value)

def analysis_fingerprint(cmd: ScreeningCommand) -> str:
    payload = {
        "schema_version": cmd.schema_version,
        "tenant_id": cmd.tenant_id,
        "resume_hash": cmd.resume_hash,
        "jd_hash": cmd.jd_hash,
        "requisition_id": cmd.requisition_id,
        "criteria_version": cmd.criteria_version,
        "role_template_id": cmd.role_template_id,
        "scoring_weights": normalize_config(cmd.scoring_weights),
        "skill_overrides": normalize_config(cmd.skill_overrides),
        "algorithm_version": cmd.algorithm_version,
    }
    canonical = "v1:" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def command_to_dict(cmd: ScreeningCommand) -> dict:
    return asdict(cmd)

def command_from_dict(data: dict) -> ScreeningCommand:
    return ScreeningCommand(
        schema_version=data.get("schema_version") or "v1",
        tenant_id=int(data["tenant_id"]),
        user_id=int(data["user_id"]),
        resume_hash=data["resume_hash"],
        jd_hash=data["jd_hash"],
        algorithm_version=data.get("algorithm_version") or ALGORITHM_VERSION_DEFAULT,
        candidate_id=data.get("candidate_id"),
        artifact_id=str(data["artifact_id"]) if data.get("artifact_id") else None,
        requisition_id=data.get("requisition_id"),
        role_template_id=data.get("role_template_id"),
        criteria_version=data.get("criteria_version"),
        scoring_weights=data.get("scoring_weights"),
        skill_overrides=data.get("skill_overrides"),
    )


def build_screening_command(
    db,
    *,
    tenant_id: int,
    user_id: int,
    resume_hash: str,
    jd_hash: str,
    candidate_id: Optional[int] = None,
    artifact_id: Optional[str] = None,
    requisition_id: Optional[int] = None,
    role_template_id: Optional[int] = None,
    scoring_weights: Optional[dict] = None,
    skill_overrides: Optional[dict] = None,
    algorithm_version: str = ALGORITHM_VERSION_DEFAULT,
) -> ScreeningCommand:
    from app.backend.models.db_models import Requisition
    from app.backend.services.scoring_weights import scoring_weights_for_command

    criteria_version = None
    resolved_template_id = role_template_id
    if requisition_id is not None:
        req = db.query(Requisition).filter(
            Requisition.id == requisition_id,
            Requisition.tenant_id == tenant_id,
        ).first()
        if not req:
            raise CrossTenantRequisitionError("Requisition not found")
        criteria_version = req.current_criteria_version
        if resolved_template_id is None:
            resolved_template_id = req.legacy_role_template_id
    normalized_weights = scoring_weights_for_command(scoring_weights) if scoring_weights else None
    return ScreeningCommand(
        schema_version="v1",
        tenant_id=tenant_id,
        user_id=user_id or 0,
        resume_hash=resume_hash,
        jd_hash=jd_hash,
        algorithm_version=algorithm_version or ALGORITHM_VERSION_DEFAULT,
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        requisition_id=requisition_id,
        role_template_id=resolved_template_id,
        criteria_version=criteria_version,
        scoring_weights=normalized_weights,
        skill_overrides=skill_overrides,
    )


async def enqueue_screening(**kwargs):
    from app.backend.services.queue_manager import get_queue_manager

    return await get_queue_manager().enqueue_job(**kwargs)


def execute_screening(
    db,
    cmd: ScreeningCommand,
    *,
    resume_text: str,
    jd_text: str,
    parsed_data: dict,
    pipeline_result: dict,
    file_hash: Optional[str] = None,
    filename: str = "resume.pdf",
    file_content: Optional[bytes] = None,
    gap_analysis: Optional[dict] = None,
    action: Optional[str] = None,
    converted_pdf_content: Optional[bytes] = None,
):
    import json
    import time

    from app.backend.models.db_models import Candidate
    from app.backend.routes.analyze import (
        _get_or_create_candidate,
        _link_to_requisition,
        _store_candidate_profile,
        _upsert_screening_result,
    )
    from app.backend.services.metrics import SCREENING_DURATION_SECONDS, SCREENING_TOTAL

    started = time.perf_counter()
    try:
        parsed = parsed_data or {}
        gaps = gap_analysis if gap_analysis is not None else {}
        content = file_content if file_content is not None else (resume_text or "").encode("utf-8")
        is_dup = False
        if cmd.candidate_id is not None:
            existing = (
                db.query(Candidate)
                .filter(
                    Candidate.id == cmd.candidate_id,
                    Candidate.tenant_id == cmd.tenant_id,
                )
                .first()
            )
            if existing is None:
                raise CandidateNotFoundError("Candidate not found")
            candidate_id = existing.id
            is_dup = action == "use_existing"
        else:
            candidate_id, is_dup = _get_or_create_candidate(
                db,
                parsed,
                cmd.tenant_id,
                file_hash=file_hash,
                gap_analysis=gaps,
                profile_quality=pipeline_result.get("analysis_quality", "medium") if pipeline_result else "medium",
                action=action,
                file_content=content,
                filename=filename,
                converted_pdf_content=converted_pdf_content,
                resume_text=parsed.get("raw_text", resume_text),
            )
        from app.backend.services.candidate_processing_policy import (
            PROCESSING_RESUME_ANALYSIS,
            enforce_candidate_processing_policy,
        )
        enforce_candidate_processing_policy(
            db,
            tenant_id=cmd.tenant_id,
            candidate_id=candidate_id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        cand = db.get(Candidate, candidate_id)
        if cand and action != "use_existing":
            _store_candidate_profile(
                cand,
                parsed,
                gaps,
                file_hash,
                pipeline_result.get("analysis_quality", "medium") if pipeline_result else "medium",
                file_content=content,
                filename=filename,
                db=db,
            )
        db_result = _upsert_screening_result(
            db,
            tenant_id=cmd.tenant_id,
            candidate_id=candidate_id,
            role_template_id=cmd.role_template_id,
            resume_text=parsed.get("raw_text", resume_text),
            jd_text=jd_text,
            parsed_data=json.dumps(parsed, default=str),
            analysis_result=json.dumps(pipeline_result or {}, default=str),
            narrative_status="pending",
            pipeline_result=pipeline_result,
            requisition_id=cmd.requisition_id,
        )
        if cmd.requisition_id and cmd.user_id:
            _link_to_requisition(db, cmd.requisition_id, cmd.tenant_id, candidate_id, db_result.id, cmd.user_id)
        cmd.candidate_id = candidate_id
        SCREENING_TOTAL.labels(result="success").inc()
        return db_result, is_dup
    except Exception:
        SCREENING_TOTAL.labels(result="failure").inc()
        raise
    finally:
        SCREENING_DURATION_SECONDS.observe(time.perf_counter() - started)


def apply_screening_pipeline_result(db, db_result, pipeline_result: dict | None):
    """Update scores on an already-persisted ScreeningResult without re-resolving the candidate."""
    from app.backend.routes.analyze_helpers import (
        _populate_denormalized_columns,
        _restore_preserved_scores,
        _should_preserve_analysis_scores,
        _write_ai_decision_log,
    )

    if db_result is None:
        return None
    previous_analysis_result = db_result.analysis_result
    previous_deterministic_score = db_result.deterministic_score
    payload = dict(pipeline_result or {})
    if _should_preserve_analysis_scores(db_result, db_result.resume_text, db_result.jd_text):
        payload = _restore_preserved_scores(
            payload,
            previous_analysis_result=previous_analysis_result,
            previous_deterministic_score=previous_deterministic_score,
        )
    was_new = not previous_analysis_result or previous_analysis_result in ("{}", "")
    db_result.analysis_result = json.dumps(payload, default=str)
    _populate_denormalized_columns(db_result, payload)
    _write_ai_decision_log(
        db,
        db_result,
        payload,
        decision_type="INITIAL_ANALYSIS" if was_new else "REANALYSIS",
        required=True,
    )
    db.commit()
    db.refresh(db_result)
    return db_result
