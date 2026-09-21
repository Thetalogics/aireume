"""Idempotent legacy import of ScreeningResult rows into ScreeningDecision."""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy.orm import Session

from app.backend.models.db_models import ScreeningDecision, ScreeningResult
from app.backend.services.decision_service import (
    DECISION_LEGACY_IMPORTED,
    create_screening_decision,
    find_projection_mismatches,
    repair_projection,
)


def _payload(result: ScreeningResult) -> dict[str, Any]:
    try:
        data = json.loads(result.analysis_result or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


DEFAULT_BATCH_SIZE = 100


def import_screening_result(db: Session, *, screening_result_id: int) -> ScreeningDecision:
    result = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.id == screening_result_id)
        .with_for_update()
        .one()
    )
    existing = (
        db.query(ScreeningDecision)
        .filter_by(screening_result_id=result.id, decision_type=DECISION_LEGACY_IMPORTED)
        .one_or_none()
    )
    if existing is not None:
        if result.current_decision_id is None:
            result.current_decision_id = existing.id
        return existing
    keep_existing_current = result.current_decision_id is not None
    payload = _payload(result)
    fit = payload.get("fit_score")
    if fit is None:
        fit = result.deterministic_score
    components = payload.get("components") or {
        "skills": 0,
        "experience": 0,
        "architecture": 0,
        "education": 0,
        "timeline": 0,
        "domain": 0,
    }
    if fit == 0:
        components = {key: 0 for key in components}
    risk = payload.get("risk_penalty")
    if risk is None:
        risk = 0
    return create_screening_decision(
        db,
        screening_result_id=result.id,
        decision_type=DECISION_LEGACY_IMPORTED,
        scores={"components": components, "risk_penalty": risk},
        eligibility={
            "status": result.eligibility_status,
            "reason": result.eligibility_reason,
            "gate_trace": [],
        },
        recommendation=payload.get("final_recommendation"),
        scoring_weights=None,
        algorithm_version=None,
        input_snapshot={
            "schema_version": "decision_input_v1",
            "legacy": True,
            "components": components,
            "risk_penalty": risk,
        },
        policy_context=None,
        actor_id=None,
        operation_id=f"legacy-import:{result.id}",
        analysis_generation=result.analysis_generation,
        provenance_complete=False,
        model_context=None,
        prompt_context=None,
        update_current=not keep_existing_current,
    )


def _result_ids(db: Session, *, after_id: int, limit: int) -> list[int]:
    rows = (
        db.query(ScreeningResult.id)
        .filter(ScreeningResult.id > after_id)
        .order_by(ScreeningResult.id.asc())
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def dry_run_legacy_decisions(
    db: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    after_id: int = 0,
    max_rows: int | None = None,
) -> dict[str, int]:
    """Count eligible/existing rows without writing. Keyset-paginated."""
    processed = 0
    eligible = 0
    existing_legacy = 0
    native_current = 0
    last_id = after_id
    remaining = max_rows
    while True:
        take = batch_size if remaining is None else min(batch_size, remaining)
        if take <= 0:
            break
        ids = _result_ids(db, after_id=last_id, limit=take)
        if not ids:
            break
        for result_id in ids:
            result = db.get(ScreeningResult, result_id)
            last_id = result_id
            processed += 1
            if result is None:
                continue
            legacy = (
                db.query(ScreeningDecision.id)
                .filter_by(screening_result_id=result.id, decision_type=DECISION_LEGACY_IMPORTED)
                .first()
            )
            if legacy is not None:
                existing_legacy += 1
                continue
            eligible += 1
            if result.current_decision_id is not None:
                native_current += 1
        if remaining is not None:
            remaining -= len(ids)
    estimated_batches = (processed + batch_size - 1) // batch_size if processed else 0
    return {
        "processed": processed,
        "eligible": eligible,
        "existing_legacy": existing_legacy,
        "native_current": native_current,
        "estimated_batches": estimated_batches,
        "last_id": last_id,
    }


def import_legacy_decisions(
    db: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    after_id: int = 0,
    max_rows: int | None = None,
    commit: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """Import eligible ScreeningResult rows using id-keyset batches.

    Commits after each batch. Resume with ``after_id``. Does not overwrite an
    existing native Phase-2 current pointer.
    """
    if dry_run:
        return dry_run_legacy_decisions(
            db, batch_size=batch_size, after_id=after_id, max_rows=max_rows
        )
    processed = 0
    created = 0
    skipped_existing = 0
    skipped_native_current = 0
    failed = 0
    last_id = after_id
    remaining = max_rows
    while True:
        take = batch_size if remaining is None else min(batch_size, remaining)
        if take <= 0:
            break
        ids = _result_ids(db, after_id=last_id, limit=take)
        if not ids:
            break
        for result_id in ids:
            last_id = result_id
            processed += 1
            try:
                result = db.get(ScreeningResult, result_id)
                native_before = result.current_decision_id if result is not None else None
                existed = (
                    db.query(ScreeningDecision.id)
                    .filter_by(
                        screening_result_id=result_id,
                        decision_type=DECISION_LEGACY_IMPORTED,
                    )
                    .first()
                    is not None
                )
                decision = import_screening_result(db, screening_result_id=result_id)
                if existed:
                    skipped_existing += 1
                else:
                    created += 1
                    if native_before is not None:
                        skipped_native_current += 1
                _ = decision
            except Exception:
                failed += 1
                db.rollback()
        if commit:
            db.commit()
        if remaining is not None:
            remaining -= len(ids)
    return {
        "processed": processed,
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_native_current": skipped_native_current,
        "failed": failed,
        "last_id": last_id,
    }


def check_governance_consistency(db, *, tenant_id=None):
    return find_projection_mismatches(db, tenant_id=tenant_id)


def repair_current_projection(db, *, screening_result_id: int, dry_run: bool = True):
    return repair_projection(db, screening_result_id=screening_result_id, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy screening results as LEGACY_IMPORTED decisions.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.backend.db.database import SessionLocal

    db = SessionLocal()
    try:
        stats = import_legacy_decisions(
            db,
            batch_size=args.batch_size,
            after_id=args.after_id,
            max_rows=args.max_rows,
            dry_run=args.dry_run,
        )
        print(stats)
    finally:
        db.close()


if __name__ == "__main__":
    main()
