"""Standardized fit score computation — single source of truth."""

import logging
from typing import Any, Dict, List, Optional

from app.backend.services.constants import (
    DEFAULT_WEIGHTS,
    RECOMMENDATION_THRESHOLDS,
    combine_skill_ratios,
)
from app.backend.services.risk_calculator import compute_risk_penalty

log = logging.getLogger(__name__)


def scalar_breakdown_score(val: Any, default: int = 50) -> int:
    """Extract numeric score from a score_breakdown dimension (dict or legacy int)."""
    if isinstance(val, dict):
        score = val.get("score", default)
        return int(score) if score is not None else default
    if isinstance(val, (int, float)):
        return int(val)
    return default


def _compute_team_gap_bonus(matched_skills: list, team_gaps: list) -> float:
    """Bonus score (0-100) for candidates who fill team skill gaps."""
    if not team_gaps:
        return 0.0
    gaps_lower = [g.lower() for g in team_gaps]
    # matched_skills can be strings or dicts with "skill" key
    matched_lower = []
    for s in matched_skills:
        if isinstance(s, dict):
            matched_lower.append(s.get("skill", "").lower())
        else:
            matched_lower.append(s.lower())
    gaps_filled = [g for g in gaps_lower if g in matched_lower]
    return (len(gaps_filled) / len(team_gaps)) * 100


def _apply_trend_factor(skill: str, skill_trends: list) -> float:
    """Per-skill multiplier based on market trend direction."""
    if not skill_trends:
        return 1.0
    trend = next((t for t in skill_trends if t.get("skill", "").lower() == skill.lower()), None)
    if not trend:
        return 1.0
    growth = trend.get("growth_pct", 0)
    direction = trend.get("direction", "stable")
    if direction == "rising":
        return min(1.2, 1.0 + (abs(growth) / 100) * 0.2)
    elif direction == "falling":
        return max(0.8, 1.0 - (abs(growth) / 100) * 0.2)
    return 1.0


def _apply_outcome_factor(skill: str, outcome_patterns: list, *, enabled: bool) -> float:
    """Optional contextual multiplier. Never applied unless explicitly enabled."""
    if not enabled or not outcome_patterns:
        return 1.0
    pattern = next((p for p in outcome_patterns if p.get("skill", "").lower() == skill.lower()), None)
    if not pattern or int(pattern.get("sample_size") or 0) < 30:
        return 1.0
    if pattern.get("confidence") in (None, "low"):
        return 1.0
    success_rate = pattern.get("success_rate", 0.5)
    return 0.8 + (success_rate * 0.4)


def _generate_exp_explanation(actual: float, required: float, score: int) -> str:
    """Generate human-readable explanation for experience score."""
    if not required:
        return "No specific experience requirement specified"
    if actual >= required * 1.5:
        return f"{actual:.1f} years significantly exceeds {required:.0f} year requirement"
    elif actual >= required:
        return f"{actual:.1f} years meets/exceeds {required:.0f} year requirement"
    elif actual >= required * 0.7:
        return f"{actual:.1f} years is close to {required:.0f} year requirement"
    else:
        return f"{actual:.1f} years falls short of {required:.0f} year requirement"


def compute_fit_score(
    scores: Dict[str, Any],
    scoring_weights: Optional[Dict] = None,
    risk_signals: Optional[List[dict]] = None,
    risk_penalty: Optional[float] = None,
    jd_analysis: Optional[Dict] = None,
    phase3_context: Optional[Dict] = None,
    skill_match_result: Optional[Dict] = None,
    industry: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute weighted fit score, risk signals, and recommendation.

    When custom scoring_weights are provided, they are applied to scoring.
    When no custom weights are provided but industry is specified, industry-specific
    weights from constants.py are used. Otherwise DEFAULT_WEIGHTS is used.

    When jd_analysis is provided with required_skills and nice_to_have_skills,
    skill_score is computed with a 70/30 weight split (required/nice-to-have).

    When skill_match_result contains matched_skills_detailed, confidence-weighted
    scoring is used instead of binary counting — alias matches (0.95) and
    substring matches (0.8) contribute proportionally less than exact matches (1.0).
    Hierarchy-inferred matches are excluded from the ratio (informational only).

    When phase3_context is provided with team_gaps, skill_trends, and/or
    outcome_patterns, the scoring is adjusted accordingly:
    - skill_trends: per-skill multiplier on required_ratio
    - outcome_patterns: per-skill multiplier on required_ratio
    - team_gaps: adds a team_fit dimension with weight redistribution
    """
    from app.backend.services.constants import get_industry_weights
    from app.backend.services.scoring_weights import effective_scoring_weights, normalize_risk_weight

    # Determine weights: custom > industry > default
    if scoring_weights:
        w = scoring_weights.copy()
    elif industry:
        w = get_industry_weights(industry)
    else:
        w = DEFAULT_WEIGHTS.copy()
    w["risk"] = normalize_risk_weight(w.get("risk"))

    skill_score    = scalar_breakdown_score(scores.get("skill_score", 50))
    exp_score      = scalar_breakdown_score(scores.get("exp_score", 50))
    arch_score     = scores.get("arch_score",      50)
    edu_score      = scores.get("edu_score",       60)
    timeline_score = scores.get("timeline_score", 85)
    domain_score   = scores.get("domain_score",    60)
    actual_years   = scores.get("actual_years",    0)
    required_years = scores.get("required_years",  0)
    matched_skills = scores.get("matched_skills",  [])
    missing_skills = scores.get("missing_skills",  [])
    required_count = scores.get("required_count",  0)
    employment_gaps = scores.get("employment_gaps", [])
    short_stints    = scores.get("short_stints",   [])

    # Extract JD skill lists for tiered scoring
    jd_required = []
    jd_nice_to_have = []
    if jd_analysis:
        jd_required = jd_analysis.get("required_skills", []) or []
        jd_nice_to_have = jd_analysis.get("nice_to_have_skills", []) or []

    # ── Initialize tracking vars for score_breakdown evidence ──────────────────
    required_matched = []
    nice_matched = []
    required_confidence_sum = 0.0
    nice_confidence_sum = 0.0
    matched_detailed = (skill_match_result or {}).get("matched_skills_detailed", [])
    confidence_detailed = [m for m in matched_detailed
                           if m.get("match_type") != "hierarchy_inferred"] if matched_detailed else []
    missing_required = missing_skills  # default; overridden in tiered branch
    proficiency_requirements = (jd_analysis or {}).get("skill_proficiency_requirements") if jd_analysis else None

    # Compute tiered skill score when nice-to-have skills are available
    if jd_nice_to_have:
        required_lower = [r.lower() for r in jd_required if isinstance(r, str)]
        nice_lower = [n.lower() for n in jd_nice_to_have if isinstance(n, str)]

        # ── Confidence-weighted scoring when matched_skills_detailed is available ──
        # (matched_detailed and confidence_detailed already initialized above)

        if confidence_detailed:
            # Confidence-weighted scoring for required skills
            required_confidence_sum = 0.0
            nice_confidence_sum = 0.0

            for m in confidence_detailed:
                skill_name = m.get("skill", "").lower()
                confidence = m.get("confidence", 1.0)

                if skill_name in required_lower:
                    required_confidence_sum += confidence
                elif skill_name in nice_lower:
                    nice_confidence_sum += confidence

            required_ratio = (required_confidence_sum / max(len(jd_required), 1)) * 100

            # Nice-to-have: use confidence if detailed data covers them,
            # otherwise fall back to binary counting
            nice_from_detailed = [m for m in confidence_detailed
                                  if m.get("skill", "").lower() in nice_lower]
            if nice_from_detailed:
                nice_ratio = (nice_confidence_sum / max(len(jd_nice_to_have), 1)) * 100
            else:
                # matched_skills_detailed currently only tracks required-skill matches;
                # fall back to binary counting for nice-to-have
                nice_matched = [s for s in matched_skills if isinstance(s, str) and s.lower() in nice_lower]
                nice_ratio = len(nice_matched) / max(len(jd_nice_to_have), 1) * 100
        else:
            # Fallback to binary counting (backward compatible)
            required_matched = [s for s in matched_skills if isinstance(s, str) and s.lower() in required_lower]
            nice_matched = [s for s in matched_skills if isinstance(s, str) and s.lower() in nice_lower]
            required_ratio = len(required_matched) / max(len(jd_required), 1) * 100
            nice_ratio = len(nice_matched) / max(len(jd_nice_to_have), 1) * 100

        # Apply proficiency-aware scoring when proficiency requirements are present
        # proficiency_requirements already initialized above
        # Build required_matched list for proficiency and Phase 3 calculations
        required_matched = [s for s in matched_skills if isinstance(s, str) and s.lower() in required_lower]
        if proficiency_requirements and required_matched:
            from app.backend.services.hybrid_pipeline import (
                _compute_proficiency_score,
            )
            # Build minimal candidate data for proficiency estimation
            candidate_skills_data = {
                "skills_identified": matched_skills,
                "total_effective_years": actual_years,
                "work_experience": scores.get("work_experience", []),
            }
            prof_factor = _compute_proficiency_score(
                required_matched, candidate_skills_data, proficiency_requirements,
            )
            required_ratio = required_ratio * prof_factor

        skill_score = combine_skill_ratios(required_ratio, nice_ratio)

        # ── Phase 3 scoring integration (trends + outcomes) ──────────────────
        phase3_ctx = phase3_context or {}
        team_gaps = phase3_ctx.get("team_gaps", [])
        skill_trends = phase3_ctx.get("skill_trends", [])
        outcome_patterns = phase3_ctx.get("outcome_patterns", [])

        if skill_trends and required_matched:
            trend_adjusted_count = 0.0
            for s in required_matched:
                skill_name = s if isinstance(s, str) else s.get("skill", "")
                trend_adjusted_count += _apply_trend_factor(skill_name, skill_trends)
            avg_trend = trend_adjusted_count / max(len(required_matched), 1)
            required_ratio = min(100, required_ratio * avg_trend)

        if outcome_patterns and required_matched:
            enabled = bool(phase3_ctx.get("historical_outcomes_enabled"))
            outcome_adjusted_count = 0.0
            for s in required_matched:
                skill_name = s if isinstance(s, str) else s.get("skill", "")
                outcome_adjusted_count += _apply_outcome_factor(
                    skill_name, outcome_patterns, enabled=enabled
                )
            avg_outcome = outcome_adjusted_count / max(len(required_matched), 1)
            required_ratio = min(100, required_ratio * avg_outcome)

        # Recompute skill_score after trend/outcome adjustments
        skill_score = combine_skill_ratios(required_ratio, nice_ratio)

        # For risk signals, use missing required skills only
        missing_required = [s for s in missing_skills if isinstance(s, str) and s.lower() in required_lower]
        required_count_for_risk = len(jd_required)
    else:
        missing_required = missing_skills
        required_count_for_risk = required_count

    # ── Phase 3: team gap bonus (computed outside tiered branch too) ────────
    phase3_ctx = phase3_context or {}
    team_gaps = phase3_ctx.get("team_gaps", [])
    team_gap_bonus = _compute_team_gap_bonus(matched_skills, team_gaps) if team_gaps else 0.0

    # ── Risk signals (deterministic Python — not LLM) ─────────────────────────
    if risk_signals is None:
        risk_signals = []

        has_critical_gap = any(g.get("severity") == "critical" for g in employment_gaps)
        if has_critical_gap:
            risk_signals.append({"type": "gap",           "severity": "high",
                                  "description": "Critical employment gap detected (12+ months)"})

        skill_miss_pct = (len(missing_required) / max(required_count_for_risk, 1)) * 100
        if skill_miss_pct >= 50:
            risk_signals.append({"type": "skill_gap",     "severity": "high",
                                  "description": f"Missing {len(missing_required)}/{required_count_for_risk} required skills"})
        elif skill_miss_pct >= 30:
            risk_signals.append({"type": "skill_gap",     "severity": "medium",
                                  "description": f"Missing {len(missing_required)}/{required_count_for_risk} required skills"})

        if domain_score < 40:
            risk_signals.append({"type": "domain_mismatch", "severity": "medium",
                                  "description": "Candidate domain does not closely match role requirements"})

        if len(short_stints) >= 3:
            risk_signals.append({"type": "stability",     "severity": "medium",
                                  "description": f"{len(short_stints)} short stints (<6 months) detected"})
        elif len(short_stints) >= 2:
            risk_signals.append({"type": "stability",     "severity": "low",
                                  "description": f"{len(short_stints)} short stints detected"})

        if required_years > 0 and actual_years > required_years * 2:
            risk_signals.append({"type": "overqualified",  "severity": "low",
                                  "description": f"Candidate has {actual_years}y experience vs {required_years}y required"})

    # ── Risk penalty ──────────────────────────────────────────────────────────
    if risk_penalty is None:
        risk_penalty = compute_risk_penalty(risk_signals)

    # ── Fit score ──────────────────────────────────────────────────────────────
    # Employment timeline is recruiter context, not a default score dimension.
    timeline_w = 0.0 if not (scoring_weights and "timeline" in scoring_weights) else w.get("timeline", 0)
    skills_w = w.get("skills", DEFAULT_WEIGHTS["skills"])
    if timeline_w == 0 and not (scoring_weights and "timeline" in scoring_weights):
        skills_w = skills_w + w.get("timeline", DEFAULT_WEIGHTS.get("timeline", 0))

    merged = {
        "skills": skills_w,
        "experience": w.get("experience", DEFAULT_WEIGHTS["experience"]),
        "architecture": w.get("architecture", DEFAULT_WEIGHTS["architecture"]),
        "education": w.get("education", DEFAULT_WEIGHTS["education"]),
        "domain": w.get("domain", DEFAULT_WEIGHTS["domain"]),
        "risk": w.get("risk", DEFAULT_WEIGHTS["risk"]),
    }
    if scoring_weights and "timeline" in scoring_weights:
        merged["timeline"] = timeline_w
    eff = effective_scoring_weights(merged, team_gap_active=team_gap_bonus > 0)
    risk_w = normalize_risk_weight(eff.get("risk"))

    fit_score = round(
        skill_score      * eff["skills"] +
        team_gap_bonus   * eff.get("team_gap", 0.0) +
        exp_score        * eff["experience"] +
        arch_score       * eff["architecture"] +
        edu_score        * eff["education"] +
        timeline_score   * (eff.get("timeline", 0.0) if scoring_weights and "timeline" in scoring_weights else 0.0) +
        domain_score     * eff["domain"] -
        risk_penalty     * risk_w
    )
    fit_score = max(0, min(100, fit_score))

    # ── Recommendation ────────────────────────────────────────────────────────
    if fit_score >= RECOMMENDATION_THRESHOLDS["shortlist"]:
        recommendation = "Shortlist"
        risk_level = "Low"
    elif fit_score >= RECOMMENDATION_THRESHOLDS["consider"]:
        recommendation = "Consider"
        risk_level = "Medium" if risk_signals else "Low"
    else:
        recommendation = "Reject"
        risk_level = "High" if any(r["severity"] == "high" for r in risk_signals) else "Medium"

    # ── Build detailed score_breakdown with evidence chains ───────────────────────
    # Use matched_detailed / confidence_detailed already initialized above
    all_detailed = matched_detailed  # alias for clarity
    confidence_weighted = bool(all_detailed)
    avg_confidence = round(
        sum(m.get("confidence", 1.0) for m in all_detailed) / max(len(all_detailed), 1), 2
    ) if all_detailed else 1.0

    # ── Skill match details ─────────────────────────────────────────────────────
    skill_match_details = {
        "score":               skill_score,
        "confidence_weighted": confidence_weighted,
        "avg_confidence":      avg_confidence,
        "required_total":      len(jd_required),
        "required_matched":    len(required_matched) if not confidence_weighted else int(required_confidence_sum),
        "nice_total":          len(jd_nice_to_have),
        "nice_matched":        len(nice_matched) if not confidence_weighted else (int(nice_confidence_sum) if nice_confidence_sum else len(nice_matched)),
        "missing_required":    missing_required[:10],
        "matched_details": [
            {
                "skill":       m.get("skill", ""),
                "confidence":  m.get("confidence", 1.0),
                "match_type":  m.get("match_type", "exact"),
            }
            for m in (all_detailed[:15] if all_detailed else [])
        ],
    }

    # Add proficiency adjustments if available
    if proficiency_requirements and required_matched:
        proficiency_details = []
        for skill in required_matched[:10]:
            skill_name = skill if isinstance(skill, str) else skill.get("skill", "")
            req_level = proficiency_requirements.get(skill_name.lower())
            if req_level:
                proficiency_details.append({
                    "skill":    skill_name,
                    "required": req_level,
                    "factor":   "applied",
                })
        if proficiency_details:
            skill_match_details["proficiency_adjustments"] = proficiency_details

    # Add team gap bonus if present
    if team_gap_bonus > 0:
        skill_match_details["team_gap_bonus"] = round(team_gap_bonus, 1)

    # Add trend factors if applied
    if phase3_context and phase3_context.get("skill_trends"):
        trend_applied = []
        for t in phase3_context["skill_trends"][:5]:
            factor = _apply_trend_factor(t["skill"], phase3_context["skill_trends"])
            if factor != 1.0:
                trend_applied.append({"skill": t["skill"], "factor": round(factor, 2), "direction": t.get("direction", "stable")})
        if trend_applied:
            skill_match_details["trend_factors_applied"] = trend_applied

    # ── Experience match details ────────────────────────────────────────────────
    experience_match_details = {
        "score":          exp_score,
        "actual_years":   round(actual_years, 1) if actual_years else None,
        "required_years": required_years if required_years else None,
        "explanation":    _generate_exp_explanation(actual_years, required_years, exp_score),
    }

    return {
        "fit_score":            fit_score,
        "final_recommendation": recommendation,
        "risk_level":           risk_level,
        "risk_signals":         risk_signals,
        "risk_penalty":         risk_penalty,
        "score_breakdown": {
            "skill_match":      skill_match_details,
            "experience_match": experience_match_details,
            "stability":        timeline_score,
            "education":        edu_score,
            "architecture":     arch_score,
            "domain_fit":       domain_score,
            "timeline":         timeline_score,
            "risk_penalty":     risk_penalty,
        },
    }


def compute_deterministic_score(features: dict, eligibility, weights: Optional[Dict] = None) -> int:
    """Compute a deterministic score with hard caps based on eligibility and feature quality.

    Args:
        features: dict with keys:
            - core_skill_match: float (0-1)
            - secondary_skill_match: float (0-1)
            - domain_match: float (0-1)
            - relevant_experience: float (0-1, normalized)
            - total_experience: float (years)
        eligibility: EligibilityResult from eligibility_service
        weights: Optional dict supporting multiple schemas:
            - New schema: {core_competencies, experience, domain_fit, education, ...}
            - Legacy schema: {skills, experience, stability, education}
            - Internal schema: {skills, experience, architecture, education, timeline, domain, risk}
            - Direct schema: {core_skill_match, secondary_skill_match, domain_match, relevant_experience}
            Defaults to 40/15/25/20 split when None or when keys don't match.

    Returns:
        Integer score 0-100 with deterministic caps applied
    """
    # Default weight split for deterministic features
    w_core = 0.40
    w_secondary = 0.15
    w_domain = 0.25
    w_experience = 0.20
    
    if weights is not None:
        # Priority 1: Direct mapping (already in deterministic schema)
        if "core_skill_match" in weights:
            w_core = weights.get("core_skill_match", 0.40)
            w_secondary = weights.get("secondary_skill_match", 0.15)
            w_domain = weights.get("domain_match", 0.25)
            w_experience = weights.get("relevant_experience", 0.20)
        # Priority 2: New 7-weight schema (core_competencies, domain_fit, experience)
        elif "core_competencies" in weights:
            # Map new schema to deterministic features
            w_core = weights.get("core_competencies", 0.30)  # core skills
            w_secondary = weights.get("role_excellence", 0.10) * 0.5  # partial contribution
            w_domain = weights.get("domain_fit", 0.20)  # domain fit
            w_experience = weights.get("experience", 0.20)  # experience
            # Normalize to sum to 1.0
            total = w_core + w_secondary + w_domain + w_experience
            if total > 0:
                w_core = w_core / total
                w_secondary = w_secondary / total
                w_domain = w_domain / total
                w_experience = w_experience / total
        # Priority 3: Internal 7-weight schema (skills, domain, experience)
        elif "skills" in weights and "architecture" in weights:
            # Map internal schema to deterministic features
            w_core = weights.get("skills", 0.30)  # core skills
            w_secondary = weights.get("architecture", 0.15) * 0.5  # partial contribution
            w_domain = weights.get("domain", 0.10)  # domain fit
            w_experience = weights.get("experience", 0.20)  # experience
            # Normalize to sum to 1.0
            total = w_core + w_secondary + w_domain + w_experience
            if total > 0:
                w_core = w_core / total
                w_secondary = w_secondary / total
                w_domain = w_domain / total
                w_experience = w_experience / total
        # Priority 4: Legacy 4-weight schema (skills, experience, stability, education)
        elif "skills" in weights and "stability" in weights:
            w_core = weights.get("skills", 0.40)
            w_secondary = 0.05  # minimal secondary in legacy
            w_domain = weights.get("stability", 0.15) * 0.5  # partial contribution
            w_experience = weights.get("experience", 0.35)
            # Normalize to sum to 1.0
            total = w_core + w_secondary + w_domain + w_experience
            if total > 0:
                w_core = w_core / total
                w_secondary = w_secondary / total
                w_domain = w_domain / total
                w_experience = w_experience / total

    # Base score from weighted features
    score = (
        features.get("core_skill_match", 0) * w_core +
        features.get("secondary_skill_match", 0) * w_secondary +
        features.get("domain_match", 0) * w_domain +
        features.get("relevant_experience", 0) * w_experience
    ) * 100  # Scale to 0-100

    # Normalize to 0-100 range
    score = max(0, min(100, score))

    # Graduated penalties instead of hard caps.  These replace the previous
    # hard caps that rejected qualified candidates in non-tech domains.
    caps_applied = []

    if not eligibility.eligible:
        # Significant penalty for ineligibility, but not a hard ceiling
        score = max(0, score - 20)
        caps_applied.append("ineligible_penalty")

    domain_match = features.get("domain_match", 0)
    if domain_match < 0.3:
        # Mild penalty for low domain match; if the candidate is ineligible because
        # of domain mismatch, the penalty above already applies.
        score = max(0, score - 10)
        caps_applied.append("low_domain_match_penalty")

    core_skill_match = features.get("core_skill_match", 0)
    if core_skill_match < 0.3:
        score = max(0, score - 10)
        caps_applied.append("low_core_skills_penalty")

    return int(score)


def align_decision_to_score(score, decision_explanation: dict | None = None):
    """Force the badge to match the number the recruiter sees."""
    from app.backend.services.constants import recommendation_from_score

    rec = recommendation_from_score(score)
    aligned = dict(decision_explanation) if decision_explanation else None
    if aligned is not None:
        aligned["decision"] = rec
    return rec, aligned


def explain_decision(features: dict, eligibility) -> dict:
    """Generate a structured, deterministic explanation of the scoring decision.

    Returns:
        {
            "decision": "Shortlist" | "Consider" | "Reject",
            "reasons": list of strings explaining the decision,
            "feature_summary": dict of feature scores,
            "caps_applied": list of cap reasons if any
        }
    """
    from app.backend.services.constants import RECOMMENDATION_THRESHOLDS

    score = compute_deterministic_score(features, eligibility)
    caps_applied = []
    reasons = []

    # Determine decision based on thresholds
    if score >= RECOMMENDATION_THRESHOLDS["shortlist"]:
        decision = "Shortlist"
    elif score >= RECOMMENDATION_THRESHOLDS["consider"]:
        decision = "Consider"
    else:
        decision = "Reject"

    # Build reasons
    if not eligibility.eligible:
        caps_applied.append("ineligible_penalty")
        if eligibility.reason == "domain_mismatch":
            reasons.append(f"Domain mismatch: JD requires {eligibility.details.get('jd_domain', 'unknown')}, candidate is {eligibility.details.get('candidate_domain', 'unknown')}")
        elif eligibility.reason == "low_core_skills":
            reasons.append(f"Core skill match low: {eligibility.details.get('core_skill_match', 0):.0%} (threshold {eligibility.details.get('threshold', 0.15):.0%})")
        elif eligibility.reason == "no_relevant_experience":
            reasons.append("No relevant experience detected")

    if features.get("domain_match", 0) < 0.3:
        caps_applied.append("low_domain_match_penalty")
        reasons.append(f"Low domain match: {features.get('domain_match', 0):.0%}")

    if features.get("core_skill_match", 0) < 0.3:
        caps_applied.append("low_core_skills_penalty")
        reasons.append(f"Low core skill match: {features.get('core_skill_match', 0):.0%}")

    if not reasons:
        if decision == "Shortlist":
            reasons.append("Strong match across all criteria")
        elif decision == "Consider":
            reasons.append("Moderate match — review recommended")
        else:
            reasons.append("Below threshold across multiple criteria")

    return {
        "decision": decision,
        "score": score,
        "reasons": reasons,
        "feature_summary": {k: round(v, 3) if isinstance(v, float) else v for k, v in features.items()},
        "caps_applied": caps_applied,
    }
