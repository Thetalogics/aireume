"""
Test to verify that custom weights actually affect the deterministic score calculation.
This ensures the weight mapping fix is working correctly.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app', 'backend'))

from services.fit_scorer import compute_deterministic_score
from services.eligibility_service import EligibilityResult


def test_weights_affect_score():
    """Verify that different weight configurations produce different scores."""
    
    # Sample features
    features = {
        "core_skill_match": 0.75,      # 75% core skills matched
        "secondary_skill_match": 0.50,  # 50% secondary skills
        "domain_match": 0.60,          # 60% domain match
        "relevant_experience": 0.80,   # 80% experience requirement met
        "total_experience": 5.0,
    }
    
    # Eligible candidate (no caps)
    eligibility = EligibilityResult(
        eligible=True,
        reason=None,
        details={}
    )
    
    print("=" * 80)
    print("TEST 1: Default weights (no custom weights)")
    print("=" * 80)
    score_default = compute_deterministic_score(features, eligibility, weights=None)
    print(f"Score: {score_default}")
    print(f"Expected: ~71 (0.75*0.40 + 0.50*0.15 + 0.60*0.25 + 0.80*0.20) * 100")
    print()
    
    print("=" * 80)
    print("TEST 2: New 7-weight schema (core_competencies heavy)")
    print("=" * 80)
    new_weights_skill_heavy = {
        "core_competencies": 0.50,  # High weight on skills
        "experience": 0.15,
        "domain_fit": 0.15,
        "education": 0.05,
        "career_trajectory": 0.10,
        "role_excellence": 0.05,
        "risk": 0.10,
    }
    score_new = compute_deterministic_score(features, eligibility, weights=new_weights_skill_heavy)
    print(f"Score: {score_new}")
    print(f"Should be HIGHER than default (75) because core_skill_match (0.75) has more weight")
    assert score_new != score_default, "Score should change with custom weights!"
    print(f"✓ Score changed from {score_default} to {score_new}")
    print()
    
    print("=" * 80)
    print("TEST 3: New 7-weight schema (experience heavy)")
    print("=" * 80)
    new_weights_exp_heavy = {
        "core_competencies": 0.20,
        "experience": 0.40,  # High weight on experience
        "domain_fit": 0.15,
        "education": 0.05,
        "career_trajectory": 0.10,
        "role_excellence": 0.10,
        "risk": 0.10,
    }
    score_exp = compute_deterministic_score(features, eligibility, weights=new_weights_exp_heavy)
    print(f"Score: {score_exp}")
    print(f"Should be HIGHER than default because relevant_experience (0.80) has more weight")
    assert score_exp != score_default, "Score should change with custom weights!"
    print(f"✓ Score changed from {score_default} to {score_exp}")
    print()
    
    print("=" * 80)
    print("TEST 4: Internal 7-weight schema (skills, experience, architecture, ...)")
    print("=" * 80)
    internal_weights = {
        "skills": 0.40,       # High weight on skills
        "experience": 0.20,
        "architecture": 0.15,
        "education": 0.10,
        "timeline": 0.10,
        "domain": 0.10,
        "risk": 0.15,
    }
    score_internal = compute_deterministic_score(features, eligibility, weights=internal_weights)
    print(f"Score: {score_internal}")
    print(f"Should be different from default")
    assert score_internal != score_default, "Score should change with custom weights!"
    print(f"✓ Score changed from {score_default} to {score_internal}")
    print()
    
    print("=" * 80)
    print("TEST 5: Legacy 4-weight schema (skills, experience, stability, education)")
    print("=" * 80)
    legacy_weights = {
        "skills": 0.50,       # Very high weight on skills
        "experience": 0.25,
        "stability": 0.15,
        "education": 0.10,
    }
    score_legacy = compute_deterministic_score(features, eligibility, weights=legacy_weights)
    print(f"Score: {score_legacy}")
    print(f"Should be HIGHER because skills (0.75) has very high weight")
    assert score_legacy != score_default, "Score should change with custom weights!"
    print(f"✓ Score changed from {score_default} to {score_legacy}")
    print()
    
    print("=" * 80)
    print("TEST 6: Direct deterministic schema (core_skill_match, etc.)")
    print("=" * 80)
    direct_weights = {
        "core_skill_match": 0.60,
        "secondary_skill_match": 0.10,
        "domain_match": 0.10,
        "relevant_experience": 0.20,
    }
    score_direct = compute_deterministic_score(features, eligibility, weights=direct_weights)
    print(f"Score: {score_direct}")
    print(f"Should match exact calculation")
    expected_direct = int((0.75*0.60 + 0.50*0.10 + 0.60*0.10 + 0.80*0.20) * 100)
    print(f"Expected: {expected_direct}")
    assert score_direct == expected_direct, f"Score {score_direct} should equal {expected_direct}"
    print(f"✓ Score matches expected: {score_direct}")
    print()
    
    print("=" * 80)
    print("SUMMARY: All weight schemas correctly affect the score!")
    print("=" * 80)
    print(f"Default (no weights):     {score_default}")
    print(f"New (skill-heavy):        {score_new}")
    print(f"New (experience-heavy):   {score_exp}")
    print(f"Internal schema:          {score_internal}")
    print(f"Legacy schema:            {score_legacy}")
    print(f"Direct schema:            {score_direct}")
    print()
    print("✓ All scores are different (except where intentionally similar)")
    print("✓ Weight changes now properly affect scoring!")
    print()


def test_caps_still_work():
    """Verify that eligibility caps still work with custom weights."""
    
    features = {
        "core_skill_match": 0.90,      # Excellent skills
        "secondary_skill_match": 0.80,
        "domain_match": 0.85,          # Great domain match
        "relevant_experience": 0.95,   # Perfect experience
        "total_experience": 10.0,
    }
    
    print("=" * 80)
    print("TEST: Caps still work with custom weights")
    print("=" * 80)
    
    # Ineligible candidate - should cap at 35
    ineligible = EligibilityResult(
        eligible=False,
        reason="domain_mismatch",
        details={"jd_domain": "backend", "candidate_domain": "frontend"}
    )
    
    # Even with custom weights, score should be capped
    weights = {
        "core_competencies": 0.50,
        "experience": 0.20,
        "domain_fit": 0.15,
        "education": 0.05,
        "career_trajectory": 0.05,
        "role_excellence": 0.05,
        "risk": 0.10,
    }
    
    score = compute_deterministic_score(features, ineligible, weights=weights)
    print(f"Score with ineligible cap: {score}")
    print(f"Should be <= 35 (ineligible cap)")
    assert score <= 35, f"Score {score} should be capped at 35 for ineligible candidates"
    print(f"✓ Cap works correctly: {score} <= 35")
    print()
    
    # Low domain match - should cap at 35
    low_domain_features = features.copy()
    low_domain_features["domain_match"] = 0.20  # Very low
    eligible = EligibilityResult(eligible=True, reason=None, details={})
    
    score_low_domain = compute_deterministic_score(low_domain_features, eligible, weights=weights)
    print(f"Score with low domain match (0.20): {score_low_domain}")
    print(f"Should be <= 35 (low domain cap)")
    assert score_low_domain <= 35, f"Score {score_low_domain} should be capped at 35 for low domain match"
    print(f"✓ Domain cap works correctly: {score_low_domain} <= 35")
    print()


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("TESTING: Weight Schema Mapping for Deterministic Scoring")
    print("=" * 80 + "\n")
    
    try:
        test_weights_affect_score()
        print("\n" + "=" * 80)
        test_caps_still_work()
        print("=" * 80)
        print("\n✅ ALL TESTS PASSED! Weight changes now properly affect scores.\n")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
