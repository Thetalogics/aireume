from app.backend.services.interview_kit_quality import (
    FORBIDDEN_STEMS,
    get_spoken_line,
    lint_interview_kit,
)


def test_flags_repeated_walk_me_through():
    kit = {
        "kit_version": 3,
        "threads": [{
            "steps": [
                {"text": "Walk me through your SAP work."},
                {"text": "Walk me through your team setup."},
            ],
        }],
    }
    result = lint_interview_kit(kit)
    assert result["ok"] is False
    assert any("walk me" in i["message"].lower() for i in result["issues"])


def test_deterministic_kit_passes_production_lint():
    from app.backend.services.background_enrichment import _deterministic_interview_kit
    from app.backend.services.recruiter_voice_personalizer import _apply_minimal_personalization

    python_result = {
        "candidate_profile": {
            "current_role": "Recruiter",
            "total_effective_years": 4,
            "name": "Monika",
        },
        "jd_analysis": {
            "domain": "hr",
            "role_title": "Talent Acquisition Partner",
            "required_skills": ["sourcing", "screening"],
        },
        "skill_analysis": {
            "matched_skills": ["sourcing"],
            "missing_skills": ["screening"],
        },
        "parsed_data": {},
        "fit_score": 31,
    }
    kit = _deterministic_interview_kit(python_result)
    polished = _apply_minimal_personalization(
        kit,
        {"resume_anchors": {"current_company": "Acme", "current_role": "Recruiter"}},
    )
    result = lint_interview_kit(polished)
    assert result["ok"] is True, result["issues"]


def test_deterministic_gap_prompts_pass_lint():
    from app.backend.services.interview_playbook_templates import risk_gap_thread_steps

    for family in ("talent_acquisition", "engineering", "general"):
        steps = risk_gap_thread_steps(family, "sourcing", {})
        result = lint_interview_kit({"threads": [{"steps": steps}]})
        assert result["ok"] is True, result["issues"]


def test_flags_this_role_needs_stem():
    kit = {"threads": [{"steps": [{"text": "This role needs Kubernetes in production."}]}]}
    result = lint_interview_kit(kit)
    assert result["ok"] is False


def test_passes_personalized_question():
    kit = {
        "threads": [{
            "steps": [{
                "spoken_text": (
                    "At Acme you ran the S/4 cutover — what did you personally own through go-live?"
                ),
            }],
        }],
    }
    result = lint_interview_kit(kit)
    assert result["ok"] is True


def test_get_spoken_line_prefers_spoken_text():
    assert get_spoken_line({"text": "OLD", "spoken_text": "NEW"}) == "NEW"


def test_forbidden_stems_tuple_not_empty():
    assert len(FORBIDDEN_STEMS) >= 3
