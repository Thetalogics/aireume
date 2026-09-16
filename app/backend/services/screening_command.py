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
