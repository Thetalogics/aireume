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
    assert (ROOT / "app" / "backend" / "VERSION").read_text(encoding="utf-8").strip() == version


def test_version_is_read_from_the_file_beside_the_module(tmp_path):
    """A VERSION file three directories up must not satisfy the import.

    In the backend image, that climb lands on /app/VERSION, which the running
    container does not contain. The file that ships inside COPY app/ is the one
    next to version.py.
    """
    from app.backend.version import read_version

    module = tmp_path / "app" / "backend" / "version.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (module.parent / "VERSION").write_text("9.9.9\n", encoding="utf-8")

    assert read_version(module) == "9.9.9"
