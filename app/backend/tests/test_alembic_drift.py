"""Hard-failing Alembic proofs: byte-stable <=080 history and post-080 schema contract.

Generic `alembic check` is not authoritative here. `alembic/env.py` detaches
Phase 2 columns from live SQLAlchemy metadata so revision 001's create_all
cannot materialize 081-owned columns. Autogenerate/check therefore compares
a deliberately incomplete metadata graph to the live database and can return
nonzero for expected isolation, or miss real post-080 drift. This module
asserts the schema contract and 082 cycle explicitly instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.backend.tests.reliability_env import require_postgres

ROOT = Path(__file__).resolve().parents[3]
HEAD = "082_reliability_closure"
THROUGH_REVISION = "080_phase1_reliability_closure"
MANIFEST_PATH = ROOT / "alembic" / "historical_migration_manifest.json"
VERSIONS_DIR = ROOT / "alembic" / "versions"
PREFIX_RE = re.compile(r"^(\d+)_")

REQUIRED_TABLES = {
    "screening_decisions",
    "decision_narratives",
    "training_runs",
    "worker_heartbeats",
    "idempotency_keys",
}
REQUIRED_COLUMNS = {
    "screening_results": {"current_decision_id"},
    "training_examples": {"screening_decision_id"},
    "ai_decision_logs": {"screening_decision_id"},
    "idempotency_keys": {"state", "owner_token", "lease_expires_at"},
}
REQUIRED_UNIQUE_CONSTRAINTS = {
    "screening_decisions": {
        "uq_screening_decision_result_version",
        "uq_screening_decision_tenant_operation_type",
    },
    "decision_narratives": {"uq_decision_narrative_operation"},
}
REQUIRED_FOREIGN_KEYS = {
    "screening_results": {"fk_screening_results_current_decision"},
    "ai_decision_logs": {"fk_ai_decision_logs_screening_decision"},
    "training_examples": {"fk_training_examples_screening_decision"},
}
REQUIRED_INDEXES = {
    "training_runs": {"ix_training_runs_tenant"},
    "screening_results": {"ix_screening_results_current_decision_id"},
    "ai_decision_logs": {"ix_ai_decision_logs_screening_decision_id"},
    "training_examples": {"ix_training_examples_screening_decision_id"},
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _numeric_prefix(name: str) -> int | None:
    match = PREFIX_RE.match(name)
    return int(match.group(1)) if match else None


def _files_map(payload: dict) -> dict[str, str]:
    files = payload.get("files")
    assert isinstance(files, list) and files, "manifest files must be a [{name, sha256}] list"
    mapped: dict[str, str] = {}
    for row in files:
        assert isinstance(row, dict), row
        name = row.get("name")
        digest = row.get("sha256")
        assert isinstance(name, str) and name.endswith(".py"), row
        assert isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), name
        assert name not in mapped, name
        mapped[name] = digest
    return mapped


def _load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert payload.get("algorithm") == "sha256", payload.get("algorithm")
    assert payload.get("through_revision") == THROUGH_REVISION, payload.get("through_revision")
    payload["files"] = _files_map(payload)
    return payload


def _assert_historical_bytes(versions_dir: Path, files: dict[str, str]) -> None:
    mismatches: list[str] = []
    for name, expected in files.items():
        path = versions_dir / name
        if not path.is_file():
            mismatches.append(f"{name}: missing from {versions_dir}")
            continue
        actual = _sha256_file(path)
        if actual != expected:
            mismatches.append(
                f"{name}: sha256 mismatch expected={expected} actual={actual}"
            )
    extra_historical = []
    for path in sorted(versions_dir.glob("*.py")):
        prefix = _numeric_prefix(path.name)
        if prefix is None:
            extra_historical.append(path.name)
            continue
        if prefix <= 80 and path.name not in files:
            extra_historical.append(path.name)
    assert not mismatches, "historical migration checksum failures:\n" + "\n".join(mismatches)
    assert extra_historical == [], extra_historical


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


def _constraint_names(insp, table: str) -> set[str]:
    names = {c["name"] for c in insp.get_unique_constraints(table) if c.get("name")}
    names.update(idx["name"] for idx in insp.get_indexes(table) if idx.get("unique") and idx.get("name"))
    return names


def _fk_names(insp, table: str) -> set[str]:
    return {fk.get("name") for fk in insp.get_foreign_keys(table) if fk.get("name")}


def _index_names(insp, table: str) -> set[str]:
    return {idx["name"] for idx in insp.get_indexes(table) if idx.get("name")}


def _assert_post_080_schema(conn) -> None:
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    missing_tables = REQUIRED_TABLES - tables
    assert not missing_tables, missing_tables
    for table, cols in REQUIRED_COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        assert cols <= have, (table, cols - have)
    for table, expected in REQUIRED_UNIQUE_CONSTRAINTS.items():
        have = _constraint_names(insp, table)
        assert expected <= have, (table, expected - have)
    for table, expected in REQUIRED_FOREIGN_KEYS.items():
        have = _fk_names(insp, table)
        assert expected <= have, (table, expected - have)
    for table, expected in REQUIRED_INDEXES.items():
        have = _index_names(insp, table)
        assert expected <= have, (table, expected - have)

    pk = insp.get_pk_constraint("idempotency_keys")
    pk_cols = set(pk.get("constrained_columns") or [])
    assert {"key", "tenant_id", "endpoint"} <= pk_cols or "uq_idempotency_key_tenant_endpoint" in _constraint_names(
        insp, "idempotency_keys"
    ), pk
    hb_pk = set((insp.get_pk_constraint("worker_heartbeats").get("constrained_columns") or []))
    assert hb_pk == {"role"}, hb_pk
    training_uniques = _constraint_names(insp, "training_runs")
    assert "ix_training_runs_tenant" in training_uniques or "tenant_id" in {
        tuple(c.get("column_names") or ())
        for c in insp.get_indexes("training_runs")
        if c.get("unique")
        for _ in [0]
    } or any(idx.get("unique") and "tenant_id" in (idx.get("column_names") or []) for idx in insp.get_indexes("training_runs"))


def test_m4_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD


def test_m5_historical_migrations_match_checksum_manifest():
    payload = _load_manifest()
    _assert_historical_bytes(VERSIONS_DIR, payload["files"])


def test_m5_phase2_revisions_are_not_manifest_members():
    payload = _load_manifest()
    files = payload["files"]
    assert "081_phase2_screening_decisions.py" not in files
    assert "082_reliability_closure.py" not in files
    assert (VERSIONS_DIR / "081_phase2_screening_decisions.py").is_file()
    assert (VERSIONS_DIR / "082_reliability_closure.py").is_file()


def test_m5_tampered_historical_file_fails(tmp_path):
    payload = _load_manifest()
    files = payload["files"]
    versions = tmp_path / "versions"
    versions.mkdir()
    for name in files:
        shutil.copy2(VERSIONS_DIR / name, versions / name)
    target = "080_phase1_reliability_closure.py"
    path = versions / target
    path.write_bytes(path.read_bytes() + b"\n# tampered\n")
    try:
        _assert_historical_bytes(versions, files)
    except AssertionError as exc:
        assert target in str(exc)
        assert "sha256 mismatch" in str(exc)
    else:
        raise AssertionError("tampered historical migration did not fail checksum verification")


@pytest.mark.timeout(180)
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
            _assert_post_080_schema(conn)
        engine.dispose()

        _alembic(isolated, "downgrade", "081_phase2_screening_decisions")
        engine = create_engine(isolated)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "081_phase2_screening_decisions"
            assert "training_runs" not in inspect(conn).get_table_names()
            assert "worker_heartbeats" not in inspect(conn).get_table_names()
            idemp_cols = {c["name"] for c in inspect(conn).get_columns("idempotency_keys")}
            assert "state" not in idemp_cols
        engine.dispose()

        _alembic(isolated, "upgrade", HEAD)
        engine = create_engine(isolated)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD
            _assert_post_080_schema(conn)
        engine.dispose()
    finally:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
        admin.dispose()
