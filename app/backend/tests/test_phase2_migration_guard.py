from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REVISION_001 = ROOT / "alembic" / "versions" / "001_enrich_candidates_add_caches.py"


def test_revision_001_does_not_mention_phase2_schema():
    text = REVISION_001.read_text(encoding="utf-8")
    assert "screening_decisions" not in text
    assert "decision_narratives" not in text
    assert "current_decision_id" not in text
    assert "081" not in text


def test_phase2_schema_is_owned_by_081():
    text = (ROOT / "alembic" / "versions" / "081_phase2_screening_decisions.py").read_text(
        encoding="utf-8"
    )
    assert "create_table" in text
    assert "screening_decisions" in text
    assert "current_decision_id" in text
    assert "uq_decision_narrative_operation" in text
