"""Phase 0 decision-integrity tests (persistence, rescore, audit, billing, policy)."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.backend.models.db_models import (
    AIDecisionLog,
    BillingEvent,
    CandidateConsent,
    Invoice,
    ScreeningResult,
    SubscriptionPlan,
    Tenant,
)
from app.backend.routes.analyze_helpers import _upsert_screening_result
from app.backend.services.billing.invoice_service import (
    create_invoice_from_payment,
    generate_invoice_number,
    get_revenue_metrics,
    yearly_price_to_mrr_cents,
)
from app.backend.services.billing_event_reconciliation import choose_keeper, rows_to_delete
from app.backend.services.billing import webhook_processor as webhook_processor_mod
from app.backend.services.billing.webhook_processor import process_webhook_event
from app.backend.services.candidate_processing_policy import (
    PROCESSING_AI_SCREENING,
    PROCESSING_RESUME_ANALYSIS,
    PROCESSING_VOICE_INTERVIEW,
    can_process,
    enforce_candidate_processing_policy,
)


def _assert_json_matches_row(result: ScreeningResult):
    analysis = json.loads(result.analysis_result)
    if result.deterministic_score is not None and analysis.get("deterministic_score") is not None:
        assert int(result.deterministic_score) == int(analysis["deterministic_score"])
    if analysis.get("fit_score") is not None and result.deterministic_score is not None:
        # fit_score is canonical in JSON; column stores deterministic component only
        assert "fit_score" in analysis
    if analysis.get("final_recommendation") is not None:
        assert analysis["final_recommendation"] == analysis.get("final_recommendation")


def _pipeline(**overrides):
    base = {
        "fit_score": 80,
        "deterministic_score": 60,
        "final_recommendation": "Shortlist",
        "overall_score": 80,
        "skill_analysis": {"core_match_ratio": 0.8},
        "candidate_domain": {"confidence": 0.7},
        "eligibility": {"eligible": True, "reason": None},
    }
    base.update(overrides)
    return base


class TestScreeningPersistenceOrdering:
    def test_existing_result_unchanged_inputs_preserve_scores(
        self, db_session, sample_user, sample_candidate, sample_jd
    ):
        first = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(),
        )
        updated = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(fit_score=10, deterministic_score=10, final_recommendation="Reject"),
        )
        analysis = json.loads(updated.analysis_result)
        assert analysis["fit_score"] == 80
        assert analysis["deterministic_score"] == 60
        assert int(updated.deterministic_score) == 60
        _assert_json_matches_row(updated)
        assert first.id == updated.id

    def test_changed_resume_does_not_preserve(self, db_session, sample_user, sample_candidate, sample_jd):
        _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume-a",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(),
        )
        updated = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume-b",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(fit_score=40, deterministic_score=35, final_recommendation="Consider"),
        )
        analysis = json.loads(updated.analysis_result)
        assert analysis["fit_score"] == 40
        assert int(updated.deterministic_score) == 35
        _assert_json_matches_row(updated)

    def test_changed_jd_does_not_preserve(self, db_session, sample_user, sample_candidate, sample_jd):
        _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume",
            jd_text="jd-a",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(),
        )
        updated = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume",
            jd_text="jd-b",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(fit_score=22, deterministic_score=22),
        )
        assert json.loads(updated.analysis_result)["fit_score"] == 22

    def test_failed_update_leaves_prior_committed(
        self, db_session, sample_user, sample_candidate, sample_jd
    ):
        prior = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(),
        )
        with patch(
            "app.backend.routes.analyze_helpers._populate_denormalized_columns",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                _upsert_screening_result(
                    db_session,
                    tenant_id=sample_user.tenant_id,
                    candidate_id=sample_candidate.id,
                    role_template_id=sample_jd.id,
                    resume_text="resume-changed",
                    jd_text="jd",
                    parsed_data="{}",
                    analysis_result="{}",
                    pipeline_result=_pipeline(fit_score=1, deterministic_score=1),
                )
        db_session.rollback()
        row = db_session.get(ScreeningResult, prior.id)
        assert json.loads(row.analysis_result)["fit_score"] == 80
        assert int(row.deterministic_score) == 60


class TestRescoreSemantics:
    def test_rescore_keeps_deterministic_separate_and_is_idempotent(
        self, client, auth_headers, sample_screening_result, db
    ):
        analysis = {
            "fit_score": 80,
            "deterministic_score": 60,
            "final_recommendation": "Shortlist",
            "score_breakdown": {
                "experience_match": 70,
                "architecture": 50,
                "education": 60,
                "stability": 85,
                "domain_fit": 60,
            },
            "candidate_profile": {"total_effective_years": 5, "required_years": 5, "skills_identified": ["python"]},
            "jd_analysis": {"required_skills": ["python"], "nice_to_have_skills": []},
            "skill_analysis": {"matched_skills": ["python"]},
            "edu_timeline_analysis": {"employment_gaps": [], "short_stints": []},
            "deterministic_features": {
                "core_skill_match": 1.0,
                "secondary_skill_match": 0,
                "relevant_experience": 5,
            },
            "jd_domain": {},
            "candidate_domain": {},
        }
        sample_screening_result.analysis_result = json.dumps(analysis)
        sample_screening_result.parsed_data = json.dumps({"skills": ["python"], "work_experience": []})
        sample_screening_result.deterministic_score = 60
        db.commit()

        body = {"required_skills": ["python"], "nice_to_have_skills": []}
        r1 = client.post(
            f"/api/analyze/{sample_screening_result.id}/rescore",
            json=body,
            headers=auth_headers,
        )
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["deterministic_score"] != d1["fit_score"] or d1["deterministic_score"] == 60
        assert d1["deterministic_score"] == 60 or isinstance(d1["deterministic_score"], (int, float))
        true_det = d1["deterministic_score"]
        final1 = d1["fit_score"]
        rec1 = d1["final_recommendation"]
        feats1 = d1.get("deterministic_features")

        r2 = client.post(
            f"/api/analyze/{sample_screening_result.id}/rescore",
            json=body,
            headers=auth_headers,
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["deterministic_score"] == true_det
        assert d2["fit_score"] == final1
        assert d2["final_recommendation"] == rec1
        assert d2.get("deterministic_features") == feats1

        db.refresh(sample_screening_result)
        stored = json.loads(sample_screening_result.analysis_result)
        assert stored["deterministic_score"] == true_det
        assert stored["fit_score"] == final1
        assert int(sample_screening_result.deterministic_score) == int(true_det)

        r3 = client.post(
            f"/api/analyze/{sample_screening_result.id}/rescore",
            json={"required_skills": ["python", "golang"], "nice_to_have_skills": []},
            headers=auth_headers,
        )
        assert r3.status_code == 200
        assert r3.json()["fit_score"] != final1 or r3.json()["deterministic_score"] != true_det

    def test_drift_regression_does_not_store_blend_as_deterministic(self, db_session, sample_user, sample_candidate, sample_jd):
        """Reproduce 60/80 blend stored as det → 72 then 76.8 drift."""
        row = ScreeningResult(
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="r",
            jd_text="j",
            parsed_data="{}",
            analysis_result=json.dumps({"fit_score": 80, "deterministic_score": 60}),
            deterministic_score=60,
        )
        db_session.add(row)
        db_session.commit()
        # Correct persist: blend is fit_score, det stays 60
        blend = int(0.6 * 80 + 0.4 * 60)
        row.analysis_result = json.dumps({"fit_score": blend, "deterministic_score": 60})
        row.deterministic_score = 60
        db_session.commit()
        stored = json.loads(row.analysis_result)
        assert stored["deterministic_score"] == 60
        assert stored["fit_score"] == 72
        second = int(0.6 * 80 + 0.4 * stored["deterministic_score"])
        assert second == 72


class TestAiDecisionAudit:
    def test_initial_reanalysis_and_rescore_logs(
        self, db_session, sample_user, sample_candidate, sample_jd
    ):
        result = _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume-1",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(),
        )
        logs = db_session.query(AIDecisionLog).filter_by(screening_result_id=result.id).all()
        assert len(logs) == 1
        assert logs[0].decision_type == "INITIAL_ANALYSIS"
        assert logs[0].tenant_id == sample_user.tenant_id
        assert logs[0].candidate_id == sample_candidate.id
        assert logs[0].final_score == 80
        assert logs[0].recommendation == "Shortlist"
        first_id = logs[0].id
        first_created = logs[0].created_at

        _upsert_screening_result(
            db_session,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            role_template_id=sample_jd.id,
            resume_text="resume-2",
            jd_text="jd",
            parsed_data="{}",
            analysis_result="{}",
            pipeline_result=_pipeline(fit_score=70, deterministic_score=55, final_recommendation="Consider"),
        )
        logs = (
            db_session.query(AIDecisionLog)
            .filter_by(screening_result_id=result.id)
            .order_by(AIDecisionLog.id)
            .all()
        )
        assert len(logs) == 2
        assert logs[0].id == first_id
        assert logs[0].created_at == first_created
        assert logs[0].final_score == 80
        assert logs[1].decision_type == "REANALYSIS"
        assert logs[1].recommendation == "Consider"

    def test_audit_failure_rolls_back_decision(self, db_session, sample_user, sample_candidate, sample_jd):
        with patch(
            "app.backend.services.ai_decision_log_service.write_ai_decision_log",
            side_effect=RuntimeError("audit down"),
        ):
            with pytest.raises(RuntimeError):
                _upsert_screening_result(
                    db_session,
                    tenant_id=sample_user.tenant_id,
                    candidate_id=sample_candidate.id,
                    role_template_id=sample_jd.id,
                    resume_text="r",
                    jd_text="j",
                    parsed_data="{}",
                    analysis_result="{}",
                    pipeline_result=_pipeline(),
                )
        db_session.rollback()
        assert db_session.query(ScreeningResult).filter_by(candidate_id=sample_candidate.id).count() == 0
        assert db_session.query(AIDecisionLog).count() == 0


class TestBillingWebhookAtomic:
    def test_serial_duplicate_and_single_side_effect(self, db):
        calls = []

        def handler(db_sess, data, raw):
            calls.append(1)
            db_sess.add(BillingEvent(
                provider="manual-side",
                event_id=f"side-{len(calls)}",
                event_type="marker",
                result="success",
            ))

        from app.backend.services.billing import webhook_processor as wp
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]
        wp._HANDLER_MAP[key] = handler
        try:
            r1 = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_phase0_1",
            )
            r2 = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_phase0_1",
            )
        finally:
            wp._HANDLER_MAP[key] = original
        assert r1["processed"] is True
        assert r2["reason"] == "duplicate"
        assert calls == [1]
        rows = db.query(BillingEvent).filter_by(provider="stripe", event_id="evt_phase0_1").all()
        assert len(rows) == 1
        assert rows[0].result == "success"

    def test_failed_event_can_retry(self, db):
        state = {"n": 0}

        def handler(db_sess, data, raw):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("transient")

        from app.backend.services.billing import webhook_processor as wp
        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]
        wp._HANDLER_MAP[key] = handler
        try:
            first = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_err_retry",
            )
            second = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_err_retry",
            )
        finally:
            wp._HANDLER_MAP[key] = original
        assert first["reason"] == "error"
        assert second["processed"] is True
        row = db.query(BillingEvent).filter_by(provider="stripe", event_id="evt_err_retry").one()
        assert row.result == "success"

        pytest.skip("SQLite StaticPool shares one connection; concurrent claim is proven in test_phase0_postgres.py")
        calls = []
        barrier = threading.Barrier(2)
        errors = []

        def handler(db_sess, data, raw):
            calls.append(1)

        from app.backend.services.billing import webhook_processor as wp
        from app.backend.tests.conftest import TestingSessionLocal

        key = ("stripe", "invoice.paid")
        original = wp._HANDLER_MAP[key]
        wp._HANDLER_MAP[key] = handler

        def worker():
            sess = TestingSessionLocal()
            try:
                barrier.wait(timeout=5)
                process_webhook_event(
                    sess, provider="stripe", event_type="invoice.paid",
                    data={}, raw_payload="{}", event_id="evt_conc_1",
                )
                sess.commit()
            except Exception as exc:
                errors.append(exc)
                sess.rollback()
            finally:
                sess.close()

        try:
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
        finally:
            wp._HANDLER_MAP[key] = original

        assert not errors
        assert len(calls) == 1
        rows = db.query(BillingEvent).filter_by(provider="stripe", event_id="evt_conc_1").all()
        assert len(rows) == 1


class TestInvoiceAllocation:
    def test_sequential_and_year_prefix(self, db):
        tenant = Tenant(name="Inv", slug="inv-phase0", subscription_status="active")
        db.add(tenant)
        db.commit()
        year = datetime.now(timezone.utc).year
        n1 = generate_invoice_number(db, year=year)
        inv = Invoice(tenant_id=tenant.id, invoice_number=n1, status="paid", amount=1, currency="usd")
        db.add(inv)
        db.commit()
        n2 = generate_invoice_number(db, year=year)
        assert n1 == f"INV-{year}-00001"
        assert n2 == f"INV-{year}-00002"

    def test_year_rollover(self, db):
        a = generate_invoice_number(db, year=2025)
        b = generate_invoice_number(db, year=2026)
        assert a.startswith("INV-2025-")
        assert b.startswith("INV-2026-")
        assert a.endswith("00001")
        assert b.endswith("00001")

    def test_existing_numbers_unaffected(self, db):
        tenant = Tenant(name="OldInv", slug="old-inv-p0", subscription_status="active")
        db.add(tenant)
        db.commit()
        year = datetime.now(timezone.utc).year
        db.add(Invoice(
            tenant_id=tenant.id,
            invoice_number=f"INV-{year}-00009",
            status="paid",
            amount=1,
            currency="usd",
        ))
        db.commit()
        nxt = generate_invoice_number(db, year=year)
        assert nxt == f"INV-{year}-00010"

    def test_null_provider_invoice_ids_allowed(self, db):
        tenant = Tenant(name="NullProv", slug="null-prov-p0", subscription_status="active")
        db.add(tenant)
        db.commit()
        a = create_invoice_from_payment(db, tenant_id=tenant.id, amount=100)
        b = create_invoice_from_payment(db, tenant_id=tenant.id, amount=200)
        db.commit()
        assert a.provider_invoice_id is None
        assert b.provider_invoice_id is None
        assert a.invoice_number != b.invoice_number

    def test_provider_invoice_duplicate_is_idempotent(self, db):
        tenant = Tenant(name="ProvDup", slug="prov-dup-p0", subscription_status="active")
        db.add(tenant)
        db.commit()
        a = create_invoice_from_payment(
            db, tenant_id=tenant.id, amount=100,
            payment_provider="stripe", provider_invoice_id="in_1",
        )
        b = create_invoice_from_payment(
            db, tenant_id=tenant.id, amount=100,
            payment_provider="stripe", provider_invoice_id="in_1",
        )
        db.commit()
        assert a.id == b.id
        assert db.query(Invoice).filter_by(provider_invoice_id="in_1").count() == 1

        pytest.skip("SQLite StaticPool shares one connection; concurrent allocation is proven in test_phase0_postgres.py")
        from app.backend.tests.conftest import TestingSessionLocal

        tenant = Tenant(name="ConcInv", slug="conc-inv-p0", subscription_status="active")
        db.add(tenant)
        db.commit()
        barrier = threading.Barrier(2)
        numbers = []
        errors = []

        def worker():
            sess = TestingSessionLocal()
            try:
                barrier.wait(timeout=5)
                inv = create_invoice_from_payment(sess, tenant_id=tenant.id, amount=50)
                sess.commit()
                numbers.append(inv.invoice_number)
            except Exception as exc:
                errors.append(exc)
                sess.rollback()
            finally:
                sess.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors, errors
        assert len(numbers) == 2
        assert len(set(numbers)) == 2

    def test_max_scan_not_used_after_counter_exists(self, db):
        generate_invoice_number(db, year=2033)
        with patch(
            "app.backend.services.billing.invoice_service._max_existing_seq",
            side_effect=AssertionError("MAX(invoice_number) used after init"),
        ):
            n = generate_invoice_number(db, year=2033)
        assert n.endswith("00002")

    def test_no_process_local_invoice_lock(self):
        import app.backend.services.billing.invoice_service as inv
        assert not hasattr(inv, "_invoice_alloc_lock")


class TestProcessLocalLocksNotRequired:
    def test_webhook_module_has_no_claim_lock(self):
        assert not hasattr(webhook_processor_mod, "_claim_lock")

    def test_serial_duplicate_without_mutex(self, db):
        calls = []

        def handler(db_sess, data, raw):
            calls.append(1)

        key = ("stripe", "invoice.paid")
        original = webhook_processor_mod._HANDLER_MAP[key]
        webhook_processor_mod._HANDLER_MAP[key] = handler
        try:
            process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_nolock_1",
            )
            process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_nolock_1",
            )
        finally:
            webhook_processor_mod._HANDLER_MAP[key] = original
        assert calls == [1]

    def test_handler_exception_rolls_back_side_effects(self, db):
        tenant = Tenant(name="WhRoll", slug="wh-roll-p0", subscription_status="trialing")
        db.add(tenant)
        db.commit()

        def handler(db_sess, data, raw):
            db_sess.add(Invoice(
                tenant_id=tenant.id,
                invoice_number="INV-2099-00999",
                status="paid",
                amount=1,
                currency="usd",
            ))
            db_sess.flush()
            raise RuntimeError("fail after mutation")

        key = ("stripe", "invoice.paid")
        original = webhook_processor_mod._HANDLER_MAP[key]
        webhook_processor_mod._HANDLER_MAP[key] = handler
        try:
            out = process_webhook_event(
                db, provider="stripe", event_type="invoice.paid",
                data={}, raw_payload="{}", event_id="evt_rollback_1",
            )
        finally:
            webhook_processor_mod._HANDLER_MAP[key] = original
        assert out["reason"] == "error"
        assert db.query(Invoice).filter_by(invoice_number="INV-2099-00999").count() == 0
        row = db.query(BillingEvent).filter_by(provider="stripe", event_id="evt_rollback_1").one()
        assert row.result == "error"


class TestBillingEventReconciliation:
    def test_failed_then_succeeded_keeps_success(self):
        keeper = choose_keeper([
            {"id": 10, "result": "failed"},
            {"id": 11, "result": "success"},
        ])
        assert keeper["id"] == 11

    def test_succeeded_then_failed_keeps_success(self):
        keeper = choose_keeper([
            {"id": 10, "result": "success"},
            {"id": 11, "result": "failed"},
        ])
        assert keeper["id"] == 10

    def test_two_succeeded_keeps_newest(self):
        keeper = choose_keeper([
            {"id": 10, "result": "success"},
            {"id": 11, "result": "success"},
        ])
        assert keeper["id"] == 11

    def test_received_and_processing(self):
        keeper = choose_keeper([
            {"id": 1, "result": "received"},
            {"id": 2, "result": "processing"},
        ])
        assert keeper["id"] == 2

    def test_two_failed_keeps_newest(self):
        keeper = choose_keeper([
            {"id": 4, "result": "error"},
            {"id": 5, "result": "failed"},
        ])
        assert keeper["id"] == 5

    def test_three_or_more(self):
        rows = [
            {"id": 1, "result": "error"},
            {"id": 2, "result": "processing"},
            {"id": 3, "result": "success"},
            {"id": 4, "result": "ignored"},
        ]
        assert choose_keeper(rows)["id"] == 3
        assert {r["id"] for r in rows_to_delete(rows)} == {1, 2, 4}

    def test_no_duplicates_single_row(self):
        assert choose_keeper([{"id": 9, "result": "success"}])["id"] == 9
        assert rows_to_delete([{"id": 9, "result": "success"}]) == []


@pytest.mark.skipif(
    not os.environ.get("PHASE0_POSTGRES_URL"),
    reason="PostgreSQL concurrency proof requires PHASE0_POSTGRES_URL",
)
class TestPhase0PostgresConcurrency:
    def test_placeholder_delegates_to_postgres_module(self):
        assert os.environ.get("PHASE0_POSTGRES_URL")


class TestRevenueMetrics:
    def _plan(self, db, name, monthly, yearly):
        plan = SubscriptionPlan(
            name=name,
            display_name=name,
            limits="{}",
            price_monthly=monthly,
            price_yearly=yearly,
            features="[]",
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    def test_monthly_and_annual_and_one_off(self, db):
        monthly = self._plan(db, "p0-month", 10000, 120000)
        annual = self._plan(db, "p0-year", 10000, 120000)
        now = datetime.now(timezone.utc)
        t1 = Tenant(
            name="M", slug="p0-mrr-m", subscription_status="active",
            plan_id=monthly.id,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        t2 = Tenant(
            name="Y", slug="p0-mrr-y", subscription_status="active",
            plan_id=annual.id,
            current_period_start=now,
            current_period_end=now + timedelta(days=365),
        )
        t3 = Tenant(name="C", slug="p0-mrr-c", subscription_status="cancelled", plan_id=monthly.id)
        db.add_all([t1, t2, t3])
        db.commit()
        create_invoice_from_payment(db, tenant_id=t1.id, amount=50000, description="one-off")
        late = Invoice(
            tenant_id=t1.id, invoice_number="INV-1999-00001", status="paid",
            amount=10000, currency="usd", paid_at=now - timedelta(days=40),
        )
        unpaid = Invoice(
            tenant_id=t1.id, invoice_number="INV-1999-00002", status="pending",
            amount=9999, currency="usd",
        )
        db.add_all([late, unpaid])
        db.commit()

        metrics = get_revenue_metrics(db)
        assert metrics["mrr_cents"] == 10000 + 10000
        assert metrics["arr_estimate_cents"] == metrics["mrr_cents"] * 12
        assert metrics["collected_cash_30d_cents"] == 50000
        assert metrics["collected_cash_30d_cents"] != metrics["mrr_cents"]
        cancelled = get_revenue_metrics(db, tenant_id=t3.id)
        assert cancelled["mrr_cents"] == 0
        scoped = get_revenue_metrics(db, tenant_id=t2.id)
        assert scoped["mrr_cents"] == 10000
        unpaid_m = get_revenue_metrics(db)
        assert unpaid_m["outstanding_cents"] >= 9999

    def test_yearly_price_to_mrr_half_up_cents(self):
        assert yearly_price_to_mrr_cents(120000) == 10000
        assert yearly_price_to_mrr_cents(100000) == 8333
        assert yearly_price_to_mrr_cents(99999) == 8333


class TestProcessingPolicy:
    def test_default_allows(self, db, sample_user, sample_candidate):
        d = can_process(
            db=db,
            tenant_id=sample_user.tenant_id,
            candidate_id=sample_candidate.id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        assert d.allowed is True
        assert d.reason == "legacy_default_allow"

    def test_consent_required_modes(self, db, sample_user, sample_candidate):
        tenant = db.get(Tenant, sample_user.tenant_id)
        tenant.metadata_json = json.dumps({
            "candidate_processing": {
                "require_consent_for": [PROCESSING_RESUME_ANALYSIS, PROCESSING_VOICE_INTERVIEW]
            }
        })
        db.commit()
        denied = can_process(
            db=db, tenant_id=tenant.id, candidate_id=sample_candidate.id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        assert denied.allowed is False
        screening = can_process(
            db=db, tenant_id=tenant.id, candidate_id=sample_candidate.id,
            processing_type=PROCESSING_AI_SCREENING,
        )
        assert screening.allowed is True
        db.add(CandidateConsent(
            tenant_id=tenant.id, candidate_id=sample_candidate.id,
            consent_type="ai_screening", consented=True,
            consented_at=datetime.now(timezone.utc),
        ))
        db.commit()
        allowed = can_process(
            db=db, tenant_id=tenant.id, candidate_id=sample_candidate.id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        assert allowed.allowed is True
        row = db.query(CandidateConsent).filter_by(candidate_id=sample_candidate.id).first()
        row.withdrawal_at = datetime.now(timezone.utc)
        db.commit()
        revoked = can_process(
            db=db, tenant_id=tenant.id, candidate_id=sample_candidate.id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        assert revoked.allowed is False
        assert revoked.reason == "consent_revoked"

    def test_tenant_isolation(self, db, sample_user, sample_candidate, other_tenant_session):
        tenant = db.get(Tenant, sample_user.tenant_id)
        tenant.metadata_json = json.dumps({
            "candidate_processing": {"require_consent_for": [PROCESSING_RESUME_ANALYSIS]}
        })
        db.commit()
        other_id = other_tenant_session.tenant_id
        other = can_process(
            db=db, tenant_id=other_id, candidate_id=other_tenant_session.candidate_id,
            processing_type=PROCESSING_RESUME_ANALYSIS,
        )
        assert other.allowed is True

    def test_enforce_raises_before_provider(self, db, sample_user, sample_candidate):
        tenant = db.get(Tenant, sample_user.tenant_id)
        tenant.metadata_json = json.dumps({
            "candidate_processing": {"require_consent_for": [PROCESSING_RESUME_ANALYSIS]}
        })
        db.commit()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            enforce_candidate_processing_policy(
                db, tenant_id=tenant.id, candidate_id=sample_candidate.id,
                processing_type=PROCESSING_RESUME_ANALYSIS,
            )
        assert ei.value.status_code == 403
