"""Hard-failing Alembic / ORM drift proofs for post-080 objects."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.backend.tests.reliability_env import require_postgres

ROOT = Path(__file__).resolve().parents[3]
HEAD = "082_reliability_closure"
REQUIRED_TABLES = {
    "screening_decisions",
    "decision_narratives",
    "training_runs",
    "worker_heartbeats",
    "idempotency_keys",
}
REQUIRED_COLUMNS = {
    "screening_results": {"current_decision_id"},
    "idempotency_keys": {"state", "owner_token", "lease_expires_at"},
}


def _alembic(url: str, *args: str):
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_m4_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD


def test_m5_no_historical_migration_edits():
    proc = subprocess.run(
        ["git", "diff", "--name-only", "e7acd55575e6c79d19578f037d96bf4b8611ed80", "--", "alembic/versions"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    changed = [line.replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    forbidden = [
        name
        for name in changed
        if "/versions/" in name or name.startswith("alembic/versions/")
    ]
    historical = []
    for name in forbidden:
        base = Path(name).name
        if base.startswith("082") or base.startswith("081"):
            continue
        historical.append(name)
    assert historical == []


def test_m1_m3_m6_head_schema_and_082_cycle():
    url = require_postgres()
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    dbname = f"aria_drift_{uuid.uuid4().hex[:8]}"
    admin = create_engine(parsed.set(database="postgres"))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE DATABASE {dbname}"))
    isolated = parsed.set(database=dbname).render_as_string(hide_password=False)
    try:
        _alembic(isolated, "upgrade", "head")
        engine = create_engine(isolated)
        with engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert revision == HEAD
            tables = set(inspect(conn).get_table_names())
            missing = REQUIRED_TABLES - tables
            assert not missing, missing
            for table, cols in REQUIRED_COLUMNS.items():
                have = {c["name"] for c in inspect(conn).get_columns(table)}
                assert cols <= have, (table, cols - have)
        engine.dispose()

        _alembic(isolated, "downgrade", "081_phase2_screening_decisions")
        _alembic(isolated, "upgrade", HEAD)
        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=str(ROOT),
            env={**os.environ, "DATABASE_URL": isolated, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            # Phase 2 env.py isolates historical create_all; require explicit post-080 objects.
            assert "Target database is not up to date" not in (check.stdout + check.stderr)
            engine = create_engine(isolated)
            with engine.connect() as conn:
                assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD
                assert inspect(conn).has_table("training_runs")
                assert inspect(conn).has_table("worker_heartbeats")
            engine.dispose()
    finally:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
        admin.dispose()
