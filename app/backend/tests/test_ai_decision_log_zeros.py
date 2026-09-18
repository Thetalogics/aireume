"""AI decision log must preserve legitimate zero scores."""
from app.backend.services.ai_decision_log_service import (
    DECISION_INITIAL,
    first_not_none,
    write_log_from_pipeline,
)


def test_first_not_none_keeps_zero():
    assert first_not_none(0, 35) == 0
    assert first_not_none(None, 0, 35) == 0
    assert first_not_none(None, None) is None
    assert first_not_none(None, 42) == 42


def test_fit_score_zero_not_replaced_by_overall(db, seed_subscription_plans):
    from app.backend.models.db_models import ScreeningResult, Tenant

    tenant = Tenant(name="ZeroScore", slug="zero-score-fit")
    db.add(tenant)
    db.flush()
    result = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result="{}",
    )
    db.add(result)
    db.flush()
    log = write_log_from_pipeline(
        db,
        result,
        {"fit_score": 0, "overall_score": 35, "final_recommendation": "Reject"},
        decision_type=DECISION_INITIAL,
    )
    db.flush()
    assert log.final_score == 0.0


def test_deterministic_score_zero_not_replaced_by_meta(db, seed_subscription_plans):
    from app.backend.models.db_models import ScreeningResult, Tenant

    tenant = Tenant(name="ZeroDet", slug="zero-score-det")
    db.add(tenant)
    db.flush()
    result = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result="{}",
    )
    db.add(result)
    db.flush()
    log = write_log_from_pipeline(
        db,
        result,
        {
            "fit_score": 10,
            "deterministic_score": 0,
            "_meta": {"deterministic_score": 42},
        },
        decision_type=DECISION_INITIAL,
    )
    db.flush()
    assert log.deterministic_score == 0.0
    assert log.final_score == 10.0


def test_none_falls_back(db, seed_subscription_plans):
    from app.backend.models.db_models import ScreeningResult, Tenant

    tenant = Tenant(name="NoneFb", slug="zero-score-none")
    db.add(tenant)
    db.flush()
    result = ScreeningResult(
        tenant_id=tenant.id,
        resume_text="r",
        jd_text="j",
        parsed_data="{}",
        analysis_result="{}",
    )
    db.add(result)
    db.flush()
    log = write_log_from_pipeline(
        db,
        result,
        {
            "fit_score": None,
            "overall_score": 35,
            "deterministic_score": None,
            "_meta": {"deterministic_score": 7},
        },
        decision_type=DECISION_INITIAL,
    )
    db.flush()
    assert log.final_score == 35.0
    assert log.deterministic_score == 7.0
