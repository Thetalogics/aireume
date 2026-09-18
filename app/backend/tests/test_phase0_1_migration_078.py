"""078 adds actor_id FK when 077 already created the column without one."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("PHASE0_POSTGRES_URL"),
        reason="PostgreSQL migration proof requires PHASE0_POSTGRES_URL",
    ),
    pytest.mark.timeout(0),
]

ROOT = Path(__file__).resolve().parents[3]


def _engine():
    return create_engine(os.environ["PHASE0_POSTGRES_URL"])


def _alembic(*args: str):
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["PHASE0_POSTGRES_URL"]
    env["PYTHONPATH"] = str(ROOT)
    subprocess.check_call(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(ROOT),
        env=env,
    )


def _current() -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["PHASE0_POSTGRES_URL"]
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def _ensure_077():
    cur = _current()
    if "078_phase0_1_audit_corrections" in cur:
        _alembic("downgrade", "077_phase0_core_audit")
    else:
        _alembic("upgrade", "077_phase0_core_audit")


def _has_actor_fk(conn) -> bool:
    return conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.table_constraints
            WHERE constraint_name = 'fk_ai_decision_logs_actor_id_users'
              AND table_name = 'ai_decision_logs'
            """
        )
    ).scalar() == 1


@pytest.fixture(scope="module")
def pg():
    engine = _engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        conn.commit()
    yield engine
    engine.dispose()


def test_077_has_actor_column_without_requiring_fk(pg):
    _ensure_077()
    with pg.connect() as conn:
        col = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ai_decision_logs' AND column_name = 'actor_id'
                """
            )
        ).scalar()
        assert col == 1
        assert not _has_actor_fk(conn)


def test_078_adds_actor_fk_and_delete_sets_null(pg):
    _ensure_077()
    _alembic("upgrade", "078_phase0_1_audit_corrections")
    token = uuid.uuid4().hex[:12]
    with pg.begin() as conn:
        tenant_id = conn.execute(
            text(
                """
                INSERT INTO tenants (
                    name, slug, created_at, subscription_status,
                    analyses_count_this_month, storage_used_bytes,
                    metadata_json, onboarding_completed
                )
                VALUES ('fk078', :s, NOW(), 'active', 0, 0, '{}', false)
                RETURNING id
                """
            ),
            {"s": f"p01-fk078-{token}"},
        ).scalar_one()
        user_id = conn.execute(
            text(
                """
                INSERT INTO users (
                    tenant_id, email, hashed_password, role, is_active, created_at,
                    getting_started_progress, preferences_json, is_platform_admin,
                    mfa_enabled, refresh_epoch, email_verified
                )
                VALUES (:tid, :email, 'x', 'admin', true, NOW(), '{}', '{}', false, false, 0, false)
                RETURNING id
                """
            ),
            {"tid": tenant_id, "email": f"p01fk078{token}@example.com"},
        ).scalar_one()
        log_id = conn.execute(
            text(
                "INSERT INTO ai_decision_logs (tenant_id, actor_id, fallback_used) "
                "VALUES (:tid, :uid, false) RETURNING id"
            ),
            {"tid": tenant_id, "uid": user_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ai_decision_logs (tenant_id, actor_id, fallback_used) "
                "VALUES (:tid, NULL, false)"
            ),
            {"tid": tenant_id},
        )
        assert _has_actor_fk(conn)
    with pg.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO ai_decision_logs (tenant_id, actor_id, fallback_used) "
                    "VALUES (:tid, 2147483646, false)"
                ),
                {"tid": tenant_id},
            )
            conn.commit()
        conn.rollback()
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        remaining = conn.execute(
            text("SELECT actor_id FROM ai_decision_logs WHERE id = :id"),
            {"id": log_id},
        ).scalar_one()
    assert remaining is None
