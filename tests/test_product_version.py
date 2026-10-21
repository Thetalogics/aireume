import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_product_version_has_one_source():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert f"version-{version}" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"**Version**: {version}" in (ROOT / "PRODUCT_SPECIFICATION.md").read_text(encoding="utf-8")
    assert json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"] == version
    assert json.loads((ROOT / "app/frontend/package.json").read_text(encoding="utf-8"))["version"] == version
    from app.backend.version import VERSION
    assert VERSION == version
