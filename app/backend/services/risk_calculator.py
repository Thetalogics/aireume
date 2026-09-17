"""Standardized risk penalty calculation — single source of truth."""

from app.backend.services.constants import RISK_SEVERITY_PENALTIES


_PROXY_RISK_TYPES = frozenset({"gap", "stability", "overqualified", "job_hopper"})


def compute_risk_penalty(risk_signals: list[dict]) -> float:
    """Compute risk penalty from a list of risk signal dicts.

    Employment-gap, short-tenure, and overqualification signals are recruiter
    context only and never enter the authoritative penalty.
    """
    return sum(
        RISK_SEVERITY_PENALTIES.get(r.get("severity", "low"), 0)
        for r in risk_signals
        if (r.get("type") or "") not in _PROXY_RISK_TYPES
    )
