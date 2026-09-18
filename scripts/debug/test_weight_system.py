"""
Comprehensive Test Suite for Intelligent Scoring Weights System

Tests backward compatibility, weight conversion, and new features.
Run this before deployment to ensure nothing is broken.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app', 'backend'))

def test_weight_mapper():
    """Test weight mapping utility for backward compatibility"""
    print("\n=== Testing Weight Mapper ===")
    
    from services.weight_mapper import (
        convert_to_new_schema,
        detect_weight_schema,
        normalize_weights,
        map_legacy_to_new,
        map_old_backend_to_new
    )
    
    # Test 1: Legacy 4-weight schema
    print("\n1. Testing legacy 4-weight schema conversion...")
    legacy_weights = {
        "skills": 0.40,
        "experience": 0.35,
        "stability": 0.15,
        "education": 0.10
    }
    
    schema_type = detect_weight_schema(legacy_weights)
    assert schema_type == "legacy", f"Expected 'legacy', got '{schema_type}'"
    print(f"   ✓ Detected schema: {schema_type}")
    
    converted = convert_to_new_schema(legacy_weights)
    assert "core_competencies" in converted, "Missing core_competencies"
    assert "risk" in converted, "Missing risk"
    total = sum(v for k, v in converted.items() if k != "risk")
    assert 0.98 <= total <= 1.02, f"Weights don't sum to 1.0: {total}"
    print(f"   ✓ Converted successfully, total: {total:.2f}")
    print(f"   ✓ Converted weights: {converted}")
    
    # Test 2: Old backend 7-weight schema
    print("\n2. Testing old backend 7-weight schema conversion...")
    old_backend = {
        "skills": 0.30,
        "experience": 0.20,
        "architecture": 0.15,
        "education": 0.10,
        "timeline": 0.10,
        "domain": 0.10,
        "risk": 0.15
    }
    
    schema_type = detect_weight_schema(old_backend)
    assert schema_type == "old_backend", f"Expected 'old_backend', got '{schema_type}'"
    print(f"   ✓ Detected schema: {schema_type}")
    
    converted = convert_to_new_schema(old_backend)
    assert "core_competencies" in converted, "Missing core_competencies"
    assert "role_excellence" in converted, "Missing role_excellence"
    assert "domain_fit" in converted, "Missing domain_fit"
    # Check that mapping preserves relative proportions
    total = sum(v for k, v in converted.items() if k != "risk")
    assert 0.98 <= total <= 1.02, f"Weights don't sum to 1.0: {total}"
    print(f"   ✓ Converted successfully, total: {total:.2f}")
    print(f"   ✓ Converted weights: {converted}")
    
    # Test 3: New 7-weight schema (should pass through)
    print("\n3. Testing new 7-weight schema (pass-through)...")
    new_weights = {
        "core_competencies": 0.30,
        "experience": 0.20,
        "domain_fit": 0.20,
        "education": 0.10,
        "career_trajectory": 0.10,
        "role_excellence": 0.10,
        "risk": 0.10
    }
    
    schema_type = detect_weight_schema(new_weights)
    assert schema_type == "new", f"Expected 'new', got '{schema_type}'"
    print(f"   ✓ Detected schema: {schema_type}")
    
    converted = convert_to_new_schema(new_weights)
    assert converted == normalize_weights(new_weights), "New schema altered incorrectly"
    print(f"   ✓ Passed through unchanged")
    
    # Test 4: None/empty weights (should return defaults)
    print("\n4. Testing None/empty weights...")
    converted = convert_to_new_schema(None)
    assert "core_competencies" in converted, "Missing core_competencies in defaults"
    print(f"   ✓ Returns defaults correctly")
    
    print("\n✅ All weight mapper tests passed!")
    return True


def test_pipeline_compatibility():
    """Test that pipelines handle both old and new weights"""
    print("\n=== Testing Pipeline Compatibility ===")
    
    try:
        from services.hybrid_pipeline import compute_fit_score
        
        # Test with old weights
        print("\n1. Testing hybrid pipeline with old weights...")
        old_weights = {
            "skills": 0.30,
            "experience": 0.20,
            "architecture": 0.15,
            "education": 0.10,
            "timeline": 0.10,
            "domain": 0.10,
            "risk": 0.15
        }
        
        scores = {
            "skill_score": 80,
            "exp_score": 70,
            "arch_score": 60,
            "edu_score": 75,
            "timeline_score": 85,
            "domain_score": 65,
            "actual_years": 5,
            "required_years": 3,
            "matched_skills": ["Python", "FastAPI"],
            "missing_skills": ["React"],
            "required_count": 3,
            "employment_gaps": [],
            "short_stints": []
        }
        
        result = compute_fit_score(scores, old_weights)
        assert "fit_score" in result, "Missing fit_score"
        assert "final_recommendation" in result, "Missing final_recommendation"
        assert 0 <= result["fit_score"] <= 100, f"Invalid fit_score: {result['fit_score']}"
        print(f"   ✓ Old weights work, fit_score: {result['fit_score']}")
        
        # Test with new weights
        print("\n2. Testing hybrid pipeline with new weights...")
        new_weights = {
            "core_competencies": 0.30,
            "experience": 0.20,
            "domain_fit": 0.20,
            "education": 0.10,
            "career_trajectory": 0.10,
            "role_excellence": 0.10,
            "risk": 0.10
        }
        
        result = compute_fit_score(scores, new_weights)
        assert "fit_score" in result, "Missing fit_score"
        assert 0 <= result["fit_score"] <= 100, f"Invalid fit_score: {result['fit_score']}"
        print(f"   ✓ New weights work, fit_score: {result['fit_score']}")
        
        # Test with legacy weights
        print("\n3. Testing hybrid pipeline with legacy 4-weight...")
        legacy_weights = {
            "skills": 0.40,
            "experience": 0.35,
            "stability": 0.15,
            "education": 0.10
        }
        
        result = compute_fit_score(scores, legacy_weights)
        assert "fit_score" in result, "Missing fit_score"
        assert 0 <= result["fit_score"] <= 100, f"Invalid fit_score: {result['fit_score']}"
        print(f"   ✓ Legacy weights work, fit_score: {result['fit_score']}")
        
        print("\n✅ All pipeline compatibility tests passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_migration():
    """Verify database migration structure"""
    print("\n=== Testing Database Migration ===")
    
    try:
        # Check migration file exists
        migration_file = "alembic/versions/009_intelligent_scoring_weights.py"
        if not os.path.exists(migration_file):
            print(f"   ❌ Migration file not found: {migration_file}")
            return False
        print(f"   ✓ Migration file exists")
        
        # Read and verify migration content
        with open(migration_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        required_fields = [
            "is_active",
            "version_number",
            "role_category",
            "weight_reasoning",
            "suggested_weights_json"
        ]
        
        for field in required_fields:
            if field not in content:
                print(f"   ❌ Missing field in migration: {field}")
                return False
        print(f"   ✓ All required fields present in migration")
        
        # Check for nullable=True (backward compatibility)
        if "nullable=True" not in content:
            print(f"   ⚠️  Warning: Fields may not be nullable (could break existing data)")
        else:
            print(f"   ✓ Fields are nullable (backward compatible)")
        
        # Check for downgrade function
        if "def downgrade()" not in content:
            print(f"   ⚠️  Warning: No downgrade function (rollback may be difficult)")
        else:
            print(f"   ✓ Downgrade function exists (rollback supported)")
        
        print("\n✅ Database migration structure verified!")
        return True
        
    except Exception as e:
        print(f"\n❌ Migration verification failed: {e}")
        return False


def test_api_routes():
    """Test API route modifications don't have syntax errors"""
    print("\n=== Testing API Routes ===")
    
    try:
        # Test analyze.py imports
        print("\n1. Testing analyze.py imports...")
        from routes.analyze import router
        print(f"   ✓ analyze.py imports successfully")
        
        # Test candidates.py imports
        print("\n2. Testing candidates.py imports...")
        from routes.candidates import router as candidates_router
        print(f"   ✓ candidates.py imports successfully")
        
        print("\n✅ All API route imports successful!")
        return True
        
    except Exception as e:
        print(f"\n❌ API route test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_weight_suggester():
    """Test weight suggester fallback functionality"""
    print("\n=== Testing Weight Suggester ===")
    
    try:
        from services.weight_suggester import (
            get_default_weights_for_category,
            get_role_excellence_label,
            create_fallback_suggestion
        )
        
        # Test default weights for different categories
        print("\n1. Testing default weights for role categories...")
        categories = ["technical", "sales", "hr", "marketing", "operations", "leadership"]
        
        for category in categories:
            weights = get_default_weights_for_category(category)
            assert isinstance(weights, dict), f"Invalid weights for {category}"
            assert "core_competencies" in weights, f"Missing core_competencies for {category}"
            assert "risk" in weights, f"Missing risk for {category}"
            total = sum(v for k, v in weights.items() if k != "risk")
            assert 0.98 <= total <= 1.02, f"Weights don't sum to 1.0 for {category}: {total}"
            print(f"   ✓ {category}: {total:.2f}")
        
        # Test role excellence labels
        print("\n2. Testing role excellence labels...")
        for category in categories:
            label = get_role_excellence_label(category)
            assert isinstance(label, str), f"Invalid label for {category}"
            assert len(label) > 0, f"Empty label for {category}"
            print(f"   ✓ {category}: {label}")
        
        # Test fallback suggestion
        print("\n3. Testing fallback suggestion...")
        jd_text = "Senior Backend Engineer with 8+ years of experience in Python and FastAPI"
        suggestion = create_fallback_suggestion(jd_text, "technical")
        assert "role_category" in suggestion, "Missing role_category"
        assert "suggested_weights" in suggestion, "Missing suggested_weights"
        assert suggestion["fallback"] == True, "Should be marked as fallback"
        print(f"   ✓ Fallback suggestion created: {suggestion['role_category']}")
        
        print("\n✅ All weight suggester tests passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Weight suggester test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Run all tests and report results"""
    print("\n" + "="*60)
    print("INTELLIGENT SCORING WEIGHTS SYSTEM - PRE-DEPLOYMENT TESTS")
    print("="*60)
    
    results = {
        "Weight Mapper": test_weight_mapper(),
        "Pipeline Compatibility": test_pipeline_compatibility(),
        "Database Migration": test_database_migration(),
        "API Routes": test_api_routes(),
        "Weight Suggester": test_weight_suggester()
    }
    
    print("\n" + "="*60)
    print("TEST RESULTS SUMMARY")
    print("="*60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name:.<40} {status}")
        if not passed:
            all_passed = False
    
    print("="*60)
    
    if all_passed:
        print("\n🎉 ALL TESTS PASSED - SAFE TO DEPLOY")
        print("\nNext steps:")
        print("1. Backup database: pg_dump -U user -d resume_ai > backup.sql")
        print("2. Run migration: alembic upgrade head")
        print("3. Restart backend: docker-compose restart backend")
        print("4. Monitor logs for any issues")
    else:
        print("\n⚠️  SOME TESTS FAILED - DO NOT DEPLOY")
        print("\nPlease fix the failing tests before deployment.")
    
    print("\n")
    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
