"""PostgreSQL tests for migration 077 BillingEvent duplicate reconciliation SQL."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.environ.get("PHASE0_POSTGRES_URL"),
    reason="PostgreSQL migration proof requires PHASE0_POSTGRES_URL",
)

_RECONCILE = """
DELETE FROM billing_events_migtest
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY provider, event_id
                   ORDER BY
                       CASE lower(coalesce(result, ''))
                           WHEN 'success' THEN 0
                           WHEN 'processing' THEN 1
                           WHEN 'error' THEN 2
                           WHEN 'failed' THEN 2
                           WHEN 'ignored' THEN 3
                           ELSE 9
                       END,
                       id DESC
               ) AS rn
        FROM billing_events_migtest
        WHERE event_id IS NOT NULL
    ) ranked
    WHERE rn > 1
)
"""


@pytest.fixture(scope="module")
def conn():
    engine = create_engine(os.environ["PHASE0_POSTGRES_URL"])
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS billing_events_migtest"))
        c.execute(text(
            """
            CREATE TABLE billing_events_migtest (
                id integer primary key,
                provider text not null,
                event_id text not null,
                result text
            )
            """
        ))
    yield engine
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS billing_events_migtest"))
    engine.dispose()


def _seed(engine, rows):
    with engine.begin() as c:
        c.execute(text("DELETE FROM billing_events_migtest"))
        for row in rows:
            c.execute(
                text(
                    "INSERT INTO billing_events_migtest (id, provider, event_id, result) "
                    "VALUES (:id, :provider, :event_id, :result)"
                ),
                row,
            )
        c.execute(text(_RECONCILE))
        kept = c.execute(
            text("SELECT id, result FROM billing_events_migtest ORDER BY id")
        ).fetchall()
    return kept


class TestMigration077ReconciliationSQL:
    def test_failed_plus_succeeded(self, conn):
        kept = _seed(conn, [
            {"id": 10, "provider": "stripe", "event_id": "e1", "result": "failed"},
            {"id": 11, "provider": "stripe", "event_id": "e1", "result": "success"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(11, "success")]

    def test_succeeded_plus_failed(self, conn):
        kept = _seed(conn, [
            {"id": 10, "provider": "stripe", "event_id": "e2", "result": "success"},
            {"id": 11, "provider": "stripe", "event_id": "e2", "result": "failed"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(10, "success")]

    def test_two_succeeded(self, conn):
        kept = _seed(conn, [
            {"id": 10, "provider": "stripe", "event_id": "e3", "result": "success"},
            {"id": 11, "provider": "stripe", "event_id": "e3", "result": "success"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(11, "success")]

    def test_received_plus_processing(self, conn):
        kept = _seed(conn, [
            {"id": 1, "provider": "stripe", "event_id": "e4", "result": "received"},
            {"id": 2, "provider": "stripe", "event_id": "e4", "result": "processing"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(2, "processing")]

    def test_two_failed(self, conn):
        kept = _seed(conn, [
            {"id": 4, "provider": "stripe", "event_id": "e5", "result": "error"},
            {"id": 5, "provider": "stripe", "event_id": "e5", "result": "failed"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(5, "failed")]

    def test_three_or_more(self, conn):
        kept = _seed(conn, [
            {"id": 1, "provider": "stripe", "event_id": "e6", "result": "error"},
            {"id": 2, "provider": "stripe", "event_id": "e6", "result": "processing"},
            {"id": 3, "provider": "stripe", "event_id": "e6", "result": "success"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(3, "success")]

    def test_no_duplicates(self, conn):
        kept = _seed(conn, [
            {"id": 9, "provider": "stripe", "event_id": "e7", "result": "success"},
        ])
        assert [(r[0], r[1]) for r in kept] == [(9, "success")]
