"""Real PostgreSQL concurrency proofs for Phase 2 decision governance.

Every competing operation owns an independent SQLAlchemy session. The suite
skips locally when PostgreSQL is absent, but GOVERNANCE_POSTGRES_REQUIRED=1
fails closed so the required CI job cannot silently skip.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.backend.models.db_models import (
    AIDecisionLog,
    Candidate,
    DecisionNarrative,
    ScreeningDecision,
    ScreeningResult,
    Tenant,
    TrainingExample,
    User,
)
from app.backend.tests.reliability_env import postgres_url, require_postgres


ALGORITHM_VERSION = "aria-fit-v1"
WEIGHTS = {
    "skills": 0.30,
    "experience": 0.20,
    "architecture": 0.15,
    "education": 0.10,
    "timeline": 0.15,
    "domain": 0.10,
    "risk": 0.10,
}
SCORES = {
    "components": {
        "skills": 80,
        "experience": 70,
        "architecture": 60,
        "education": 50,
        "timeline": 90,
        "domain": 75,
    },
    "risk_penalty": 10,
}
SNAPSHOT = {
    "schema_version": "decision_input_v1",
    "resume_hash": "d" * 64,
    "jd_hash": "e" * 64,
    "parser_snapshot_hash": "f" * 64,
    **SCORES,
}
POLICY = {
    "policy_version": "candidate-processing-v1",
    "processing_purpose": "candidate screening",
    "consent_required": True,
    "consent_present": True,
    "processing_allowed": True,
}


def _require_governance_postgres() -> str:
    if not postgres_url() and os.environ.get("GOVERNANCE_POSTGRES_REQUIRED") == "1":
        pytest.fail(
            "GOVERNANCE_POSTGRES_REQUIRED=1 but RELIABILITY_POSTGRES_URL "
            "or PHASE0_POSTGRES_URL is not configured",
            pytrace=False,
        )
    return require_postgres()


def _decision_service():
    module_name = "app.backend.services.decision_service"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(
                f"expected RED: Phase 2 service {module_name} is not implemented",
                pytrace=False,
            )
        raise


def _legacy_importer():
    module_name = "app.backend.scripts.backfill_screening_decisions"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(
                f"expected RED: Phase 2 legacy importer {module_name} is not implemented",
                pytrace=False,
            )
        raise


@pytest.fixture(scope="module")
def pg_engine():
    url = _require_governance_postgres()
    engine = create_engine(
        url,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        engine.dispose()
        if os.environ.get("GOVERNANCE_POSTGRES_REQUIRED") == "1":
            pytest.fail(f"GOVERNANCE_POSTGRES_REQUIRED=1 but PostgreSQL is unreachable: {exc}")
        pytest.skip(f"PostgreSQL governance tests require a reachable database: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture()
def PgSession(pg_engine):
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        bind=pg_engine,
    )


@dataclass(frozen=True)
class PgCase:
    tenant_id: int
    actor_ids: tuple[int, int]
    result_id: int


@pytest.fixture()
def pg_case(PgSession) -> PgCase:
    db = PgSession()
    try:
        token = uuid.uuid4().hex
        tenant = Tenant(name="Governance PostgreSQL", slug=f"gov-pg-{token[:20]}")
        db.add(tenant)
        db.flush()
        actors = [
            User(
                tenant_id=tenant.id,
                email=f"gov-{index}-{token}@example.invalid",
                hashed_password="not-a-real-password-hash",
                role="recruiter",
                is_active=True,
            )
            for index in range(2)
        ]
        candidate = Candidate(tenant_id=tenant.id, name="PostgreSQL Test Candidate")
        db.add_all([*actors, candidate])
        db.flush()
        result = ScreeningResult(
            tenant_id=tenant.id,
            candidate_id=candidate.id,
            resume_text="Synthetic backend engineering experience.",
            jd_text="Synthetic backend engineering requirements.",
            parsed_data="{}",
            analysis_result="{}",
            analysis_generation=1,
        )
        db.add(result)
        db.commit()
        return PgCase(tenant.id, (actors[0].id, actors[1].id), result.id)
    finally:
        db.close()


def _create(
    db,
    case: PgCase,
    *,
    operation_id: str,
    decision_type: str = "REANALYSIS",
    expected_current_decision_id: int | None = None,
):
    return _decision_service().create_screening_decision(
        db,
        screening_result_id=case.result_id,
        decision_type=decision_type,
        scores=SCORES,
        eligibility={"status": True, "reason": None, "gate_trace": []},
        recommendation="Consider",
        scoring_weights=WEIGHTS,
        algorithm_version=ALGORITHM_VERSION,
        input_snapshot=SNAPSHOT,
        policy_context=POLICY,
        actor_id=case.actor_ids[0],
        operation_id=operation_id,
        analysis_generation=1,
        expected_current_decision_id=expected_current_decision_id,
    )


def _run_threads(count: int, worker):
    barrier = threading.Barrier(count)
    values: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def guarded(index: int):
        try:
            barrier.wait(timeout=20)
            value = worker(index)
            with lock:
                values.append(value)
        except BaseException as exc:  # captured for assertion in the test thread
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "governance worker timed out"
    return values, errors


def test_postgres_twenty_concurrent_creations_allocate_sequential_unique_versions(
    PgSession, pg_case
):
    def worker(index: int):
        db = PgSession()
        try:
            decision = _create(db, pg_case, operation_id=f"pg-version-{index}-{uuid.uuid4()}")
            db.commit()
            return decision.decision_version
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    versions, errors = _run_threads(20, worker)
    assert not errors, errors
    assert sorted(versions) == list(range(1, 21))
    assert len(set(versions)) == 20
    check = PgSession()
    try:
        model = _decision_service().ScreeningDecision
        rows = check.query(model).filter_by(screening_result_id=pg_case.result_id).all()
        assert sorted(row.decision_version for row in rows) == list(range(1, 21))
        assert len({row.id for row in rows}) == 20
    finally:
        check.close()


def test_postgres_twenty_duplicate_operations_create_one_decision(PgSession, pg_case):
    operation_id = f"pg-idempotent-{uuid.uuid4()}"

    def worker(_index: int):
        db = PgSession()
        try:
            decision = _create(db, pg_case, operation_id=operation_id)
            db.commit()
            return decision.id
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    ids, errors = _run_threads(20, worker)
    assert not errors, errors
    assert len(set(ids)) == 1
    check = PgSession()
    try:
        model = _decision_service().ScreeningDecision
        assert (
            check.query(model)
            .filter_by(tenant_id=pg_case.tenant_id, source_operation_id=operation_id)
            .count()
            == 1
        )
        assert (
            check.query(AIDecisionLog)
            .filter_by(tenant_id=pg_case.tenant_id, screening_result_id=pg_case.result_id)
            .count()
            == 1
        )
    finally:
        check.close()


def test_postgres_concurrent_reanalyses_leave_one_explicit_current_pointer(
    PgSession, pg_case
):
    setup = PgSession()
    try:
        initial = _create(
            setup,
            pg_case,
            operation_id=f"pg-current-initial-{uuid.uuid4()}",
            decision_type="INITIAL_ANALYSIS",
        )
        setup.commit()
        initial_id = initial.id
    finally:
        setup.close()

    def worker(index: int):
        db = PgSession()
        try:
            decision = _create(
                db,
                pg_case,
                operation_id=f"pg-reanalysis-{index}-{uuid.uuid4()}",
                expected_current_decision_id=initial_id,
            )
            db.commit()
            return decision.id
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    ids, errors = _run_threads(2, worker)
    assert not errors, errors
    assert len(set(ids)) == 2
    check = PgSession()
    try:
        result = check.get(ScreeningResult, pg_case.result_id)
        assert result.current_decision_id in ids
        model = _decision_service().ScreeningDecision
        assert check.query(model).filter_by(screening_result_id=pg_case.result_id).count() == 3
    finally:
        check.close()


def test_postgres_concurrent_human_overrides_accept_one_and_conflict_one(
    PgSession, pg_case
):
    setup = PgSession()
    try:
        current = _create(
            setup,
            pg_case,
            operation_id=f"pg-override-base-{uuid.uuid4()}",
            decision_type="INITIAL_ANALYSIS",
        )
        setup.commit()
        current_id = current.id
    finally:
        setup.close()

    service = _decision_service()

    def worker(index: int):
        db = PgSession()
        try:
            override = service.create_human_override(
                db,
                screening_result_id=pg_case.result_id,
                expected_current_decision_id=current_id,
                actor_id=pg_case.actor_ids[index],
                operation_id=f"pg-override-{index}-{uuid.uuid4()}",
                recommendation=("Shortlist" if index == 0 else "Reject"),
                reason_code="RECRUITER_JUDGMENT",
                reason_text="Independent synthetic concurrency proof.",
            )
            db.commit()
            return ("created", override.id)
        except service.StaleDecisionConflict:
            db.rollback()
            return ("conflict", 409)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    outcomes, errors = _run_threads(2, worker)
    assert not errors, errors
    assert sorted(status for status, _ in outcomes) == ["conflict", "created"]
    assert [value for status, value in outcomes if status == "conflict"] == [409]


def test_postgres_projection_and_audit_are_atomic_and_exactly_once(PgSession, pg_case):
    db = PgSession()
    try:
        decision = _create(
            db,
            pg_case,
            operation_id=f"pg-atomic-{uuid.uuid4()}",
            decision_type="INITIAL_ANALYSIS",
        )
        db.commit()
        result = db.get(ScreeningResult, pg_case.result_id)
        assert result.current_decision_id == decision.id
        assert result.deterministic_score == decision.deterministic_score
        assert result.eligibility_status == decision.eligibility_status
        audit = (
            db.query(AIDecisionLog)
            .filter_by(screening_decision_id=decision.id)
            .one()
        )
        assert audit.tenant_id == decision.tenant_id
        assert audit.screening_result_id == decision.screening_result_id
        assert audit.decision_type == decision.decision_type
        assert audit.actor_id == decision.actor_id
        assert audit.screening_decision_id == decision.id
    finally:
        db.close()


def test_postgres_legacy_import_is_unique_under_concurrency(PgSession, pg_case):
    def worker(_index: int):
        db = PgSession()
        try:
            decision = _legacy_importer().import_screening_result(
                db, screening_result_id=pg_case.result_id
            )
            db.commit()
            return decision.id
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    ids, errors = _run_threads(20, worker)
    assert not errors, errors
    assert len(set(ids)) == 1
    check = PgSession()
    try:
        model = _decision_service().ScreeningDecision
        rows = (
            check.query(model)
            .filter_by(
                screening_result_id=pg_case.result_id,
                decision_type="LEGACY_IMPORTED",
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].provenance_complete is False
    finally:
        check.close()


def test_postgres_projection_failure_rolls_back_decision(PgSession, pg_case, monkeypatch):
    service = _decision_service()

    def boom(*_args, **_kwargs):
        raise RuntimeError("projection failed after decision insert")

    monkeypatch.setattr(service, "apply_decision_projection", boom)
    db = PgSession()
    try:
        with pytest.raises(RuntimeError, match="projection failed"):
            _create(db, pg_case, operation_id=f"pg-proj-fail-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
            db.commit()
        db.rollback()
    finally:
        db.close()
    check = PgSession()
    try:
        result = check.get(ScreeningResult, pg_case.result_id)
        assert result.current_decision_id is None
        assert check.query(service.ScreeningDecision).filter_by(screening_result_id=pg_case.result_id).count() == 0
        assert check.query(AIDecisionLog).filter_by(screening_result_id=pg_case.result_id).count() == 0
    finally:
        check.close()


def test_postgres_audit_failure_rolls_back_decision(PgSession, pg_case, monkeypatch):
    import app.backend.services.ai_decision_log_service as audit

    def boom(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit, "write_ai_decision_log", boom)
    db = PgSession()
    try:
        with pytest.raises(RuntimeError, match="audit down"):
            _create(db, pg_case, operation_id=f"pg-audit-fail-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
            db.commit()
        db.rollback()
    finally:
        db.close()
    check = PgSession()
    try:
        result = check.get(ScreeningResult, pg_case.result_id)
        assert result.current_decision_id is None
        assert check.query(_decision_service().ScreeningDecision).filter_by(screening_result_id=pg_case.result_id).count() == 0
        assert check.query(AIDecisionLog).filter_by(screening_result_id=pg_case.result_id).count() == 0
    finally:
        check.close()


def test_postgres_legacy_batch_is_idempotent_and_protects_native_current(PgSession, pg_case):
    importer = _legacy_importer()
    setup = PgSession()
    try:
        native = _create(
            setup,
            pg_case,
            operation_id=f"pg-native-{uuid.uuid4()}",
            decision_type="INITIAL_ANALYSIS",
        )
        setup.commit()
        native_id = native.id
        extras = []
        for index in range(3):
            result = ScreeningResult(
                tenant_id=pg_case.tenant_id,
                candidate_id=setup.get(ScreeningResult, pg_case.result_id).candidate_id,
                resume_text="legacy",
                jd_text="legacy",
                parsed_data="{}",
                analysis_result='{"fit_score": 0, "final_recommendation": null, "risk_penalty": 0}',
                deterministic_score=0,
            )
            setup.add(result)
            setup.flush()
            extras.append(result.id)
        setup.commit()
    finally:
        setup.close()

    first = PgSession()
    try:
        stats = importer.import_legacy_decisions(first, batch_size=2, after_id=0)
        first.commit()
        first_created = stats["created"]
        first_processed = stats["processed"]
        last_id = stats["last_id"]
        assert first_created >= 1
        assert first_processed >= first_created
    finally:
        first.close()
    second = PgSession()
    try:
        stats = importer.import_legacy_decisions(second, batch_size=2, after_id=0)
        second.commit()
        assert stats["created"] == 0
        assert stats["skipped_existing"] >= first_created
        model = _decision_service().ScreeningDecision
        legacy = (
            second.query(model)
            .filter_by(decision_type="LEGACY_IMPORTED", screening_result_id=pg_case.result_id)
            .all()
        )
        assert len(legacy) == 1
        assert second.get(ScreeningResult, pg_case.result_id).current_decision_id == native_id
        for extra_id in extras:
            extra = (
                second.query(model)
                .filter_by(screening_result_id=extra_id, decision_type="LEGACY_IMPORTED")
                .one()
            )
            assert extra.provenance_complete is False
            assert extra.algorithm_version is None
            assert extra.final_fit_score == 0
            assert extra.model_context is None
            assert extra.prompt_context is None
            assert extra.effective_weights in (None, {})
    finally:
        second.close()
    assert last_id > 0


def test_postgres_formula_and_projection_match(PgSession, pg_case):
    db = PgSession()
    try:
        decision = _create(db, pg_case, operation_id=f"pg-formula-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
        db.commit()
        reproduced = _decision_service()
        from app.backend.services.decision_reproducibility import recompute_formula_trace

        trace = recompute_formula_trace(decision.formula_trace)
        assert trace["post_clamp_total"] == decision.final_fit_score
        result = db.get(ScreeningResult, pg_case.result_id)
        payload = __import__("json").loads(result.analysis_result)
        assert payload["fit_score"] == decision.final_fit_score
        assert result.deterministic_score == decision.deterministic_score
        assert payload["component_fit_score"] == decision.component_fit_score
        assert payload["final_recommendation"] == decision.effective_recommendation
    finally:
        db.close()


def test_postgres_schema_constraints_and_tenant_operation_isolation(PgSession, pg_case):
    db = PgSession()
    try:
        rows = db.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'screening_decisions'::regclass "
                "ORDER BY conname"
            )
        ).fetchall()
        names = {row[0] for row in rows}
        assert "uq_screening_decision_result_version" in names
        assert "uq_screening_decision_tenant_operation_type" in names
        first = _create(db, pg_case, operation_id="shared-op", decision_type="INITIAL_ANALYSIS")
        db.commit()
        other = Tenant(name="Other Gov", slug=f"gov-other-{uuid.uuid4().hex[:12]}")
        db.add(other)
        db.flush()
        candidate = Candidate(tenant_id=other.id, name="Other Candidate")
        db.add(candidate)
        db.flush()
        other_result = ScreeningResult(
            tenant_id=other.id,
            candidate_id=candidate.id,
            resume_text="x",
            jd_text="y",
            parsed_data="{}",
            analysis_result="{}",
        )
        db.add(other_result)
        db.flush()
        other_case = PgCase(other.id, pg_case.actor_ids, other_result.id)
        second = _create(db, other_case, operation_id="shared-op", decision_type="INITIAL_ANALYSIS")
        db.commit()
        assert first.id != second.id
        assert first.source_operation_id == second.source_operation_id
        same_tenant_other = ScreeningResult(
            tenant_id=pg_case.tenant_id,
            candidate_id=db.get(ScreeningResult, pg_case.result_id).candidate_id,
            resume_text="z",
            jd_text="z",
            parsed_data="{}",
            analysis_result="{}",
        )
        db.add(same_tenant_other)
        db.flush()
        with pytest.raises(_decision_service().OperationScopeError):
            _create(
                db,
                PgCase(pg_case.tenant_id, pg_case.actor_ids, same_tenant_other.id),
                operation_id="shared-op",
                decision_type="INITIAL_ANALYSIS",
            )
    finally:
        db.close()


def test_p2c3_same_narrative_operation_is_unique(PgSession, pg_case):
    setup = PgSession()
    try:
        decision = _create(setup, pg_case, operation_id=f"p2c3-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
        setup.commit()
        decision_id = decision.id
    finally:
        setup.close()

    def worker(_index: int):
        db = PgSession()
        try:
            row = _decision_service().store_decision_narrative(
                db,
                decision_id=decision_id,
                structured_output={"summary": "same operation"},
                operation_id="narrative-same",
            )
            db.commit()
            return row.id
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    ids, errors = _run_threads(20, worker)
    assert not errors, errors
    assert len(set(ids)) == 1
    check = PgSession()
    try:
        assert check.query(DecisionNarrative).filter_by(screening_decision_id=decision_id).count() == 1
    finally:
        check.close()


def test_p2c4_distinct_narrative_operations(PgSession, pg_case):
    db = PgSession()
    try:
        decision = _create(db, pg_case, operation_id=f"p2c4-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
        db.commit()
        a = _decision_service().store_decision_narrative(
            db, decision_id=decision.id, structured_output={"summary": "a"}, operation_id="narrative-a"
        )
        b = _decision_service().store_decision_narrative(
            db, decision_id=decision.id, structured_output={"summary": "b"}, operation_id="narrative-b"
        )
        db.commit()
        assert a.id != b.id
        assert (
            db.query(DecisionNarrative).filter_by(screening_decision_id=decision.id).count()
            == 2
        )
    finally:
        db.close()


def test_p2c5_result_deletion_fk_lifecycle(PgSession, pg_case):
    db = PgSession()
    try:
        decision = _create(db, pg_case, operation_id=f"p2c5-{uuid.uuid4()}", decision_type="INITIAL_ANALYSIS")
        db.commit()
        _decision_service().store_decision_narrative(
            db,
            decision_id=decision.id,
            structured_output={"summary": "bound"},
            operation_id="p2c5-narr",
        )
        example = TrainingExample(
            tenant_id=pg_case.tenant_id,
            screening_result_id=pg_case.result_id,
            screening_decision_id=decision.id,
            outcome="hired",
        )
        db.add(example)
        db.commit()
        decision_id = decision.id
        example_id = example.id
        audit_id = (
            db.query(AIDecisionLog.id).filter_by(screening_decision_id=decision_id).scalar()
        )
        candidate_id = db.get(ScreeningResult, pg_case.result_id).candidate_id
        from app.backend.services.gdpr_service import hard_delete_candidate

        outcome = hard_delete_candidate(db, candidate_id, pg_case.tenant_id)
        assert outcome.get("error") is None
        db.expire_all()
        assert db.get(ScreeningDecision, decision_id) is None
        assert db.query(DecisionNarrative).filter_by(screening_decision_id=decision_id).count() == 0
        audit = db.get(AIDecisionLog, audit_id)
        assert audit is not None
        assert audit.screening_decision_id is None
        assert db.get(TrainingExample, example_id) is None
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_p2c1_p2c2_phase2_schema_appears_only_after_081():
    url = _require_governance_postgres()
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    dbname = f"aria_p2c_{uuid.uuid4().hex[:8]}"
    admin = parsed.set(database="postgres")
    engine = create_engine(admin)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"CREATE DATABASE {dbname}"))
    target = parsed.set(database=dbname).render_as_string(hide_password=False)
    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["DATABASE_URL"] = target
    env["PYTHONPATH"] = str(root)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "080_phase1_reliability_closure"],
            cwd=root,
            env=env,
            check=True,
        )
        probe = create_engine(target)
        with probe.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('screening_decisions')")).scalar() is None
            cols = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='screening_results'"
                    )
                )
            }
            assert "current_decision_id" not in cols
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=env,
            check=True,
        )
        with probe.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('screening_decisions')")).scalar()
            assert connection.execute(text("SELECT to_regclass('decision_narratives')")).scalar()
            cols = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='screening_results'"
                    )
                )
            }
            assert "current_decision_id" in cols
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert revision == "082_reliability_closure"
        probe.dispose()
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
        engine.dispose()


def test_governance_postgres_gate_is_fail_closed(monkeypatch):
    monkeypatch.delenv("RELIABILITY_POSTGRES_URL", raising=False)
    monkeypatch.delenv("PHASE0_POSTGRES_URL", raising=False)
    monkeypatch.setenv("GOVERNANCE_POSTGRES_REQUIRED", "1")
    with pytest.raises(pytest.fail.Exception, match="GOVERNANCE_POSTGRES_REQUIRED=1"):
        _require_governance_postgres()
