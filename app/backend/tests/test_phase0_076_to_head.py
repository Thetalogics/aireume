"""PostgreSQL: production-risk upgrade path 076_candidate_merge → head with dirty invoices."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("PHASE0_POSTGRES_URL"),
        reason="PostgreSQL 076→head proof requires PHASE0_POSTGRES_URL",
    ),
    pytest.mark.timeout(0),
]

ROOT = Path(__file__).resolve().parents[3]
START = "076_candidate_merge"
HEAD = "082_reliability_closure"


def _engine():
    return create_engine(os.environ["PHASE0_POSTGRES_URL"])


def _alembic(*args: str, check: bool = True):
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["PHASE0_POSTGRES_URL"]
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(ROOT),
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _revision(conn) -> str | None:
    row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    return None if row is None else row[0]


def _insert_tenant(conn, name: str, slug: str) -> int:
    return conn.execute(
        text(
            """
            INSERT INTO tenants (
                name, slug, created_at, subscription_status,
                analyses_count_this_month, storage_used_bytes,
                metadata_json, onboarding_completed
            )
            VALUES (:name, :slug, NOW(), 'active', 0, 0, '{}', false)
            RETURNING id
            """
        ),
        {"name": name, "slug": slug},
    ).scalar_one()


def _insert_invoice(conn, *, tid, num, amount, provider, pid, issued, paid=None, status="paid"):
    conn.execute(
        text(
            """
            INSERT INTO invoices (
                tenant_id, invoice_number, status, amount, currency,
                payment_provider, provider_invoice_id, issued_at, paid_at
            ) VALUES (
                :tid, :num, :status, :amount, 'usd',
                :provider, :pid, :issued, :paid
            )
            """
        ),
        {
            "tid": tid,
            "num": num,
            "status": status,
            "amount": amount,
            "provider": provider,
            "pid": pid,
            "issued": issued,
            "paid": paid,
        },
    )


def _has_unique_index(conn) -> bool:
    return conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_invoice_provider_invoice_id'")
    ).scalar() == 1


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


def _count(conn, provider, pid) -> int:
    return conn.execute(
        text(
            "SELECT COUNT(*) FROM invoices WHERE payment_provider=:p AND provider_invoice_id=:pid"
        ),
        {"p": provider, "pid": pid},
    ).scalar_one()


@pytest.fixture(scope="module")
def pg():
    from sqlalchemy.engine import make_url

    url = os.environ["PHASE0_POSTGRES_URL"]
    previous_phase0 = os.environ.get("PHASE0_POSTGRES_URL")
    previous_db = os.environ.get("DATABASE_URL")
    parsed = make_url(url)
    dbname = f"aria_p076_{uuid.uuid4().hex[:8]}"
    admin = create_engine(parsed.set(database="postgres"))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE DATABASE {dbname}"))
    isolated = parsed.set(database=dbname).render_as_string(hide_password=False)
    os.environ["PHASE0_POSTGRES_URL"] = isolated
    os.environ["DATABASE_URL"] = isolated
    engine = create_engine(isolated)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        _alembic("upgrade", START)
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
        admin.dispose()
        if previous_phase0 is not None:
            os.environ["PHASE0_POSTGRES_URL"] = previous_phase0
        if previous_db is not None:
            os.environ["DATABASE_URL"] = previous_db


def _reset_076(pg):
    current = _alembic("current", check=False).stdout
    if HEAD in current or "082_" in current or "081_" in current or "077_phase0_core_audit" in current:
        _alembic("downgrade", START)
    else:
        _alembic("upgrade", START)
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM invoices WHERE invoice_number LIKE 'INV-P01-%'"))
        conn.execute(text("DROP INDEX IF EXISTS uq_invoice_provider_invoice_id"))


def test_alembic_has_exactly_one_head():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from alembic.config import Config; from alembic.script import ScriptDirectory; "
         "s=ScriptDirectory.from_config(Config('alembic.ini')); "
         "heads=s.get_heads(); print(len(heads)); print(heads[0] if heads else '')"],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines[0] == "1"
    assert lines[1] == HEAD


def test_clean_076_to_head(pg):
    _reset_076(pg)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        assert _revision(conn) == HEAD
        assert _has_unique_index(conn)
        assert _has_actor_fk(conn)


def test_equivalent_stripe_duplicates_076_to_head(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-stripe-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "s", f"p01-s-{token}")
        _insert_invoice(conn, tid=tid, num=f"INV-P01-S-1-{token}", amount=4900,
                        provider="stripe", pid=pid, issued=now, paid=now)
        _insert_invoice(conn, tid=tid, num=f"INV-P01-S-2-{token}", amount=4900,
                        provider="stripe", pid=pid, issued=now, paid=now)
        assert _revision(conn) == START
        assert not _has_unique_index(conn)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        assert _revision(conn) == HEAD
        assert _count(conn, "stripe", pid) == 1
        assert _has_unique_index(conn)
        assert _has_actor_fk(conn)


def test_equivalent_razorpay_duplicates_076_to_head(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-rzp-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "r", f"p01-r-{token}")
        _insert_invoice(conn, tid=tid, num=f"INV-P01-R-1-{token}", amount=9900,
                        provider="razorpay", pid=pid, issued=now, paid=now)
        _insert_invoice(conn, tid=tid, num=f"INV-P01-R-2-{token}", amount=9900,
                        provider="razorpay", pid=pid, issued=now, paid=now)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        assert _count(conn, "razorpay", pid) == 1
        assert _revision(conn) == HEAD


def test_cross_provider_same_external_id_retained(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-cross-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "x", f"p01-x-{token}")
        _insert_invoice(conn, tid=tid, num=f"INV-P01-X-1-{token}", amount=100,
                        provider="stripe", pid=pid, issued=now, paid=now)
        _insert_invoice(conn, tid=tid, num=f"INV-P01-X-2-{token}", amount=100,
                        provider="razorpay", pid=pid, issued=now, paid=now)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        assert _count(conn, "stripe", pid) == 1
        assert _count(conn, "razorpay", pid) == 1


def test_null_provider_invoice_ids_unaffected(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "n", f"p01-n-{token}")
        _insert_invoice(conn, tid=tid, num=f"INV-P01-N-1-{token}", amount=50,
                        provider="manual", pid=None, issued=now, paid=now)
        _insert_invoice(conn, tid=tid, num=f"INV-P01-N-2-{token}", amount=50,
                        provider="manual", pid=None, issued=now, paid=now)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE :p"),
            {"p": f"INV-P01-N-%-{token}"},
        ).scalar_one()
        assert n == 2


def test_three_equivalent_duplicates_one_canonical(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-three-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "t", f"p01-t-{token}")
        for i in range(3):
            _insert_invoice(conn, tid=tid, num=f"INV-P01-T-{i}-{token}", amount=1200,
                            provider="stripe", pid=pid, issued=now, paid=now)
    _alembic("upgrade", "head")
    with pg.connect() as conn:
        assert _count(conn, "stripe", pid) == 1


def test_conflicting_amount_aborts_without_data_loss(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-amt-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        tid = _insert_tenant(conn, "ca", f"p01-ca-{token}")
        _insert_invoice(conn, tid=tid, num=f"INV-P01-CA-1-{token}", amount=100,
                        provider="stripe", pid=pid, issued=now, paid=now)
        _insert_invoice(conn, tid=tid, num=f"INV-P01-CA-2-{token}", amount=999,
                        provider="stripe", pid=pid, issued=now, paid=now)
        assert _revision(conn) == START
    proc = _alembic("upgrade", "head", check=False)
    assert proc.returncode != 0
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "stripe" in combined.lower()
    assert pid in combined
    assert "cannot be auto-merged" in combined.lower() or "conflict" in combined.lower()
    with pg.connect() as conn:
        assert _revision(conn) == START
        assert _count(conn, "stripe", pid) == 2
        assert not _has_unique_index(conn)


def test_conflicting_tenant_aborts_without_data_loss(pg):
    _reset_076(pg)
    token = uuid.uuid4().hex[:12]
    pid = f"p01-ten-{token}"
    now = datetime.now(timezone.utc)
    with pg.begin() as conn:
        a = _insert_tenant(conn, "a", f"p01-cta-{token}")
        b = _insert_tenant(conn, "b", f"p01-ctb-{token}")
        _insert_invoice(conn, tid=a, num=f"INV-P01-CT-1-{token}", amount=100,
                        provider="stripe", pid=pid, issued=now, paid=now)
        _insert_invoice(conn, tid=b, num=f"INV-P01-CT-2-{token}", amount=100,
                        provider="stripe", pid=pid, issued=now, paid=now)
    proc = _alembic("upgrade", "head", check=False)
    assert proc.returncode != 0
    with pg.connect() as conn:
        assert _revision(conn) == START
        assert _count(conn, "stripe", pid) == 2
        assert not _has_unique_index(conn)


def test_actor_fk_after_076_to_head(pg):
    _reset_076(pg)
    _alembic("upgrade", "head")
    token = uuid.uuid4().hex[:12]
    with pg.begin() as conn:
        tenant_id = _insert_tenant(conn, "fk", f"p01-fk-{token}")
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
            {"tid": tenant_id, "email": f"p01fk{token}@example.com"},
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
