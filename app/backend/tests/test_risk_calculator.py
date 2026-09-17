"""Tests for risk_calculator.py — risk penalty computation."""

import pytest

from app.backend.services.risk_calculator import compute_risk_penalty


class TestComputeRiskPenalty:
    """Tests for compute_risk_penalty."""

    def test_empty_list_returns_0(self):
        assert compute_risk_penalty([]) == 0

    def test_single_high_severity(self):
        signals = [{"severity": "high"}]
        assert compute_risk_penalty(signals) == 20

    def test_single_medium_severity(self):
        signals = [{"severity": "medium"}]
        assert compute_risk_penalty(signals) == 10

    def test_single_low_severity(self):
        signals = [{"severity": "low"}]
        assert compute_risk_penalty(signals) == 4

    def test_mixed_severities(self):
        signals = [
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        assert compute_risk_penalty(signals) == 20 + 10 + 4

    def test_unknown_severity_defaults_to_0(self):
        signals = [{"severity": "critical"}]
        assert compute_risk_penalty(signals) == 0

    def test_missing_severity_key_defaults_to_low(self):
        """Gap/stability types are excluded from automated scoring (AUD-025)."""
        signals = [{"type": "gap"}]
        assert compute_risk_penalty(signals) == 0

    def test_multiple_missing_severity_keys(self):
        signals = [{"type": "gap"}, {"type": "stability"}]
        assert compute_risk_penalty(signals) == 0

    def test_combination_with_unknown_and_missing(self):
        signals = [
            {"severity": "high"},
            {"severity": "unknown"},
            {"description": "missing severity"},
        ]
        assert compute_risk_penalty(signals) == 20 + 0 + 4
