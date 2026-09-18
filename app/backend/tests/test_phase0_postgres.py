"""PostgreSQL-only Phase 0 concurrency proofs.

Requires PHASE0_POSTGRES_URL (never hard-code credentials).
Uses independent SQLAlchemy sessions per worker; no process mutex.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.backend.models.db_models import BillingEvent, Invoice, InvoiceYearCounter, Tenant
from app.backend.services.billing.invoice_service import (
    create_invoice_from_payment,
    generate_invoice_number,
)
from app.backend.services.billing import webhook_processor as wp
from app.backend.services.billing.webhook_processor import process_webhook_event

pytestmark = pytest.mark.skipif(
    not os.environ.get("PHASE0_POSTGRES_URL"),
    reason="PostgreSQL concurrency proof requires PHASE0_POSTGRES_URL",
)


@pytest.fixture(scope="module")
def pg_engine():
    url = os.environ["PHASE0_POSTGRES_URL"]
    engine = create_engine(url, pool_size=20, max_overflow=40)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


@pytest.fixture()
def PgSession(pg_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


@pytest.fixture()
def pg_tenant(PgSession):
    db = PgSession()
    try:
        slug = f"p0-pg-{uuid.uuid4().hex[:16]}"
        t = Tenant(name="P0PG", slug=slug[:80], subscription_status="active")
        db.add(t)
        db.commit()
        db.refresh(t)
        yield t.id
    finally:
        db.close()


class TestPostgresWebhookConcurrency:
    def test_fifty_duplicate_deliveries_one_side_effect(self, PgSession, pg_tenant):
        event_id = f"evt-pg-50-{pg_tenant}"
        barrier = threading.Barrier(50)
        calls = []
        errors = []
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]

        def handler(db_sess, data, raw):
            calls.append(1)
            db_sess.add(Invoice(
                tenant_id=pg_tenant,
                invoice_number=f"INV-SIDE-{event_id[-12:]}",
                status="paid",
                amount=1,
                currency="usd",
            ))

        wp._HANDLER_MAP[key] = handler

        def worker():
            sess = PgSession()
            try:
                barrier.wait(timeout=30)
                process_webhook_event(
                    sess, provider="stripe", event_type="invoice.paid",
                    data={}, raw_payload="{}", event_id=event_id,
                )
            except Exception as exc:
                errors.append(exc)
                sess.rollback()
            finally:
                sess.close()

        try:
            threads = [threading.Thread(target=worker) for _ in range(50)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        finally:
            wp._HANDLER_MAP[key] = original

        assert not errors, errors
        assert len(calls) == 1
        db = PgSession()
        try:
            rows = db.query(BillingEvent).filter_by(provider="stripe", event_id=event_id).all()
            assert len(rows) == 1
            assert rows[0].result == "success"
            assert db.query(Invoice).filter_by(invoice_number=f"INV-SIDE-{event_id[-12:]}").count() == 1
        finally:
            db.close()

    def test_serial_duplicate(self, PgSession, pg_tenant):
        event_id = f"evt-pg-serial-{pg_tenant}"
        calls = []
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]
        wp._HANDLER_MAP[key] = lambda db_sess, data, raw: calls.append(1)
        db = PgSession()
        try:
            r1 = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id=event_id,
            )
            r2 = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id=event_id,
            )
        finally:
            wp._HANDLER_MAP[key] = original
            db.close()
        assert r1["processed"] is True
        assert r2["reason"] == "duplicate"
        assert calls == [1]

    def test_failed_then_retry(self, PgSession):
        event_id = f"evt-pg-retry-{os.getpid()}"
        state = {"n": 0}
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]

        def handler(db_sess, data, raw):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("transient")

        wp._HANDLER_MAP[key] = handler
        db = PgSession()
        try:
            first = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id=event_id,
            )
            second = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id=event_id,
            )
            row = db.query(BillingEvent).filter_by(provider="stripe", event_id=event_id).one()
        finally:
            wp._HANDLER_MAP[key] = original
            db.close()
        assert first["reason"] == "error"
        assert second["processed"] is True
        assert row.result == "success"

    def test_rollback_before_commit(self, PgSession, pg_tenant):
        event_id = f"evt-pg-rb-{pg_tenant}"
        number = f"INV-RB-{pg_tenant}"
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]

        def handler(db_sess, data, raw):
            db_sess.add(Invoice(
                tenant_id=pg_tenant, invoice_number=number,
                status="paid", amount=7, currency="usd",
            ))
            db_sess.flush()
            raise RuntimeError("boom")

        wp._HANDLER_MAP[key] = handler
        db = PgSession()
        try:
            out = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id=event_id,
            )
            assert out["reason"] == "error"
            assert db.query(Invoice).filter_by(invoice_number=number).count() == 0
            ev = db.query(BillingEvent).filter_by(provider="stripe", event_id=event_id).one()
            assert ev.result == "error"
        finally:
            wp._HANDLER_MAP[key] = original
            db.close()


class TestPostgresInvoiceConcurrency:
    def test_one_hundred_unique_allocations(self, PgSession, pg_tenant):
        year = 2098
        db = PgSession()
        try:
            db.query(InvoiceYearCounter).filter_by(year=year).delete()
            db.commit()
        finally:
            db.close()

        barrier = threading.Barrier(100)
        numbers = []
        errors = []

        def worker():
            sess = PgSession()
            try:
                barrier.wait(timeout=30)
                num = generate_invoice_number(sess, year=year)
                sess.add(Invoice(
                    tenant_id=pg_tenant, invoice_number=num,
                    status="paid", amount=1, currency="usd",
                ))
                sess.commit()
                numbers.append(num)
            except Exception as exc:
                errors.append(exc)
                sess.rollback()
            finally:
                sess.close()

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        assert not errors, errors[:5]
        assert len(numbers) == 100
        assert len(set(numbers)) == 100

    def test_provider_invoice_concurrent_one_row(self, PgSession, pg_tenant):
        barrier = threading.Barrier(8)
        ids = []
        errors = []

        def worker():
            sess = PgSession()
            try:
                barrier.wait(timeout=15)
                inv = create_invoice_from_payment(
                    sess, tenant_id=pg_tenant, amount=99,
                    payment_provider="stripe", provider_invoice_id=f"in_pg_{pg_tenant}",
                )
                sess.commit()
                ids.append(inv.id)
            except Exception as exc:
                errors.append(exc)
                sess.rollback()
            finally:
                sess.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors
        assert len(set(ids)) == 1
        db = PgSession()
        try:
            assert db.query(Invoice).filter_by(
                payment_provider="stripe",
                provider_invoice_id=f"in_pg_{pg_tenant}",
            ).count() == 1
        finally:
            db.close()
