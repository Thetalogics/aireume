"""Single-owner Alembic upgrade with PostgreSQL advisory lock."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.backend.db.database_url import normalize_database_url

log = logging.getLogger("aria.migration_owner")
MIGRATION_LOCK_KEY = 1095914313  # stable int; not a secret


class MigrationOwnerError(RuntimeError):
    pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def assert_single_alembic_head() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "heads"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MigrationOwnerError(proc.stderr or proc.stdout or "alembic heads failed")
    heads = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip() and not line.startswith("ERROR")]
    if len(heads) != 1:
        raise MigrationOwnerError(f"expected exactly one Alembic head, got {heads!r}")
    return heads[0].split()[0]


def run_upgrade_head() -> str:
    """Acquire advisory lock (if PostgreSQL) and run `alembic upgrade head`."""
    head = assert_single_alembic_head()
    url, is_postgres = normalize_database_url(os.getenv("DATABASE_URL", ""))
    if not is_postgres:
        raise MigrationOwnerError("migration owner requires PostgreSQL DATABASE_URL")
    engine = create_engine(url)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        locked = connection.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY}
        ).scalar()
        if not locked:
            raise MigrationOwnerError("another process holds the ARIA migration advisory lock")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                cwd=_repo_root(),
                check=False,
            )
            if proc.returncode != 0:
                raise MigrationOwnerError("alembic upgrade head failed")
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
    engine.dispose()
    log.info("migration_owner completed head=%s", head)
    return head


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        run_upgrade_head()
    except MigrationOwnerError as exc:
        log.error("migration owner failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
