"""Central candidate-processing policy (architectural readiness, not legal advice).

Default: allow all processing so existing deployments keep working.
Tenants may opt into consent requirements via Tenant.metadata_json:

    {
      "candidate_processing": {
        "require_consent_for": ["RESUME_ANALYSIS", "AI_SCREENING", "VOICE_INTERVIEW", "TRANSCRIPT_ANALYSIS"]
      }
    }

Consent rows are tenant-scoped. Production code does not special-case candidate IDs
or test environments.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.backend.models.db_models import CandidateConsent, Tenant

log = logging.getLogger(__name__)

PROCESSING_RESUME_ANALYSIS = "RESUME_ANALYSIS"
PROCESSING_AI_SCREENING = "AI_SCREENING"
PROCESSING_VOICE_INTERVIEW = "VOICE_INTERVIEW"
PROCESSING_TRANSCRIPT_ANALYSIS = "TRANSCRIPT_ANALYSIS"

_CONSENT_TYPE_FOR_PROCESSING = {
    PROCESSING_RESUME_ANALYSIS: "ai_screening",
    PROCESSING_AI_SCREENING: "ai_screening",
    PROCESSING_VOICE_INTERVIEW: "voice_interview",
    PROCESSING_TRANSCRIPT_ANALYSIS: "ai_screening",
}


@dataclass(frozen=True)
class ProcessingPolicyDecision:
    allowed: bool
    reason: str
    policy_basis: str
    processing_type: str


def can_process(
    *,
    db: Session,
    tenant_id: int,
    candidate_id: Optional[int],
    processing_type: str,
    context: Optional[dict] = None,
) -> ProcessingPolicyDecision:
    """Evaluate whether this tenant/candidate/processing_type may proceed."""
    del context  # reserved for future policy inputs; unused by default
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    required = _required_processing_types(tenant)
    if processing_type not in required:
        return ProcessingPolicyDecision(
            allowed=True,
            reason="legacy_default_allow",
            policy_basis="tenant.metadata_json.candidate_processing.require_consent_for empty or type not listed",
            processing_type=processing_type,
        )
    if not candidate_id:
        return ProcessingPolicyDecision(
            allowed=False,
            reason="consent_required_missing_candidate",
            policy_basis="consent required but no candidate id",
            processing_type=processing_type,
        )
    consent_type = _CONSENT_TYPE_FOR_PROCESSING.get(processing_type, "ai_screening")
    row = (
        db.query(CandidateConsent)
        .filter(
            CandidateConsent.tenant_id == tenant_id,
            CandidateConsent.candidate_id == candidate_id,
            CandidateConsent.consent_type == consent_type,
        )
        .first()
    )
    if row is None or not row.consented:
        return ProcessingPolicyDecision(
            allowed=False,
            reason="consent_missing",
            policy_basis=f"require_consent_for includes {processing_type}",
            processing_type=processing_type,
        )
    if row.withdrawal_at is not None:
        withdrawn = row.withdrawal_at
        if withdrawn.tzinfo is None:
            withdrawn = withdrawn.replace(tzinfo=timezone.utc)
        if withdrawn <= datetime.now(timezone.utc):
            return ProcessingPolicyDecision(
                allowed=False,
                reason="consent_revoked",
                policy_basis="CandidateConsent.withdrawal_at set",
                processing_type=processing_type,
            )
    return ProcessingPolicyDecision(
        allowed=True,
        reason="consent_granted",
        policy_basis=f"CandidateConsent.consent_type={consent_type}",
        processing_type=processing_type,
    )


def enforce_candidate_processing_policy(
    db: Session,
    *,
    tenant_id: int,
    candidate_id: Optional[int],
    processing_type: str,
    context: Optional[dict] = None,
) -> ProcessingPolicyDecision:
    decision = can_process(
        db=db,
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        processing_type=processing_type,
        context=context,
    )
    if not decision.allowed:
        log.info(
            "candidate processing denied tenant_id=%s candidate_id=%s type=%s reason=%s",
            tenant_id, candidate_id, processing_type, decision.reason,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Candidate processing is not permitted under the current tenant policy",
                "reason": decision.reason,
                "processing_type": processing_type,
            },
        )
    return decision


def _required_processing_types(tenant: Tenant | None) -> set[str]:
    if tenant is None or not tenant.metadata_json:
        return set()
    try:
        meta = json.loads(tenant.metadata_json)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(meta, dict):
        return set()
    block = meta.get("candidate_processing") or {}
    items = block.get("require_consent_for") or []
    if not isinstance(items, list):
        return set()
    return {str(x) for x in items}
