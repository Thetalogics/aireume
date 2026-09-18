"""
Quick verification that the weight fix is working correctly.
Run this to confirm the fix is active.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app', 'backend'))

from services.fit_scorer import compute_deterministic_score
from services.eligibility_service import EligibilityResult


def verify_fix():
    """Quick verification that weights affect scores."""
    
    print("\n" + "=" * 70)
    print("WEIGHT FIX VERIFICATION")
    print("=" * 70 + "\n")
    
    # Test features
    features = {
        "core_skill_match": 0.80,
        "secondary_skill_match": 0.60,
        "domain_match": 0.70,
        "relevant_experience": 0.75,
        "total_experience": 5.0,
    }
    
    eligibility = EligibilityResult(eligible=True, reason=None, details={})
    
    # Test 1: Default weights
    score_default = compute_deterministic_score(features, eligibility, weights=None)
    print(f"1. Default weights score: {score_default}")
    
    # Test 2: Custom weights (skill-heavy)
    custom_weights = {
        "core_competencies": 0.50,
        "experience": 0.15,
        "domain_fit": 0.15,
        "education": 0.05,
        "career_trajectory": 0.10,
        "role_excellence": 0.05,
        "risk": 0.10,
    }
    score_custom = compute_deterministic_score(features, eligibility, weights=custom_weights)
    print(f"2. Custom weights score:  {score_custom}")
    
    # Verify
    print("\n" + "-" * 70)
    if score_custom != score_default:
        print("✅ FIX VERIFIED: Custom weights produce different scores!")
        print(f"   Score changed from {score_default} to {score_custom} ({score_custom - score_default:+d} points)")
        print("\nThe weight system is working correctly.")
        print("AI-generated and custom weights will now affect candidate scores.")
        success = True
    else:
        print("❌ FIX NOT WORKING: Scores are the same!")
        print("   There may be an issue with the weight mapping.")
        success = False
    
    print("=" * 70 + "\n")
    return success


if __name__ == "__main__":
    try:
        success = verify_fix()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
