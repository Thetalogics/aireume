"""
LLM Weight Suggester Service

Analyzes job descriptions and suggests optimal scoring weights based on:
- Role category detection (technical/sales/hr/marketing/operations/leadership)
- Seniority level analysis
- Key requirements identification
- Company culture signals

Provides intelligent, context-aware weight recommendations with reasoning.
"""

import logging
from typing import Dict, Optional, Any

from app.backend.services.weight_mapper import NEW_DEFAULT_WEIGHTS, normalize_weights

log = logging.getLogger(__name__)


# ─── LLM Prompt for Weight Suggestion ────────────────────────────────────────

WEIGHT_SUGGESTER_PROMPT = """Analyze this job description and suggest optimal scoring weights for candidate evaluation.

JOB DESCRIPTION:
{jd_text}

TASK:
1. Detect the role category (technical/sales/hr/marketing/operations/leadership/other)
2. Identify seniority level (junior/mid/senior/lead/executive)
3. Determine key success factors for this role
4. Suggest optimal scoring weights

OUTPUT VALID JSON ONLY (no markdown, no explanation outside JSON):
{{
  "role_category": "technical|sales|hr|marketing|operations|leadership|other",
  "seniority_level": "junior|mid|senior|lead|executive",
  "key_requirements": ["requirement1", "requirement2", "requirement3"],
  "suggested_weights": {{
    "core_competencies": 0.25-0.40,
    "experience": 0.15-0.30,
    "domain_fit": 0.15-0.25,
    "education": 0.05-0.15,
    "career_trajectory": 0.10-0.15,
    "role_excellence": 0.10-0.20,
    "risk": 0.05 to 0.15
  }},
  "weight_evidence": {{
    "core_competencies": ["Verbatim JD phrase justifying this weight"],
    "experience": ["Verbatim JD phrase justifying this weight"],
    "domain_fit": ["Verbatim JD phrase justifying this weight"],
    "education": ["Verbatim JD phrase justifying this weight"],
    "career_trajectory": ["Verbatim JD phrase justifying this weight"],
    "role_excellence": ["Verbatim JD phrase justifying this weight"],
    "risk": ["Verbatim JD phrase justifying this weight"]
  }},
  "weight_delta_reasons": {{
    "core_competencies": "Why this differs from default (1 sentence)",
    "experience": "Why this differs from default (1 sentence)",
    "domain_fit": "Why this differs from default (1 sentence)",
    "education": "Why this differs from default (1 sentence)",
    "career_trajectory": "Why this differs from default (1 sentence)",
    "role_excellence": "Why this differs from default (1 sentence)",
    "risk": "Why this differs from default (1 sentence)"
  }},
  "role_excellence_label": "What this measures for this specific role",
  "reasoning": "Brief explanation of why these weights (2-3 sentences)",
  "confidence": 0.0-1.0
}}

GUIDELINES:
- Weights must sum to 1.0 (excluding negative risk penalty)
- Junior roles: Higher education/skills, lower experience
- Senior roles: Higher experience/role_excellence, lower education
- Technical roles: Higher role_excellence (architecture/design)
- Sales roles: Higher core_competencies, role_excellence = revenue achievement
- HR roles: Higher education (certifications), role_excellence = strategic impact
- Startup culture: Lower career_trajectory (job hopping OK)
- Enterprise culture: Higher career_trajectory (stability valued)
- weight_evidence: Use VERBATIM phrases from the JD (exact words) to justify each weight. Never paraphrase.
- weight_delta_reasons: Compare your suggested weight to the default (Core Comp 30%, Exp 20%, Domain 20%, Edu 10%, Career 10%, Role Excel 10%, Risk -10%). Explain WHY you changed it in one sentence per dimension. If you kept it the same, say "Aligned with default — [reason]".
"""


def _normalize_weight_suggestion(result: dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and normalize a parsed LLM weight suggestion."""
    required_fields = ["role_category", "suggested_weights", "reasoning"]
    if not all(field in result for field in required_fields):
        log.warning("LLM response missing required fields: %s", result)
        return None

    suggested_weights = result.get("suggested_weights", {})
    if not isinstance(suggested_weights, dict):
        log.warning("suggested_weights is not a dictionary")
        return None

    required_weight_keys = [
        "core_competencies",
        "experience",
        "domain_fit",
        "education",
        "career_trajectory",
        "role_excellence",
        "risk",
    ]

    for key in required_weight_keys:
        if key not in suggested_weights:
            log.warning("Missing weight key: %s, using default", key)
            suggested_weights[key] = NEW_DEFAULT_WEIGHTS.get(key, 0.10)

    result["suggested_weights"] = normalize_weights(suggested_weights)
    result.setdefault("seniority_level", "unknown")
    result.setdefault("key_requirements", [])
    result.setdefault("role_excellence_label", "Role-Specific Excellence")
    result.setdefault("confidence", 0.75)
    result.setdefault("weight_evidence", {})
    result.setdefault("weight_delta_reasons", {})
    for key in required_weight_keys:
        if key not in result["weight_evidence"]:
            result["weight_evidence"][key] = []
        if key not in result["weight_delta_reasons"]:
            result["weight_delta_reasons"][key] = ""

    log.info(
        "Weight suggestion successful: %s role, confidence: %s",
        result["role_category"],
        result["confidence"],
    )
    return result


async def suggest_weights_for_jd(jd_text: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """
    Analyze job description and suggest optimal scoring weights using LLM.

    Returns None if LLM fails or returns invalid data.
    """
    if not jd_text or len(jd_text.strip()) < 50:
        log.warning("JD text too short for weight suggestion, using defaults")
        return None

    try:
        from app.backend.services.app_llm_client import generate_app_json

        log.info("Requesting weight suggestions from LLM")
        result = await generate_app_json(
            WEIGHT_SUGGESTER_PROMPT.format(jd_text=jd_text[:3000]),
            max_output_tokens=800,
            temperature=0.0,
            timeout=float(timeout),
            log_label="weight_suggester",
        )
        if not result:
            log.warning("Empty or invalid response from LLM for weight suggestion")
            return None
        return _normalize_weight_suggestion(result)
    except Exception as e:
        log.exception("Error getting weight suggestions from LLM: %s", e)
        return None


def get_default_weights_for_category(role_category: str) -> Dict[str, float]:
    """
    Get sensible default weights for a role category when LLM is unavailable.
    """
    defaults = {
        "technical": {
            "core_competencies": 0.30,
            "experience": 0.20,
            "domain_fit": 0.20,
            "education": 0.05,
            "career_trajectory": 0.10,
            "role_excellence": 0.15,
            "risk": 0.10,
        },
        "sales": {
            "core_competencies": 0.35,
            "experience": 0.25,
            "domain_fit": 0.15,
            "education": 0.05,
            "career_trajectory": 0.10,
            "role_excellence": 0.10,
            "risk": 0.10,
        },
        "hr": {
            "core_competencies": 0.30,
            "experience": 0.25,
            "domain_fit": 0.15,
            "education": 0.10,
            "career_trajectory": 0.15,
            "role_excellence": 0.05,
            "risk": 0.10,
        },
        "marketing": {
            "core_competencies": 0.35,
            "experience": 0.20,
            "domain_fit": 0.20,
            "education": 0.05,
            "career_trajectory": 0.10,
            "role_excellence": 0.10,
            "risk": 0.10,
        },
        "operations": {
            "core_competencies": 0.30,
            "experience": 0.25,
            "domain_fit": 0.15,
            "education": 0.10,
            "career_trajectory": 0.15,
            "role_excellence": 0.05,
            "risk": 0.10,
        },
        "leadership": {
            "core_competencies": 0.25,
            "experience": 0.30,
            "domain_fit": 0.20,
            "education": 0.05,
            "career_trajectory": 0.10,
            "role_excellence": 0.10,
            "risk": 0.10,
        },
    }

    return defaults.get(role_category.lower(), NEW_DEFAULT_WEIGHTS.copy())


def get_role_excellence_label(role_category: str) -> str:
    """Get the appropriate label for role_excellence factor based on role category."""
    labels = {
        "technical": "System Design & Architecture",
        "sales": "Revenue Achievement & Quota Attainment",
        "hr": "Strategic HR Impact & Culture Building",
        "marketing": "Campaign Strategy & Brand Impact",
        "operations": "Process Optimization & Efficiency",
        "leadership": "Strategic Vision & Leadership Impact",
    }

    return labels.get(role_category.lower(), "Role-Specific Excellence")


def create_fallback_suggestion(jd_text: str, role_category: str = "technical") -> Dict[str, Any]:
    """Create a fallback weight suggestion when LLM is unavailable."""
    jd_lower = jd_text.lower()

    if any(word in jd_lower for word in ["engineer", "developer", "architect", "devops", "backend", "frontend"]):
        detected_category = "technical"
    elif any(word in jd_lower for word in ["sales", "account executive", "bdr", "revenue"]):
        detected_category = "sales"
    elif any(word in jd_lower for word in ["hr", "human resources", "recruiter", "talent"]):
        detected_category = "hr"
    elif any(word in jd_lower for word in ["marketing", "brand", "campaign", "growth"]):
        detected_category = "marketing"
    else:
        detected_category = role_category

    return {
        "role_category": detected_category,
        "seniority_level": "unknown",
        "key_requirements": [],
        "suggested_weights": get_default_weights_for_category(detected_category),
        "weight_evidence": {},
        "weight_delta_reasons": {},
        "role_excellence_label": get_role_excellence_label(detected_category),
        "reasoning": f"Using default weights for {detected_category} role (LLM unavailable)",
        "confidence": 0.50,
        "fallback": True,
    }
