"""
Weight Mapping Utility for Intelligent Scoring System

Provides backward-compatible translation between old 4-weight schema and new 7-weight schema.
Ensures existing functionality continues working while enabling new intelligent features.

Old Schema (4 weights):
- skills, experience, stability, education

New Schema (7 weights):
- core_competencies, experience, domain_fit, education, career_trajectory, role_excellence, risk
"""

from typing import Dict, Optional, Any
import logging

from app.backend.services.constants import (
    LEGACY_WEIGHTS,
    NEW_DEFAULT_WEIGHTS,
    OLD_BACKEND_WEIGHTS,
)

log = logging.getLogger(__name__)


# ─── Legacy weight schema (for backward compatibility) ───────────────────────
# LEGACY_WEIGHTS is now imported from constants.py

# ─── New universal-adaptive weight schema ────────────────────────────────────
# NEW_DEFAULT_WEIGHTS is now imported from constants.py

# ─── Old backend schema (7 tech-centric weights) ──────────────────────────────
# OLD_BACKEND_WEIGHTS is now imported from constants.py


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Normalize positive weights to sum to 1.0. Risk is a positive penalty magnitude."""
    from app.backend.services.scoring_weights import normalize_risk_weight

    if not weights:
        return NEW_DEFAULT_WEIGHTS.copy()

    positive_weights = {k: v for k, v in weights.items() if k != "risk" and v > 0}
    risk_value = weights.get("risk", NEW_DEFAULT_WEIGHTS["risk"])

    total = sum(positive_weights.values())
    if total == 0:
        log.warning("All weights are zero, using defaults")
        return NEW_DEFAULT_WEIGHTS.copy()

    normalized = {k: v / total for k, v in positive_weights.items()}
    normalized["risk"] = normalize_risk_weight(risk_value)
    return normalized


def map_legacy_to_new(legacy_weights: Dict[str, float]) -> Dict[str, float]:
    """
    Map old 4-weight schema to new 7-weight schema.
    
    Legacy mapping:
    - skills → core_competencies (direct)
    - experience → experience (direct)
    - stability → career_trajectory (direct)
    - education → education (direct)
    - Missing weights filled with intelligent defaults
    
    Args:
        legacy_weights: Old 4-weight schema {skills, experience, stability, education}
        
    Returns:
        New 7-weight schema with intelligent defaults for missing weights
    """
    if not legacy_weights:
        return NEW_DEFAULT_WEIGHTS.copy()
    
    # Direct mappings
    new_weights = {
        "core_competencies": legacy_weights.get("skills", 0.30),
        "experience": legacy_weights.get("experience", 0.20),
        "career_trajectory": legacy_weights.get("stability", 0.10),
        "education": legacy_weights.get("education", 0.10),
    }
    
    # Calculate remaining weight to distribute
    used_weight = sum(new_weights.values())
    remaining = 1.0 - used_weight
    
    # Distribute remaining weight intelligently
    if remaining > 0:
        # Split remaining between domain_fit and role_excellence
        new_weights["domain_fit"] = remaining * 0.6
        new_weights["role_excellence"] = remaining * 0.4
    else:
        # Use defaults if over-allocated
        new_weights["domain_fit"] = 0.20
        new_weights["role_excellence"] = 0.10
    
    # Add risk penalty (standard default)
    new_weights["risk"] = 0.10
    
    return normalize_weights(new_weights)


def map_old_backend_to_new(old_weights: Dict[str, float]) -> Dict[str, float]:
    """
    Map old 7-weight tech-centric schema to new universal schema.
    
    Old backend mapping:
    - skills → core_competencies
    - experience → experience
    - architecture → role_excellence
    - education → education
    - timeline → career_trajectory
    - domain → domain_fit
    - risk → risk
    
    Args:
        old_weights: Old tech-centric 7-weight schema
        
    Returns:
        New universal 7-weight schema
    """
    if not old_weights:
        return NEW_DEFAULT_WEIGHTS.copy()

    from app.backend.services.scoring_weights import normalize_risk_weight
    
    # Direct 1:1 mapping - preserve exact values
    new_weights = {
        "core_competencies": old_weights.get("skills", 0.30),
        "experience": old_weights.get("experience", 0.20),
        "role_excellence": old_weights.get("architecture", 0.10),
        "education": old_weights.get("education", 0.10),
        "career_trajectory": old_weights.get("timeline", 0.10),
        "domain_fit": old_weights.get("domain", 0.20),
        "risk": normalize_risk_weight(old_weights.get("risk", 0.10)),
    }
    
    # Only normalize if weights don't sum correctly
    total = sum(v for k, v in new_weights.items() if k != "risk")
    if total < 0.98 or total > 1.02:
        return normalize_weights(new_weights)
    
    return new_weights


def detect_weight_schema(weights: Dict[str, float]) -> str:
    """
    Detect which weight schema is being used.
    
    Returns:
        "legacy" - Old 4-weight frontend schema
        "old_backend" - Old 7-weight tech-centric schema
        "new" - New 7-weight universal schema
        "unknown" - Cannot determine
    """
    if not weights:
        return "unknown"
    
    keys = set(weights.keys())
    
    # Check for new schema
    new_keys = {"core_competencies", "domain_fit", "career_trajectory", "role_excellence"}
    if any(k in keys for k in new_keys):
        return "new"
    
    # Check for old backend schema
    old_backend_keys = {"skills", "architecture", "timeline", "domain"}
    if all(k in keys for k in old_backend_keys):
        return "old_backend"
    
    # Check for legacy frontend schema (4 weights only)
    legacy_keys = {"skills", "experience", "stability", "education"}
    if keys == legacy_keys or keys.issubset(legacy_keys):
        return "legacy"
    
    return "unknown"


def convert_to_new_schema(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """
    Universal converter: accepts any weight schema and returns new schema.
    
    This is the main entry point for weight conversion. Automatically detects
    the input schema and converts to the new universal schema.
    
    Args:
        weights: Weights in any supported schema (or None)
        
    Returns:
        Weights in new universal schema
    """
    if not weights:
        return NEW_DEFAULT_WEIGHTS.copy()
    
    schema_type = detect_weight_schema(weights)
    
    if schema_type == "new":
        # Already in new format, just normalize
        return normalize_weights(weights)
    elif schema_type == "legacy":
        # Convert from 4-weight frontend schema
        log.info("Converting legacy 4-weight schema to new schema")
        return map_legacy_to_new(weights)
    elif schema_type == "old_backend":
        # Convert from old 7-weight tech-centric schema
        log.info("Converting old backend 7-weight schema to new schema")
        return map_old_backend_to_new(weights)
    else:
        # Unknown schema, merge with defaults
        log.warning(f"Unknown weight schema, merging with defaults: {weights}")
        merged = {**NEW_DEFAULT_WEIGHTS, **weights}
        return normalize_weights(merged)


UNIVERSAL_WEIGHT_KEYS = frozenset({
    "core_competencies",
    "experience",
    "domain_fit",
    "education",
    "career_trajectory",
    "role_excellence",
    "risk",
})


def validate_and_normalize_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Validate and normalize weights to the universal 7-weight schema.

    Accepts any supported schema (legacy 4-weight, old backend 7-weight, or
    new universal 7-weight), converts to universal, and validates value ranges.

    Returns:
        Normalized weights in universal schema.
    """
    converted = convert_to_new_schema(weights)

    for key in UNIVERSAL_WEIGHT_KEYS:
        if key not in converted:
            converted[key] = NEW_DEFAULT_WEIGHTS[key]
        val = converted[key]
        if not isinstance(val, (int, float)):
            log.warning("Weight '%s' has non-numeric value %r, using default", key, val)
            converted[key] = NEW_DEFAULT_WEIGHTS[key]
        else:
            converted[key] = float(val)

    for extra_key in set(converted.keys()) - UNIVERSAL_WEIGHT_KEYS:
        del converted[extra_key]

    return normalize_weights(converted)


def migrate_stored_weights(weights_json: Optional[str]) -> Optional[str]:
    """Migrate a stored JSON weight string to the universal schema.

    Used for one-time migration of existing RoleTemplate.scoring_weights and
    Tenant.scoring_weights values. Returns JSON string of normalized universal
    weights, or None if input was None/empty.

    Args:
        weights_json: JSON string of weights in any schema (or None)

    Returns:
        JSON string of normalized universal weights, or None
    """
    import json

    if not weights_json:
        return None

    try:
        weights = json.loads(weights_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Could not parse stored weights JSON, returning None")
        return None

    normalized = validate_and_normalize_weights(weights)
    return json.dumps(normalized)


def get_weight_labels(role_category: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    Get adaptive labels for weights based on role category.
    
    Args:
        role_category: Role type (technical/sales/hr/marketing/etc)
        
    Returns:
        Dictionary mapping weight keys to {label, tooltip}
    """
    # Universal labels (work for all roles)
    universal_labels = {
        "experience": {
            "label": "Experience Level",
            "tooltip": "Years of experience and seniority in the role"
        },
        "education": {
            "label": "Education & Credentials",
            "tooltip": "Relevant degrees, certifications, and continuous learning"
        },
        "career_trajectory": {
            "label": "Career Progression",
            "tooltip": "Career growth trajectory, job stability, and progression patterns"
        },
        "risk": {
            "label": "Risk Assessment",
            "tooltip": "Penalty for red flags, gaps, and inconsistencies"
        },
    }
    
    # Adaptive labels based on role category
    if role_category == "technical":
        return {
            **universal_labels,
            "core_competencies": {
                "label": "Tech Stack Match",
                "tooltip": "Alignment with required technical skills and technologies"
            },
            "domain_fit": {
                "label": "Technical Domain",
                "tooltip": "Expertise in the technical domain (backend/frontend/devops/etc)"
            },
            "role_excellence": {
                "label": "System Design & Architecture",
                "tooltip": "Technical depth, system design skills, and architectural expertise"
            },
        }
    elif role_category == "sales":
        return {
            **universal_labels,
            "core_competencies": {
                "label": "Sales Competencies",
                "tooltip": "Core sales skills: pipeline management, negotiation, closing"
            },
            "domain_fit": {
                "label": "Sales Domain",
                "tooltip": "Experience in the sales domain (B2B/B2C/Enterprise/SMB)"
            },
            "role_excellence": {
                "label": "Revenue Achievement",
                "tooltip": "Track record of quota attainment, deal size, and win rate"
            },
        }
    elif role_category == "hr":
        return {
            **universal_labels,
            "core_competencies": {
                "label": "HR Competencies",
                "tooltip": "Core HR skills: talent acquisition, employee relations, compliance"
            },
            "domain_fit": {
                "label": "HR Specialization",
                "tooltip": "Expertise in HR specialty (recruitment/L&D/compensation/etc)"
            },
            "role_excellence": {
                "label": "Strategic HR Impact",
                "tooltip": "Strategic HR initiatives, culture building, and organizational impact"
            },
        }
    elif role_category == "marketing":
        return {
            **universal_labels,
            "core_competencies": {
                "label": "Marketing Competencies",
                "tooltip": "Core marketing skills: campaigns, analytics, content strategy"
            },
            "domain_fit": {
                "label": "Marketing Channel",
                "tooltip": "Expertise in marketing channels (digital/brand/growth/content)"
            },
            "role_excellence": {
                "label": "Campaign Strategy",
                "tooltip": "Strategic campaign planning, brand impact, and growth metrics"
            },
        }
    else:
        # Default/generic labels
        return {
            **universal_labels,
            "core_competencies": {
                "label": "Core Competencies",
                "tooltip": "Essential skills and competencies for this role"
            },
            "domain_fit": {
                "label": "Domain/Industry Fit",
                "tooltip": "Relevant domain or industry expertise"
            },
            "role_excellence": {
                "label": "Role Excellence Factor",
                "tooltip": "Role-specific differentiator and excellence indicator"
            },
        }
