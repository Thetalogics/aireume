"""Logical backup/restore proof against real PostgreSQL when configured."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.backend.tests.reliability_env import require_postgres


def _run(cmd, **kwargs):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        class _Missing:
            returncode = 1
            stdout = ""
            stderr = "not found"
        if kwargs.get("check"):
            raise
        return _Missing()


def _pg_bin(tool: str) -> list[str] | None:
    if _run([tool, "--version"]).returncode == 0:
        return [tool]
    if _run(["docker", "exec", "aria-phase2-postgres", tool, "--version"]).returncode == 0:
        return ["docker", "exec", "-e", "PGPASSWORD=aria_test", "aria-phase2-postgres", tool]
    return None


@pytest.mark.timeout(60)
def test_f7_dump_and_restore_known_row():
    url = require_postgres()
    source = create_engine(url)
    with source.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS backup_probe (id int primary key, note text)"))
        conn.execute(
            text(
                "INSERT INTO backup_probe (id, note) VALUES (1, 'restore-me') "
                "ON CONFLICT (id) DO UPDATE SET note=EXCLUDED.note"
            )
        )
    dump_tool = _pg_bin("pg_dump")
    restore_tool = _pg_bin("pg_restore")
    if dump_tool is None or restore_tool is None:
        pytest.fail("pg_dump/pg_restore required for backup restore proof")
    parsed = url.replace("postgresql+psycopg2://", "postgresql://")
    dump = Path(tempfile.gettempdir()) / "aria_backup_probe.dump"
    if dump_tool[0] == "docker":
        remote = "/tmp/aria_backup_probe.dump"
        _run(dump_tool + ["-U", "aria_test", "-d", "aria_phase2_head", "-Fc", "-f", remote, "-t", "backup_probe"], check=True)
        _run(["docker", "cp", f"aria-phase2-postgres:{remote}", str(dump)], check=True)
        _run(restore_tool + ["--list", remote], check=True)
        with source.begin() as conn:
            conn.execute(text("DELETE FROM backup_probe"))
        _run(restore_tool + ["--data-only", "-U", "aria_test", "-d", "aria_phase2_head", "-t", "backup_probe", remote], check=True)
    else:
        _run(dump_tool + ["--dbname", parsed, "-Fc", "-f", str(dump), "-t", "backup_probe"], check=True)
        assert dump.stat().st_size > 0
        _run(restore_tool + ["--list", str(dump)], check=True)
        with source.begin() as conn:
            conn.execute(text("DELETE FROM backup_probe"))
        _run(restore_tool + ["--data-only", "--dbname", parsed, "-t", "backup_probe", str(dump)], check=True)
    with source.connect() as conn:
        note = conn.execute(text("SELECT note FROM backup_probe WHERE id=1")).scalar()
    assert note == "restore-me"
