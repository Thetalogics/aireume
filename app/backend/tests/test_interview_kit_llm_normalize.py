"""Tests for interview kit LLM normalization and validation."""

from app.backend.services.background_enrichment import (
    _kit_meets_minimum,
    _normalize_interview_kit,
    MIN_USABLE_KIT_QUESTIONS,
)


class TestNormalizeInterviewKit:
    def test_maps_questions_key_to_steps(self):
        raw = {
            "interview_questions": {
                "threads": [
                    {
                        "id": "thread_1",
                        "kind": "ownership",
                        "questions": [
                            {"text": "What did you own at Acme?", "spoken_text": "What did you own at Acme?"},
                            {"text": "How large was the team?", "spoken_text": "How large was the team?"},
                        ],
                    },
                ],
            },
        }
        out = _normalize_interview_kit(raw)
        assert len(out["threads"]) == 1
        assert len(out["threads"][0]["steps"]) == 2

    def test_builds_threads_from_legacy_arrays(self):
        raw = {
            "interview_questions": {
                "threads": [],
                "technical_questions": [{"text": "Explain your ETL pipeline?"}],
                "experience_deep_dive_questions": [{"text": "Tell me about your last role?"}],
            },
        }
        out = _normalize_interview_kit(raw)
        assert len(out["threads"]) >= 1
        assert sum(len(t.get("steps") or []) for t in out["threads"]) >= 2

    def test_wraps_single_thread_returned_as_root_object(self):
        raw = {
            "id": "ownership",
            "kind": "ownership",
            "title": "Ownership",
            "steps": [{"text": f"Question {i}?"} for i in range(MIN_USABLE_KIT_QUESTIONS)],
        }

        out = _normalize_interview_kit(raw)

        assert len(out["threads"]) == 1
        assert len(out["threads"][0]["steps"]) == MIN_USABLE_KIT_QUESTIONS
        assert _kit_meets_minimum(raw) is True

    def test_rejects_string_interview_questions(self):
        raw = {"interview_questions": "not an object"}
        out = _normalize_interview_kit(raw)
        assert out["threads"] == []

    def test_kit_meets_minimum_requires_enough_steps(self):
        ok = {
            "interview_questions": {
                "threads": [
                    {"id": "a", "steps": [{"text": f"Question {i}?"} for i in range(MIN_USABLE_KIT_QUESTIONS)]},
                ],
            },
        }
        assert _kit_meets_minimum(ok) is True

        empty = {"interview_questions": {"threads": [], "technical_questions": []}}
        assert _kit_meets_minimum(empty) is False
