"""Tenant-scoped screening decision history, comparison, override, and export."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user
from app.backend.middleware.rbac import require_active_recruiter, require_candidate_read_access
from app.backend.models.db_models import ScreeningDecision, ScreeningResult, User
from app.backend.services.decision_service import (
    DecisionNotFoundError,
    StaleDecisionConflict,
    compare_decisions,
    create_human_override,
    get_screening_decision,
    list_screening_decisions,
    serialize_screening_decision,
)

router = APIRouter(prefix="/api/results", tags=["decisions"])


class HumanOverrideRequest(BaseModel):
    expected_current_decision_id: int
    recommendation: str
    reason_code: str
    reason_text: str | None = None
    operation_id: str | None = None


def _visible_result(db: Session, user: User, result_id: int) -> ScreeningResult:
    result = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.id == result_id, ScreeningResult.tenant_id == user.tenant_id)
        .one_or_none()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Screening result not found")
    if result.candidate_id:
        require_candidate_read_access(db, user, result.candidate_id)
    return result


@router.get("/{result_id}/decisions")
def list_decisions(
    result_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = _visible_result(db, current_user, result_id)
    offset = (page - 1) * page_size
    try:
        rows = (
            db.query(ScreeningDecision)
            .options(selectinload(ScreeningDecision.narratives))
            .filter_by(tenant_id=current_user.tenant_id, screening_result_id=result.id)
            .order_by(ScreeningDecision.decision_version.desc(), ScreeningDecision.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        total = (
            db.query(ScreeningDecision)
            .filter_by(tenant_id=current_user.tenant_id, screening_result_id=result.id)
            .count()
        )
    except DecisionNotFoundError:
        raise HTTPException(status_code=404, detail="Decision history not found")
    return {
        "items": [
            serialize_screening_decision(row, current_id=result.current_decision_id)
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "current_decision_id": result.current_decision_id,
    }


@router.get("/{result_id}/decisions/{decision_id}")
def decision_detail(
    result_id: int,
    decision_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = _visible_result(db, current_user, result_id)
    try:
        decision = get_screening_decision(
            db,
            tenant_id=current_user.tenant_id,
            screening_result_id=result.id,
            decision_id=decision_id,
        )
    except DecisionNotFoundError:
        raise HTTPException(status_code=404, detail="Decision not found")
    payload = serialize_screening_decision(decision, current_id=result.current_decision_id)
    payload["explanation"] = decision.explanation_payload
    return payload


@router.get("/{result_id}/decisions/{decision_id}/compare/{other_id}")
def compare_decision_pair(
    result_id: int,
    decision_id: int,
    other_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = _visible_result(db, current_user, result_id)
    try:
        left = get_screening_decision(
            db, tenant_id=current_user.tenant_id, screening_result_id=result.id, decision_id=decision_id
        )
        right = get_screening_decision(
            db, tenant_id=current_user.tenant_id, screening_result_id=result.id, decision_id=other_id
        )
    except DecisionNotFoundError:
        raise HTTPException(status_code=404, detail="Decision not found")
    return compare_decisions(left, right)


@router.get("/{result_id}/decisions/{decision_id}/export")
def export_decision(
    result_id: int,
    decision_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = _visible_result(db, current_user, result_id)
    try:
        decision = get_screening_decision(
            db, tenant_id=current_user.tenant_id, screening_result_id=result.id, decision_id=decision_id
        )
    except DecisionNotFoundError:
        raise HTTPException(status_code=404, detail="Decision not found")
    return serialize_screening_decision(decision, current_id=result.current_decision_id)


@router.post("/{result_id}/decisions/override")
def override_decision(
    result_id: int,
    body: HumanOverrideRequest,
    current_user: User = Depends(require_active_recruiter),
    db: Session = Depends(get_db),
):
    result = _visible_result(db, current_user, result_id)
    try:
        decision = create_human_override(
            db,
            screening_result_id=result.id,
            expected_current_decision_id=body.expected_current_decision_id,
            actor_id=current_user.id,
            operation_id=body.operation_id or f"override:{result.id}:{body.expected_current_decision_id}:{current_user.id}",
            recommendation=body.recommendation,
            reason_code=body.reason_code,
            reason_text=body.reason_text,
        )
        db.commit()
    except StaleDecisionConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail="Current decision changed")
    except DecisionNotFoundError:
        db.rollback()
        raise HTTPException(status_code=404, detail="Decision not found")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_screening_decision(decision, current_id=result.current_decision_id)
