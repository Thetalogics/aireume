"""Classify and normalize SQLAlchemy database URLs without creating an engine."""
from __future__ import annotations

from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError


def normalize_database_url(raw: str | None) -> tuple[str, bool]:
    """Return ``(normalized_url, is_postgres)``.

    Recognizes PostgreSQL including driver-qualified dialects
    (``postgresql+psycopg``, ``postgresql+psycopg2``). Bare filesystem
    paths remain SQLite, matching historical ``database.py`` behavior.
    """
    raw = (raw or "").strip() or "./resume_screener.db"
    if "://" in raw:
        try:
            url = make_url(raw)
        except ArgumentError:
            return f"sqlite:///{raw}", False
        driver = (url.drivername or "").lower()
        if driver == "postgres":
            url = url.set(drivername="postgresql")
            driver = "postgresql"
        backend = driver.split("+", 1)[0]
        if backend in ("postgresql", "postgres"):
            return url.render_as_string(hide_password=False), True
        if backend == "sqlite":
            return url.render_as_string(hide_password=False), False
        return f"sqlite:///{raw}", False
    if raw.startswith("./") or raw.startswith("/") or not raw.startswith("sqlite"):
        return f"sqlite:///{raw}", False
    return raw, False
