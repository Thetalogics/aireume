# Weight System Guide

## Overview

The Resume AI system uses a sophisticated, multi-schema weight system that allows users to customize how candidates are scored. This guide explains how weights work, the different schemas supported, and how to use them effectively.

## Problem Solved

**Before the fix:** When users changed weights (either manually or via AI suggestions), the fit score remained the same because the deterministic scorer wasn't properly mapping the weight keys.

**After the fix:** All weight changes now properly affect the scoring calculation across all weight schemas.

## Weight Schemas

The system supports **4 different weight schemas** for backward compatibility and flexibility:

### 1. New 7-Weight Schema (Recommended)

This is the modern, universal-adaptive schema used by the AI weight suggestion system:

```json
{
  "core_competencies": 0.30,    // Main skills - LLM interprets per role
  "experience": 0.20,           // Years + seniority
  "domain_fit": 0.20,           // LLM interprets domain context
  "education": 0.10,            // Degrees, certs, learning
  "career_trajectory": 0.10,    // Progression + stability
  "role_excellence": 0.10,      // Role-specific differentiator
  "risk": 0.10                  // Red flags penalty magnitude (positive; legacy negatives are abs()'d)
}
```

**Use Cases:**
- AI-suggested weights via `/api/analyze/suggest-weights`
- Role-adaptive scoring (technical, sales, HR, marketing, etc.)
- Modern frontend with UniversalWeightsPanel

### 2. Internal 7-Weight Schema

This is the internal backend schema used by `compute_fit_score`:

```json
{
  "skills": 0.30,       // Core technical skills
  "experience": 0.20,   // Years of experience
  "architecture": 0.15, // System design, leadership
  "education": 0.10,    // Educational background
  "timeline": 0.10,     // Career continuity
  "domain": 0.10,       // Domain/industry fit
  "risk": 0.15          // Risk penalty weight (positive, applied as negative)
}
```

**Use Cases:**
- Internal scoring calculations
- Legacy backend integrations

### 3. Legacy 4-Weight Schema

The original simplified schema:

```json
{
  "skills": 0.40,       // All skills combined
  "experience": 0.35,   // Years of experience
  "stability": 0.15,    // Career stability
  "education": 0.10     // Educational background
}
```

**Use Cases:**
- Legacy frontend integrations
- Simple scoring scenarios

### 4. Direct Deterministic Schema

The internal deterministic feature weights:

```json
{
  "core_skill_match": 0.40,        // Core skills match ratio (0-1)
  "secondary_skill_match": 0.15,   // Secondary skills match ratio (0-1)
  "domain_match": 0.25,            // Domain similarity score (0-1)
  "relevant_experience": 0.20      // Experience fulfillment ratio (0-1)
}
```

**Use Cases:**
- Direct deterministic scoring
- Advanced custom integrations

## How Weights Flow Through the System

### Analysis Pipeline

```
1. User provides weights (any schema)
   ↓
2. convert_to_new_schema() - Converts to New 7-Weight Schema
   ↓
3. compute_fit_score() - Uses Internal 7-Weight Schema
   ↓
4. compute_deterministic_score() - Maps from New 7-Weight Schema
   ↓
5. Final fit_score (0-100)
```

### Key Functions

#### `convert_to_new_schema()` (weight_mapper.py)
- Accepts any weight schema
- Converts to New 7-Weight Schema
- Handles legacy, internal, and new schemas

#### `compute_fit_score()` (fit_scorer.py)
- Calculates weighted score from individual component scores
- Uses Internal 7-Weight Schema
- Applies risk penalties
- Returns fit_score (0-100) + recommendation

#### `compute_deterministic_score()` (fit_scorer.py) **[FIXED]**
- Calculates score from feature ratios (0-1 scale)
- **Now properly maps ALL weight schemas**
- Applies eligibility caps
- Returns deterministic score (0-100)

## Weight Presets

The system includes several presets for common scenarios:

### Balanced (Default)
```json
{
  "core_competencies": 0.30,
  "experience": 0.20,
  "domain_fit": 0.20,
  "education": 0.10,
  "career_trajectory": 0.10,
  "role_excellence": 0.10,
  "risk": 0.10
}
```

### Skill-Heavy
For roles where technical skills are paramount:
```json
{
  "core_competencies": 0.40,
  "experience": 0.20,
  "domain_fit": 0.15,
  "education": 0.05,
  "career_trajectory": 0.10,
  "role_excellence": 0.10,
  "risk": 0.10
}
```

### Experience-Heavy
For senior roles requiring proven track record:
```json
{
  "core_competencies": 0.25,
  "experience": 0.35,
  "domain_fit": 0.15,
  "education": 0.05,
  "career_trajectory": 0.10,
  "role_excellence": 0.10,
  "risk": 0.10
}
```

### Domain-Focused
For specialized roles requiring industry knowledge:
```json
{
  "core_competencies": 0.25,
  "experience": 0.20,
  "domain_fit": 0.30,
  "education": 0.05,
  "career_trajectory": 0.10,
  "role_excellence": 0.10,
  "risk": 0.10
}
```

## Using AI-Suggested Weights

The system can analyze a job description and suggest optimal weights:

### API Endpoint
```
POST /api/analyze/suggest-weights
Form data: job_description=<JD text>
```

### Response
```json
{
  "role_category": "technical",
  "seniority_level": "senior",
  "suggested_weights": {
    "core_competencies": 0.35,
    "experience": 0.25,
    "domain_fit": 0.20,
    "education": 0.05,
    "career_trajectory": 0.10,
    "role_excellence": 0.05,
    "risk": 0.10
  },
  "role_excellence_label": "Architecture & System Design",
  "reasoning": "This senior technical role emphasizes core competencies...",
  "confidence": 0.85
}
```

### Usage in Analysis
```javascript
// Frontend: Get AI suggestions
const suggestions = await fetch('/api/analyze/suggest-weights', {
  method: 'POST',
  body: formData
});

// Use suggested weights in analysis
const analysis = await fetch('/api/analyze/stream', {
  method: 'POST',
  body: {
    resume: file,
    job_description: jd,
    scoring_weights: JSON.stringify(suggestions.suggested_weights)
  }
});
```

## Weight Impact Examples

### Example 1: Skill-Heavy Weights
**Candidate Profile:**
- Core skills: 75% match
- Experience: 60% match
- Domain: 50% match

**With Balanced Weights:**
```
Score = 0.75*0.30 + 0.60*0.20 + 0.50*0.20 + ... = ~65
```

**With Skill-Heavy Weights:**
```
Score = 0.75*0.40 + 0.60*0.20 + 0.50*0.15 + ... = ~70
```

**Result:** Score increases by 5 points because the candidate's strong skills (75%) are weighted more heavily.

### Example 2: Experience-Heavy Weights
**Candidate Profile:**
- Core skills: 60% match
- Experience: 90% match
- Domain: 70% match

**With Balanced Weights:**
```
Score = 0.60*0.30 + 0.90*0.20 + 0.70*0.20 + ... = ~72
```

**With Experience-Heavy Weights:**
```
Score = 0.60*0.25 + 0.90*0.35 + 0.70*0.15 + ... = ~78
```

**Result:** Score increases by 6 points because the candidate's strong experience (90%) is weighted more heavily.

## Testing Your Weights

Use the provided test script to verify weights work correctly:

```bash
python test_weight_fix.py
```

This test verifies:
1. ✅ Default weights produce baseline scores
2. ✅ New 7-weight schema affects scores
3. ✅ Internal 7-weight schema affects scores
4. ✅ Legacy 4-weight schema affects scores
5. ✅ Direct deterministic schema affects scores
6. ✅ Eligibility caps still work with custom weights

## Troubleshooting

### Problem: Score doesn't change when I modify weights

**Cause:** You might be using an outdated version before the fix.

**Solution:**
1. Ensure you're running the latest code (after May 2026)
2. Check that weights are being sent as JSON string in `scoring_weights` form field
3. Verify weights sum to approximately 1.0 (excluding risk)

### Problem: AI suggestions don't seem optimal

**Cause:** The LLM might need more context or the JD might be ambiguous.

**Solution:**
1. Ensure JD has at least 80 words
2. Include clear role title and requirements
3. Check the `confidence` score in the response
4. Manually adjust weights if needed

### Problem: Weights are rejected by the API

**Cause:** Weights JSON exceeds 4KB size limit.

**Solution:**
1. Keep weights simple (7 keys max)
2. Don't nest objects or arrays
3. Use simple float values (0.0-1.0)

## Best Practices

1. **Start with AI suggestions** - Let the AI analyze the JD and suggest weights
2. **Fine-tune manually** - Adjust based on your specific hiring priorities
3. **Test with known candidates** - Verify scores align with your expectations
4. **Document your presets** - Save weight configurations for different role types
5. **Compare scenarios** - Use the comparison view to see how different weights affect scores

## Technical Details

### Weight Normalization

All weights are automatically normalized to sum to 1.0:

```python
total = sum(weights.values())
normalized = {k: v/total for k, v in weights.items()}
```

### Risk Weight

The `risk` weight is special:
- In New 7-Weight Schema: Negative value (e.g., -0.10)
- In Internal Schema: Positive value (e.g., 0.15), applied as negative
- Risk penalty is calculated separately and multiplied by risk weight

### Score Clamping

Final scores are always clamped to 0-100:

```python
fit_score = max(0, min(100, fit_score))
```

### Eligibility Caps

Even with custom weights, these caps apply:
- Ineligible candidates: max score = 35
- Low domain match (<30%): max score = 35
- Low core skills (<30%): max score = 40

## Migration Notes

If you're upgrading from an older version:

1. **Database**: No migration needed - all fields are backward compatible
2. **API**: Existing integrations continue working unchanged
3. **Frontend**: Old 4-weight schema still supported
4. **Scores**: Existing scores remain unchanged; only new analyses use updated logic

## Support

For questions or issues:
- Check the test suite: `app/backend/tests/test_fit_scorer.py`
- Review the weight mapper: `app/backend/services/weight_mapper.py`
- See the constants: `app/backend/services/constants.py`
