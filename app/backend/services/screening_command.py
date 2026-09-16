from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

ALGORITHM_VERSION_DEFAULT = "1.0"

class CrossTenantRequisitionError(ValueError):
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
        scoring_weights=scoring_weights,
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
):
    import json

    from app.backend.models.db_models import Candidate
    from app.backend.routes.analyze import (
        _get_or_create_candidate,
        _link_to_requisition,
        _store_candidate_profile,
        _upsert_screening_result,
    )

    parsed = parsed_data or {}
    gaps = gap_analysis if gap_analysis is not None else {}
    content = file_content if file_content is not None else (resume_text or "").encode("utf-8")
    candidate_id, _ = _get_or_create_candidate(
        db,
        parsed,
        cmd.tenant_id,
        file_hash=file_hash,
        gap_analysis=gaps,
        profile_quality=pipeline_result.get("analysis_quality", "medium") if pipeline_result else "medium",
        file_content=content,
        filename=filename,
        resume_text=parsed.get("raw_text", resume_text),
    )
    cand = db.get(Candidate, candidate_id)
    if cand:
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
    return db_result
