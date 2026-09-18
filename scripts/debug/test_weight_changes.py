#!/usr/bin/env python3
"""
Quick test to verify custom and AI weights are working.
Run this after deploying to staging to verify weights affect scores.
"""

import sys
sys.path.insert(0, 'app/backend')

from services.fit_scorer import compute_deterministic_score, compute_fit_score
from services.hybrid_pipeline import convert_to_new_schema

# Test data (sample candidate features)
features = {
    "core_skill_match": 0.43,
    "secondary_skill_match": 0.25,
    "domain_match": 0.55,
    "relevant_experience": 0.17,
    "total_experience": 2.0,
}

# Mock eligibility
class MockEligibility:
    def __init__(self):
        self.eligible = True
        self.cap = None
        self.cap_reason = None

eligibility = MockEligibility()

# Test 1: Default weights
print("=" * 60)
print("TEST 1: Default Weights")
print("=" * 60)
default_weights = None
score_default = compute_deterministic_score(features, eligibility, default_weights)
print(f"Score with default weights: {score_default}")
print()

# Test 2: Custom weights (skill-heavy)
print("=" * 60)
print("TEST 2: Custom Weights - Skill-Heavy")
print("=" * 60)
skill_heavy_weights = {
    "core_competencies": 0.50,  # Increased from 0.30
    "experience": 0.15,         # Decreased from 0.20
    "domain_fit": 0.15,         # Decreased from 0.20
    "education": 0.10,
    "career_trajectory": 0.10,
    "role_excellence": 0.10,
    "risk": 0.10
}
print(f"Custom weights: {skill_heavy_weights}")
converted = convert_to_new_schema(skill_heavy_weights)
print(f"Converted to new schema: {converted}")
score_custom = compute_deterministic_score(features, eligibility, skill_heavy_weights)
print(f"Score with custom weights: {score_custom}")
print(f"Score difference: {score_custom - score_default} points")
print()

# Test 3: AI-suggested weights
print("=" * 60)
print("TEST 3: AI-Suggested Weights (Technical Role)")
print("=" * 60)
ai_weights = {
    "core_competencies": 0.40,
    "experience": 0.25,
    "domain_fit": 0.15,
    "education": 0.10,
    "career_trajectory": 0.10,
    "role_excellence": 0.10,
    "risk": 0.10
}
print(f"AI weights: {ai_weights}")
converted_ai = convert_to_new_schema(ai_weights)
print(f"Converted to new schema: {converted_ai}")
score_ai = compute_deterministic_score(features, eligibility, ai_weights)
print(f"Score with AI weights: {score_ai}")
print(f"Score difference from default: {score_ai - score_default} points")
print()

# Test 4: Experience-heavy weights
print("=" * 60)
print("TEST 4: Custom Weights - Experience-Heavy")
print("=" * 60)
exp_heavy_weights = {
    "core_competencies": 0.20,  # Decreased from 0.30
    "experience": 0.35,         # Increased from 0.20
    "domain_fit": 0.20,
    "education": 0.10,
    "career_trajectory": 0.15,
    "role_excellence": 0.10,
    "risk": 0.10
}
print(f"Experience-heavy weights: {exp_heavy_weights}")
converted_exp = convert_to_new_schema(exp_heavy_weights)
print(f"Converted to new schema: {converted_exp}")
score_exp = compute_deterministic_score(features, eligibility, exp_heavy_weights)
print(f"Score with experience-heavy weights: {score_exp}")
print(f"Score difference from default: {score_exp - score_default} points")
print()

# Summary
print("=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Default weights score:          {score_default}")
print(f"Skill-heavy weights score:      {score_custom} (diff: {score_custom - score_default:+d})")
print(f"AI-suggested weights score:     {score_ai} (diff: {score_ai - score_default:+d})")
print(f"Experience-heavy weights score: {score_exp} (diff: {score_exp - score_default:+d})")
print()

# Verify weights are working
all_different = (
    score_default != score_custom or
    score_default != score_ai or
    score_default != score_exp
)

if all_different:
    print("✅ SUCCESS: Different weights produce different scores!")
    print("   The weight system is working correctly.")
else:
    print("❌ FAILURE: All scores are the same despite different weights!")
    print("   There may still be an issue with weight handling.")

print()
print("=" * 60)
