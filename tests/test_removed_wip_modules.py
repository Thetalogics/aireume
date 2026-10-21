from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_unreferenced_wip_modules_are_not_in_the_tree():
    removed = [
        ROOT / "app/backend/services/wip/consensus_analyzer.py",
        ROOT / "app/backend/services/wip/calibration_service.py",
    ]
    present = [str(path.relative_to(ROOT)) for path in removed if path.exists()]
    assert present == []
