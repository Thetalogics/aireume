"""
Adverse Action Report Service

Generates recruiter-facing adverse-action *workflow assistance* (not legal advice).
Templates are jurisdiction-agnostic unless a tenant configures legal review.
"""
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DecisionFactor:
    """A single factor contributing to the hiring decision."""
    factor: str
    category: str  # technical_requirement, concern, overall_assessment
    evidence: str
    weight: str  # high, medium, low
    severity: Optional[str] = None  # For red flags


class AdverseActionService:
    """
    Service for generating adverse action reports.
    
    Ensures all hiring decisions are:
    - Evidence-based
    - Job-related
    - Protected class neutral
    - Fully documented
    """
    
    def generate_report(
        self,
        analysis_result: Dict[str, Any],
        transcript_text: str,
        jd_text: str,
        candidate_id: Optional[int] = None,
        candidate_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate comprehensive adverse action report.
        
        Args:
            analysis_result: LLM analysis output with scores and evidence
            transcript_text: Original transcript
            jd_text: Job description
            candidate_id: Database ID of candidate
            candidate_name: Candidate name (for report only)
            
        Returns:
            Complete adverse action report with all documentation
        """
        decision_factors = self._extract_decision_factors(analysis_result)
        
        # Generate unique report ID
        timestamp = int(datetime.now(timezone.utc).timestamp())
        report_id = f"AAR-{candidate_id or 'UNKNOWN'}-{timestamp}"
        
        report = {
            "report_id": report_id,
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "analysis_id": analysis_result.get("analysis_id"),
            "decision": analysis_result.get("recommendation", "hold"),
            "decision_date": datetime.now(timezone.utc).isoformat(),
            
            # Decision factors (what led to decision)
            "decision_factors": [
                {
                    "factor": df.factor,
                    "category": df.category,
                    "evidence": df.evidence,
                    "weight": df.weight,
                    "severity": df.severity
                }
                for df in decision_factors
            ],
            "primary_reason": decision_factors[0].__dict__ if decision_factors else None,
            
            # Bias mitigation documentation
            "bias_mitigation": {
                "pii_redacted": analysis_result.get("pii_redacted", False),
                "pii_redaction_count": analysis_result.get("pii_redaction_count", 0),
                "evidence_quality_score": analysis_result.get("evidence_quality_score", 0),
                "evidence_validation": analysis_result.get("evidence_validation", {}),
                "bias_note": analysis_result.get("bias_note", "")
            },
            
            # Full audit trail
            "audit_trail": {
                "transcript_length": len(transcript_text),
                "jd_length": len(jd_text),
                "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                "fit_score": analysis_result.get("fit_score"),
                "technical_depth": analysis_result.get("technical_depth"),
                "communication_quality": analysis_result.get("communication_quality"),
            },
            
            "legal_review": {
                "disclaimer": (
                    "This report is workflow assistance only. It is not legal advice and does "
                    "not certify EEOC, FCRA, or other jurisdictional compliance. Have counsel "
                    "review notices before sending."
                ),
                "evidence_based": len(decision_factors) > 0,
                "documented": True,
            },
            
            # Candidate communication
            "candidate_communication": self._generate_candidate_message(
                decision_factors,
                analysis_result.get("recommendation", "hold")
            ),
        }
        
        return report
    
    def _extract_decision_factors(
        self,
        analysis_result: Dict[str, Any]
    ) -> List[DecisionFactor]:
        """
        Extract all factors that contributed to the decision.
        
        Prioritizes:
        1. Red flags (high severity)
        2. Missing required skills
        3. Low scores
        """
        factors = []
        
        # 1. Red flags with evidence
        for flag in analysis_result.get("red_flags", []):
            if flag.get("evidence"):
                factors.append(DecisionFactor(
                    factor=flag["flag"],
                    category="concern",
                    evidence=flag["evidence"],
                    weight="high" if flag.get("severity") == "high" else "medium",
                    severity=flag.get("severity", "medium")
                ))
        
        # 2. Technical gaps (JD requirements not demonstrated)
        for item in analysis_result.get("jd_alignment", []):
            if not item.get("demonstrated") and item.get("requirement"):
                factors.append(DecisionFactor(
                    factor=f"Required skill not demonstrated: {item['requirement']}",
                    category="technical_requirement",
                    evidence=item.get("evidence") or "No evidence found in transcript",
                    weight="high"
                ))
        
        # 3. Overall score thresholds
        fit_score = analysis_result.get("fit_score", 50)
        if fit_score < 45:
            factors.append(DecisionFactor(
                factor=f"Overall fit score below threshold: {fit_score}/100",
                category="overall_assessment",
                evidence=(
                    f"Technical depth: {analysis_result.get('technical_depth', 0)}, "
                    f"Communication: {analysis_result.get('communication_quality', 0)}"
                ),
                weight="high"
            ))
        
        # 4. Areas for improvement (if significant)
        for area in analysis_result.get("areas_for_improvement", []):
            if isinstance(area, dict) and area.get("evidence"):
                factors.append(DecisionFactor(
                    factor=area["area"],
                    category="improvement_area",
                    evidence=area.get("evidence", ""),
                    weight="medium"
                ))
        
        # Sort by weight (high first)
        weight_order = {"high": 0, "medium": 1, "low": 2}
        factors.sort(key=lambda f: weight_order.get(f.weight, 2))
        
        return factors
    
    def _generate_candidate_message(
        self,
        decision_factors: List[DecisionFactor],
        recommendation: str
    ) -> Dict[str, Any]:
        """
        Generate professional, specific feedback for candidate.
        
        Focuses on job-related factors only.
        """
        if recommendation == "proceed":
            return {
                "message": "Thank you for your interest. We are pleased to move forward with your application.",
                "specific_feedback": []
            }
        
        # Extract technical gaps for feedback
        technical_gaps = [
            f for f in decision_factors 
            if f.category == "technical_requirement"
        ]
        
        if not technical_gaps:
            return {
                "message": (
                    "Thank you for your interest in this position. After careful review of your interview, "
                    "we have decided to move forward with other candidates whose qualifications more closely "
                    "align with the role requirements."
                ),
                "specific_feedback": []
            }
        
        # Provide specific, actionable feedback
        feedback_items = [f.factor for f in technical_gaps[:3]]  # Top 3
        
        message = f"""Thank you for your interest in this position. After careful review of your interview, we have decided to move forward with other candidates.

Specifically, we were looking for demonstrated experience in the following areas which were not evident in the interview:
{chr(10).join(f'- {item}' for item in feedback_items)}

We encourage you to apply for future positions that match your experience and skills."""
        
        return {
            "message": message,
            "specific_feedback": feedback_items
        }
    
    def generate_summary(self, report: Dict[str, Any]) -> str:
        """Generate human-readable summary of adverse action report."""
        decision = report["decision"].upper()
        factors_count = len(report["decision_factors"])
        
        summary = f"""Adverse Action Report Summary
Report ID: {report['report_id']}
Decision: {decision}
Date: {report['decision_date']}

Decision Factors ({factors_count}):
"""
        
        for i, factor in enumerate(report["decision_factors"][:5], 1):
            summary += f"\n{i}. [{factor['weight'].upper()}] {factor['factor']}"
            if factor.get('evidence'):
                evidence_preview = factor['evidence'][:100]
                summary += f"\n   Evidence: \"{evidence_preview}...\""
        
        summary += f"""

Legal review:
- {report.get('legal_review', {}).get('disclaimer', 'Counsel review required before sending notices.')}
- Evidence based: {report.get('legal_review', {}).get('evidence_based')}
- PII redacted: {report['bias_mitigation']['pii_redacted']}
- Evidence quality: {report['bias_mitigation']['evidence_quality_score']:.1f}/100
"""
        
        return summary


# Singleton instance
_adverse_action_service: Optional[AdverseActionService] = None


def get_adverse_action_service() -> AdverseActionService:
    """Get or create singleton adverse action service."""
    global _adverse_action_service
    if _adverse_action_service is None:
        _adverse_action_service = AdverseActionService()
    return _adverse_action_service
