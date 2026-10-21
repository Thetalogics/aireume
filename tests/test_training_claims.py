from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_training_copy_is_prompt_personalization():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "PRODUCT_SPECIFICATION.md").read_text(encoding="utf-8")
    team = (ROOT / "app/frontend/src/pages/TeamPage.jsx").read_text(encoding="utf-8")
    assert "tenant prompt personalization" in readme
    assert "does not train weights" in readme
    assert "TRAINING_ENABLED=true" in readme
    assert "No weight training" in spec
    assert "Prompt personalization" in team
    assert "fine-tun" not in team.lower()
