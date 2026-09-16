# app/backend/tests/test_audit_phase_c_secrets_entitlement.py
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("INTEGRATION_MASTER_KEY", "ab" * 32)

from app.backend.models.db_models import ATSConnection, Tenant
from app.backend.services.ats_connector import GenericAdapter
from app.backend.services.integration_secrets import decrypt_secret, encrypt_secret, secret_configured


def test_roundtrip_not_plaintext():
    ct = encrypt_secret("ghp_secret_value")
    assert ct != "ghp_secret_value"
    assert ct.startswith("enc:v1:")
    assert decrypt_secret(ct) == "ghp_secret_value"


def test_none_passthrough():
    assert encrypt_secret(None) is None
    assert decrypt_secret(None) is None
    assert secret_configured(None) is False


def test_wrong_key_does_not_include_secret(monkeypatch):
    ct = encrypt_secret("super-secret")
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", "cd" * 32)
    import app.backend.services.integration_secrets as sec

    monkeypatch.setattr(sec, "_current_keys", lambda: {1: bytes.fromhex("cd" * 32)})
    with pytest.raises(Exception) as ei:
        decrypt_secret(ct)
    assert "super-secret" not in str(ei.value)


def test_decrypt_enc_v1_after_master_rotation(monkeypatch):
    key_a = "aa" * 32
    key_b = "bb" * 32
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", key_a)
    monkeypatch.delenv("INTEGRATION_MASTER_KEY_PREVIOUS", raising=False)
    ct = encrypt_secret("rotate-me")
    assert ct.startswith("enc:v1:")
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", key_b)
    monkeypatch.setenv("INTEGRATION_MASTER_KEY_PREVIOUS", key_a)
    assert decrypt_secret(ct) == "rotate-me"


def test_ats_create_encrypts_api_key_and_hides_from_json(auth_client, db, seed_subscription_plans):
    resp = auth_client.post(
        "/api/ats/connections",
        json={"provider": "generic", "label": "Sec ATS", "api_key": "plain-key"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key_configured"] is True
    assert "plain-key" not in resp.text
    assert "api_key" not in body
    db.expire_all()
    conn = db.get(ATSConnection, body["id"])
    assert conn.api_key.startswith("enc:v")


def test_adapter_headers_use_decrypted_api_key():
    conn = MagicMock()
    conn.api_key = encrypt_secret("plain-key")
    conn.webhook_secret = None
    headers = GenericAdapter().get_headers(conn)
    assert headers["Authorization"] == "Bearer plain-key"
    assert "enc:v" not in headers["Authorization"]


def test_ats_update_encrypts_all_secret_fields(auth_client, db, seed_subscription_plans):
    created = auth_client.post(
        "/api/ats/connections",
        json={"provider": "generic", "label": "Up ATS", "api_key": "k1"},
    )
    assert created.status_code == 201
    conn_id = created.json()["id"]
    resp = auth_client.put(
        f"/api/ats/connections/{conn_id}",
        json={"api_key": "k2", "api_secret": "s2", "webhook_secret": "w2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "k2" not in resp.text
    assert "s2" not in resp.text
    assert "w2" not in resp.text
    assert "api_key" not in body
    assert "api_secret" not in body
    assert "webhook_secret" not in body
    db.expire_all()
    conn = db.get(ATSConnection, conn_id)
    assert conn.api_key.startswith("enc:v")
    assert conn.api_secret.startswith("enc:v")
    assert conn.webhook_secret.startswith("enc:v")


def test_backfill_encrypts_plaintext_and_is_idempotent(db, seed_subscription_plans):
    from app.backend.services.integration_secrets import backfill_ats_secrets, decrypt_secret

    tenant = Tenant(name="ats-bf", slug="ats-bf")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    conn = ATSConnection(
        tenant_id=tenant.id,
        provider="generic",
        label="legacy",
        api_key="plain-api",
        api_secret="plain-secret",
        webhook_secret="plain-hook",
    )
    already = ATSConnection(
        tenant_id=tenant.id,
        provider="generic",
        label="already",
        api_key=encrypt_secret("kept"),
        api_secret=None,
        webhook_secret="",
    )
    db.add_all([conn, already])
    db.commit()
    first = backfill_ats_secrets(db)
    assert first["encrypted"] >= 3
    db.refresh(conn)
    db.refresh(already)
    kept = already.api_key
    assert conn.api_key.startswith("enc:v")
    assert decrypt_secret(conn.api_key) == "plain-api"
    assert decrypt_secret(conn.api_secret) == "plain-secret"
    assert decrypt_secret(conn.webhook_secret) == "plain-hook"
    second = backfill_ats_secrets(db)
    db.refresh(already)
    assert already.api_key == kept
    assert second["encrypted"] == 0


def test_production_encrypt_without_master_key_fails_closed(monkeypatch):
    monkeypatch.delenv("INTEGRATION_MASTER_KEY", raising=False)
    monkeypatch.setenv("TESTING", "false")
    import app.backend.services.integration_secrets as sec

    monkeypatch.setattr(sec, "_is_testing", lambda: False)
    monkeypatch.setattr(sec, "_current_keys", lambda: {})
    with pytest.raises(RuntimeError, match="INTEGRATION_MASTER_KEY"):
        encrypt_secret("must-not-store-plain")


def test_check_quota_uses_effective_plan_not_raw_assignment(db, seed_subscription_plans):
    from app.backend.models.db_models import SubscriptionPlan, Tenant
    from app.backend.routes.analyze_helpers import _check_and_increment_usage
    from app.backend.services.billing.quota import check_quota

    starter = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "starter").first()
    growth = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "growth").first()
    tenant = Tenant(
        name="quota-stale-plan",
        slug="quota-stale-plan",
        plan_id=growth.id,
        subscription_status="canceled",
        analyses_count_this_month=0,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    pre = check_quota(tenant.id, db)
    assert pre["plan"] in ("starter", "free")
    starter_limit = __import__("json").loads(starter.limits)["analyses_per_month"]
    growth_limit = __import__("json").loads(growth.limits)["analyses_per_month"]
    assert pre["limit"] == starter_limit
    assert pre["limit"] != growth_limit

    tenant.analyses_count_this_month = starter_limit
    db.commit()
    blocked = check_quota(tenant.id, db)
    assert blocked["allowed"] is False
    allowed, _ = _check_and_increment_usage(db, tenant.id, 1, 1)
    assert allowed is False


def test_check_quota_active_paid_and_trial(db, seed_subscription_plans):
    from datetime import datetime, timedelta, timezone
    from app.backend.models.db_models import SubscriptionPlan, Tenant
    from app.backend.services.billing.quota import check_quota

    growth = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "growth").first()
    growth_limit = __import__("json").loads(growth.limits)["analyses_per_month"]
    paid = Tenant(
        name="quota-paid",
        slug="quota-paid",
        plan_id=growth.id,
        subscription_status="active",
    )
    trial = Tenant(
        name="quota-trial",
        slug="quota-trial",
        plan_id=growth.id,
        subscription_status="trialing",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=5),
    )
    expired_trial = Tenant(
        name="quota-trial-exp",
        slug="quota-trial-exp",
        plan_id=growth.id,
        subscription_status="trialing",
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add_all([paid, trial, expired_trial])
    db.commit()
    assert check_quota(paid.id, db)["limit"] == growth_limit
    assert check_quota(trial.id, db)["limit"] == growth_limit
    expired = check_quota(expired_trial.id, db)
    assert expired["plan"] in ("starter", "free")
    assert expired["limit"] != growth_limit
