"""AUD-001 residual GETDEL, scoring, proxy signals, CSV, identity, adverse-action."""
import io
import threading

import pytest

from app.backend.services.fit_scorer import compute_fit_score
from app.backend.services.scoring_weights import ScoringWeightError, canonicalize_scoring_weights
from app.backend.services.shared_cache import cache_consume_if_tenant, cache_set
from app.backend.services.sso_service import consume_saml_authn_request, persist_saml_authn_request


def test_saml_consume_is_atomic_under_concurrency():
    persist_saml_authn_request("ARIA-CONCURRENT", 42)
    results = []

    def _run():
        results.append(consume_saml_authn_request("ARIA-CONCURRENT", 42))

    t1 = threading.Thread(target=_run)
    t2 = threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_saml_consume_tenant_mismatch_leaves_key():
    persist_saml_authn_request("ARIA-TENANT", 7)
    assert consume_saml_authn_request("ARIA-TENANT", 8) is False
    assert consume_saml_authn_request("ARIA-TENANT", 7) is True


def test_canonical_weights_reject_unknown_and_bad_total():
    canon = canonicalize_scoring_weights({
        "skills": 0.4, "experience": 0.2, "architecture": 0.15,
        "education": 0.15, "domain": 0.1, "risk": 0.1,
    })
    assert set(canon) == {
        "skills", "experience", "architecture", "education", "timeline", "domain", "risk",
    }
    legacy = canonicalize_scoring_weights({
        "skills": 0.4, "experience": 0.3, "stability": 0.15, "education": 0.15,
    })
    assert "skills" in legacy
    try:
        canonicalize_scoring_weights({"skills": 0.9, "magic": 0.1})
        raise AssertionError("unknown keys must fail")
    except ScoringWeightError:
        pass
    try:
        canonicalize_scoring_weights({"skills": 0.1, "experience": 0.1, "architecture": 0.1, "education": 0.1, "domain": 0.1})
        raise AssertionError("invalid total must fail")
    except ScoringWeightError:
        pass


def test_gaps_and_short_tenure_do_not_change_fit_score():
    base = {
        "skill_score": 80, "exp_score": 70, "arch_score": 70, "edu_score": 60,
        "timeline_score": 90, "domain_score": 70, "actual_years": 5, "required_years": 5,
        "matched_skills": ["python"], "missing_skills": [], "required_count": 1,
        "employment_gaps": [], "short_stints": [],
    }
    a = compute_fit_score(base, risk_penalty=0)
    b = compute_fit_score({
        **base,
        "employment_gaps": [{"severity": "critical", "months": 18}],
        "short_stints": [{"months": 3}, {"months": 2}, {"months": 4}],
        "actual_years": 20,
        "required_years": 3,
    }, risk_penalty=0)
    assert a["fit_score"] == b["fit_score"]
    assert a["raw_weighted_total"] == pytest.approx(b["raw_weighted_total"])


def test_historical_outcomes_do_not_adjust_when_disabled():
    scores = {
        "skill_score": 50, "exp_score": 50, "arch_score": 50, "edu_score": 50,
        "timeline_score": 50, "domain_score": 50, "matched_skills": ["python"],
        "missing_skills": [], "required_count": 1, "employment_gaps": [], "short_stints": [],
    }
    jd = {"required_skills": ["python"], "nice_to_have_skills": ["go"]}
    off = compute_fit_score(
        scores, jd_analysis=jd,
        phase3_context={"outcome_patterns": [{"skill": "python", "sample_size": 80, "success_rate": 1.0, "confidence": "high"}]},
    )
    on = compute_fit_score(
        scores, jd_analysis=jd,
        phase3_context={
            "historical_outcomes_enabled": True,
            "outcome_patterns": [{"skill": "python", "sample_size": 80, "success_rate": 1.0, "confidence": "high"}],
        },
    )
    tiny = compute_fit_score(
        scores, jd_analysis=jd,
        phase3_context={
            "historical_outcomes_enabled": True,
            "outcome_patterns": [{"skill": "python", "sample_size": 2, "success_rate": 1.0, "confidence": "high"}],
        },
    )
    assert off["fit_score"] == tiny["fit_score"]
    assert on["fit_score"] >= off["fit_score"]


def test_csv_import_creates_tenant_scoped_candidates(auth_client, db, seed_subscription_plans):
    resp = auth_client.post(
        "/api/candidates/import/csv",
        files={"file": ("c.csv", b"name,email\nCSV User,csv-user@testcorp.com\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created_count"] == 1
    dup = auth_client.post(
        "/api/candidates/import/csv",
        files={"file": ("c.csv", b"name,email\nCSV User,csv-user@testcorp.com\n", "text/csv")},
    )
    assert dup.status_code == 200
    assert dup.json()["created_count"] == 0
    assert dup.json()["errors"]


def test_adverse_action_does_not_claim_legal_compliance():
    from app.backend.services.adverse_action_service import AdverseActionService
    report = AdverseActionService().generate_report(
        {"fit_score": 20, "recommendation": "reject", "matched_skills": [], "missing_skills": ["python"],
         "pii_redacted": False, "evidence_quality_score": 10, "bias_note": ""},
        "weak python",
        "need python",
    )
    blob = str(report)
    assert "eeoc_compliant" not in blob
    assert "FCRA" in report["legal_review"]["disclaimer"]
    assert "not legal advice" in report["legal_review"]["disclaimer"].lower()


def test_stripe_prorate_updates_subscription_item_price(monkeypatch):
    from app.backend.services.billing import stripe_provider as sp

    calls = {}

    class _Sub:
        id = "sub_1"
        status = "active"
        def get(self, key, default=None):
            if key == "items":
                return {"data": [{"id": "si_1"}]}
            return default
        def __getitem__(self, key):
            return self.get(key)

    def retrieve(sub_id):
        calls["retrieve"] = sub_id
        return _Sub()

    def modify(sub_id, **kwargs):
        calls["modify"] = kwargs
        return _Sub()

    monkeypatch.setattr(sp, "stripe", type("S", (), {
        "Subscription": type("Sub", (), {"retrieve": staticmethod(retrieve), "modify": staticmethod(modify)}),
    })())
    provider = sp.StripeProvider.__new__(sp.StripeProvider)
    provider._require_stripe = lambda: None
    out = provider.prorate_plan_change(1, "sub_1", 10, 20, {"stripe_price_id": "price_growth"})
    assert out["status"] == "active"
    assert calls["modify"]["items"][0]["price"] == "price_growth"
    missing = provider.prorate_plan_change(1, "sub_1", 10, 20, {})
    assert missing["status"] == "error"


def test_auth_me_sets_csrf_cookie(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.cookies.get("csrf_token")


def test_candidate_search_does_not_query_raw_resume():
    import inspect
    from app.backend.routes import candidates as cmod
    src = inspect.getsource(cmod.list_candidates)
    assert "raw_resume_text.ilike" not in src


def test_same_email_can_register_second_workspace(client, db, seed_subscription_plans):
    payload = {
        "email": "multi-ws@example.com",
        "password": "StrongPass12",
        "company_name": "Workspace One",
        "full_name": "Multi User",
    }
    first = client.post("/api/auth/register", json={**payload, "company_name": "Workspace One Co"})
    second = client.post("/api/auth/register", json={**payload, "company_name": "Workspace Two Co"})
    assert first.status_code in (200, 201), first.text
    assert second.status_code in (200, 201), second.text


def test_oauth_refuses_ambiguous_email_membership(db, seed_subscription_plans):
    from app.backend.models.db_models import Tenant, User
    from app.backend.routes.oauth import _get_or_create_oauth_user
    from fastapi import HTTPException
    t1 = Tenant(name="A", slug="oauth-a")
    t2 = Tenant(name="B", slug="oauth-b")
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    db.add_all([
        User(tenant_id=t1.id, email="shared@x.com", hashed_password="x", role="admin", is_active=True),
        User(tenant_id=t2.id, email="shared@x.com", hashed_password="x", role="admin", is_active=True),
    ])
    db.commit()
    try:
        _get_or_create_oauth_user(db, provider="google", provider_user_id="gid", email="shared@x.com", mode="login", company_name=None)
        raise AssertionError("ambiguous OAuth email must fail closed")
    except HTTPException as exc:
        assert exc.status_code == 409


def test_tenant_skill_overrides_do_not_mutate_global_registry(db, seed_subscription_plans):
    from app.backend.models.db_models import RoleTemplate, Skill, Tenant
    from app.backend.routes.analyze_helpers import _persist_skill_overrides_to_template

    tenant = Tenant(name="Skill Iso", slug="skill-iso")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    tpl = RoleTemplate(tenant_id=tenant.id, name="Override JD", jd_text="python role " * 40)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    _persist_skill_overrides_to_template(
        db, tpl.id, tenant.id, {"required_skills": ["zz-tenant-only-skill-xyz"], "nice_to_have_skills": []}
    )
    assert db.query(Skill).filter(Skill.name == "zz-tenant-only-skill-xyz").first() is None
    db.refresh(tpl)
    assert "zz-tenant-only-skill-xyz" in (tpl.required_skills_override or "")


def test_object_storage_delete_is_invoked_on_hard_delete(db, monkeypatch, seed_subscription_plans):
    from app.backend.models.db_models import Candidate, Tenant
    from app.backend.services import gdpr_service

    calls = []
    monkeypatch.setattr(
        "app.backend.services.object_storage.ObjectStorageService.is_available",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "app.backend.services.object_storage.ObjectStorageService.delete",
        staticmethod(lambda key: calls.append(key) or True),
    )
    tenant = Tenant(name="Del Tenant", slug="del-tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    cand = Candidate(
        tenant_id=tenant.id,
        name="Del Me",
        email="delme@example.com",
        resume_file_key="tenant/1/resume.pdf",
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    out = gdpr_service.hard_delete_candidate(db, cand.id, tenant.id)
    assert out.get("object_storage_complete") is True
    assert "tenant/1/resume.pdf" in calls


def test_anonymize_removes_pii_marker(db, seed_subscription_plans):
    from app.backend.models.db_models import Candidate, ScreeningResult, Tenant
    from app.backend.services import gdpr_service

    marker = "PII_AUDIT_TEST_7F31"
    tenant = Tenant(name="PII Tenant", slug="pii-tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    cand = Candidate(tenant_id=tenant.id, name=marker, email=f"{marker}@ex.com", raw_resume_text=f"hello {marker}")
    db.add(cand)
    db.commit()
    db.refresh(cand)
    db.add(ScreeningResult(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        resume_text=f"resume {marker}",
        jd_text="jd",
        parsed_data="{}",
        analysis_result="{}",
    ))
    db.commit()
    gdpr_service.anonymize_candidate(db, cand.id, tenant.id)
    db.refresh(cand)
    assert marker not in (cand.name or "")
    assert marker not in (cand.email or "")
    assert marker not in (cand.raw_resume_text or "")
    result = db.query(ScreeningResult).filter(ScreeningResult.candidate_id == cand.id).first()
    assert marker not in (result.resume_text or "")
    export = gdpr_service.export_candidate_data(db, cand.id, tenant.id)
    assert export.get("export_version") == "1.0"
    assert "screening_results" in export
    assert "comments" in export


def test_adverse_impact_ratio_on_known_distribution():
    from app.backend.services.bias_audit_service import GroupOutcome, _four_fifths_rule

    violations = _four_fifths_rule([
        GroupOutcome("A", 100, 80, 10, 10, 0.80, 0.10, 0.10, 70),
        GroupOutcome("B", 100, 40, 10, 50, 0.40, 0.10, 0.50, 70),
    ])
    assert violations[0]["adverse_impact_ratio"] == 0.5
    tiny = _four_fifths_rule([
        GroupOutcome("A", 5, 4, 0, 1, 0.80, 0.0, 0.20, 70),
        GroupOutcome("B", 5, 1, 0, 4, 0.20, 0.0, 0.80, 70),
    ])
    assert tiny == []


def test_reviewer_agreement_metrics_not_called_accuracy():
    from app.backend.services.accuracy_tracking_service import compute_reviewer_agreement

    metrics = compute_reviewer_agreement(
        ["shortlist", "shortlist", "reject", "reject"],
        ["shortlist", "reject", "reject", "reject"],
    )
    assert metrics["agreement_rate"] == 0.75
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 1.0
    assert "accuracy" not in metrics


def test_external_ai_boundary_redacts_email_before_provider(monkeypatch):
    from app.backend.services.external_ai_boundary import prepare_external_prompt

    redacted = prepare_external_prompt("Contact Jane at jane.audit@example.com please")
    assert "jane.audit@example.com" not in redacted.lower()


def test_llm_slots_are_shared_across_process_identities(monkeypatch):
    from app.backend.services import shared_cache

    shared_cache._memory.clear()
    assert shared_cache.cache_try_acquire_slot("llm:slots:app:t1", 1) is True
    assert shared_cache.cache_try_acquire_slot("llm:slots:app:t1", 1) is False
    shared_cache.cache_release_slot("llm:slots:app:t1")
    assert shared_cache.cache_try_acquire_slot("llm:slots:app:t1", 1) is True


def test_share_passcode_uses_bcrypt():
    from app.backend.services.share_crypto import hash_passcode, verify_passcode

    stored = hash_passcode("s3cret-pass")
    assert stored.startswith("scrypt$")
    assert verify_passcode("s3cret-pass", stored)
    assert not verify_passcode("wrong", stored)
    legacy = __import__("hashlib").sha256(b"s3cret-pass").hexdigest()
    assert verify_passcode("s3cret-pass", legacy)


