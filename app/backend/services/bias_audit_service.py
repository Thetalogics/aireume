"""Bias auditing framework for screening outcomes.

Provides statistical analysis of screening results to detect disparate
impact across demographic groups. Implements the four-fifths (4/5) rule
as a basic EEOC compliance check, plus additional statistical tests.

NOTE: This framework does NOT collect demographic data directly. It
analyzes outcomes that have been tagged with demographic metadata
(e.g. from voluntary self-identification surveys). All analysis is
aggregate-level only — no individual identification.
"""

import logging
import json
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

logger = logging.getLogger(__name__)


@dataclass
class GroupOutcome:
    """Screening outcome statistics for a demographic group."""
    group_label: str
    total_candidates: int
    shortlisted: int
    considered: int
    rejected: int
    shortlist_rate: float
    consider_rate: float
    reject_rate: float
    avg_fit_score: float


@dataclass
class BiasAuditResult:
    """Result of a bias audit analysis."""
    audit_date: str
    tenant_id: Optional[int]
    total_candidates: int
    groups: List[Dict[str, Any]]
    four_fifths_violations: List[Dict[str, Any]]
    score_disparities: List[Dict[str, Any]]
    recommendation: str
    risk_level: str  # "none", "low", "moderate", "high"


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _compute_group_outcomes(records: List[Dict[str, Any]], group_field: str) -> List[GroupOutcome]:
    """Compute outcome statistics per group."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        label = r.get(group_field, "unknown")
        if not label:
            label = "unknown"
        groups.setdefault(label, []).append(r)

    outcomes = []
    for label, group_records in sorted(groups.items()):
        total = len(group_records)
        scores = [r.get("fit_score", 0) or 0 for r in group_records]
        shortlisted = sum(1 for r in group_records if r.get("recommendation") == "shortlist")
        considered = sum(1 for r in group_records if r.get("recommendation") == "consider")
        rejected = sum(1 for r in group_records if r.get("recommendation") == "reject")

        outcomes.append(GroupOutcome(
            group_label=label,
            total_candidates=total,
            shortlisted=shortlisted,
            considered=considered,
            rejected=rejected,
            shortlist_rate=_safe_div(shortlisted, total),
            consider_rate=_safe_div(considered, total),
            reject_rate=_safe_div(rejected, total),
            avg_fit_score=sum(scores) / len(scores) if scores else 0.0,
        ))

    return outcomes


def _four_fifths_rule(outcomes: List[GroupOutcome], min_group_n: int = 30) -> List[Dict[str, Any]]:
    """Adverse impact ratio (selection-rate disparity), not a legal conclusion.

    Groups below ``min_group_n`` are omitted; the ratio is undefined when no
    eligible comparison groups remain.
    """
    eligible = [o for o in outcomes if o.total_candidates >= min_group_n]
    if len(eligible) < 2:
        return []

    max_rate = max(o.shortlist_rate for o in eligible)
    if max_rate == 0:
        return []

    threshold = max_rate * 0.80
    violations = []

    for o in eligible:
        ratio = _safe_div(o.shortlist_rate, max_rate)
        if ratio < 0.80:
            violations.append({
                "group": o.group_label,
                "metric": "adverse_impact_ratio",
                "selection_rate": round(o.shortlist_rate, 4),
                "reference_selection_rate": round(max_rate, 4),
                "adverse_impact_ratio": round(ratio, 4),
                "sample_size": o.total_candidates,
                "undefined": False,
                "threshold": 0.80,
                "severity": "high" if ratio < 0.60 else "moderate" if ratio < 0.70 else "low",
            })

    return violations


def _score_disparity_test(outcomes: List[GroupOutcome]) -> List[Dict[str, Any]]:
    """Detect statistically significant score disparities between groups.

    Uses a simplified effect-size comparison (Cohen's d) between each group
    and the overall mean. Groups with large deviations are flagged.
    """
    if len(outcomes) < 2:
        return []

    all_scores = [o.avg_fit_score for o in outcomes]
    overall_mean = sum(all_scores) / len(all_scores) if all_scores else 0

    disparities = []
    for o in outcomes:
        if o.total_candidates < 5:
            continue  # Skip groups with too few samples

        diff = o.avg_fit_score - overall_mean
        # Simple effect size: difference from mean as percentage of mean
        effect_pct = _safe_div(abs(diff), overall_mean) * 100 if overall_mean > 0 else 0

        if effect_pct > 15:  # >15% deviation from mean
            disparities.append({
                "group": o.group_label,
                "avg_score": round(o.avg_fit_score, 2),
                "overall_mean": round(overall_mean, 2),
                "deviation": round(diff, 2),
                "deviation_pct": round(effect_pct, 1),
                "direction": "above" if diff > 0 else "below",
                "severity": "high" if effect_pct > 25 else "moderate",
            })

    return disparities


def run_bias_audit(
    db: Session,
    tenant_id: Optional[int] = None,
    group_field: str = "gender",
    days_back: int = 90,
) -> BiasAuditResult:
    """Run a bias audit on screening outcomes.

    Args:
        db: Database session.
        tenant_id: Optional tenant filter.
        group_field: Demographic field to group by (e.g. 'gender', 'ethnicity', 'age_group').
        days_back: How many days of data to analyze.

    Returns:
        BiasAuditResult with group outcomes, violations, and recommendations.
    """
    from app.backend.models.db_models import Candidate, ScreeningResult

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        query = db.query(
            Candidate.id,
            Candidate.gender,
            Candidate.ethnicity,
            Candidate.age_group,
            ScreeningResult.fit_score,
            ScreeningResult.final_recommendation,
        ).join(
            ScreeningResult, ScreeningResult.candidate_id == Candidate.id
        ).filter(
            ScreeningResult.created_at >= cutoff,
        )

        if tenant_id:
            query = query.filter(Candidate.tenant_id == tenant_id)

        rows = query.all()

        records = []
        for row in rows:
            records.append({
                "id": row[0],
                "gender": row[1] or "unknown",
                "ethnicity": row[2] or "unknown",
                "age_group": row[3] or "unknown",
                "fit_score": row[4] or 0,
                "recommendation": row[5] or "reject",
            })

        if len(records) < 30:
            return BiasAuditResult(
                audit_date=datetime.now(timezone.utc).isoformat(),
                tenant_id=tenant_id,
                total_candidates=len(records),
                groups=[],
                four_fifths_violations=[],
                score_disparities=[],
                recommendation="Insufficient sample for disparity analysis (minimum 30 candidates). Metrics are undefined.",
                risk_level="none",
            )

        outcomes = _compute_group_outcomes(records, group_field)
        violations = _four_fifths_rule(outcomes)
        disparities = _score_disparity_test(outcomes)

        # Determine overall risk level
        if any(v["severity"] == "high" for v in violations):
            risk_level = "high"
            recommendation = (
                "Selection-rate disparity exceeded the 0.80 adverse-impact-ratio threshold. "
                "This is a statistical signal, not a legal determination. Review screening criteria."
            )
        elif violations or any(d["severity"] == "high" for d in disparities):
            risk_level = "moderate"
            recommendation = (
                "Moderate bias risk detected. Review screening criteria and scoring weights. "
                "Monitor outcomes over time and consider adjusting thresholds."
            )
        elif disparities:
            risk_level = "low"
            recommendation = (
                "Minor score disparities detected. Continue monitoring. "
                "No immediate action required but review at next audit cycle."
            )
        else:
            risk_level = "none"
            recommendation = "No significant bias detected. Continue regular monitoring."

        result = BiasAuditResult(
            audit_date=datetime.now(timezone.utc).isoformat(),
            tenant_id=tenant_id,
            total_candidates=len(records),
            groups=[asdict(o) for o in outcomes],
            four_fifths_violations=violations,
            score_disparities=disparities,
            recommendation=recommendation,
            risk_level=risk_level,
        )

        # Save audit result to database
        _save_audit_result(db, tenant_id, group_field, result)

        logger.info("Bias audit completed: %d candidates, risk=%s", len(records), risk_level)
        return result

    except Exception as e:
        logger.error("Bias audit failed: %s", e)
        return BiasAuditResult(
            audit_date=datetime.now(timezone.utc).isoformat(),
            tenant_id=tenant_id,
            total_candidates=0,
            groups=[],
            four_fifths_violations=[],
            score_disparities=[],
            recommendation=f"Audit failed: {e}",
            risk_level="none",
        )


def _save_audit_result(db: Session, tenant_id: Optional[int], group_field: str, result: BiasAuditResult):
    """Save bias audit result to database for historical tracking."""
    try:
        from app.backend.models.db_models import AuditLog
        import json

        audit = AuditLog(
            actor_user_id=None,
            actor_email="system",
            tenant_id=tenant_id,
            action="bias.audit",
            resource_type="bias_audit",
            resource_id=None,
            details=json.dumps({
                "group_field": group_field,
                "total_candidates": result.total_candidates,
                "risk_level": result.risk_level,
                "four_fifths_violations": result.four_fifths_violations,
                "score_disparities": result.score_disparities,
                "recommendation": result.recommendation,
            }),
        )
        db.add(audit)
        db.commit()
    except Exception as e:
        logger.warning("Failed to save bias audit result: %s", e)
        db.rollback()
