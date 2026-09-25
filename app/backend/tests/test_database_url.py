"""Regression: DATABASE_URL classification for SQLite and PostgreSQL dialects."""
from pathlib import Path

from app.backend.db.database_url import normalize_database_url


def test_sqlite_relative_path():
    url, is_pg = normalize_database_url("./resume_screener.db")
    assert url == "sqlite:///./resume_screener.db"
    assert is_pg is False


def test_sqlite_memory_url():
    url, is_pg = normalize_database_url("sqlite:///:memory:")
    assert url.startswith("sqlite:")
    assert is_pg is False


def test_postgresql_plain():
    url, is_pg = normalize_database_url("postgresql://phase0:secret@127.0.0.1:5432/app")
    assert is_pg is True
    assert url.startswith("postgresql://")
    assert "secret" in url
    assert "sqlite" not in url


def test_postgresql_psycopg_not_treated_as_sqlite():
    raw = "postgresql+psycopg://phase0:secret@127.0.0.1:55432/phase0"
    url, is_pg = normalize_database_url(raw)
    assert is_pg is True
    assert "postgresql+psycopg://" in url
    assert not url.startswith("sqlite:")


def test_postgresql_psycopg2_not_treated_as_sqlite():
    raw = "postgresql+psycopg2://phase0:secret@127.0.0.1:5432/phase0"
    url, is_pg = normalize_database_url(raw)
    assert is_pg is True
    assert "postgresql+psycopg2://" in url
    assert not url.startswith("sqlite:")


def test_legacy_postgres_scheme_normalized():
    url, is_pg = normalize_database_url("postgres://phase0:secret@127.0.0.1:5432/app")
    assert is_pg is True
    assert url.startswith("postgresql://")


def test_reliability_ci_uses_installed_postgres_driver():
    root = Path(__file__).resolve().parents[3]
    requirements = (root / "app/backend/requirements.txt").read_text()
    workflow = (root / ".github/workflows/ci.yml").read_text()

    assert "psycopg2-binary==" in requirements
    assert "postgresql+psycopg2://" in workflow
    assert "postgresql+psycopg://" not in workflow


def test_backend_requirements_are_top_level_pinned():
    root = Path(__file__).resolve().parents[3]
    requirements = (root / "app/backend/requirements.txt").read_text().splitlines()

    floating = []
    for line in requirements:
        requirement = line.strip()
        if not requirement or requirement.startswith("#"):
            continue
        if "==" not in requirement:
            floating.append(requirement)

    assert floating == []
