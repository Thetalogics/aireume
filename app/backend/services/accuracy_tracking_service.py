"""
Accuracy Tracking Service - Track recruiter overrides and A/B test scoring variants.

This module provides:
1. Recording of when recruiters override AI recommendations
2. A/B testing framework for scoring algorithm changes
3. Analytics data for accuracy dashboard
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

log = logging.getLogger("aria.accuracy")


def record_recommendation_override(
    db: Session,
    screening_result_id: int,
    original_recommendation: str,
    final_recommendation: str,
    override_reason: Optional[str] = None,
    industry: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> bool:
    """Record when a recruiter overrides the AI recommendation.

    This data is used to calculate accuracy metrics and identify
    areas where the scoring algorithm needs improvement.

    Args:
        db: Database session
        screening_result_id: ID of the screening result
        original_recommendation: AI's original recommendation
        final_recommendation: Recruiter's override
        override_reason: Optional reason for override
        industry: Detected or specified industry
        tenant_id: Tenant ID for multi-tenant isolation

    Returns:
        True if recorded successfully
    """
    try:
        from app.backend.models.db_models import ScreeningResult

        result = db.query(ScreeningResult).filter(
            ScreeningResult.id == screening_result_id
        ).first()

        if not result:
            log.warning(f"Screening result {screening_result_id} not found")
            return False

        # Store override info in metadata JSON
        metadata = result.metadata or {}
        if not isinstance(metadata, dict):
            metadata = {}

        metadata["override"] = {
            "original": original_recommendation,
            "final": final_recommendation,
            "reason": override_reason,
            "industry": industry,
            "overridden_at": datetime.now(timezone.utc).isoformat(),
        }

        result.metadata = metadata
        db.commit()

        log.info(
            f"Recorded override for screening {screening_result_id}: "
            f"{original_recommendation} -> {final_recommendation}"
        )
        return True

    except Exception as e:
        log.error(f"Failed to record override: {e}")
        db.rollback()
        return False


def get_override_rate_by_industry(
    db: Session,
    tenant_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, float]:
    """Calculate override rate by industry.

    Args:
        db: Database session
        tenant_id: Optional tenant filter
        days: Number of days to analyze

    Returns:
        Dict mapping industry to override rate (0-1)
    """
    try:
        from app.backend.models.db_models import ScreeningResult
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = db.query(ScreeningResult).filter(
            ScreeningResult.created_at >= cutoff
        )

        if tenant_id:
            query = query.filter(ScreeningResult.tenant_id == tenant_id)

        results = query.all()

        industry_stats: Dict[str, Dict[str, int]] = {}

        for r in results:
            metadata = r.metadata or {}
            if not isinstance(metadata, dict):
                continue

            industry = metadata.get("override", {}).get("industry") or "unknown"

            if industry not in industry_stats:
                industry_stats[industry] = {"total": 0, "overridden": 0}

            industry_stats[industry]["total"] += 1

            if "override" in metadata:
                industry_stats[industry]["overridden"] += 1

        # Calculate rates
        override_rates = {}
        for industry, stats in industry_stats.items():
            if stats["total"] > 0:
                override_rates[industry] = stats["overridden"] / stats["total"]
            else:
                override_rates[industry] = 0.0

        return override_rates

    except Exception as e:
        log.error(f"Failed to get override rates: {e}")
        return {}


def compute_reviewer_agreement(
    predicted: List[str],
    reviewed: List[str],
    positive_label: str = "shortlist",
) -> Dict[str, Any]:
    """Agreement with human labels. This is not model accuracy vs ground truth."""
    if len(predicted) != len(reviewed) or not predicted:
        return {
            "agreement_rate": None,
            "precision": None,
            "recall": None,
            "sample_size": len(predicted),
            "undefined": True,
        }
    n = len(predicted)
    agree = sum(1 for a, b in zip(predicted, reviewed) if a == b)
    tp = sum(1 for a, b in zip(predicted, reviewed) if a == positive_label and b == positive_label)
    fp = sum(1 for a, b in zip(predicted, reviewed) if a == positive_label and b != positive_label)
    fn = sum(1 for a, b in zip(predicted, reviewed) if a != positive_label and b == positive_label)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "agreement_rate": agree / n,
        "precision": precision,
        "recall": recall,
        "sample_size": n,
        "undefined": False,
        "metric_definition": "agreement_rate is reviewer/AI label match, not ground-truth accuracy",
    }


def get_accuracy_metrics(
    db: Session,
    tenant_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """Reviewer agreement metrics. Not model accuracy."""
    override_rates = get_override_rate_by_industry(db, tenant_id, days)
    rates = list(override_rates.values())
    mean_override = (sum(rates) / len(rates)) if rates else 0.0
    return {
        "overall_agreement_rate": 1.0 - mean_override,
        "overall_accuracy": 1.0 - mean_override,  # deprecated alias
        "override_rates_by_industry": override_rates,
        "total_screens": None,
        "period_days": days,
        "metric_definition": "agreement with recruiter overrides, not ground-truth accuracy",
    }


# ─── A/B Testing Framework ───────────────────────────────────────────────────

class ScoringExperiment:
    """A/B test for scoring algorithm variants."""

    def __init__(
        self,
        experiment_name: str,
        description: str = "",
    ):
        self.experiment_name = experiment_name
        self.description = description
        self.variants: Dict[str, Dict[str, Any]] = {}

    def add_variant(
        self,
        variant_name: str,
        weights: Optional[Dict[str, float]] = None,
        description: str = "",
    ) -> None:
        """Add a variant to the experiment."""
        self.variants[variant_name] = {
            "weights": weights,
            "description": description,
            "impressions": 0,
            "overrides": 0,
        }

    def record_impression(
        self,
        variant_name: str,
        recommendation: str,
        was_overridden: bool,
    ) -> None:
        """Record an impression and outcome for a variant."""
        if variant_name not in self.variants:
            log.warning(f"Unknown variant: {variant_name}")
            return

        self.variants[variant_name]["impressions"] += 1
        if was_overridden:
            self.variants[variant_name]["overrides"] += 1

    def get_results(self) -> Dict[str, Any]:
        """Get experiment results."""
        results = {
            "experiment_name": self.experiment_name,
            "description": self.description,
            "variants": {},
        }

        for name, data in self.variants.items():
            impressions = data["impressions"]
            overrides = data["overrides"]

            results["variants"][name] = {
                "description": data["description"],
                "impressions": impressions,
                "override_rate": overrides / impressions if impressions > 0 else 0,
                "agreement_rate": 1.0 - (overrides / impressions if impressions > 0 else 0),
                "accuracy": 1.0 - (overrides / impressions if impressions > 0 else 0),
            }

        return results


# Global experiment registry
_experiments: Dict[str, ScoringExperiment] = {}


def get_experiment(name: str) -> Optional[ScoringExperiment]:
    """Get an existing experiment by name."""
    return _experiments.get(name)


def create_experiment(
    name: str,
    description: str = "",
) -> ScoringExperiment:
    """Create a new A/B experiment."""
    exp = ScoringExperiment(name, description)
    _experiments[name] = exp
    return exp


def record_experiment_outcome(
    experiment_name: str,
    variant_name: str,
    recommendation: str,
    was_overridden: bool,
) -> bool:
    """Record outcome for an A/B test variant."""
    exp = _experiments.get(experiment_name)
    if not exp:
        log.warning(f"Experiment not found: {experiment_name}")
        return False

    exp.record_impression(variant_name, recommendation, was_overridden)
    return True
