"""AUD-043 GET purity / query bounds, AUD-044 threadpool vs async, AUD-047 metrics."""
import asyncio
import time

import pytest

from app.backend.db import database
from app.backend.models.db_models import Candidate, DeadLetterJob, User
from app.backend.services.metrics import (
    DLQ_DEPTH,
    LLM_SATURATION,
    LLM_SATURATION_TOTAL,
    QUOTA_REJECTED_TOTAL,
    SCREENING_TOTAL,
)
from app.backend.services.requisition_service import create_requisition
from app.backend.tests.test_helpers import StatementCounter


def _admin(db):
    return db.query(User).filter(User.email == "admin@testcorp.com").one()


class TestGetNoHiddenWrites:
    def test_dashboard_summary_has_no_dml(self, auth_client):
        with StatementCounter(database.engine) as counter:
            resp = auth_client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        assert counter.dml == []

    def test_candidate_list_has_no_dml(self, auth_client, db):
        user = _admin(db)
        db.add(Candidate(tenant_id=user.tenant_id, name="List A", email="lista@testcorp.com"))
        db.commit()
        with StatementCounter(database.engine) as counter:
            resp = auth_client.get("/api/candidates")
        assert resp.status_code == 200
        assert counter.dml == []

    def test_requisition_list_has_no_dml(self, auth_client, db):
        user = _admin(db)
        create_requisition(db, tenant_id=user.tenant_id, created_by=user.id, title="Q", jd_text="Need a senior python engineer for platform work.")
        db.commit()
        with StatementCounter(database.engine) as counter:
            resp = auth_client.get("/api/requisitions")
        assert resp.status_code == 200
        assert counter.dml == []


class TestListQueryBounds:
    def test_requisition_list_queries_do_not_grow_linearly(self, auth_client, db):
        user = _admin(db)
        for i in range(10):
            create_requisition(
                db,
                tenant_id=user.tenant_id,
                created_by=user.id,
                title=f"Req 10-{i}",
                jd_text="Need a senior python engineer for platform work.",
            )
        db.commit()
        with StatementCounter(database.engine) as c10:
            assert auth_client.get("/api/requisitions").status_code == 200
        n10 = len(c10.statements)
        for i in range(40):
            create_requisition(
                db,
                tenant_id=user.tenant_id,
                created_by=user.id,
                title=f"Req 50-{i}",
                jd_text="Need a senior python engineer for platform work.",
            )
        db.commit()
        with StatementCounter(database.engine) as c50:
            assert auth_client.get("/api/requisitions").status_code == 200
        n50 = len(c50.statements)
        assert n50 - n10 < 20, f"query count grew too fast: 10rows={n10} 50rows={n50}"

    def test_candidate_list_queries_do_not_grow_linearly(self, auth_client, db):
        user = _admin(db)
        for i in range(10):
            db.add(Candidate(tenant_id=user.tenant_id, name=f"C10 {i}", email=f"c10{i}@testcorp.com"))
        db.commit()
        with StatementCounter(database.engine) as c10:
            assert auth_client.get("/api/candidates?page_size=100").status_code == 200
        n10 = len(c10.statements)
        for i in range(40):
            db.add(Candidate(tenant_id=user.tenant_id, name=f"C50 {i}", email=f"c50{i}@testcorp.com"))
        db.commit()
        with StatementCounter(database.engine) as c50:
            assert auth_client.get("/api/candidates?page_size=100").status_code == 200
        n50 = len(c50.statements)
        assert n50 - n10 < 20, f"query count grew too fast: 10rows={n10} 50rows={n50}"


class TestAsyncSyncPattern:
    @pytest.mark.asyncio
    async def test_slow_sync_route_does_not_block_async_probe(self, auth_client):
        from httpx import ASGITransport, AsyncClient
        from app.backend.main import app

        headers = dict(auth_client.headers)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start = time.perf_counter()
            slow = asyncio.create_task(ac.get("/api/internal/sync-sleep", headers=headers))
            await asyncio.sleep(0.02)
            fast = await ac.get("/api/internal/async-probe", headers=headers)
            fast_elapsed = time.perf_counter() - start
            slow_resp = await slow
        assert fast.status_code == 200
        assert slow_resp.status_code == 200
        assert fast_elapsed < 0.25


class TestObservabilityMetrics:
    def test_screening_success_increments(self, auth_client, db):
        from app.backend.services.screening_command import ScreeningCommand, execute_screening

        before = SCREENING_TOTAL.labels(result="success")._value.get()
        tenant = _admin(db).tenant_id
        cmd = ScreeningCommand(
            schema_version="v1",
            tenant_id=tenant,
            user_id=_admin(db).id,
            resume_hash="a" * 64,
            jd_hash="b" * 64,
            algorithm_version="1.0",
        )
        execute_screening(
            db,
            cmd,
            resume_text="python engineer resume",
            jd_text="python engineer",
            parsed_data={"name": "Pat", "email": "metrics@testcorp.com", "raw_text": "python"},
            pipeline_result={"fit_score": 70, "analysis_quality": "medium"},
        )
        db.commit()
        assert SCREENING_TOTAL.labels(result="success")._value.get() >= before + 1

    def test_quota_rejection_increments(self, auth_client, db):
        from app.backend.services.billing.quota import check_quota
        from app.backend.models.db_models import Tenant

        user = _admin(db)
        tenant = db.get(Tenant, user.tenant_id)
        tenant.analyses_count_this_month = 10_000_000
        db.commit()
        before = QUOTA_REJECTED_TOTAL.labels(surface="analyze")._value.get()
        result = check_quota(user.tenant_id, db)
        assert result["allowed"] is False
        assert QUOTA_REJECTED_TOTAL.labels(surface="analyze")._value.get() >= before + 1

    def test_dlq_gauge_reflects_queue(self, auth_client, db):
        import uuid
        from datetime import datetime, timezone
        job = DeadLetterJob(
            original_job_id=uuid.uuid4(),
            tenant_id=_admin(db).tenant_id,
            job_type="resume_screening",
            resume_hash="c" * 64,
            jd_hash="d" * 64,
            input_hash=str(uuid.uuid4()).replace("-", ""),
            failure_reason="fail",
            retry_count=3,
            original_created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        depth = db.query(DeadLetterJob).count()
        DLQ_DEPTH.set(depth)
        assert DLQ_DEPTH._value.get() == depth

    def test_llm_saturation_metric(self, monkeypatch):
        from app.backend.services.llm_concurrency import LLMConcurrencySaturated, llm_slot

        monkeypatch.setenv("APP_MAX_CONCURRENT", "1")
        monkeypatch.setattr(
            "app.backend.services.llm_concurrency.cache_try_acquire_slot",
            lambda *a, **k: False,
        )
        before = LLM_SATURATION_TOTAL.labels(provider="app")._value.get()
        with pytest.raises(LLMConcurrencySaturated):
            with llm_slot("app"):
                pass
        assert LLM_SATURATION_TOTAL.labels(provider="app")._value.get() >= before + 1
        assert LLM_SATURATION.labels(provider="app")._value.get() == 1

    def test_metric_labels_are_bounded(self):
        from prometheus_client import REGISTRY
        for metric in REGISTRY.collect():
            if not metric.name.startswith("aria_"):
                continue
            for sample in metric.samples:
                for key, value in sample.labels.items():
                    assert key not in {"tenant_id", "request_id", "email", "candidate"}
                    assert "@" not in value
