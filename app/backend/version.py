from pathlib import Path


def read_version(module_file: Path) -> str:
    """Product version shipped next to this module.

    The backend image copies the app tree to /app/app. A file reached by walking
    three parents up from version.py is /app/VERSION, which that copy does not
    include. The file beside this module is inside the copied tree.
    """
    path = Path(module_file).resolve().parent / "VERSION"
    return path.read_text(encoding="utf-8").strip()


VERSION = read_version(Path(__file__))
